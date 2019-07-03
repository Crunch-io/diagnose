"""Probes, a library for instrumenting code at runtime."""

from .managers import ProbeManager
from . import instruments
from .probes import FunctionProbe

# A global since it should be one per process.
# You _may_ make another, but most people will just want the one.
manager = ProbeManager()

__all__ = ("FunctionProbe", "manager", "ProbeManager", "instruments")
