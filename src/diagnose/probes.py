"""Wrappers to monitor function execution."""

from collections import namedtuple
import datetime
import functools
import linecache
import sys
import time
import traceback

import hunter
import six

from diagnose import patchlib

omitted = object()

# A registry of FunctionProbe instances.
# Since probes patch functions, it's imperative that the same function
# not be patched twice, even in test suites or other scenarios where
# two services might be applying probes at the same time. This global
# registry enforces that. Callers should add instruments to probes instead.
# The FunctionProbe class itself uses this to disallow attaching two
# FunctionProbes to the same target.
active_probes = {}


def attach_to(target):
    """Return the probe attached to the given target, or a new one if needed."""
    probe = active_probes.get(target, None)
    if probe is None:
        probe = FunctionProbe(target)
        active_probes[target] = probe
    return probe


def start_all():
    for probe in six.itervalues(active_probes):
        probe.start()


class FunctionProbe(object):
    """A wrapper for a function, to monitor its execution.

    target: a dotted Python path to the function to wrap.
    instruments: a dict of ("spec id": Instrument instance) pairs.

    When started, a FunctionProbe "monkey-patches" its target, replacing
    it with a wrapper function. That wrapper calls the original target
    function, calling each instrument at the event it specifies: call, end,
    or return. If the event is "end", then the probe registers a trace function
    (via `sys.settrace`) and then calls those instruments just before the
    original target function returns; the instrument values are evaluated
    in the globals and locals of that frame. "Call" and "return" instruments
    are evaluated before or after the function returns; they are much less
    invasive since they do not call settrace, but they are limited to reading
    the function's inputs, outputs, and timing.
    """

    def __init__(self, target, instruments=None):
        if target in active_probes:
            raise RuntimeError(
                "Cannot apply two probes to the same target. "
                "Try calling attach_to(target) instead of FunctionProbe(target)."
            )
        self.target = target
        if instruments is None:
            instruments = {}
        self.instruments = instruments
        self.patches = []
        active_probes[target] = self

    def __str__(self):
        return "%s(target=%r, instruments=%r)" % (
            self.__class__.__name__,
            self.target,
            self.instruments,
        )

    __repr__ = __str__

    def make_wrapper(self, base):
        varnames = self.maybe_unwrap(base).__code__.co_varnames

        @functools.wraps(base)
        def probe_wrapper(*args, **kwargs):
            now = datetime.datetime.utcnow()

            hotspots = HotspotsFinder()
            instruments_by_event = {"call": [], "return": [], "end": []}
            for I in six.itervalues(self.instruments):
                if I.expires and now > I.expires:
                    continue
                if I.check_call(self, *args, **kwargs):
                    instruments_by_event[I.event].append(I)
                    if I.event in ("call", "return"):
                        if "hotspots" in I.value or "hotspots" in (
                            I.custom.get("tags") or ""
                        ):
                            hotspots.enabled = True

            target_obj, target_func_name = self.target.rsplit(".", 1)
            is_unwrapped = base.__code__.co_name == target_func_name
            if instruments_by_event["end"]:
                # We have instruments that require evaluation in the local
                # context of the function. Call sys.settrace() to gain access.
                predicate = hunter.When(
                    hunter.Query(
                        # Only trace returns (this will include exceptions!)...
                        kind="return",
                        # ...and only in the given function...
                        function=target_func_name,
                        # ...(no deeper).
                        depth=0,
                    )
                    if is_unwrapped
                    else hunter.Query(
                        # Only trace returns (this will include exceptions!)...
                        kind="return",
                        # ...and only in the given function...
                        function=target_func_name,
                        # ...but we don't know how many times it's been wrapped.
                        # Use the module instead as an approximate match.
                        # This may catch other functions with the same name
                        # in the same module, but not much we can do about
                        # that without a custom Cython Query.
                        module_in=target_obj,
                    ),
                    TraceHandler(self, instruments_by_event["end"]),
                )
                tracer = hunter.Tracer(
                    # There's no need to call threading.settrace() because
                    # a) we're targeting a function we're about to call
                    #    in the same thread,
                    # b) we're going to undo it immediately after, and
                    # c) it would collide with other threads if they did
                    #    the same concurrently.
                    threading_support=False
                ).trace(predicate)
            elif hotspots.enabled:
                # We have instruments that require timing internal lines.
                # Call sys.settrace() to gain access.
                predicate = hunter.When(
                    hunter.Query(
                        # Only trace lines...
                        kind="line",
                        # ...and only in the given function...
                        function=target_func_name,
                        # ...(no deeper).
                        depth=1,
                    )
                    if is_unwrapped
                    else hunter.Query(
                        # Only trace lines...
                        kind="line",
                        # ...and only in the given function...
                        function=target_func_name,
                        # ...but we don't know how many times it's been wrapped.
                        # Use the module instead as an approximate match.
                        # This may catch other functions with the same name
                        # in the same module, but not much we can do about
                        # that without a custom Cython Query.
                        module_in=target_obj,
                    ),
                    hotspots,
                )
                tracer = hunter.Tracer(
                    # There's no need to call threading.settrace() because
                    # a) we're targeting a function we're about to call
                    #    in the same thread,
                    # b) we're going to undo it immediately after, and
                    # c) it would collide with other threads if they did
                    #    the same concurrently.
                    threading_support=False
                ).trace(predicate)
            else:
                tracer = None

            try:
                if instruments_by_event["call"] or instruments_by_event["return"]:
                    start = time.time()
                    _locals = {
                        "start": start,
                        "now": now,
                        "args": args,
                        "kwargs": kwargs,
                        "frame": sys._getframe(),
                    }
                    # Add positional args to locals by name.
                    for i, argname in enumerate(varnames[: len(args)]):
                        if argname not in ("args", "kwargs"):
                            _locals[argname] = args[i]
                    # Add kwargs to locals
                    _locals.update(kwargs)

                for instrument in instruments_by_event["call"]:
                    try:
                        instrument.fire(instrument.mgr.global_namespace, _locals)
                    except:
                        try:
                            instrument.handle_error(self)
                        except:
                            traceback.print_exc()

                # Execute the base function and obtain its result.
                try:
                    result = base(*args, **kwargs)
                except:
                    result = sys.exc_info()[1]
                    raise
                finally:
                    if hotspots.enabled:
                        hotspots.finish()
                        _locals["hotspots"] = hotspots

                    if instruments_by_event["return"]:
                        end = time.time()
                        elapsed = end - start
                        _locals.update(
                            {"result": result, "end": end, "elapsed": elapsed}
                        )

                    for instrument in instruments_by_event["return"]:
                        try:
                            instrument.fire(instrument.mgr.global_namespace, _locals)
                        except:
                            try:
                                instrument.handle_error(self)
                            except:
                                traceback.print_exc()

                return result
            finally:
                if tracer is not None:
                    tracer.stop()

        return probe_wrapper

    @staticmethod
    def maybe_unwrap(func):
        """Return the given function, without its probe_wrapper if it has one."""
        if getattr(getattr(func, "__code__", None), "co_name", "") == "probe_wrapper":
            return func.__closure__[0].cell_contents
        else:
            try:
                # If the given func is a func returned from @functools.wraps(orig),
                # then the only cell in its closure will be the `orig` function.
                # There may be funcs with a single cell that is a function
                # that is not the wrapped function, in which case we will
                # return the wrong signature here, but use of wraps is so
                # much more common and SO useful to unwrap that we risk it.
                if len(func.__closure__) == 1:
                    f = func.__closure__[0].cell_contents
                    if hasattr(f, "__code__"):
                        return f
            except (AttributeError, IndexError, TypeError):
                pass

        return func

    def start(self):
        """Apply self.patches. Safe to call after already started."""
        if not self.patches:
            self.patches = patchlib.make_patches(self.target, self.make_wrapper)
        for p in self.patches:
            if not hasattr(p, "is_local"):
                p.start()

    def stop(self):
        for p in self.patches:
            try:
                p.stop()
            except RuntimeError:
                # Already stopped. Ignore.
                pass


