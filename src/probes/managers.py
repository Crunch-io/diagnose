"""Managers for probes."""

import datetime
import itertools
import math
import sys
import threading
import time
import traceback

from . import instruments
from .probelib import FunctionProbe


def mag(obj, base=10):
    """Return the magnitude of the object (or its length), or -1 if size is 0."""
    try:
        size = len(obj)
    except TypeError:
        size = obj

    try:
        m = int(math.log10(size) if base == 10 else math.log(size, base))
    except:
        m = -1

    return m


# ----------------------------- Probe Manager ----------------------------- #


class ProbeManager(object):
    """A Manager for Probes, which applies probes to targets.

    Since probes patch functions, it's imperative that the same function
    not be patched twice, even in test suites or other scenarios where
    two services might be applying probes at the same time. This
    singleton manager enforces that; instead, callers add instruments
    to probes.

    specs: a dict of {id: spec} dicts, each of which defines an instrument. Fields:
        * target: the dotted path to a function to wrap in a FunctionProbe
        * lastmodified: the datetime when the spec was last changed
        * lifespan: an int; the number of minutes from lastmodified before
                    the instrument should expire.
        * instrument: a dict of Instrument params (type, name, value, internal, custom).
                      The "expires" param is calculated from lastmodified + lifespan.
        * applied: a dict of {process_id: info} pairs, where "info" is a dict with:
            * lm: the "lastmodified" datetime of the instrument when
                 it was applied to the given process. Monitors can use
                 this to know when processes have the latest spec or not.
            * err: a string with any error raised in the given process, or None
    """

    probes = {}
    instrument_classes = {
        "log": instruments.LogInstrument,
        "hist": instruments.HistogramInstrument,
        "incr": instruments.IncrementInstrument,
    }
    lock = threading.Lock()

    # Adjust here any modules or other globals you want instruments to access.
    # Instrument.value could of course __import__("whatever") but that is slow.
    global_namespace = {"datetime": datetime, "math": math, "time": time, "mag": mag}

    def __init__(self, process_id=None):
        self.probes = {}
        self.target_map = {}
        self.hardcoded_specs = {}
        self.specs = {}
        self.process_id = process_id
        self.apply_thread = None
        self.period = 60

    def hardcode(self, type, name, value, internal=False, custom=None):
        def marker(f):
            classname = sys._getframe(1).f_code.co_name
            if classname == "<module>":
                target = "%s.%s" % (f.__module__, f.func_name)
            else:
                target = "%s.%s.%s" % (f.__module__, classname, f.func_name)
            self.hardcoded_specs[hash(target)] = {
                "target": target,
                "instrument": {
                    "type": type,
                    "name": name,
                    "value": value,
                    "internal": internal,
                    "custom": custom or {},
                },
                "lifespan": None,
                "lastmodified": datetime.datetime.utcnow(),
                "applied": {},
            }
            return f

        return marker

    def apply(self):
        """Add/remove probes to match our spec."""
        with self.lock:
            self._apply()

    def _apply(self):
        seen_instruments = {}
        for id, doc in itertools.chain(
            self.hardcoded_specs.iteritems(), self.specs.iteritems()
        ):
            target = doc["target"]
            seen_instruments[id] = target
            probe, I, cls, expires = None, None, None, None
            try:
                probe = self.probes.get(self.target_map.get(target, None), None)
                if probe is None:
                    probe = FunctionProbe(target, instruments={}, mgr=self)
                    self.target_map[target] = probe.target_id
                    self.probes[probe.target_id] = probe
                    probe.start()

                lifespan = doc["lifespan"]
                if lifespan is None:
                    expires = None
                else:
                    expires = doc["lastmodified"] + datetime.timedelta(minutes=lifespan)

                cls = self.instrument_classes[doc["instrument"]["type"]]

                # Add or modify instruments
                I = probe.instruments.get(id, None)
                modified = False
                if I is None or I.__class__ != cls:
                    probe.instruments[id] = cls(expires=expires, **doc["instrument"])
                    modified = True
                else:
                    for key in ("name", "value", "internal", "custom"):
                        if getattr(I, key) != doc["instrument"][key]:
                            setattr(I, key, doc["instrument"][key])
                            modified = True
                    if I.expires != expires:
                        I.expires = expires
                        modified = True
                if modified:
                    self.mark(id, doc)
            except:
                if I is None:
                    if cls is None:
                        cls = self.instrument_classes[doc["instrument"]["type"]]
                    I = cls(expires=expires, **doc["instrument"])
                self.handle_error(probe, I)
                self.mark(id, doc, exception=True)

        # Remove defunct instruments, probes
        for target_id, probe in self.probes.items():
            probe.instruments = dict(
                [
                    (spec_id, instrument)
                    for spec_id, instrument in probe.instruments.iteritems()
                    if seen_instruments.get(spec_id, None) == probe.target
                ]
            )
            if not probe.instruments:
                p = self.probes.pop(target_id, None)
                if p is not None:
                    p.stop()

    def apply_probes_in_background(self, period=60):
        self.period = period
        if self.apply_thread is None:
            self.apply_thread = t = threading.Thread(target=self._cycle)
            t.setName("apply_probes_in_background")
            t.daemon = True
            t.start()

    def _cycle(self):
        while True:
            time.sleep(self.period)
            try:
                self.apply()
                # log.info("Probe Manager applied")
            except:
                self.handle_error()

    def check_call(self, probe, instrument, *args, **kwargs):
        """Return True if the given instrument should be applied, False otherwise.

        By default, this always returns True. Override this in a subclass
        to check the supplied function args/kwargs, or other state,
        such as instrument.custom, environment variables, or threadlocals.
        """
        return True

    def get_tags(self):
        return []

    def handle_error(self, probe=None, instrument=None):
        """Handle any error raised by an instrument.

        By default, this prints the error.
        Override it to log or whatever else you prefer.
        """
        traceback.print_exc()

    def mark(self, id, doc, exception=False):
        """Record probe application success/failure."""
        doc = self.specs.get(id, None)
        if doc is not None:
            error = None
            if exception:
                error = "Error: %s\n%s" % (
                    repr(sys.exc_info()[1]),
                    traceback.format_exc(),
                )

            newval = {"lm": doc["lastmodified"], "err": error}
            if doc["applied"].get(self.process_id, {}) != newval:
                doc["applied"][self.process_id] = newval


class MongoDBProbeManager(ProbeManager):
    """A ProbeManager which reads and writes specs in MongoDB.

    collection: a pymongo collection in which specs are stored.
    """

    def __init__(self, process_id, collection, id_field="id"):
        self.probes = {}
        self.target_map = {}
        self.hardcoded_specs = {}
        self.process_id = process_id
        self.apply_thread = None
        self.period = 60
        self.collection = collection
        self.id_field = id_field

    @property
    def specs(self):
        return dict((doc[self.id_field], doc) for doc in self.collection.find())

    def mark(self, id, doc, exception=False):
        """Record probe application success/failure."""
        doc = self.specs.get(id, None)
        if doc is not None:
            error = None
            if exception:
                error = "Error: %s\n%s" % (
                    repr(sys.exc_info()[1]),
                    traceback.format_exc(),
                )

            newval = {"lm": doc["lastmodified"], "err": error}
            if doc["applied"].get(self.process_id, {}) != newval:
                doc["applied"][self.process_id] = newval
                self.collection.update(
                    {self.id_field: id},
                    {
                        "$set": {
                            # Oh, Mongo. You and your dots.
                            "applied.%s"
                            % self.process_id.replace(".", "_"): newval
                        }
                    },
                )
