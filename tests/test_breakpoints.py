from contextlib import contextmanager
import inspect
import time


from diagnose.breakpoints import Breakpoint, do
from diagnose.test_fixtures import Thing


class Man:
    def __init__(self):
        self.mints = 0

    def add_mint(self):
        self.mints += 1

    def add_mints(self, num):
        for m in range(num):
            self.add_mint()


def square_of_x(x):
    return x * x


def square_of_x_plus_1(x):
    return square_of_x(x + 1)


fn_module = inspect.getmodule(square_of_x)


class TestBreakpointEvent:
    def test_event_call(self):
        thing = Thing()

        # When we fire on call...
        with Breakpoint.block(Thing.advance_stage) as bp:
            assert thing.stage is None
            bp.start_thread(thing.advance_stage, "done")
            bp.wait()

            # The function must not have been entered yet
            assert thing.stage is None

        # ...but after the context exits and the block is released,
        # the context must join() the thread, so that by this point,
        # the function must have completed execution
        assert thing.stage == "done"

    def test_event_return(self):
        thing = Thing()

        # When we fire on return...
        with Breakpoint.block(Thing.advance_stage, event="return") as bp:
            assert thing.stage is None
            bp.start_thread(thing.advance_stage, "done")
            bp.wait()

            # The function must have completed by this point
            assert thing.stage == "done"


class TestBreakpointCondition:
    def test_none_condition(self):
        thing = Thing()

        with Breakpoint.block(Thing.advance_stage) as bp:
            bp.start_thread(thing.advance_multiple)
            bp.wait()

            # At this point, because our breakpoint is on the "call" event,
            # the alpha call has fired and blocked but not completed.
            assert thing.stage is None

            bp.release()
            bp.wait()

            bp.release()
            bp.wait()

            # At this point, because our breakpoint is on the "call" event,
            # the alpha and beta calls have fired and completed, but the
            # gamma call, although it has fired, has not yet executed.
            assert thing.stage == "beta"

        # ...but after the context exits and the block is released,
        # the context must join() the thread, so that by this point,
        # the function must have completed execution
        assert thing.stage == "delta"

    def test_callable_condition(self):
        thing = Thing()

        with Breakpoint.block(
            Thing.advance_stage, condition=lambda self, to: to == "gamma"
        ) as bp:
            bp.start_thread(thing.advance_multiple)
            bp.wait()

            # At this point, because our breakpoint is on the "call" event,
            # the alpha and beta calls have fired and completed, but the
            # gamma call, although it has fired, has not yet executed.
            assert thing.stage == "beta"

        # ...but after the context exits and the block is released,
        # the context must join() the thread, so that by this point,
        # the function must have completed execution
        assert thing.stage == "delta"

    def test_numeric_condition(self):
        thing = Thing()

        with Breakpoint.block(Thing.advance_stage, condition=1) as bp:
            bp.start_thread(thing.advance_multiple)
            bp.wait()

            # At this point, because our breakpoint is on the "call" event,
            # the alpha call has fired and completed, but the beta call,
            # although it has fired, has not yet executed.
            assert thing.stage == "alpha"

        # ...but after the context exits and the block is released,
        # the context must join() the thread, so that by this point,
        # the function must have completed execution
        assert thing.stage == "delta"

    def test_numeric_list_condition(self):
        thing = Thing()

        with Breakpoint.block(Thing.advance_stage, condition=[1, 3]) as bp:
            bp.start_thread(thing.advance_multiple)
            bp.wait()

            # At this point, because our breakpoint is on the "call" event,
            # the alpha call has fired and completed, but the beta call,
            # although it has fired, has not yet executed.
            assert thing.stage == "alpha"

            bp.release()
            bp.wait()
            assert thing.stage == "gamma"

        # ...but after the context exits and the block is released,
        # the context must join() the thread, so that by this point,
        # the function must have completed execution
        assert thing.stage == "delta"


class TestBreakpointConditionNotMet:
    @contextmanager
    def assertRaises(self, exctype, arg0=None):
        try:
            yield
        except exctype as exc:
            if arg0:
                assert exc.args[0] == arg0
        else:
            raise AssertionError("%s exception not raised." % (exctype,))

    def test_none_condition_not_met(self):
        thing = Thing()

        with self.assertRaises(
            AssertionError, "Breakpoint condition on %s was not met." % (Thing.add5,)
        ):
            with Breakpoint.block(Thing.add5, timeout=0.1) as bp:
                bp.start_thread(thing.advance_multiple)
                with self.assertRaises(RuntimeError):
                    bp.wait()
                assert thing.stage == "delta"

    def test_callable_condition_not_met(self):
        thing = Thing()

        with self.assertRaises(
            AssertionError,
            "Breakpoint condition on %s was not met." % (Thing.advance_stage,),
        ):
            with Breakpoint.block(
                Thing.advance_stage,
                condition=lambda self, to: to == "omicron",
                timeout=0.2,
            ) as bp:
                bp.start_thread(thing.advance_multiple)
                with self.assertRaises(RuntimeError):
                    bp.wait()
                assert thing.stage == "delta"

    def test_numeric_condition_not_met(self):
        thing = Thing()

        with self.assertRaises(
            AssertionError,
            "Breakpoint condition on %s was not met for iteration 987."
            % (Thing.advance_stage,),
        ):
            with Breakpoint.block(
                Thing.advance_stage, condition=987, timeout=0.2
            ) as bp:
                bp.start_thread(thing.advance_multiple)
                with self.assertRaises(RuntimeError):
                    bp.wait()
                assert thing.stage == "delta"

    def test_numeric_list_condition_not_met(self):
        thing = Thing()

        with self.assertRaises(
            AssertionError,
            "Breakpoint condition on %s was not met for iterations [987]."
            % (Thing.advance_stage,),
        ):
            with Breakpoint.block(
                Thing.advance_stage, condition=[1, 987], timeout=0.2
            ) as bp:
                bp.start_thread(thing.advance_multiple)
                bp.wait()

                bp.release()
                with self.assertRaises(RuntimeError):
                    bp.wait()
                assert thing.stage == "delta"

    def test_no_wait(self):
        thing = Thing()

        with Breakpoint.block(Thing.advance_stage) as bp:
            bp.start_thread(thing.advance_multiple)
            assert bp._started_threads != []

            # Even though we do not call wait(), when the context exits,
            # it must release() any blocked threads and then join() them.

        assert bp._started_threads == []


