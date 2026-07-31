"""Stage 8 CPython 3.11 recursive structure-recovery shapes."""


def scan_until(text, start, stop):
    size = len(text)
    index = start
    while index < size and text[index] != stop:
        index += 1
    return index, text[:index]


def collect_prefix(values, marker):
    result = []
    index = 0
    size = len(values)
    while index < size and values[index] is not marker:
        if values[index] is None:
            index += 1
            continue
        result.append(values[index])
        index += 1
    return result, index


def nested_compound(rows, stop):
    positions = []
    outer = 0
    while outer < len(rows):
        row = rows[outer]
        inner = 0
        while inner < len(row) and row[inner] != stop:
            inner += 1
        positions.append(inner)
        outer += 1
    return positions


def chained_guard(values):
    index = 0
    size = len(values)
    while (
        index < size
        and values[index] >= 0
        and values[index] % 2 == 0
    ):
        index += 1
    return index
