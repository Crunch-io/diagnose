"""A tool to detect and synchronize execution or simulate errors during tests.

Use a Breakpoint to detect concurrent calls, simulate deterministic
interleavings of operations, or throw internal errors which would
otherwise be left to concurrency accidents or complicated calls
to sleep(). All of this can be done entirely in tests, without infecting
production code with a bunch of scaffolding.

When a Breakpoint is hit, its condition is checked (if any), and the True
or False result is appended to its .calls. Callpoints without a condition
append True every time they are called. Test code may call bp.wait(),
which blocks until there has been at least one (conditional) call. For example:

    with Breakpoint("path.to.obj.func", event="return") as bp:
        bp.start_thread(foo)  # start running foo in a thread
        bp.wait()             # wait for that thread to hit the function

        assert some_effect_that_func_causes()

If the system somehow hits the breakpoint before the test calls wait(),
then wait() simply returns immediately.
If no calls happen within the timeout, wait() throws a RuntimeError.
If test code does not call wait(), and the "with" block exits with no calls
recorded, it will throw a RuntimeError. You can use this to assert calls even
in completely synchronous code.

Breakpoints may be conditional based on the arguments being passed.
Set bp.condition to a callable which takes the same arguments as the
target function; if it returns True, the call is recorded as successful.
Alternately, the condition may be an int or list of ints, in which case
it will be considered successful on those numbered calls (starting from 0).

When a breakpoint is hit, the breakpoint's "stackframe" attribute is
set to the current frame. Using this, you can inspect the call
stack or function arguments while inside the "with" block.

Blocking Breakpoints
--------------------

A blocking Breakpoint does not merely record calls, but blocks the caller
on successful calls until released. This lets it behave like a semaphore,
allowing a test and the system (the thread being tested) to synchronize
their execution. The test calls bp.wait(), which blocks; when the system
hits the breakpoint, they switch: the system blocks and the test unblocks.
While the system is blocked, the test is free to inspect or alter state
without fear.

For example:

    with Breakpoint.block((obj, funcname)) as bp:
        bp.start_thread(foo)  # start running foo in a thread
        bp.wait()             # wait for that thread to hit the function

        assert foo.is_running
        foo.do_something()

The test may unblock the system by explicitly calling bp.release(),
or just waiting for the "with" block to exit. If neither happens
within the timeout, the system thread throws RuntimeError.

Erroring Breakpoints
--------------------

An erroring Breakpoint is initialized with an exception instance, which is raised
whenever the condition is met. Use this to simulate errors inside concurrent
code at specific points.

For example:

    with Breakpoint.error("path.to.obj.func") as bp:
        bp.start_thread(foo)  # start running foo in a thread
        bp.wait()             # wait for that thread to hit the function

        assert "server thread failed" in error_logs

The `do` class
--------------

Breakpoints have more power, but can be confusing because you have to
declare what to run after you declare where it should block. The `do` class
allows you to reverse that order, and use a more English-like chain of
adjustments, with less boilerplate. It assumes you want to do the most
common thing, which is start a thread running some function and then wait
for it to hit the Breakpoint. For example, instead of:

    with Breakpoint("path.to.obj.func", event="return") as bp:
        bp.start_thread(foo)
        bp.wait()
        etc

...this class lets you write:

    with do(foo).until("path.to.obj.func").returns:
        etc

Entering the context starts a thread running the given function and will
wait() for the Breakpoint to be hit. Exiting the context will join()
the thread.

Most do() methods and properties return the do() instance itself, so it doesn't
matter which one comes last; they can all be passed to a `with` block, like so:

    t = do(foo)
    with t.until("path.to.obj.func").returns:
        etc

This is especially handy for another common case: gathering results. Every do()
instance stores the output in self.results automatically, even if you call it
more than once:

    inputs = ["a", "b"]
    t = do(len, inputs)
    with t:
        pass
    inputs.append("c")
    with t:
        pass
    assert t.results == [2, 3]

"""

import functools
import inspect
import threading
import time

import six

from diagnose import patchlib


omitted = object()


