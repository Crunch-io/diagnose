import functools

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
        for k, v in rowobj.iteritems():
            dictcols.setdefault(k, [None] * i).append(v)
    for k, v in dictcols.items():
        if len(v) < i:
            dictcols[k] += [None] * (i - len(v))
    return dictcols


class Thing:
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


def orig(term):
    return term[:2] + "a!"


funcs = {"orig": orig}


@diagnose.instruments.ProbeTestInstrument("mult_by_8", "result")
def mult_by_8(arg):
    return arg * 8
