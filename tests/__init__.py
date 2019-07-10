from contextlib import contextmanager
import datetime
import unittest

import diagnose
from diagnose import probes

diagnose.manager.instrument_classes.setdefault(
    "test", diagnose.instruments.ProbeTestInstrument
)


class ProbeTestCase(unittest.TestCase):
    @contextmanager
    def probe(self, type, name, target, value, lifespan=1, custom=None, event="return"):
        mgr = diagnose.manager
        instrument_id = None
        try:
            instrument_id = "probe-%s" % name
            mgr.specs[instrument_id] = {
                "target": target,
                "instrument": {
                    "type": type,
                    "name": name,
                    "value": value,
                    "event": event,
                    "custom": custom or {},
                },
                "lifespan": lifespan,
                "lastmodified": datetime.datetime.utcnow(),
                "applied": {},
            }
            mgr.apply()
            yield probes.active_probes[target]
        finally:
            if instrument_id is not None:
                mgr.specs.pop(instrument_id, None)
                mgr.apply()