class Breakpoint:
    """A tool to detect and synchronize execution or simulate errors during tests."""

    check_interval = 0.1
    """The period, in seconds, at which to poll for timeout."""

    patch_all_referrers = None
    """If True, all references to the given target will be patched.
    If False, only the given reference will be patched. If None
    (the default), all references will be patched when `target`
    is a string (dotted-import path), but only the given reference
    will be patched if `target` is an (object, attribute-name) tuple."""

    def __init__(
        self, target, event="call", condition=None, timeout=10.0, fire=None,
    ):
        """
        target:
            Function to be patched: may be the dotted-import path as a string
            or an (obj, funcname) tuple.
        event:
            "call" (default) to fire when the function is entered,
            "return" to fire when the function exits,
            "error" to fire when the function throws an Exception.
        condition:
            None to always fire, or a callable that takes the same args
            as the patched target and returns True to fire, False to not.
            Alternately, it may be an int or list of ints, in which case
            it will fire on those numbered calls.
        timeout:
            The default time, in seconds, to wait().
        fire:
            None (the default) to take no action when the Breakpoint is hit.
            Pass a no-arg callable to perform some other action.
            Use Breakpoint.block() or Breakpoint.error() to use
            the builtin actions.
        """
        self.target = target
        self.event = event
        self.condition = condition
        self.timeout = timeout

        self._started_threads = []
        self.stackframe = None

        self.calls = []
        self.hits = 0
        self.fire = fire

    def _make_wrapper(self, base):
        """A function wrpper which fires any internal action for a Breakpoint."""

        @functools.wraps(base)
        def breakpoint_wrapper(*args, **kwargs):
            self.stackframe = inspect.currentframe()
            if self.event == "call":
                if self._condition_met(args, kwargs):
                    if self.fire is not None:
                        self.fire()

            try:
                result = base(*args, **kwargs)
            except Exception:
                if self.event == "error":
                    if self._condition_met(args, kwargs):
                        if self.fire is not None:
                            self.fire()
                raise
            else:
                if self.event == "return":
                    if self._condition_met(args, kwargs):
                        if self.fire is not None:
                            self.fire()
            finally:
                # Best practice is not to hold onto stackframe longer
                # than it is needed.
                self.stackframe = None

            return result

        return breakpoint_wrapper

    def _condition_met(self, args, kwargs):
        if self.condition is None:
            met = True
        elif isinstance(self.condition, int):
            met = len(self.calls) == self.condition
        elif isinstance(self.condition, (set, tuple, list)):
            met = len(self.calls) in self.condition
        elif callable(self.condition):
            met = self.condition(*args, **kwargs)
        else:
            raise TypeError(
                "Breakpoint.condition must be None, an int or list of ints, or a callable."
            )
        self.calls.append(met)
        if met:
            self.hits += 1
        return met

    def __enter__(self):
        self.calls = []
        self.hits = 0
        self._started_threads = []
        self.release()

        if self.target is None:
            self.patches = []
        else:
            patch_all = self.patch_all_referrers
            if patch_all is None:
                patch_all = isinstance(self.target, six.string_types)
            self.patches = patchlib.make_patches(
                self.target, self._make_wrapper, patch_all_referrers=patch_all
            )

        for p in self.patches:
            p.start()

        return self

    def __exit__(self, type, value, traceback):
        while self.patches:
            p = self.patches.pop(0)
            p.stop()

        self.release()

        while self._started_threads:
            t = self._started_threads.pop(0)
            t.join()

        if type is not None:
            # There was already an error, don't suppress it.
            return False

        if self.target is not None:
            if isinstance(self.condition, int):
                if len(self.calls) <= self.condition or not self.calls[self.condition]:
                    raise AssertionError(
                        "Breakpoint condition on %s was not met for iteration %s."
                        % (self.target, self.condition)
                    )
            elif isinstance(self.condition, (set, tuple, list)):
                not_called = [
                    c
                    for c in self.condition
                    if len(self.calls) <= c or not self.calls[c]
                ]
                if not_called:
                    raise AssertionError(
                        "Breakpoint condition on %s was not met for iterations %s."
                        % (self.target, not_called)
                    )
            else:
                if not any(self.calls):
                    raise AssertionError(
                        "Breakpoint condition on %s was not met." % (self.target,)
                    )

    def start_thread(self, func, *args, **kwargs):
        """Execute the given func (a callable) in another thread.

        Tests may call this to run the system in the background.

        Threads started here will be joined on breakpoint context exit.
        """
        t = threading.Thread(
            target=func,
            name="Breakpoint_%s_%s" % (func.__name__, len(self._started_threads)),
            args=args,
            kwargs=kwargs,
        )
        self._started_threads.append(t)
        t.start()

    def join(self):
        """Wait for all threads started by this instance to complete."""
        for thread in self._started_threads:
            thread.join()

    def wait(self, timeout=omitted, hits=1):
        """Block until the breakpoint is hit by other thread(s).

        Tests should call this to wait for other threads to call the
        breakpoint.target function. Once they do, this method returns and the
        test may proceed.

        If `hits` is greater than 1, this function waits until the breakpoint
        has been hit the given number of times before returning.
        """
        if self.target is None:
            return

        if timeout is omitted:
            timeout = self.timeout

        start = time.time()
        while self.hits < hits:
            if timeout is not None and time.time() - start > timeout:
                raise RuntimeError(
                    "Breakpoint on %s (event='%s') not hit after %s seconds."
                    % (self.target, self.event, timeout)
                )
            time.sleep(self.check_interval)

    def wait_until(self, condition, timeout=omitted):
        """Block until the condition is True, or error if the timeout is reached.

        Call this to wait for the system to advance to a certain state;
        once the given `condition` callable returns True, this function
        returns. If the timeout (or self.timeout if omitted) is reached,
        a RuntimeError is raised.
        """
        if timeout is omitted:
            timeout = self.timeout

        start = time.time()
        while not condition():
            if timeout is not None and time.time() - start > timeout:
                raise RuntimeError(
                    "Condition for %s not met after %s seconds."
                    % (self.target, timeout)
                )
            time.sleep(self.check_interval)

    # ------------------------- Blocking breakpoints ------------------------- #

    @classmethod
    def block(cls, *args, **kwargs):
        """Create a Breakpoint which blocks, to synchronize execution."""
        self = cls(*args, **kwargs)
        self.fire = self._fire_blocking
        return self

    def _fire_blocking(self):
        """The internal action for a blocking Breakpoint."""
        start = time.time()
        timeout = self.timeout
        self.blocked = True
        while self.blocked:
            if timeout is not None and time.time() - start > timeout:
                raise RuntimeError(
                    "Breakpoint on %s timed out after %s seconds."
                    % (self.target, timeout)
                )
            time.sleep(self.check_interval)

    def release(self):
        """Allow the system to proceed (until the breakpoint is hit again).

        When the system hits a blocking breakpoint, it blocks while the test proceeds.
        When the context exits, it calls this method to unblock the system.
        If the test wants to unblock the system before the context exits,
        it may call this method directly.
        """
        self.hits = 0
        self.blocked = False

    # ------------------------- Erroring breakpoints ------------------------- #

    @classmethod
    def error(cls, exception, *args, **kwargs):
        """Create a Breakpoint which throws the given exception.

        The `exception` argument must be an exception instance, which is raised
        whenever the condition is met.
        """
        self = cls(*args, **kwargs)
        self.exception = exception
        self.fire = self._fire_erroring
        return self

    def _fire_erroring(self):
        """The internal action for an erroring Breakpoint."""
        raise self.exception


