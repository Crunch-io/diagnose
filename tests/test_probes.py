import datetime
import gc
import sys
import time
from collections import defaultdict
from unittest.mock import patch

import diagnose
from diagnose import patchlib, probes, sensor
from diagnose.instruments import ProbeTestInstrument
from diagnose.test_fixtures import Thing, a_func, hard_work, mult_by_8, to_columns

from . import ProbeTestCase

registry = {}


class TestReturnEvent(ProbeTestCase):
    def test_return_event_result(self):
        with self.probe("test", "do", "diagnose.test_fixtures.Thing.do", "result") as p:
            result = Thing().do("ok")

            assert result == "<ok>"

            # The probe MUST have logged an entry
            assert list(p.instruments.values())[0].results == ["<ok>"]

    def test_return_event_elapsed(self):
        with self.probe(
            "test", "do", "diagnose.test_fixtures.Thing.do", "elapsed"
        ) as p:
            start = time.time()
            result = Thing().do("ok")
            elapsed = time.time() - start

            assert result == "<ok>"

            # The probe MUST have logged an entry
            assert list(p.instruments.values())[0].results[0] < elapsed

    def test_return_event_locals(self):
        with self.probe(
            "test", "do", "diagnose.test_fixtures.Thing.do", "sorted(locals().keys())"
        ) as p:
            result = Thing().do("ok")

            assert result == "<ok>"

            # The probe MUST have logged an entry
            assert list(p.instruments.values())[0].results == [
                [
                    "arg",
                    "args",
                    "elapsed",
                    "end",
                    "frame",
                    "kwargs",
                    "now",
                    "result",
                    "self",
                    "start",
                ]
            ]

    def test_return_event_locals_frame(self):
        probe = probes.attach_to("diagnose.test_fixtures.a_func")
        try:
            probe.start()
            probe.instruments["instrument1"] = ProbeTestInstrument(
                expires=datetime.datetime.utcnow() + datetime.timedelta(minutes=10),
                name="a_func",
                value="frame.f_back.f_code.co_name",
                custom=None,
            )
            a_func(923775)
            assert probe.instruments["instrument1"].results == [
                "test_return_event_locals_frame"
            ]
            assert probe.instruments["instrument1"].finish_called
        finally:
            probe.stop()

    def test_return_event_exception_in_target(self):
        probe = probes.attach_to("diagnose.test_fixtures.a_func")
        try:
            probe.start()
            probe.instruments["instrument1"] = i = ProbeTestInstrument(
                expires=datetime.datetime.utcnow() + datetime.timedelta(minutes=10),
                name="a_func",
                value="result",
                event="return",
                custom=None,
            )
            with self.assertRaises(TypeError):
                a_func(None)
            assert len(i.results) == 1
            assert type(i.results[0]) == TypeError
            assert i.results[0].args == (
                "unsupported operand type(s) for +: 'NoneType' and 'int'",
            )
            assert probe.instruments["instrument1"].finish_called
        finally:
            probe.stop()


class TestCallEvent(ProbeTestCase):
    def test_call_event_args(self):
        with self.probe(
            "test", "do", "diagnose.test_fixtures.Thing.do", "args", event="call"
        ) as p:
            t = Thing()
            result = t.do("ok")

            assert result == "<ok>"

            # The probe MUST have logged an entry
            assert list(p.instruments.values())[0].results == [(t, "ok")]

    def test_call_event_elapsed(self):
        with self.probe(
            "test", "do", "diagnose.test_fixtures.Thing.do", "elapsed", event="call"
        ) as p:
            errs = []
            list(p.instruments.values())[0].handle_error = lambda probe: errs.append(
                sys.exc_info()[1].args[0] if sys.exc_info()[1].args else ""
            )
            result = Thing().do("ok")

            assert result == "<ok>"

            # The probe MUST NOT have logged an entry...
            assert list(p.instruments.values())[0].results == []
            # ...but the instrument MUST have handled the error:
            assert errs == ["name 'elapsed' is not defined"]

    def test_call_event_locals(self):
        with self.probe(
            "test",
            "do",
            "diagnose.test_fixtures.Thing.do",
            "sorted(locals().keys())",
            event="call",
        ) as p:
            result = Thing().do("ok")

            assert result == "<ok>"

            # The probe MUST have logged an entry
            assert list(p.instruments.values())[0].results == [
                ["arg", "args", "frame", "kwargs", "now", "self", "start"]
            ]

    def test_call_event_locals_frame(self):
        probe = probes.attach_to("diagnose.test_fixtures.a_func")
        try:
            probe.start()
            probe.instruments["instrument1"] = ProbeTestInstrument(
                expires=datetime.datetime.utcnow() + datetime.timedelta(minutes=10),
                name="a_func",
                value="frame.f_back.f_code.co_name",
                event="call",
                custom=None,
            )
            a_func(923775)
            assert probe.instruments["instrument1"].results == [
                "test_call_event_locals_frame"
            ]
            assert probe.instruments["instrument1"].finish_called
        finally:
            probe.stop()


