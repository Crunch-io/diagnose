"""Wrappers to monitor function execution."""

from collections import namedtuple
import datetime
import functools
import gc
import linecache
import sys
import time
import traceback
import types
import weakref

import hunter
import mock


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
    for probe in active_probes.itervalues():
        probe.start()


class FunctionProbe(object):
    """A wrapper for a function, to monitor its execution.

    target: a dotted Python path to the function to wrap.
    instruments: a dict of ("spec id": Instrument instance) pairs.

    When started, a FunctionProbe "monkey-patches" its target, replacing
    it with a wrapper function. That wrapper calls the original target
    function, and then calls each instrument. If any instruments are "internal",
    the probe registers a trace function (via `sys.settrace`) and then
    calls those instruments just before the original target function returns;
    the instrument values are evaluated in the globals and locals of that frame.
    External instruments are evaluated after the function returns; they are
    much less invasive since they do not call settrace, but they are
    limited to reading the function's inputs, outputs, and timing.
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
        self.patches = {}
        active_probes[target] = self

    def __str__(self):
        return "%s(target=%r, instruments=%r)" % (
            self.__class__.__name__,
            self.target,
            self.instruments,
        )

    __repr__ = __str__

    def make_patches(self):
        """Set self.patches to a list of mock._patch objects which wrap our target function."""
        primary_patch = mock.patch(self.target)

        # Replace the target with a wrapper.
        original, local = primary_patch.get_original()
        if isinstance(original, types.FunctionType):
            base = original
        elif isinstance(original, (staticmethod, classmethod)):
            base = original.__func__
        else:
            raise TypeError(
                "Cannot probe: %s is not a function." % (repr(self.target),)
            )
        varnames = base.func_code.co_varnames

        @functools.wraps(base)
        def probe_wrapper(*args, **kwargs):
            now = datetime.datetime.utcnow()

            hotspots = HotspotsFinder()
            internals, externals = [], []
            for I in self.instruments.itervalues():
                if I.expires and now > I.expires:
                    continue
                if I.check_call(self, *args, **kwargs):
                    if I.internal:
                        internals.append(I)
                    else:
                        externals.append(I)
                        if "hotspots" in I.value or "hotspots" in (
                            I.custom.get("tags") or ""
                        ):
                            hotspots.enabled = True

            target_obj, target_func_name = self.target.rsplit(".", 1)
            is_unwrapped = base.func_code.co_name == target_func_name
            if internals:
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
                    TraceHandler(self, internals),
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
                start = time.time()
                result = base(*args, **kwargs)
                if hotspots.enabled:
                    hotspots.finish()
                end = time.time()
                elapsed = end - start
                if externals:
                    _locals = {
                        "result": result,
                        "start": start,
                        "end": end,
                        "elapsed": elapsed,
                        "now": now,
                        "args": args,
                        "kwargs": kwargs,
                        "frame": sys._getframe(),
                    }
                    if hotspots.enabled:
                        _locals["hotspots"] = hotspots
                    # Add positional args to locals by name.
                    for i, argname in enumerate(varnames[: len(args)]):
                        _locals[argname] = args[i]
                    # Add kwargs to locals
                    _locals.update(kwargs)

                    for instrument in externals:
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

        if isinstance(original, staticmethod):
            probe_wrapper = staticmethod(probe_wrapper)
        elif isinstance(original, classmethod):
            probe_wrapper = classmethod(probe_wrapper)

        primary_patch.new = probe_wrapper
        patches = {0: primary_patch}

        # Add patches for any other modules/classes which have
        # the target as an attribute, or "registry" dicts which have
        # the target as a value.
        _resolved_target = primary_patch.getter()
        for ref in gc.get_referrers(original):
            if not isinstance(ref, dict):
                continue

            names = [k for k, v in ref.items() if v is original]
            for parent in gc.get_referrers(ref):
                if parent is _resolved_target or parent is primary_patch:
                    continue

                if getattr(parent, "__dict__", None) is ref:
                    # An attribute of a module or class or instance.
                    for name in names:
                        patch_id = len(patches)
                        patch = WeakMethodPatch(
                            self.make_getter(patch_id, parent), name, probe_wrapper
                        )
                        patches[patch_id] = patch
                else:
                    for gpa in gc.get_referrers(parent):
                        if getattr(gpa, "__dict__", None) is parent:
                            # A member of a dict which is
                            # an attribute of a module or class or instance.
                            for name in names:
                                patch_id = len(patches)
                                patch = DictPatch(ref, name, probe_wrapper)
                                patches[patch_id] = patch
                            break

        self.patches = patches

    @staticmethod
    def maybe_unwrap(func):
        """Return the given function, without its probe_wrapper if it has one."""
        if getattr(getattr(func, "__code__", None), "co_name", "") == "probe_wrapper":
            return func.__closure__[0].cell_contents
        else:
            return func

    def make_getter(self, patch_id, parent):
        def callback(ref):
            p = self.patches.get(patch_id, None)
            if p:
                try:
                    p.stop()
                except RuntimeError:
                    # Already stopped. Ignore.
                    pass
                self.patches.pop(patch_id, None)

        try:
            getter = weakref.ref(parent, callback)
        except TypeError:

            def getter():
                return parent

        return getter

    def start(self):
        """Apply self.patches. Safe to call after already started."""
        if not self.patches:
            self.make_patches()
        for p in self.patches.itervalues():
            if not hasattr(p, "is_local"):
                p.start()

    def stop(self):
        for p in self.patches.itervalues():
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
            worst = max(((call[2], lineno) for lineno, call in self.calls.iteritems()))
            self.worst = CallTime(*worst, source=self.source(worst[1]))

            slowest = max(
                ((call[1], lineno) for lineno, call in self.calls.iteritems())
            )
            self.slowest = CallTime(*slowest, source=self.source(slowest[1]))
        else:
            self.worst = self.slowest = CallTime(None, None, None)

    def source(self, lineno):
        return linecache.getline(self.filename, lineno)


# ----------------------------- Weak patch ----------------------------- #


class WeakMethodPatch(object):
    """A Patch for an attribute a Python object.

    On start/__enter__, calls self.getter() which should return an object,
    then replaces the given attribute of that object with the new value.
    On stop/__exit__, replaces the same attribute with the previous value.

    Used by FunctionPatch to replace references to functions which appear in
    modules, classes, or other objects. Weak references are used internally
    so that, if the object is removed from that module etc (has no more strong
    references), then the patch is automatically abandoned.
    """

    def __init__(self, getter, attribute, new):
        self.getter = getter
        self.attribute = attribute
        self.new = new

    def get_original(self):
        target = self.getter()
        name = self.attribute

        original = omitted
        local = False

        try:
            original = target.__dict__[name]
        except (AttributeError, KeyError):
            original = getattr(target, name, omitted)
        else:
            local = True

        if original is omitted:
            raise AttributeError("%s does not have the attribute %r" % (target, name))
        return original, local

    def __enter__(self):
        """Perform the patch."""
        original, local = self.get_original()
        self.temp_original = weakref.ref(original)
        self.is_local = local
        setattr(self.getter(), self.attribute, self.new)
        return self.new

    def __exit__(self, *exc_info):
        """Undo the patch."""
        if not hasattr(self, "is_local"):
            raise RuntimeError("stop called on unstarted patcher")

        target = self.getter()
        if target is None:
            return

        original = self.temp_original()
        if original is None:
            return

        if getattr(target, self.attribute, None) is self.new:
            if self.is_local:
                setattr(target, self.attribute, original)
            else:
                delattr(target, self.attribute)
                if not hasattr(target, self.attribute):
                    # needed for proxy objects like django settings
                    setattr(target, self.attribute, original)

        del self.is_local

    def start(self):
        """Activate a patch, returning any created mock."""
        result = self.__enter__()
        return result

    def stop(self):
        """Stop an active patch."""
        return self.__exit__()


class DictPatch(object):
    """A Patch for a member of a Python dictionary.

    On start/__enter__, replaces the member of the given dictionary
    identified by the given key with a new object. On stop/__exit__,
    replaces the same key with the previous object.

    Used by FunctionPatch to replace references to functions which appear
    in any dictionary, such as a function registry.
    """

    def __init__(self, dictionary, key, new):
        self.dictionary = dictionary
        self.key = key
        self.new = new

    def get_original(self):
        return self.dictionary[self.key], True

    def __enter__(self):
        """Perform the patch."""
        original, local = self.get_original()
        self.temp_original = original
        self.is_local = local
        self.dictionary[self.key] = self.new
        return self.new

    def __exit__(self, *exc_info):
        """Undo the patch."""
        if not hasattr(self, "is_local"):
            raise RuntimeError("stop called on unstarted patcher")

        self.dictionary[self.key] = self.temp_original

        del self.is_local

    def start(self):
        """Activate a patch, returning any created mock."""
        result = self.__enter__()
        return result

    def stop(self):
        """Stop an active patch."""
        return self.__exit__()
