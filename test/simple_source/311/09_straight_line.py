"""CPython 3.11 straight-line source-recovery corpus."""

import math as mathematics
from collections import deque as Queue


CONSTANT = 7
CHAIN_LEFT = CHAIN_RIGHT = 5
temporary = "delete me"
del temporary


def marker(function):
    function.stage3 = True
    return function


@marker
def calculate(
    left: int,
    right: int = 2,
    /,
    *values: int,
    scale: int = 1,
    **options: int,
) -> str:
    vector = [left, right, *values]
    tail = vector[1:]
    tags = {left, right}
    payload = {"left": left, **options}
    pair = (left, right)
    chosen = left and right
    fallback = left or right
    same = left is right
    contains = left in vector
    negative = -left
    inverted = ~left
    total = left + right
    total *= scale
    payload["total"] = total
    del payload["left"]
    root = mathematics.sqrt(total)
    queue = Queue(vector)
    label = f"{total=}:{root:.2f}"
    return (
        label,
        payload,
        pair,
        chosen,
        fallback,
        same,
        contains,
        negative,
        inverted,
        tail,
        tags,
        tuple(queue),
    )


class Accumulator:
    factor = 2

    def __init__(self, initial=0):
        self.total = initial

    @classmethod
    def from_value(cls, value):
        return cls(value)

    @property
    def doubled(self):
        return self.total * 2


@marker
class Child(Accumulator):
    def add(self, value):
        self.total += value * self.factor
        return self.total


def make_adder(amount):
    def add(value: int = 1) -> int:
        return value + amount

    return add


def make_counter(start):
    current = start

    def increment(step=1):
        nonlocal current
        current += step
        return current

    return increment


def make_lambda(factor):
    return lambda value: value * factor


def unpack(values):
    head, *middle, tail = values
    return head, middle, tail


def fail(message):
    raise ValueError(message)
