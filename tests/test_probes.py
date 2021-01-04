import datetime
import gc
import sys
import time
import types
from collections import defaultdict

import six
from mock import patch
from six.moves import xrange

try:
    from mock import _patch as MockPatch
except ImportError:
    import mock

    MockPatch = mock._mock._patch

import diagnose
from diagnose import probes, sensor
from diagnose.instruments import ProbeTestInstrument
from diagnose.test_fixtures import (
    a_func,
    func_2,
    hard_work,
    Thing,
    to_columns,
    funcs,
    mult_by_8,
    sum4,
)

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
            self.assertEqual(len(i.results), 1)
            self.assertEqual(type(i.results[0]), TypeError)
            self.assertEqual(
                i.results[0].args,
                ("unsupported operand type(s) for +: 'NoneType' and 'int'",),
            )
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
            self.assertEqual(i.results, [13])
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
                ["source:35:    summary = len([x for x in output if x % 10 == 0])\n"]
            ]
            assert [type(value) for tags, value in i.log] == [float]
        finally:
            probe.stop()

    def test_hotspot_overhead(self):
        # Set SCALE to 5000 or something big to see how hotspot overhead
        # diminishes the more work the target function does.
        # It's low in this test suite because people like fast tests.
        SCALE = 100
        val = [dict((str(i), i) for i in xrange(100))] * SCALE
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
    return num_instances


class TestTargets(ProbeTestCase):
    def test_probe_bad_mock(self):
        p = probes.attach_to("diagnose.test_fixtures.Thing.notamethod")
        with self.assertRaises(AttributeError) as exc:
            p.start()

        if six.PY2:
            expected_message = "diagnose.test_fixtures.Thing does not have the attribute 'notamethod'"
        else:
            expected_message = "<class 'diagnose.test_fixtures.Thing'> does not have the attribute 'notamethod'"

        assert (
            exc.exception.args[0]
            == expected_message
        )

    def test_target_copies(self):
        # When module M chooses "from x import y", then mock.patching x.y
        # does not affect M.y. Similarly, an existing object instance I
        # which has I.y = y is not patched by mock.patch.
        # Assert that FunctionProbe patches (and UNpatches) all such copies of y.
        old_probes_func_2 = func_2
        old_local_func_2 = func_2

        class Entity(object):
            pass

        t = Entity()
        t.add13 = func_2
        self.assertTrue(t.add13 is old_local_func_2)

        t2 = Entity()
        t2.add13 = func_2
        self.assertTrue(t2.add13 is old_local_func_2)

        registry["in_a_dict"] = func_2
        self.assertTrue(registry["in_a_dict"] is old_local_func_2)

        probe = probes.attach_to("diagnose.test_fixtures.func_2")
        try:
            probe.start()
            probe.instruments["instrument1"] = i = ProbeTestInstrument(
                expires=datetime.datetime.utcnow() + datetime.timedelta(minutes=10),
                name="func_2",
                value="arg",
                custom=None,
            )

            # Invoking x.y is typical and works naturally...
            self.assertTrue(func_2 is not old_probes_func_2)
            func_2(44)
            self.assertEqual(i.results, [44])

            # ...but invoking M.y (we imported func_2 into test_probes' namespace)
            # is harder:
            self.assertTrue(func_2 is not old_local_func_2)
            func_2(99999)
            self.assertEqual(i.results, [44, 99999])

            # ...and invoking Entity().y is just as hard:
            self.assertTrue(t.add13 is not old_local_func_2)
            self.assertTrue(t2.add13 is not old_local_func_2)
            t.add13(1001)
            self.assertEqual(i.results, [44, 99999, 1001])

            # ...etc:
            self.assertTrue(registry["in_a_dict"] is not old_local_func_2)
            registry["in_a_dict"](777)
            self.assertEqual(i.results, [44, 99999, 1001, 777])

            # The next problem is that, while our patch is live,
            # if t2 goes out of its original scope, we've still got
            # a reference to it in our mock patch.
            if six.PY2:
                expected_result = {
                    types.ModuleType: 2,
                    Entity: 2,
                    probes.WeakMethodPatch: 3,
                    MockPatch: 1,
                    probes.DictPatch: 1,
                }
            else:
                expected_result =  defaultdict(int, {
                      types.FunctionType: 1,
                      types.ModuleType: 2,
                      MockPatch: 1,
                      diagnose.probes.WeakMethodPatch: 4,
                      diagnose.probes.DictPatch: 1,
                      Entity: 2
                })

            self.assertEqual(
                owner_types(func_2),
                expected_result,
            )
            del t2

            if six.PY2:
                expected_result = {
                    types.ModuleType: 2,
                    # The number of Entity references MUST decrease by 1.
                    Entity: 1,
                    # The number of WeakMethodPatch references MUST decrease by 1.
                    probes.WeakMethodPatch: 2,
                    MockPatch: 1,
                    probes.DictPatch: 1,
                }
            else:
                expected_result = defaultdict(int, {
                      types.FunctionType: 1,
                      types.ModuleType: 2,
                      MockPatch: 1,
                      diagnose.probes.WeakMethodPatch: 3,
                      diagnose.probes.DictPatch: 1,
                      Entity: 1
                })

            self.assertEqual(
                owner_types(func_2),
                expected_result,
            )
        finally:
            probe.stop()

        # All patches MUST be stopped
        assert func_2 is old_probes_func_2
        assert func_2 is old_local_func_2
        assert t.add13 is old_local_func_2
        func_2(123)
        func_2(456)
        t.add13(789)
        registry["in_a_dict"](101112)
        assert i.results == [44, 99999, 1001, 777]

    def test_function_registries(self):
        with self.probe("test", "orig", "diagnose.test_fixtures.orig", "result") as p:
            assert funcs["orig"]("ahem") == "aha!"

            # The probe MUST have logged an entry
            i = list(p.instruments.values())[0]
            assert i.results == ["aha!"]

            i.log = []

        assert funcs["orig"]("ahem") == "aha!"

        # The probe MUST NOT have logged an entry
        assert i.results == []

    def test_probe_nonfunc(self):
        # We REALLY should not be allowed to patch anything
        # that's not a function!
        p = probes.attach_to("diagnose.test_fixtures.Thing")
        with self.assertRaises(TypeError):
            p.start()

    def test_patch_staticmethod(self):
        with self.probe(
            "test", "quantile", "diagnose.test_fixtures.Thing.static", "result"
        ) as p:
            assert Thing().static() == 15
            assert list(p.instruments.values())[0].results == [15]

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

    def test_patch_class_decorated(self):
        probe = probes.attach_to("diagnose.test_fixtures.sum4")
        try:
            probe.start()
            instr = ProbeTestInstrument("deco", "arg4", event="call")
            probe.instruments["deco"] = instr
            assert sum4(1, 2, 3, 4) == 10
            assert instr.results == [4]
        finally:
            probe.stop()

    def test_patch_property(self):
        old_prop = Thing.exists

        with self.probe(
            "test", "quantile", "diagnose.test_fixtures.Thing.exists", "result"
        ) as p:
            assert Thing().exists is True
            assert list(p.instruments.values())[0].results == [True]
            assert Thing.exists is not old_prop

        assert Thing().exists is True
        assert Thing.exists is old_prop


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
            assert s.log == [([], "<tagless>"), (["foo:bar"], "<tagged>")]
        assert probes.active_probes.get("diagnose.test_fixtures.Thing.do") is None