class TestBreakpointFireFunction:
    def test_fire_none(self):
        thing = Thing()

        with Breakpoint(Thing.advance_stage, event="return") as bp:
            bp.start_thread(thing.advance_multiple)
            bp.wait()

            # With no fire() function, the call should simply proceed
            # once it hits the breakpoint each time.
            # Our wait() call simply ensures that the function has returned
            # at least once.
            assert thing.stage is not None

    def test_fire_blocking(self):
        thing = Thing()

        with Breakpoint.block(Thing.advance_stage, event="return") as bp:
            bp.start_thread(thing.advance_multiple)
            bp.wait()

            # With a fire() function that blocks, our wait() call returns
            # once it has blocked.
            assert thing.stage == "alpha"

    def test_fire_blocking_timeout(self):
        thing = Thing()

        caught = []

        def trapping_errors(f):
            try:
                f()
            except RuntimeError as exc:
                caught.append(exc.args[0])

        with Breakpoint.block(Thing.advance_stage, timeout=0.001) as bp:
            bp.start_thread(trapping_errors, thing.advance_multiple)
            bp.wait()
            bp.wait_until(lambda: caught, timeout=10.0)

        assert caught == [
            "Breakpoint on %s timed out after 0.001 seconds." % (Thing.advance_stage,)
        ]

    def test_fire_erroring(self):
        thing = Thing()

        caught = []

        def trapping_errors(f):
            try:
                f()
            except ValueError as exc:
                caught.append(exc.args[0])

        with Breakpoint.error(ValueError("abc"), Thing.advance_stage) as bp:
            bp.start_thread(trapping_errors, thing.advance_multiple)
            bp.wait()

        assert caught == ["abc"]


class TestBreakpointStackframe:
    def test_stackframe(self):
        with Breakpoint.block((fn_module, "square_of_x")) as bp:
            # Start something in the background that will invoke square_of_x
            bp.start_thread(square_of_x_plus_1, 5)
            # wait for square_of_x to start
            bp.wait()

            frame = bp.stackframe
            locs = frame.f_locals
            assert locs["args"] == (6,)

            caller_locs = frame.f_back.f_locals
            assert caller_locs["x"] == 5


class TestDo:
    def test_no_target(self):
        t = do()
        with t:
            pass

    def test_basic(self):
        t = do(square_of_x, 4)
        with t:
            assert t.results == [16]
        assert t.results == [16]

    def test_until(self):
        t = do(square_of_x_plus_1, 4)
        with t.until((fn_module, "square_of_x")):
            assert t.results == []
        assert t.results == [25]

    def test_beyond(self):
        t = do(square_of_x_plus_1, 4)
        with t.beyond((fn_module, "square_of_x")):
            assert t.results == [25]
        assert t.results == [25]

    def test_error_on(self):
        caught = []

        def trapping_errors(f, *args):
            try:
                f(*args)
            except ValueError as exc:
                caught.append(exc.args[0])

        t = do(trapping_errors, square_of_x_plus_1, 4)
        with t.error_on((fn_module, "square_of_x"), ValueError("xyz")):
            assert t.results == [None]
        assert t.results == [None]

        assert caught == ["xyz"]

    def test_returns(self):
        mr_creosote = Man()

        t = do(mr_creosote.add_mints, 4)
        with t.until((Man, "add_mint")):
            assert mr_creosote.mints == 0
        assert mr_creosote.mints == 4

        with t.until((Man, "add_mint")).returns:
            assert mr_creosote.mints == 5
        assert mr_creosote.mints == 8

    def test_where(self):
        mr_creosote = Man()

        t = do(mr_creosote.add_mints, 4)
        with t.until((Man, "add_mint")).where(2):
            # Note where(2) above is 0-indexed, so it actually refers
            # to the _third_ call, but because we fire on the "call" event,
            # the mints have only increased to 2 at this point.
            assert mr_creosote.mints == 2
        assert mr_creosote.mints == 4

    def test_once(self):
        mr_creosote = Man()

        t = do(mr_creosote.add_mints, 4)
        with t.until((Man, "add_mint")).returns.once:
            assert mr_creosote.mints == 1
        assert mr_creosote.mints == 4
