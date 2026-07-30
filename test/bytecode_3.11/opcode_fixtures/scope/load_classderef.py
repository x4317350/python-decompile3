"""Generate LOAD_CLASSDEREF from a class body that reads a closure."""


def class_from_closure(value):
    class Captures:
        captured = value

    return Captures