class do:
    """A helper class to run concurrent functions with Breakpoints."""

    breakpoint = None

    def __init__(self, func=None, *args, **kwargs):
        self.func = func
        self.args = args
        self.kwargs = kwargs
        self.breakpoint = Breakpoint(None)
        self.results = []

    def until(self, target, timeout=None):
        """Set a blocking Breakpoint for the given target."""
        self.breakpoint.target = target
        self.breakpoint.fire = self.breakpoint._fire_blocking
        if timeout is not None:
            self.breakpoint.timeout = timeout
        return self

    def beyond(self, target, timeout=None):
        """Set a non-blocking Breakpoint for the given target."""
        self.breakpoint.target = target
        if timeout is not None:
            self.breakpoint.timeout = timeout
        return self

    def error_on(self, target, exception, timeout=None):
        """Set an erroring Breakpoint for the given target."""
        self.breakpoint.target = target
        self.breakpoint.exception = exception
        self.breakpoint.fire = self.breakpoint._fire_erroring
        if timeout is not None:
            self.breakpoint.timeout = timeout
        return self

    @property
    def returns(self):
        """Set the Breakpoint to fire when the target returns, not when called."""
        if self.breakpoint is None:
            raise RuntimeError(
                "You must call do().until(), .beyond(), or .error_on() before declaring .returns."
            )
        self.breakpoint.event = "return"
        return self

    @property
    def errors(self):
        """Set the Breakpoint to fire when the target errors, not when called."""
        if self.breakpoint is None:
            raise RuntimeError(
                "You must call do().until(), .beyond(), or .error_on() before declaring .errors."
            )
        self.breakpoint.event = "error"
        return self

    def where(self, condition):
        """Set the Breakpoint to fire only when the given condition is met."""
        self.breakpoint.condition = condition
        return self

    @property
    def once(self):
        """Set the Breakpoint to fire only once."""
        self.breakpoint.condition = 0
        return self

    def __enter__(self):
        self.breakpoint.__enter__()
        if self.func is not None:
            self.breakpoint.start_thread(self._gather_results)
            self.breakpoint.wait()
        return self

    def __exit__(self, type, value, traceback):
        self.breakpoint.__exit__(type, value, traceback)

    def _gather_results(self):
        try:
            result = self.func(*self.args, **self.kwargs)
        except Exception as exc:
            result = exc
            raise
        finally:
            self.results.append(result)

    def release(self):
        """Release any threads blocked on the Breakpoint."""
        self.breakpoint.release()