class TraceHandler(object):
    """A sys.settrace arg, which calls instruments in the context of the frame."""

    def __init__(self, probe, instruments):
        self.probe = probe
        self.instruments = instruments

    def __call__(self, event):
        _globals = event.globals
        _locals = {"__event__": event}
        _locals.update(event.locals)
        for instrument in self.instruments:
            try:
                _g = _globals.copy()
                _g.update(instrument.mgr.global_namespace)
                instrument.fire(_g, _locals)
            except:
                try:
                    instrument.handle_error(self.probe)
                except:
                    traceback.print_exc()


CallTime = namedtuple("CallTime", ["time", "lineno", "source"])


class HotspotsFinder(object):
    """A sys.settrace arg, which records line timings."""

    def __init__(self):
        self.enabled = False
        self._last_time = None
        self._last_line = None
        self.calls = {}
        self.filename = None

    def __call__(self, event=None):
        if self._last_time is not None:
            elapsed = time.time() - self._last_time
            ll = self._last_line
            call = self.calls.get(ll, None)
            if call is None:
                # count, max, sum
                self.calls[ll] = [1, elapsed, elapsed]
            else:
                call[0] += 1
                if elapsed > call[1]:
                    call[1] = elapsed
                call[2] += elapsed

        if event is not None:
            self._last_line = event.lineno
            if self.filename is None:
                self.filename = event.filename
            # Don't include this method's time in the next line time
            self._last_time = time.time()

    def finish(self):
        # Fake the last line time
        self.__call__(event=None)

        if self.calls:
            worst = max(
                ((call[2], lineno) for lineno, call in six.iteritems(self.calls))
            )
            self.worst = CallTime(*worst, source=self.source(worst[1]))

            slowest = max(
                ((call[1], lineno) for lineno, call in six.iteritems(self.calls))
            )
            self.slowest = CallTime(*slowest, source=self.source(slowest[1]))
        else:
            self.worst = self.slowest = CallTime(None, None, None)

    def source(self, lineno):
        return linecache.getline(self.filename, lineno)
