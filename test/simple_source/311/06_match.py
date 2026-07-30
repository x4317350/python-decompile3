"""CPython 3.11 structural pattern-matching corpus."""


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
