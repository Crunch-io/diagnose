import gc
import mock
import six
import types
import weakref

omitted = object()


def make_patches(target, make_wrapper, patch_all_referrers=True):
    """Return a list of mock._patch objects which wrap the given target function.

    The `target` argument must refer to the function you wish to patch:
    either a string: the dotted import path to the function, or a 2-tuple:
    an object and the name of one of its attributes.

    The `make_wrapper` argument must be a function that takes an initial
    `base` argument and returns that function, wrapped with whatever
    functionality you like.

    If `patch_all_referrers` is True (the default), then not only the given
    reference will be patched, but all other references to the same function
    object will be patched, if possible. This includes module and class
    members, as well as entries in dictionaries (like function registries).
    One important reference that CANNOT be patched is function closures;
    that is, if you have already wrapped function A with B in the typical
    functional way:

        def B(func):
            @functools.wraps(func)
            def wrapper(*args, **kwargs):
                do_something()
                return func(*args, **kwargs)
            return wrapper

    ...then that internal call to func() will not be patched. It's not mutable.
    You can fix this by changing the wrapper function to a wrapper _class_
    which sets `self.func = func` and whose `__call__` method calls `self.func()`,
    because that can be discovered and patched.
    """
    if isinstance(target, six.string_types):
        primary_patch = mock.patch(target)
        original, local = primary_patch.get_original()
    elif isinstance(target, tuple) and len(target) == 2:
        if not isinstance(target[1], six.text_type):
            raise TypeError(
                "Targets which are (obj, funcname) 2-tuples MUST pass the funcname as a string."
            )
        primary_patch = mock.patch.object(*target)
        original, local = primary_patch.get_original()
    elif isinstance(target, (types.FunctionType, types.MethodType)):
        primary_patch = _patch_one(target)
        if primary_patch is None:
            raise TypeError("Cannot patch: %s could not be found." % (repr(target),))
        original = target
    else:
        raise TypeError(
            "Cannot patch: %s is not an (obj, attr) pair nor a dotted path name."
            % (repr(target),)
        )

    # Replace the target with a wrapper.
    if isinstance(original, (types.FunctionType, types.MethodType)):
        base = original
    elif isinstance(original, (staticmethod, classmethod)):
        base = original.__func__
    elif isinstance(original, property):
        base = original.fget
    else:
        raise TypeError("Cannot patch: %s is not a function." % (repr(target),))

    wrapper = make_wrapper(base)

    if isinstance(original, property):
        # We can't patch original.fget directly because it's read-only,
        # so we replace the whole property with a new one, passing our
        # wrapper as its fget.
        # At this time, we only patch fget. If there's enough demand,
        # we could do all three in the future, but then that would take
        # three wrapper functions, and what the instruments do
        # with three instead of one could be very confusing.
        primary_patch.new = property(
            wrapper, original.fset, original.fdel, original.__doc__
        )
    else:
        if isinstance(original, staticmethod):
            wrapper = staticmethod(wrapper)
        elif isinstance(original, classmethod):
            wrapper = classmethod(wrapper)
        primary_patch.new = wrapper

    patches = [primary_patch]

    if patch_all_referrers:
        # Add patches for any other modules/classes which have
        # the target as an attribute, or "registry" dicts which have
        # the target as a value.
        _resolved_target = primary_patch.getter()
        refs = gc.get_referrers(original)
        for ref in refs:
            if not isinstance(ref, dict):
                continue

            names = [k for k, v in ref.items() if v is original]
            seen_names = set()
            for parent in gc.get_referrers(ref):
                if parent is _resolved_target or parent is primary_patch:
                    continue
                if parent is wrapper:
                    # In Python 3.2+, `@functools.wraps(base)` above sets
                    # `wrapper.__wrapped__ = wrapped`. We don't want to
                    # patch that with itself!
                    continue

                if getattr(parent, "__dict__", None) is ref:
                    # An attribute of a "parent" module or class or instance.
                    for name in names:
                        patches.append(WeakMethodPatch(parent, name, wrapper))
                else:
                    for gpa in gc.get_referrers(parent):
                        if getattr(gpa, "__dict__", None) is parent:
                            # A member of a "parent" dict which is an attribute
                            # of a "grandparent" module or class or instance.
                            # ref[name] = original, where gpa.parent = ref
                            for name in names:
                                if name in seen_names:
                                    # Don't patch the same dict twice, or
                                    # a) we'll waste cycles, and
                                    # b) DictPatch.stop() may restore a patch
                                    # instead of the correct original.
                                    pass
                                else:
                                    patches.append(DictPatch(ref, name, wrapper))
                                    seen_names.add(name)
                            break

    return patches


