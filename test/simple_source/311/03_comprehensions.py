"""CPython 3.11 comprehension and generator-expression corpus."""


def comprehensions(rows):
    flattened = [
        value * 2
        for row in rows
        if row
        for value in row
        if value % 2
    ]
    unique = {value for row in rows for value in row if value > 0}
    indexed = {
        index: value
        for index, value in enumerate(flattened)
        if index % 2 == 0
    }
    lazy = (value**2 for value in flattened if value < 20)
    return flattened, unique, indexed, tuple(lazy)


def nested_comprehension(limit):
    return [[column for column in range(row)] for row in range(limit)]


def filtered_lambda(values):
    return [
        (lambda item: item + 1)(value)
        for value in values
        if value > 0
        if value % 2
    ]


def comprehension_scope(values):
    value = "outer"
    result = [value for value in values]
    return value, result
