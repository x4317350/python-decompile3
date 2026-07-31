def unpack_sequence(value):
    message, (filename, line, offset, text) = value
    return message, filename, line, offset, text


def unpack_extended(value):
    head, (first, *middle, last), tail = value
    return head, first, middle, last, tail


def sequence_loop(items):
    result = None
    for (left, right), extra in items:
        result = left, right, extra
    return result


def extended_loop(items):
    result = None
    for (first, *middle, last), extra in items:
        result = first, middle, last, extra
    return result


def collect(items):
    return [(left, right, extra) for (left, right), extra in items]
