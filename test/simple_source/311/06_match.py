"""CPython 3.11 structural pattern-matching corpus."""


class Point:
    __match_args__ = ("x", "y")

    def __init__(self, x, y):
        self.x = x
        self.y = y


def describe(value):
    match value:
        case None:
            return "none"
        case 0 | 1:
            return "small"
        case [first, second, *rest] if rest:
            return ("sequence", first, second, rest)
        case {"kind": "point", "x": x, "y": y}:
            return ("point", x, y)
        case complex(real=real, imag=imag):
            return ("complex", real, imag)
        case _:
            return "other"


def nested_describe(value):
    match value:
        case {"payload": [first, Point(0, y)]}:
            return ("nested", first, y)
        case {"kind": kind, **rest}:
            return ("mapping", kind, rest)
        case captured:
            return ("captured", captured)


def collect_description(value):
    messages = []
    match value:
        case 1:
            messages.append("one")
        case _:
            messages.append("other")
    return messages
