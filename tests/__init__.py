from contextlib import contextmanager
import datetime
import unittest

import probes


class ProbeTestCase(unittest.TestCase):
    @contextmanager
    def probe(self, type, name, target, value, lifespan=1, custom=None, internal=False):
        mgr = probes.manager
        mgr.instrument_classes.setdefault(
            "test", probes.instruments.ProbeTestInstrument
        )
        instrument_id = None
        try:
            instrument_id = "probe-%s" % name
            mgr.specs[instrument_id] = {
                "target": target,
                "instrument": {
                    "type": type,
                    "name": name,
                    "value": value,
                    "internal": internal,
                    "custom": custom or {},
                },
                "lifespan": lifespan,
                "lastmodified": datetime.datetime.utcnow(),
                "applied": {},
            }
            mgr.apply()
            yield mgr.probes[mgr.target_map[target]]
        finally:
            if instrument_id is not None:
                mgr.specs.pop(instrument_id, None)
                mgr.apply()
