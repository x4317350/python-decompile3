"""CPython 3.11 functions, closures, decorators, and classes corpus."""

from functools import wraps


def marker(function):
    @wraps(function)
    def wrapper(*args, **kwargs):
        return function(*args, **kwargs)

    return wrapper


@marker
def combine(
    left: int,
    right: int = 2,
    /,
    *values: int,
    scale: int = 1,
    **options: int,
) -> int:
    extra = sum(values) + sum(options.values())
    return (left + right + extra) * scale


class Accumulator:
    factor = 2

    def __init__(self, initial=0):
        self.total = initial

    def add(self, value):
        self.total += value * self.factor
        return self.total

    @classmethod
    def from_values(cls, values):
        instance = cls()
        for value in values:
            instance.add(value)
        return instance

    @property
    def doubled(self):
        return self.total * 2


class NamedAccumulator(Accumulator):
    def __init__(self, name, initial=0):
        super().__init__(initial)
        self.name = name


def make_counter(start):
    current = start

    def increment(step=1):
        nonlocal current
        current += step
        return current

    return increment


def make_scaler(factor):
    return lambda value: value * factor
