import inspect
from diagnose.breakpoints import Breakpoint


def fn_to_be_debugged(a):
    return a * a


def call_fn():
    c = 5
    return fn_to_be_debugged(c + 1)


def test_breakpoint_stackframe():
    fn_module = inspect.getmodule(fn_to_be_debugged)
    with Breakpoint(fn_module, "fn_to_be_debugged") as bp:
        bp.start_thread(
            call_fn
        )  # Start something in the background that will invoke call_fn
        bp.wait()  # wait for fn_to_be_debugged to start
        frame = bp.stackframe
        locs = frame.f_locals
        # print(locs)
        assert locs["args"] == (6,)
        call_fn_locs = frame.f_back.f_locals
        # print(call_fn_locs)
        assert call_fn_locs["c"] == 5
        bp.release()  # let fn_to_be_debugged proceed.
