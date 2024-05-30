import functools
import gc
import sys
import types
import unittest
from collections import Counter
from contextlib import contextmanager
from unittest import mock

from diagnose import patchlib, test_fixtures
from diagnose.test_fixtures import Thing, func_2, funcs, sum4

registry = {}


def owner_types(obj):
    num_instances = Counter()
    for ref in gc.get_referrers(obj):
        if not isinstance(ref, dict):
            if hasattr(ref, "__dict__"):
                ref = ref.__dict__
            else:
                continue

        for parent in gc.get_referrers(ref):
            if getattr(parent, "__dict__", None) is ref:
                num_instances[type(parent)] += 1
                break
    return dict(num_instances)


def weak_referents(patches):
    """Return the object patched by each WeakMethodPatch in the given patches."""
    return [p.getter() for p in patches if isinstance(p, patchlib.WeakMethodPatch)]


class TestMakePatches(unittest.TestCase):
    def make_wrapper(self, base):
        @functools.wraps(base)
        def test_wrapper(*args, **kwargs):
            try:
                result = base(*args, **kwargs)
                self.results.append((args, kwargs, result))
                return result
            except:
                result = sys.exc_info()[1]
                self.results.append((args, kwargs, result))
                raise

        return test_wrapper

    @contextmanager
    def patch_all(self, target):
        self.results = []
        self.patches = patchlib.make_patches(target, self.make_wrapper)
        for p in self.patches:
            p.start()
        try:
            yield
        finally:
            while self.patches:
                p = self.patches.pop(0)
                p.stop()

    def test_nonexistent(self):
        with self.assertRaises(AttributeError) as exc:
            patchlib.make_patches(
                "diagnose.test_fixtures.Thing.notamethod", self.make_wrapper
            )

        expected_message = "<class 'diagnose.test_fixtures.Thing'> does not have the attribute 'notamethod'"

        assert exc.exception.args[0] == expected_message

    def test_patch_all_referrers(self):
        # When module M chooses "from x import y", then mock.patching x.y
        # does not affect M.y. Similarly, an existing object instance I
        # which has I.y = y is not patched by mock.patch.
        # Assert that patchlib patches (and UNpatches) all such copies of y.
        old_probes_func_2 = func_2
        old_local_func_2 = func_2

        class Entity:
            pass

        t = Entity()
        t.add17 = func_2
        assert t.add17 is old_local_func_2

        t2 = Entity()
        t2.add17 = func_2
        assert t2.add17 is old_local_func_2

        registry["in_a_dict"] = func_2
        assert registry["in_a_dict"] is old_local_func_2

        # Before attaching the probe, there should be some references to func_2,
        # but not our patch objects.
        expected_result = {types.ModuleType: 2, Entity: 2}
        assert owner_types(func_2) == expected_result

        self.results = []
        with self.patch_all("diagnose.test_fixtures.func_2"):
            # Invoking x.y is typical and works naturally...
            assert func_2 is not old_probes_func_2
            func_2(44)
            assert self.results == [((44,), {}, 61)]

            # ...but invoking M.y (we imported func_2 into test_probes' namespace)
            # is harder:
            assert func_2 is not old_local_func_2
            func_2(99999)
            assert self.results == [((44,), {}, 61), ((99999,), {}, 100016)]

            # ...and invoking Entity().y is just as hard:
            assert t.add17 is not old_local_func_2
            assert t2.add17 is not old_local_func_2
            t.add17(1001)
            assert self.results == [
                ((44,), {}, 61),
                ((99999,), {}, 100016),
                ((1001,), {}, 1018),
            ]

            # ...etc:
            assert registry["in_a_dict"] is not old_local_func_2
            registry["in_a_dict"](777)
            assert self.results == [
                ((44,), {}, 61),
                ((99999,), {}, 100016),
                ((1001,), {}, 1018),
                ((777,), {}, 794),
            ]

            # The next problem is that, while our patch is live,
            # if t2 goes out of its original scope, we've still got
            # a reference to it in our mock patch.
            expected_result = {
                # These referred to func_2 before our probe was attached...
                types.ModuleType: 2,
                Entity: 2,
                # ...and these are added by attaching the probe:
                # a) the target that we passed to probes.attach_to()
                mock._patch: 1,
                # b) 3 "methods": t.add17, t2.add17, and test_probes.func_2
                patchlib.WeakMethodPatch: 3,
                # c) the registry dict.
                patchlib.DictPatch: 1,
            }
            assert owner_types(func_2) == expected_result
            # All of the WeakMethodPatch instances should still have a strong reference.
            assert set(weak_referents(self.patches)) == {t, t2, sys.modules[__name__]}

            # Delete one of our references.
            del t2
            expected_result = {
                types.ModuleType: 2,
                # The number of Entity references MUST decrease by 1.
                Entity: 1,
                mock._patch: 1,
                # The number of WeakMethodPatch references does not decrease...
                patchlib.WeakMethodPatch: 3,
                patchlib.DictPatch: 1,
            }
            assert owner_types(func_2) == expected_result
            # ...but the object referred to by WeakMethodPatch should now
            # be unavailable, having been dereferenced. That is, this
            # line asserts that WeakMethodPatch is not holding a strong
            # reference to the original object.
            assert set(weak_referents(self.patches)) == {
                t,
                None,
                sys.modules[__name__],
            }

            # Hit the probed function one more time to verify the unresolvable
            # weakref doesn't crash things.
            t.add17(1234)
            assert self.results == [
                ((44,), {}, 61),
                ((99999,), {}, 100016),
                ((1001,), {}, 1018),
                ((777,), {}, 794),
                ((1234,), {}, 1251),
            ]

        # All patches MUST be stopped
        assert func_2 is old_probes_func_2
        assert func_2 is old_local_func_2
        assert t.add17 is old_local_func_2
        func_2(123)
        func_2(456)
        t.add17(789)
        registry["in_a_dict"](101112)
        assert self.results == [
            ((44,), {}, 61),
            ((99999,), {}, 100016),
            ((1001,), {}, 1018),
            ((777,), {}, 794),
            ((1234,), {}, 1251),
        ]

    def test_function_registries(self):
        with self.patch_all("diagnose.test_fixtures.orig"):
            assert len(self.patches) == 2
            assert self.patches[0].getter() is test_fixtures
            assert self.patches[0].attribute == "orig"
            assert self.patches[1].dictionary is funcs
            assert self.patches[1].key == "orig"

            assert funcs["orig"]("ahem") == "aha!"
            # The patch MUST have logged an entry
            assert self.results == [(("ahem",), {}, "aha!")]

        self.results = []
        assert funcs["orig"]("ahem") == "aha!"
        # The patch MUST NOT have logged an entry
        assert self.results == []

    def test_probe_nonfunc(self):
        # We REALLY should not be allowed to patch anything
        # that's not a function!
        with self.assertRaises(TypeError):
            patchlib.make_patches("diagnose.test_fixtures.Thing", self.make_wrapper)

    def test_patch_staticmethod(self):
        with self.patch_all("diagnose.test_fixtures.Thing.static"):
            assert Thing().static() == 15
            assert self.results == [((), {}, 15)]

    def test_patch_class_decorated(self):
        with self.patch_all("diagnose.test_fixtures.sum4"):
            assert sum4(1, 2, 3, 4) == 10
            assert self.results == [((1, 2, 3, 4), {}, 10)]

    def test_patch_property(self):
        old_prop = Thing.exists

        with self.patch_all("diagnose.test_fixtures.Thing.exists"):
            thing = Thing()
            assert thing.exists is True
            assert self.results == [((thing,), {}, True)]
            assert Thing.exists is not old_prop

        assert Thing().exists is True
        assert Thing.exists is old_prop


class TestDottedImportAutocomplete(unittest.TestCase):
    def test_dotted_import_autocomplete(self):
        assert "gc" in patchlib.dotted_import_autocomplete("")
        assert "gc" in patchlib.dotted_import_autocomplete("g")
        assert patchlib.dotted_import_autocomplete("gc") == ["gc"]
        assert patchlib.dotted_import_autocomplete("gc.") == [
            "gc.%s" % k for k in sorted(dir(gc))
        ]
        assert patchlib.dotted_import_autocomplete("gc.get") == [
            "gc.%s" % k for k in sorted(dir(gc)) if "g" in k and "e" in k and "t" in k
        ]
        assert patchlib.dotted_import_autocomplete("gc.get_objects") == [
            "gc.get_objects"
        ]
