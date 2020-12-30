"""Managers for probe instruments."""

import datetime
import math
import six
import sys
import threading
import time
import traceback

from . import instruments
from . import probes


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


# --------------------------- Instrument Manager --------------------------- #


class InstrumentManager(object):
    """A Manager which applies instruments to probes to targets.

    specs: a dict of {id: spec} dicts, each of which defines an instrument. Fields:
        * target: the dotted path to a function to wrap in a FunctionProbe
        * lastmodified: the datetime when the spec was last changed
        * lifespan: an int; the number of minutes from lastmodified before
                    the instrument should expire.
        * instrument: a dict of Instrument params (type, name, value, event, custom).
                      The "expires" param is calculated from lastmodified + lifespan.
        * applied: a dict of {process_id: info} pairs, where "info" is a dict with:
            * lm: the "lastmodified" datetime of the instrument when
                 it was applied to the given process. Monitors can use
                 this to know when processes have the latest spec or not.
            * err: a string with any error raised in the given process, or None
    """

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
        self.specs = {}
        self.process_id = process_id
        self.apply_thread = None
        self.period = 60
        self.short_id = hex(id(self))[2:]

    def apply(self):
        """Add/remove instruments to match our spec."""
        with self.lock:
            self._apply()

    def _apply(self):
        seen_instruments = {}
        for spec_id, doc in six.iteritems(self.specs):
            full_id = "%s:%s" % (self.short_id, spec_id)
            target = doc["target"]
            seen_instruments[full_id] = target
            probe, I, cls, expires = None, None, None, None
            try:
                probe = probes.attach_to(target)

                lifespan = doc["lifespan"]
                if lifespan is None:
                    expires = None
                else:
                    expires = doc["lastmodified"] + datetime.timedelta(minutes=lifespan)

                cls = self.instrument_classes[doc["instrument"]["type"]]

                # Add or modify instruments
                I = probe.instruments.get(full_id, None)
                modified = False
                if I is None or I.__class__ != cls:
                    probe.instruments[full_id] = cls(
                        mgr=self, expires=expires, **doc["instrument"]
                    )
                    modified = True
                else:
                    for key in ("name", "value", "event", "custom"):
                        if getattr(I, key) != doc["instrument"][key]:
                            setattr(I, key, doc["instrument"][key])
                            modified = True
                    if I.expires != expires:
                        I.expires = expires
                        modified = True
                if modified:
                    self.mark(spec_id, doc)
            except:
                if I is None:
                    if cls is None:
                        cls = self.instrument_classes[doc["instrument"]["type"]]
                    I = cls(mgr=self, expires=expires, **doc["instrument"])
                self.handle_error(probe, I)
                self.mark(spec_id, doc, exception=True)

        # Remove defunct instruments, probes
        for target, probe in list(probes.active_probes.items()):
            for full_id, instrument in list(probe.instruments.items()):
                # Remove any instrument from a probe if this manager
                # thinks it doesn't apply to the same target anymore.
                if full_id.startswith(self.short_id):
                    if seen_instruments.get(full_id, None) != probe.target:
                        probe.instruments.pop(full_id, None)

            if not probe.instruments:
                p = probes.active_probes.pop(target, None)
                if p is not None:
                    p.stop()

        probes.start_all()

    def apply_in_background(self, period=60):
        self.period = period
        if self.apply_thread is None:
            self.apply_thread = t = threading.Thread(target=self._cycle)
            t.setName("diagnose.manager.apply_in_background")
            t.daemon = True
            t.start()

    def _cycle(self):
        while True:
            time.sleep(self.period)
            try:
                self.apply()
            except:
                self.handle_error()

    def check_call(self, probe, instrument, *args, **kwargs):
        """Return True if the given instrument should be called, False otherwise.

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
        """Record instrument application success/failure."""
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


class MongoDBInstrumentManager(InstrumentManager):
    """An InstrumentManager which reads and writes specs in MongoDB.

    collection: a pymongo collection in which specs are stored.
    """

    def __init__(self, process_id, collection, id_field="id"):
        self.process_id = process_id
        self.apply_thread = None
        self.period = 60
        self.collection = collection
        self.id_field = id_field
        self.short_id = hex(id(self))[2:]

    @property
    def specs(self):
        return dict((doc[self.id_field], doc) for doc in self.collection.find())

    def mark(self, id, doc, exception=False):
        """Record instrument application success/failure."""
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
