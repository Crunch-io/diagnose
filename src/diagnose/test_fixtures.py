import functools
from six.moves import xrange

import diagnose


def wrap(f):
    class Other(object):
        def __init__(self, f):
            self.f = f

    other = Other(f)

    @functools.wraps(f)
    def inner(self, arg1):
        return other.f(self, arg1 + 100)

    return inner


def a_func(arg):
    extra = 13
    output = arg + extra
    return output


def func_2(arg):
    extra = 17
    output = arg + extra
    return output


def hard_work(lower, upper):
    output = xrange(lower, upper)
    summary = len([x for x in output if x % 10 == 0])
    return summary


def to_columns(rowlist):
    """Convert [{A: 1, B: 5}, {A: 2, B: 6}] to {A: [1, 2], B: [5, 6]}."""
    dictcols = {}
    for i, rowobj in enumerate(rowlist):
        for k, v in rowobj.items():
            dictcols.setdefault(k, [None] * i).append(v)
    for k, v in dictcols.items():
        if len(v) < i:
            dictcols[k] += [None] * (i - len(v))
    return dictcols


class Thing:

    stage = None

    def __init__(self, template="<%s>"):
        self.template = template

    def do(self, arg, user_id=None):
        return self.template % arg

    @staticmethod
    def static():
        return 15

    @wrap
    def add5(self, arg1):
        return arg1 + 5

    @property
    def exists(self):
        return True

    def advance_stage(self, to):
        self.stage = to

    def advance_multiple(self):
        self.advance_stage(to="alpha")
        self.advance_stage(to="beta")
        self.advance_stage(to="gamma")
        self.advance_stage(to="delta")


class ClassDecorator(object):
    def __call__(self, fn):
        @functools.wraps(fn)
        def fn2(*args, **kwargs):
            return fn(*args, **kwargs)

        return fn2


@ClassDecorator()
def sum4(arg1, arg2, arg3, arg4):
    return arg1 + arg2 + arg3 + arg4


def orig(term):
    return term[:2] + "a!"


funcs = {"orig": orig}


@diagnose.instruments.ProbeTestInstrument("mult_by_8", "result")
def mult_by_8(arg):
    return arg * 8