def _patch_one(original):
    original_qualname = original.__qualname__

    # Try to find the original object
    refs = gc.get_referrers(original)
    for ref in refs:
        if not isinstance(ref, dict):
            continue

        names = [k for k, v in ref.items() if v is original]
        for parent in gc.get_referrers(ref):
            pq = (
                parent.__qualname__
                if isinstance(parent, type)
                else parent.__class__.__qualname__
            )
            # An attribute of a "parent" module or class or instance.
            for name in names:
                if "%s.%s" % (pq, name) == original_qualname:
                    return mock.patch.object(parent, name)


# ----------------------------- Weak patch ----------------------------- #


class WeakMethodPatch(object):
    """A Patch for an attribute a Python object.

    On start/__enter__, calls self.getter() which should return an object,
    then replaces the given attribute of that object with the new value.
    On stop/__exit__, replaces the same attribute with the previous value.

    Used by make_patches to replace references to functions which appear in
    modules, classes, or other objects. Weak references are used internally
    so that, if the object is removed from that module etc (has no more strong
    references), then the patch is automatically abandoned.
    """

    def __init__(self, obj, attribute, new):
        try:
            getter = weakref.ref(obj, self._safe_stop)
        except TypeError:

            def getter():
                return obj

        self.getter = getter
        self.attribute = attribute
        self.new = new

    def __repr__(self):
        return "%s(%s, %s, %s)" % (
            self.__class__.__name__,
            self.getter,
            self.attribute,
            self.new,
        )

    def _safe_stop(self, ref=None):
        try:
            self.stop()
        except RuntimeError:
            # Already stopped. Ignore.
            pass

    def get_original(self):
        target = self.getter()
        name = self.attribute

        original = omitted
        local = False

        try:
            original = target.__dict__[name]
        except (AttributeError, KeyError):
            original = getattr(target, name, omitted)
        else:
            local = True

        if original is omitted:
            raise AttributeError("%s does not have the attribute %r" % (target, name))
        return original, local

    def __enter__(self):
        """Perform the patch."""
        obj = self.getter()
        if obj is None:
            # The object we wanted to patch has already been garbage-collected.
            return

        original, local = self.get_original()
        self.temp_original = weakref.ref(original)
        self.is_local = local
        setattr(obj, self.attribute, self.new)
        return self.new

    def __exit__(self, *exc_info):
        """Undo the patch."""
        if not hasattr(self, "is_local"):
            raise RuntimeError("stop called on unstarted patcher")

        target = self.getter()
        if target is None:
            return

        original = self.temp_original()
        if original is None:
            return

        if getattr(target, self.attribute, None) is self.new:
            if self.is_local:
                setattr(target, self.attribute, original)
            else:
                delattr(target, self.attribute)
                if not hasattr(target, self.attribute):
                    # needed for proxy objects like django settings
                    setattr(target, self.attribute, original)

        del self.is_local

    def start(self):
        """Activate a patch, returning any created mock."""
        result = self.__enter__()
        return result

    def stop(self):
        """Stop an active patch."""
        return self.__exit__()


class DictPatch(object):
    """A Patch for a member of a Python dictionary.

    On start/__enter__, replaces the member of the given dictionary
    identified by the given key with a new object. On stop/__exit__,
    replaces the same key with the previous object.

    Used by make_patches to replace references to functions which appear
    in any dictionary, such as a function registry.
    """

    def __init__(self, dictionary, key, new):
        self.dictionary = dictionary
        self.key = key
        self.new = new

    def get_original(self):
        return self.dictionary[self.key], True

    def __enter__(self):
        """Perform the patch."""
        original, local = self.get_original()
        self.temp_original = original
        self.is_local = local
        self.dictionary[self.key] = self.new
        return self.new

    def __exit__(self, *exc_info):
        """Undo the patch."""
        if not hasattr(self, "is_local"):
            raise RuntimeError("stop called on unstarted patcher")

        self.dictionary[self.key] = self.temp_original

        del self.is_local

    def start(self):
        """Activate a patch, returning any created mock."""
        result = self.__enter__()
        return result

    def stop(self):
        """Stop an active patch."""
        return self.__exit__()
