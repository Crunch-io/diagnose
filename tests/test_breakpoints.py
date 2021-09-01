import inspect
from diagnose.breakpoints import Breakpoint


def square_of_x(x):
    return x * x


def square_of_x_plus_1(x):
    return square_of_x(x + 1)


def test_breakpoint_stackframe():
    fn_module = inspect.getmodule(square_of_x)
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
