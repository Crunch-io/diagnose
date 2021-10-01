"""Instruments which receive probe events."""

import datetime
import sys
import six

try:
    from statsd import statsd
except ImportError:
    statsd = None

import diagnose
from diagnose import probes


omitted = object()


class Instrument(object):
    """An instrument which receives FunctionProbe events.

    Attributes:
        * name: a name for the instrument; may be used in output, such as
                when constructing statsd metric names.
        * value: a Python expression to be evaluated; its result is the
                 "process variable" to be used as the instrument sees fit.
        * event: a string declaring when to fire the instrument, one of:
            * call: evaluate the value just before calling the wrapped
                    function, in a context which contains the local variables:
                * start: float time
                * now: datetime.datetime.utcnow()
                * args/kwargs: inputs to the target function; these are
                               also included in locals() by their argnames.
                * frame: sys._getframe() of the patch wrapper
             * return: the default; evaluate the value just after the wrapped
                       function returns, in a context with the additional locals:
                * result: the return value of the target function
                * end/elapsed: float times
             * end: evaluate the value in the context of the wrapped function
                    just before it returns.
        * expires: a datetime, after which point the instrument will not fire,
                   or None to mean no expiration
        * custom: a dict of any additional data for subclasses. May include
                  other information for filtering events, set points for
                  closed-loop controllers, or other information specific
                  to the kind of instrument.
        * mgr: an InstrumentManager instance. If None, defaults to the global
               diagnose.manager
    """

    error_expiration = datetime.datetime(1970, 1, 1)

    def __init__(
        self, name, value, event="return", expires=None, custom=None, mgr=None, **kwargs
    ):
        if mgr is None:
            mgr = diagnose.manager
        self.mgr = mgr
        self.name = name
        self.value = value
        self.event = event
        self.expires = expires
        self.custom = custom or {}

    def __str__(self):
        return "%s(name=%r, value=%r, event=%r, expires=%r, custom=%r)" % (
            self.__class__.__name__,
            self.name,
            self.value,
            self.event,
            self.expires,
            self.custom,
        )

    __repr__ = __str__

    def evaluate(self, value, _globals, _locals):
        # Skip eval() if a local variable name
        v = _locals.get(value, omitted)
        if v is omitted:
            v = eval(value, _globals, _locals)
        return v

    def merge_tags(self, _globals, _locals):
        tags = self.mgr.get_tags()
        tag_expr = self.custom.get("tags", None)
        if tag_expr:
            t = self.evaluate(tag_expr, _globals, _locals)
            if isinstance(t, dict):
                t = ["%s:%s" % pair for pair in six.iteritems(t)]
            if not isinstance(t, list):
                raise TypeError("Cannot send non-list of tags: %s" % (t,))
            tags = tags + t
        return tags

    def fire(self, _globals, _locals):
        raise NotImplementedError()

    def check_call(self, probe, *args, **kwargs):
        """Return True if this instrument should be applied, False otherwise.

        By default, this always returns True. Override this in a subclass
        to check the supplied function args/kwargs, or other state,
        such as self.custom, environment variables, or threadlocals.
        """
        return self.mgr.check_call(probe, self, *args, **kwargs)

    def handle_error(self, probe):
        if self.error_expiration:
            # Set self.expires to long ago, which keeps it from firing until:
            # a) someone edits the probe, or
            # b) processes restart, which could be new code that fixes things.
            # Even if it doesn't, we only get ~1 error per process,
            # not 1 per call to the target function.
            self.expires = self.error_expiration
        self.mgr.handle_error(probe, self)

    def __call__(self, f):
        """Use self as a decorator, attaching a probe to the wrapped function."""
        classname = sys._getframe(1).f_code.co_name
        if classname == "<module>":
            target = "%s.%s" % (
                f.__module__,
                getattr(f, "__name__", None) or getattr(f, "func_name"),
            )
        else:
            target = "%s.%s.%s" % (
                f.__module__,
                classname,
                getattr(f, "__name__", None) or getattr(f, "func_name"),
            )

        probe = probes.attach_to(target)
        # If we prefix the spec_id with self.mgr.short_id, then that
        # manager would immediately remove this instrument because
        # it's not in self.mgr.specs!
        # Use a hardcoded prefix instead so no manager drops it.
        probe.instruments["hardcode:%s" % (hash(target),)] = self

        return f


class LogInstrument(Instrument):
    """An instrument that prints a log message."""

    MAX_CHARS = 2000
    out = sys.stdout

    def fire(self, _globals, _locals):
        v = self.evaluate(self.value, _globals, _locals)
        if v is None:
            return

        v = str(v)
        if len(v) > self.MAX_CHARS:
            v = v[: self.MAX_CHARS - 3] + "..."

        tags = self.merge_tags(_globals, _locals)

        t = str(tags)
        if len(t) > self.MAX_CHARS:
            t = t[: self.MAX_CHARS - 3] + "..."

        self.emit(self.name, v, t)

    def emit(self, name, value, tags):
        self.out.write("Probe (%s)[tags=%s] = %s\n" % (name, tags, value))


class StatsdInstrumentBase(Instrument):
    """An instrument that sends a value to statsd."""

    MAX_CHARS = 2000

    def fire(self, _globals, _locals):
        v = self.evaluate(self.value, _globals, _locals)
        if v is None:
            return

        if not isinstance(v, six.integer_types + (int,)):
            v = str(v)
            if len(v) > self.MAX_CHARS:
                v = v[: self.MAX_CHARS] + "..."
            raise TypeError("Cannot send non-numeric metric: %s" % (v,))

        self.emit(self.name, v, self.merge_tags(_globals, _locals))

    def emit(self, name, value, tags):
        raise NotImplementedError()


class HistogramInstrument(StatsdInstrumentBase):
    def emit(self, name, value, tags):
        statsd.histogram(name, value, tags=tags)


class IncrementInstrument(StatsdInstrumentBase):
    def emit(self, name, value, tags):
        statsd.increment(name, value, tags=tags)


class ProbeTestInstrument(Instrument):
    """An instrument that stores values in self.results."""

    def __init__(self, *args, **kwargs):
        Instrument.__init__(self, *args, **kwargs)
        self.log = []

    @property
    def results(self):
        return [result for tags, result in self.log]

    def fire(self, _globals, _locals):
        v = self.evaluate(self.value, _globals, _locals)
        tags = self.merge_tags(_globals, _locals)
        self.log.append((tags, v))
