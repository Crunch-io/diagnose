import datetime
from cStringIO import StringIO
import sys

from mock import call, patch

import diagnose
from diagnose import probes
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
                "hist", "grr", "diagnose.test_fixtures.Thing.do", "len(arg)"
            ):
                result = Thing().do("ok")

            # The call MUST succeed.
            assert result == "<ok>"

            # The probe MUST have called for a histogram
            assert statsd.method_calls[0] == call.histogram("grr", 2, tags=[])

    def test_hist_instrument_err_in_eval(self):
        errs = []
        with patch(
            "diagnose.manager.handle_error",
            lambda probe, instrument=None: errs.append(sys.exc_info()[1]),
        ):
            with self.probe(
                "hist", "hmm", "diagnose.test_fixtures.Thing.do", "::len(arg)"
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
                "baz",
                "diagnose.test_fixtures.a_func",
                "result",
                custom={"tags": "{'output': arg}"},
            ):
                assert a_func(NUM) == NUM + 13

            # The probe MUST have called for a histogram with our custom tags
            assert statsd.method_calls[0] == call.histogram(
                "baz", NUM + 13, tags=["output:%s" % NUM]
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
                mgr.specs["a_func_end"] = {
                    "target": target,
                    "instrument": {
                        "type": "hist",
                        "name": "a_func_end",
                        "value": "output",
                        "event": "end",
                        "custom": {},
                    },
                    "lifespan": 1,
                    "lastmodified": datetime.datetime.utcnow(),
                    "applied": {},
                }
                mgr.specs["a_func_return"] = {
                    "target": target,
                    "instrument": {
                        "type": "hist",
                        "name": "a_func_return",
                        "value": "result",
                        "event": "return",
                        "custom": {},
                    },
                    "lifespan": 1,
                    "lastmodified": datetime.datetime.utcnow(),
                    "applied": {},
                }
                mgr.apply()
                result = a_func(78)
            finally:
                mgr.specs.pop("a_func_end", None)
                mgr.specs.pop("a_func_return", None)
                mgr.apply()

            # The call MUST succeed.
            assert result == 91

            # The instruments MUST each have logged an entry
            assert statsd.method_calls == [
                call.histogram("a_func_end", 91, tags=[]),
                call.histogram("a_func_return", 91, tags=[]),
            ]

    def test_replace_instrument(self):
        with patch("diagnose.instruments.statsd") as statsd:
            mgr = diagnose.manager
            try:
                target1 = "diagnose.test_fixtures.a_func"
                mgr.specs["a_func"] = spec = {
                    "target": target1,
                    "instrument": {
                        "type": "hist",
                        "name": "a_func",
                        "value": "arg",
                        "event": "return",
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
                target2 = "diagnose.test_fixtures.Thing.do"
                spec["target"] = target2
                mgr.apply()
                assert (
                    probes.active_probes[target2].instruments.values()[0].name
                    == "a_func"
                )
                # The old target MUST be removed from the probes
                assert target1 not in probes.active_probes

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
