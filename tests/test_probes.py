from collections import defaultdict
import datetime
import gc
import time
import types

from mock import _patch as MockPatch, patch

import probes
from probes import probelib
from probes.instruments import ProbeTestInstrument
from probes.test_fixtures import a_func, hard_work, Thing, to_columns, funcs, mult_by_8

from . import ProbeTestCase


registry = {}


class TestExternalProbe(ProbeTestCase):
    def test_external_probe_result(self):
        with self.probe(
            "test", "do", "probes.test_fixtures.Thing.do", "result", internal=False
        ) as p:
            result = Thing().do("ok")

            assert result == "<ok>"

            # The probe MUST have logged an entry
            assert p.instruments.values()[0].results == [([], "<ok>")]

    def test_external_probe_elapsed(self):
        with self.probe(
            "test", "do", "probes.test_fixtures.Thing.do", "elapsed", internal=False
        ) as p:
            start = time.time()
            result = Thing().do("ok")
            elapsed = time.time() - start

            assert result == "<ok>"

            # The probe MUST have logged an entry
            assert p.instruments.values()[0].results[0][1] < elapsed

    def test_external_probe_locals(self):
        with self.probe(
            "test",
            "do",
            "probes.test_fixtures.Thing.do",
            "sorted(locals().keys())",
            internal=False,
        ) as p:
            result = Thing().do("ok")

            assert result == "<ok>"

            # The probe MUST have logged an entry
            assert p.instruments.values()[0].results == [
                (
                    [],
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
                    ],
                )
            ]

    def test_external_caller(self):
        probe = probes.FunctionProbe("probes.test_fixtures.a_func")
        try:
            probe.start()
            probe.instruments["instrument1"] = ProbeTestInstrument(
                expires=datetime.datetime.utcnow() + datetime.timedelta(minutes=10),
                name="a_func",
                value="frame.f_back.f_code.co_name",
                internal=False,
                custom=None,
            )
            a_func(923775)
            assert probe.instruments["instrument1"].results, ["test_external_caller"]
        finally:
            probe.stop()

    def test_probe_bad_mock(self):
        with self.assertRaises(AttributeError) as exc:
            probes.FunctionProbe("probes.test_fixtures.Thing.notamethod")
        assert (
            exc.exception.message
            == "probes.test_fixtures.Thing does not have the attribute 'notamethod'"
        )


class TestInternalProbe(ProbeTestCase):
    def test_internal_instrument(self):
        probe = probes.FunctionProbe("probes.test_fixtures.a_func")
        try:
            probe.start()
            probe.instruments["instrument1"] = i = ProbeTestInstrument(
                expires=datetime.datetime.utcnow() + datetime.timedelta(minutes=10),
                name="a_func",
                value="output",
                internal=True,
                custom=None,
            )
            assert a_func(27) == 40
            assert i.results == [([], 40)]
        finally:
            probe.stop()

    def test_internal_exception_in_target(self):
        probe = probes.FunctionProbe("probes.test_fixtures.a_func")
        try:
            probe.start()
            probe.instruments["instrument1"] = i = ProbeTestInstrument(
                expires=datetime.datetime.utcnow() + datetime.timedelta(minutes=10),
                name="a_func",
                value="extra",
                internal=True,
                custom=None,
            )
            with self.assertRaises(TypeError):
                a_func(None)
            self.assertEqual(i.results, [([], 13)])
        finally:
            probe.stop()

    def test_internal_exception_in_value(self):
        probe = probes.FunctionProbe("probes.test_fixtures.a_func")
        try:
            probe.start()
            probe.instruments["instrument1"] = i = ProbeTestInstrument(
                expires=datetime.datetime.utcnow() + datetime.timedelta(minutes=10),
                name="a_func",
                value="unknown",  # Should throw NameError
                internal=True,
                custom=None,
            )
            assert a_func(1000) == 1013
            assert i.results == []
            assert i.expires == i.error_expiration
        finally:
            probe.stop()


class TestHotspotValues(ProbeTestCase):
    def test_slowest_line(self):
        probe = probes.FunctionProbe("probes.test_fixtures.hard_work")
        try:
            probe.start()
            probe.instruments["instrument1"] = i = ProbeTestInstrument(
                expires=datetime.datetime.utcnow() + datetime.timedelta(minutes=10),
                name="hard_work.slowest.time",
                value="hotspots.worst.time",
                internal=False,
                custom={
                    "tags": '{"source": "%s:%s" % (hotspots.worst.lineno, hotspots.worst.source)}'
                },
            )
            assert hard_work(0, 10000) == 1000
            assert [tags for tags, value in i.results] == [
                ["source:28:    summary = len([x for x in output if x % 10 == 0])\n"]
            ]
            assert [type(value) for tags, value in i.results] == [float]
        finally:
            probe.stop()

    def test_hotspot_overhead(self):
        # Set SCALE to 5000 or something big to see how overhead diminishes.
        # It's low in this test suite because people like fast tests.
        SCALE = 100
        val = [dict((str(i), i) for i in xrange(100))] * SCALE
        start = time.time()
        to_columns(val)
        unpatched = time.time() - start

        probe = probes.FunctionProbe("probes.test_fixtures.to_columns")
        try:
            probe.start()
            probe.instruments["instrument1"] = ProbeTestInstrument(
                expires=datetime.datetime.utcnow() + datetime.timedelta(minutes=10),
                name="to_columns.slowest.time",
                value="hotspots.worst.time",
                internal=False,
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
            "UNPATCHED: %s PATCHED: %s (%s%%)"
            % (unpatched, patched, int((patched / unpatched) * 100))
        )


