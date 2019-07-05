"""Diagnose, a library for instrumenting code at runtime."""

from . import managers
from . import instruments
from . import probes

# A global since it should be one per process.
# You _may_ make another, but most people will just want the one.
# You should probably `import diagnose` and then refer to `diagnose.manager`
# rather than `from diagnose import manager` in case some framework
# decides to replace diagnose.manager with an instance of a subclass.
manager = managers.InstrumentManager()

__all__ = ("probes", "instruments", "manager", "managers")
