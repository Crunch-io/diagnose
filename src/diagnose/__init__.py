"""Diagnose, a library for instrumenting code at runtime."""

from contextlib import contextmanager

from . import managers
from . import instruments
from . import probes

# A global since it should be one per process.
# You _may_ make another, but most people will just want the one.
# You should probably `import diagnose` and then refer to `diagnose.manager`
# rather than `from diagnose import manager` in case some framework
# decides to replace diagnose.manager with an instance of a subclass.
manager = managers.InstrumentManager()

__all__ = ("probes", "instruments", "manager", "managers", "sensor")


@contextmanager
def sensor(target, value="result", name="test_instrument", event="return", mgr=None):
    """Attach a probe to the given target and yield a ProbeTestInstrument."""
    probe = probes.attach_to(target)
    probe.instruments[name] = i = instruments.ProbeTestInstrument(
        name, value, event, expires=None, mgr=mgr
    )
    probe.start()

    yield i

    probe.instruments.pop(name)
    if not probe.instruments:
        p = probes.active_probes.pop(target, None)
        if p is not None:
            p.stop()
