import datetime
from cStringIO import StringIO
import sys

from mock import call, patch

import diagnose
from diagnose.test_fixtures import a_func, Thing

from . import ProbeTestCase


registry = {}


class TestLogInstrument(ProbeTestCase):
    def test_log_instrument(self):
        with patch("diagnose.instruments.LogInstrument.out", StringIO()) as out:
            with self.probe(
                "log", "foo", "diagnose.test_fixtures.Thing.do", "len(arg)"
            ):
                result = Thing().do("ok")

            # The call MUST succeed.
            assert result == "<ok>"

            # The probe MUST have logged an entry
            assert out.getvalue() == "Probe (foo)[tags=[]] = 2\n"

    def test_log_instrument_err_in_eval(self):
        errs = []
        with patch(
            "diagnose.manager.handle_error",
            lambda probe, instrument=None: errs.append(sys.exc_info()[1]),
        ):
            with self.probe(
                "log", "bar", "diagnose.test_fixtures.Thing.do", "::len(arg)"
            ):
                result = Thing().do("ok")

            # The call MUST succeed.
            assert result == "<ok>"

            # The manager MUST have handled the error.
            assert [e.args for e in errs] == [
                ("invalid syntax", ("<string>", 1, 1, "::len(arg)"))
            ]


class TestHistInstrument(ProbeTestCase):
    def test_hist_instrument(self):
        with patch("diagnose.instruments.statsd") as statsd:
            with self.probe(
                "hist", "foo", "diagnose.test_fixtures.Thing.do", "len(arg)"
            ):
                result = Thing().do("ok")

            # The call MUST succeed.
            assert result == "<ok>"

            # The probe MUST have called for a histogram
            assert statsd.method_calls[0] == call.histogram("foo", 2, tags=[])

    def test_hist_instrument_err_in_eval(self):
        errs = []
        with patch(
            "diagnose.manager.handle_error",
            lambda probe, instrument=None: errs.append(sys.exc_info()[1]),
        ):
            with self.probe(
                "hist", "bar", "diagnose.test_fixtures.Thing.do", "::len(arg)"
            ):
                result = Thing().do("ok")

            # The call MUST succeed.
            assert result == "<ok>"

            # The manager MUST have handled the error.
            assert [e.msg for e in errs] == ["invalid syntax"]

    def test_hist_tags(self):
        NUM = 496942560
        with patch("diagnose.instruments.statsd") as statsd:
            with self.probe(
                "hist",
                "bar",
                "diagnose.test_fixtures.a_func",
                "result",
                custom={"tags": "{'output': arg}"},
            ):
                assert a_func(NUM) == NUM + 13

            # The probe MUST have called for a histogram with our custom tags
            assert statsd.method_calls[0] == call.histogram(
                "bar", NUM + 13, tags=["output:%s" % NUM]
            )


class TestIncrInstrument(ProbeTestCase):
    def test_incr_instrument(self):
        with patch("diagnose.instruments.statsd") as statsd:
            with self.probe(
                "incr", "klee", "diagnose.test_fixtures.Thing.do", "len(arg)"
            ):
                result = Thing().do("ok")

            # The call MUST succeed.
            assert result == "<ok>"

            # The probe MUST have called for a increment
            assert statsd.method_calls[0] == call.increment("klee", 2, tags=[])

    def test_incr_nonnumeric(self):
        errs = []
        with patch(
            "diagnose.manager.handle_error",
            lambda probe, instrument=None: errs.append(sys.exc_info()[1]),
        ):
            with patch("diagnose.instruments.statsd"):
                with self.probe(
                    "incr", "klee", "diagnose.test_fixtures.Thing.do", "arg"
                ):
                    result = Thing().do("ok")

                # The call MUST succeed.
                assert result == "<ok>"

            # The manager MUST have handled the error.
            assert [e.args for e in errs] == [("Cannot send non-numeric metric: ok",)]


class TestMultipleInstruments(ProbeTestCase):
    def test_multiple_instruments(self):
        with patch("diagnose.instruments.statsd") as statsd:
            mgr = diagnose.manager
            try:
                target = "diagnose.test_fixtures.a_func"
                mgr.specs["a_func_internal"] = {
                    "target": target,
                    "instrument": {
                        "type": "hist",
                        "name": "a_func_internal",
                        "value": "output",
                        "internal": True,
                        "custom": {},
                    },
                    "lifespan": 1,
                    "lastmodified": datetime.datetime.utcnow(),
                    "applied": {},
                }
                mgr.specs["a_func_external"] = {
                    "target": target,
                    "instrument": {
                        "type": "hist",
                        "name": "a_func_external",
                        "value": "result",
                        "internal": False,
                        "custom": {},
                    },
                    "lifespan": 1,
                    "lastmodified": datetime.datetime.utcnow(),
                    "applied": {},
                }
                mgr.apply()
                result = a_func(78)
            finally:
                mgr.specs.pop("a_func_internal", None)
                mgr.specs.pop("a_func_external", None)
                mgr.apply()

            # The call MUST succeed.
            assert result == 91

            # The instruments MUST each have logged an entry
            assert statsd.method_calls == [
                call.histogram("a_func_internal", 91, tags=[]),
                call.histogram("a_func_external", 91, tags=[]),
            ]

    def test_replace_instrument(self):
        with patch("diagnose.instruments.statsd") as statsd:
            mgr = diagnose.manager
            try:
                target = "diagnose.test_fixtures.a_func"
                mgr.specs["a_func"] = spec = {
                    "target": target,
                    "instrument": {
                        "type": "hist",
                        "name": "a_func",
                        "value": "arg",
                        "internal": False,
                        "custom": {},
                    },
                    "lifespan": 1,
                    "lastmodified": datetime.datetime.utcnow(),
                    "applied": {},
                }
                mgr.apply()

                result = a_func(100)

                # The call MUST succeed.
                assert result == 113

                # The instrument MUST have logged an entry
                assert statsd.method_calls == [call.histogram("a_func", 100, tags=[])]

                # Change the probe to a different target
                spec["target"] = "diagnose.test_fixtures.Thing.do"
                mgr.apply()
                _id = mgr.target_map["diagnose.test_fixtures.Thing.do"]
                assert mgr.probes[_id].instruments.values()[0].name == "a_func"
                # The old target MUST be removed from the probes
                assert target not in mgr.probes

                # Trigger the (revised) probe
                result = Thing().do(2)

                # The call MUST succeed.
                assert result == "<2>"

                # The instrument MUST have logged an entry
                assert statsd.method_calls == [
                    call.histogram("a_func", 100, tags=[]),
                    call.histogram("a_func", 2, tags=[]),
                ]
            finally:
                mgr.specs.pop("a_func", None)
                mgr.apply()