def owner_types(obj):
    num_instances = defaultdict(int)
    for ref in gc.get_referrers(obj):
        for parent in gc.get_referrers(ref):
            if getattr(parent, "__dict__", None) is ref:
                num_instances[type(parent)] += 1
                break
        else:
            num_instances[dict] += 1
    return num_instances


class TestTargetCopies(ProbeTestCase):
    def test_target_copies(self):
        # When module M chooses "from x import y", then patching x.y
        # does not affect M.y. Similarly, an existing object instance I
        # which has I.y = y is not patched by mock.patch.
        # Assert that FunctionProbe patches (and UNpatches) all such copies of y.
        old_probes_a_func = a_func
        old_local_a_func = a_func

        class Entity(object):
            pass

        t = Entity()
        t.add13 = a_func
        self.assertTrue(t.add13 is old_local_a_func)

        t2 = Entity()
        t2.add13 = a_func
        self.assertTrue(t2.add13 is old_local_a_func)

        registry["in_a_dict"] = a_func
        self.assertTrue(registry["in_a_dict"] is old_local_a_func)

        probe = probes.FunctionProbe("probes.test_fixtures.a_func")
        try:
            probe.start()
            probe.instruments["instrument1"] = i = ProbeTestInstrument(
                expires=datetime.datetime.utcnow() + datetime.timedelta(minutes=10),
                name="a_func",
                value="arg",
                internal=False,
                custom=None,
            )

            # Invoking x.y is typical and works naturally...
            self.assertTrue(a_func is not old_probes_a_func)
            a_func(44)
            self.assertEqual(i.results, [([], 44)])

            # ...but invoking M.y (we imported a_func into test_probes' namespace)
            # is harder:
            self.assertTrue(a_func is not old_local_a_func)
            a_func(99999)
            self.assertEqual(i.results, [([], 44), ([], 99999)])

            # ...and invoking Entity().y is just as hard:
            self.assertTrue(t.add13 is not old_local_a_func)
            t.add13(1001)
            self.assertEqual(i.results, [([], 44), ([], 99999), ([], 1001)])

            # ...etc:
            self.assertTrue(registry["in_a_dict"] is not old_local_a_func)
            registry["in_a_dict"](777)
            self.assertEqual(i.results, [([], 44), ([], 99999), ([], 1001), ([], 777)])

            # The next problem is that, while our patch is live,
            # if t2 goes out of its original scope, we've still got
            # a reference to it in our mock patch.
            self.assertEqual(
                owner_types(a_func),
                {
                    types.ModuleType: 3,
                    Entity: 2,
                    dict: 2,  # why?
                    probelib.WeakMethodPatch: 4,
                    MockPatch: 1,
                    probelib.DictPatch: 1,
                },
            )
            del t2
            self.assertEqual(
                owner_types(a_func),
                {
                    types.ModuleType: 3,
                    # The number of Entity references MUST decrease by 1.
                    Entity: 1,
                    dict: 2,
                    # The number of WeakMethodPatch references MUST decrease by 1.
                    probelib.WeakMethodPatch: 3,
                    MockPatch: 1,
                    probelib.DictPatch: 1,
                },
            )
        finally:
            probe.stop()

        # All patches MUST be stopped
        assert a_func is old_probes_a_func
        assert a_func is old_local_a_func
        assert t.add13 is old_local_a_func
        a_func(123)
        a_func(456)
        t.add13(789)
        registry["in_a_dict"](101112)
        assert i.results == [([], 44), ([], 99999), ([], 1001), ([], 777)]

    def test_function_registries(self):
        with self.probe("test", "orig", "probes.test_fixtures.orig", "result") as p:
            assert funcs["orig"]("ahem") == "aha!"

            # The probe MUST have logged an entry
            i = p.instruments.values()[0]
            assert i.results == [([], "aha!")]

            i.results = []

        assert funcs["orig"]("ahem") == "aha!"

        # The probe MUST NOT have logged an entry
        assert i.results == []

    def test_probe_nonfunc(self):
        # We REALLY should not be allowed to patch anything
        # that's not a function!
        with self.assertRaises(TypeError):
            probes.FunctionProbe("probes.test_fixtures.Thing")


class TestProbeCheckCall(ProbeTestCase):
    def test_probe_check_call(self):
        def only_some_users(probe, instrument, *args, **kwargs):
            valid_ids = instrument.custom.get("valid_ids", None)
            return kwargs.get("user_id") in valid_ids

        with patch("probes.manager.check_call", only_some_users):
            with self.probe(
                "test",
                "blurg",
                "probes.test_fixtures.Thing.do",
                "result",
                custom={"valid_ids": [1, 2, 3]},
            ) as p:
                assert Thing().do("ok", user_id=2) == "<ok>"
                assert p.instruments.values()[0].results == [([], "<ok>")]

                assert Thing().do("not ok", user_id=10004) == "<not ok>"
                assert p.instruments.values()[0].results == [([], "<ok>")]


class TestProbePatching(ProbeTestCase):
    def test_patch_staticmethod(self):
        with self.probe(
            "test",
            "quantile",
            "probes.test_fixtures.Thing.static",
            "result",
            internal=False,
        ) as p:
            assert Thing().static() == 15
            assert p.instruments.values()[0].results == [([], 15)]

    def test_patch_wrapped_function_internal(self):
        probe = probes.FunctionProbe("probes.test_fixtures.Thing.add5")
        try:
            probe.start()
            instr = ProbeTestInstrument("deco", "arg1", internal=True)
            probe.instruments["deco"] = instr
            Thing().add5(13)
            assert instr.results == [([], 113)]
        finally:
            probe.stop()


class TestHardcodedProbes(ProbeTestCase):
    def test_hardcoded_probes(self):
        probes.manager.apply()
        assert mult_by_8(3) == 24
        assert [
            p.instruments.values()[0].results for p in probes.manager.probes.values()
        ] == [[([], 24)]]