class TestEndEvent(ProbeTestCase):
    def test_end_event_success(self):
        probe = probes.attach_to("diagnose.test_fixtures.a_func")
        try:
            probe.start()
            probe.instruments["instrument1"] = i = ProbeTestInstrument(
                expires=datetime.datetime.utcnow() + datetime.timedelta(minutes=10),
                name="a_func",
                value="output",
                event="end",
                custom=None,
            )
            assert a_func(27) == 40
            assert i.results == [40]
            assert probe.instruments["instrument1"].finish_called
        finally:
            probe.stop()

    def test_end_event_exception_in_target(self):
        probe = probes.attach_to("diagnose.test_fixtures.a_func")
        try:
            probe.start()
            probe.instruments["instrument1"] = i = ProbeTestInstrument(
                expires=datetime.datetime.utcnow() + datetime.timedelta(minutes=10),
                name="a_func",
                value="extra",
                event="end",
                custom=None,
            )
            with self.assertRaises(TypeError):
                a_func(None)
            assert i.results == [13]
            assert probe.instruments["instrument1"].finish_called
        finally:
            probe.stop()

    def test_end_event_exception_in_value(self):
        probe = probes.attach_to("diagnose.test_fixtures.a_func")
        try:
            errs = []
            old_handle_error = diagnose.manager.handle_error
            diagnose.manager.handle_error = lambda probe, instr: errs.append(
                sys.exc_info()[1].args[0] if sys.exc_info()[1].args[0] else ""
            )
            probe.start()
            probe.instruments["instrument1"] = i = ProbeTestInstrument(
                expires=datetime.datetime.utcnow() + datetime.timedelta(minutes=10),
                name="a_func",
                value="unknown",  # Should throw NameError
                event="end",
                custom=None,
            )
            assert a_func(1000) == 1013
            assert i.results == []
            assert i.expires == i.error_expiration
            assert errs == ["name 'unknown' is not defined"]
            assert probe.instruments["instrument1"].finish_called
        finally:
            diagnose.manager.handle_error = old_handle_error
            probe.stop()


class TestHotspotValues(ProbeTestCase):
    def test_slowest_line(self):
        probe = probes.attach_to("diagnose.test_fixtures.hard_work")
        try:
            probe.start()
            probe.instruments["instrument1"] = i = ProbeTestInstrument(
                expires=datetime.datetime.utcnow() + datetime.timedelta(minutes=10),
                name="hard_work.slowest.time",
                value="hotspots.worst.time",
                custom={
                    "tags": '{"source": "%s:%s" % (hotspots.worst.lineno, hotspots.worst.source)}'
                },
            )
            assert hard_work(0, 10000) == 1000
            assert [tags for tags, value in i.log] == [
                {"source": "34:    summary = len([x for x in output if x % 10 == 0])\n"}
            ]
            assert [type(value) for tags, value in i.log] == [float]
        finally:
            probe.stop()

    def test_hotspot_overhead(self):
        # Set SCALE to 5000 or something big to see how hotspot overhead
        # diminishes the more work the target function does.
        # It's low in this test suite because people like fast tests.
        SCALE = 100
        val = [dict((str(i), i) for i in range(100))] * SCALE
        start = time.time()
        to_columns(val)
        unpatched = time.time() - start

        probe = probes.attach_to("diagnose.test_fixtures.to_columns")
        try:
            probe.start()
            probe.instruments["instrument1"] = ProbeTestInstrument(
                expires=datetime.datetime.utcnow() + datetime.timedelta(minutes=10),
                name="to_columns.slowest.time",
                value="hotspots.worst.time",
                custom={
                    "tags": '{"source": "%s:%s" % (hotspots.worst.lineno, hotspots.worst.source)}'
                },
            )
            start = time.time()
            to_columns(val)
            patched = time.time() - start
        finally:
            probe.stop()

        print(
            "\nUNPATCHED: %s PATCHED: %s (%s%%)"
            % (unpatched, patched, int((patched / unpatched) * 100))
        )


