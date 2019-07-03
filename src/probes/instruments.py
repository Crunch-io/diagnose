"""Instruments which receive probe events."""

import datetime
import sys

try:
    from statsd import statsd
except ImportError:
    statsd = None


omitted = object()


class Instrument(object):
    """An instrument which receives FunctionProbe events.

    Attributes:
        * name: a name for the instrument; may be used in output, such as
                when constructing statsd metric names.
        * value: a Python expression to be evaluated; its result is the
                 "process variable" to be used as the instrument sees fit.
        * internal: If True, evaluate the value in the context of the
                    wrapped function (just before it returns). If False
                    (the default), evaluate the value in a wrapper
                    context, which contains the local variables:
                        * result: the return value of the target function
                        * start/end/elapsed: float times
                        * now: datetime.datetime.utcnow()
                        * args/kwargs: inputs to the target function; these are
                          also included in locals() by their argnames.
                        * frame: sys._getframe() of the patch wrapper

        * expires: a datetime, after which point the instrument will not fire,
                   or None to mean no expiration
        * custom: a dict of any additional data for subclasses. May include
                  other information for filtering events, set points for
                  closed-loop controllers, or other information specific
                  to the kind of instrument.
    """

    error_expiration = datetime.datetime(1970, 1, 1)

    def __init__(self, name, value, internal, expires=None, custom=None, **kwargs):
        self.name = name
        self.value = value
        self.internal = internal
        self.expires = expires
        self.custom = custom or {}

    def __str__(self):
        return "%s(name=%r, value=%r, internal=%r, expires=%r, custom=%r)" % (
            self.__class__.__name__,
            self.name,
            self.value,
            self.internal,
            self.expires,
            self.custom,
        )

    __repr__ = __str__

    def evaluate(self, value, eval_context):
        # Skip eval() if a local variable name
        v = eval_context[1].get(value, omitted)
        if v is omitted:
            v = eval(value, *eval_context)
        return v

    def merge_tags(self, tags, eval_context):
        eval_tags = self.custom.get("tags", None)
        if eval_tags:
            t = self.evaluate(eval_tags, eval_context)
            if isinstance(t, dict):
                t = ["%s:%s" % pair for pair in t.iteritems()]
            if not isinstance(t, list):
                raise TypeError("Cannot send non-list of tags: %s" % (t,))
            tags = tags + t
        return tags

    def __call__(self, tags, eval_context):
        raise NotImplementedError()

    def check_call(self, probe, *args, **kwargs):
        """Return True if this instrument should be applied, False otherwise.

        By default, this always returns True. Override this in a subclass
        to check the supplied function args/kwargs, or other state,
        such as self.custom, environment variables, or threadlocals.
        """
        return probe.mgr.check_call(probe, self, *args, **kwargs)

    def expire_due_to_error(self):
        if self.error_expiration:
            # Set self.expires to long ago, which keeps it from firing until:
            # a) someone edits the probe, or
            # b) processes restart, which could be new code that fixes things.
            # Even if it doesn't, we only get ~1 error per process,
            # not 1 per call to the target function.
            self.expires = self.error_expiration


class LogInstrument(Instrument):
    """An instrument that prints a log message."""

    MAX_CHARS = 2000
    out = sys.stdout

    def __call__(self, tags, eval_context):
        v = self.evaluate(self.value, eval_context)
        if v is None:
            return

        v = str(v)
        if len(v) > self.MAX_CHARS:
            v = v[: self.MAX_CHARS - 3] + "..."

        tags = self.merge_tags(tags, eval_context)

        t = str(tags)
        if len(t) > self.MAX_CHARS:
            t = t[: self.MAX_CHARS - 3] + "..."

        self.emit(self.name, v, t)

    def emit(self, name, value, tags):
        self.out.write("Probe (%s)[tags=%s] = %s\n" % (name, tags, value))


class StatsdInstrumentBase(Instrument):
    """An instrument that sends a value to statsd."""

    MAX_CHARS = 2000

    def __call__(self, tags, eval_context):
        v = self.evaluate(self.value, eval_context)
        if v is None:
            return

        if not isinstance(v, (int, float, long)):
            v = str(v)
            if len(v) > self.MAX_CHARS:
                v = v[: self.MAX_CHARS] + "..."
            raise TypeError("Cannot send non-numeric metric: %s" % (v,))

        self.emit(self.name, v, self.merge_tags(tags, eval_context))

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
        self.results = []

    def __call__(self, tags, eval_context):
        v = self.evaluate(self.value, eval_context)
        tags = self.merge_tags(tags, eval_context)
        self.results.append((tags, v))
