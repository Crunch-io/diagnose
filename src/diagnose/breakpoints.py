"""Simulate concurrent schedules by pausing execution at a given function."""
import functools
import threading
import time
import inspect

omitted = object()


class Breakpoint:
    """A tool to synchronize execution or simulate errors during tests.

    Use a Breakpoint in tests to simulate deterministic interleavings of
    operations which would otherwise be left to concurrency accidents
    or complicated calls to sleep. You can also simulate internal errors
    with the `throw` argument. All of this can be done entirely in tests,
    without infecting production code with a bunch of scaffolding.

    A Breakpoint acts like a semaphore, allowing a test and the system
    (the thread being tested) to synchronize their execution. The test calls
    bp.wait(), which blocks; when the system hits the breakpoint, they switch:
    the system blocks and the test unblocks. While the system is blocked,
    the test is free to inspect or alter state without fear. For example:

        with Breakpoint(obj, funcname) as bp:
            bp.start_thread(foo)  # start running foo in a thread
            bp.wait()             # wait for that thread to hit the function

            assert foo.is_running
            foo.set_interrupt()

    If the system somehow hits the breakpoint before the test calls wait(),
    then wait() simply returns immediately. If the system doesn't hit
    the breakpoint within the timeout, wait() throws a RuntimeError.

    The test may unblock the system by explicitly calling bp.release(),
    or just waiting for the "with" block to exit (if neither happens
    within the timeout, the system thread throws RuntimeError).

    Breakpoints may be conditional based on the arguments being passed.
    Set bp.condition to a callable which takes the same arguments as the
    breakpoint function; if it returns True, the call blocks and control
    is passed back to the test thread; if False, the call proceeds normally.
    Alternately, the condition may be an int or list of ints, in which case
    it will break on those numbered calls.

    If the `throw` argument is provided, it should be an exception instance,
    which is raised whenever the condition is met instead of blocking.
    Use this to simulate errors at specific points rather than simulating
    deterministic interleaving of operations.

    When a breakpoint is hit, the breakpoints "stackframe" attribute is
    set to the current frame. Using this, you can inspect the call
    stack or function arguments.
    """

    def __init__(
        self,
        obj,
        funcname,
        condition=None,
        timeout=10.0,
        break_on_return=False,
        throw=None,
        check_interval=0.1,
    ):
        """
        obj:
            Class or object to be patched
        funcname:
            str: Name of method/function on that object
        condition:
            None to always break when function is called, or callable that
            takes same args as patched function and returns True to break,
            False to keep going. Alternately, it may be an int or list of
            ints, in which case it will break on those numbered calls.
        break_on_return:
            False (default) to break when the function is entered,
            True to break when the function exits.
        """
        self.blocked = 0
        self.condition = condition
        self.timeout = timeout
        self.break_on_return = break_on_return
        self.throw = throw
        self.check_interval = check_interval
        self._started_threads = []
        self.stackframe = None

        self.obj = obj
        self.funcname = funcname
        self.calls = []
        f = getattr(obj, funcname)

        @functools.wraps(f)
        def breakpoint_wrapper(*args, **kwargs):
            self.stackframe = inspect.currentframe()
            if not self.break_on_return:
                self._block_if_condition_met(args, kwargs)

            result = f(*args, **kwargs)

            if self.break_on_return:
                self._block_if_condition_met(args, kwargs)

            # Best practice is not to hold onto stackframe longer
            # than it is needed.
            self.stackframe = None
            return result

        self.original = f
        self.wrapper = breakpoint_wrapper

    def _block_if_condition_met(self, args, kwargs):
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
            if self.throw is not None:
                raise self.throw

            start = time.time()
            timeout = self.timeout
            self.blocked += 1
            while self.blocked:
                if timeout is not None and time.time() - start > timeout:
                    raise RuntimeError(
                        "Breakpoint on %s.%s timed out after %s seconds."
                        % (self.obj, self.funcname, timeout)
                    )
                time.sleep(self.check_interval)

    def __enter__(self):
        self.calls = []
        self._started_threads = []
        setattr(self.obj, self.funcname, self.wrapper)
        self.blocked = 0
        return self

    def __exit__(self, type, value, traceback):
        setattr(self.obj, self.funcname, self.original)
        self.blocked = 0
        for t in self._started_threads:
            t.join()
        self._started_threads = []

        if type is not None:
            # There was already an error, don't suppress it.
            return False

        if isinstance(self.condition, int):
            if len(self.calls) <= self.condition or not self.calls[self.condition]:
                raise AssertionError(
                    "Breakpoint condition on %s was not met for iteration %s."
                    % (self.funcname, self.condition)
                )
        elif isinstance(self.condition, (set, tuple, list)):
            not_called = [
                c for c in self.condition if len(self.calls) <= c or not self.calls[c]
            ]
            if not_called:
                raise AssertionError(
                    "Breakpoint condition on %s was not met for iterations %s."
                    % (self.funcname, not_called)
                )
        else:
            if not any(self.calls):
                raise AssertionError(
                    "Breakpoint condition on %s was not met." % (self.funcname,)
                )

    def start_thread(self, target, **kwargs):
        """Execute the given target (a callable) in another thread.

        Tests may call this to run the system in the background.

        Threads started here will be joined on breakpoint context exit
        (but only *after* unblocking).
        """
        t = threading.Thread(target=target, **kwargs)
        self._started_threads.append(t)
        t.start()

    def join(self):
        """
        Waits for all the threads to complete.
        """
        for thread in self._started_threads:
            thread.join()

    def wait(self, timeout=omitted, hits=1):
        """Block until the breakpoint is hit by other thread(s).

        Tests should call this to wait for the system to advance to the
        breakpoint function. Once it does, this method returns and the
        test may proceed while the system is blocked. Call release()
        to unblock the system again.

        If `hits` is greater than 1, this function waits until the breakpoint
        has been hit the given number of times before returning.
        """
        if timeout is omitted:
            timeout = self.timeout

        start = time.time()
        while self.blocked < hits:
            if timeout is not None and time.time() - start > timeout:
                raise RuntimeError(
                    "Breakpoint on %s not hit after %s seconds."
                    % (self.funcname, timeout)
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
                    % (self.funcname, timeout)
                )
            time.sleep(self.check_interval)

    def release(self):
        """Allow the system to proceed (until the breakpoint is hit again).

        When the system hits this breakpoint, it blocks while the test
        proceeds. The test should call this method to unblock the system.
        """
        self.blocked = 0