def owner_types(obj):
    num_instances = defaultdict(int)
    for ref in gc.get_referrers(obj):
        for parent in gc.get_referrers(ref):
            if getattr(parent, "__dict__", None) is ref:
                num_instances[type(parent)] += 1
                break
    return dict(num_instances)


def weak_referents(patches):
    """Return the object patched by each WeakMethodPatch in the given patches."""
    return [p.getter() for p in patches if isinstance(p, patchlib.WeakMethodPatch)]


class TestTargets(ProbeTestCase):
    def test_probe_bad_mock(self):
        p = probes.attach_to("diagnose.test_fixtures.Thing.notamethod")
        with self.assertRaises(AttributeError) as exc:
            p.start()

        expected_message = "<class 'diagnose.test_fixtures.Thing'> does not have the attribute 'notamethod'"

        assert exc.exception.args[0] == expected_message

    def test_patch_wrapped_function_end_event(self):
        probe = probes.attach_to("diagnose.test_fixtures.Thing.add5")
        try:
            probe.start()
            instr = ProbeTestInstrument("deco", "arg1", event="end")
            probe.instruments["deco"] = instr
            Thing().add5(13)
            assert instr.results == [113]
        finally:
            probe.stop()


class TestProbeCheckCall(ProbeTestCase):
    def test_probe_check_call(self):
        def only_some_users(probe, instrument, *args, **kwargs):
            valid_ids = instrument.custom.get("valid_ids", None)
            return kwargs.get("user_id") in valid_ids

        with patch("diagnose.manager.check_call", only_some_users):
            with self.probe(
                "test",
                "blurg",
                "diagnose.test_fixtures.Thing.do",
                "result",
                custom={"valid_ids": [1, 2, 3]},
            ) as p:
                assert Thing().do("ok", user_id=2) == "<ok>"
                assert list(p.instruments.values())[0].results == ["<ok>"]

                assert Thing().do("not ok", user_id=10004) == "<not ok>"
                assert list(p.instruments.values())[0].results == ["<ok>"]


class TestHardcodedProbes(ProbeTestCase):
    def test_hardcoded_probes(self):
        diagnose.manager.apply()
        assert mult_by_8(3) == 24
        assert [
            i.results
            for p in probes.active_probes.values()
            for k, i in p.instruments.items()
            if k.startswith("hardcode:")
        ] == [[24]]

        diagnose.manager.apply()
        assert mult_by_8(3) == 24
        assert [
            i.results
            for p in probes.active_probes.values()
            for k, i in p.instruments.items()
            if k.startswith("hardcode:")
        ] == [[24, 24]]


class TestSensors(ProbeTestCase):
    def test_basic_sensor(self):
        assert probes.active_probes.get("diagnose.test_fixtures.Thing.do") is None

        with sensor("diagnose.test_fixtures.Thing.do") as s:
            assert s.log == []
            Thing().do("ok")
            assert s.results == ["<ok>"]
        assert probes.active_probes.get("diagnose.test_fixtures.Thing.do") is None

        with sensor("diagnose.test_fixtures.Thing.do") as s:
            Thing().do("tagless")
            s.custom["tags"] = '{"foo": "bar"}'
            Thing().do("tagged")
            assert s.log == [({}, "<tagless>"), ({"foo": "bar"}, "<tagged>")]
        assert probes.active_probes.get("diagnose.test_fixtures.Thing.do") is None
