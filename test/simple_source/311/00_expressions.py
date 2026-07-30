"""CPython 3.11 expression, collection, and assignment corpus."""


def expressions(value, mapping):
    vector = [1, 2, 3]
    scratch = {"temporary": value}
    payload = {
        "tuple": (value, value + 1),
        "set": {value, value + 2},
        "bytes": b"decompyle3",
    }
    del scratch["temporary"]
    mapping["value"] = (value + 1) * 2
    vector[1:] = [mapping["value"], value**2]
    label = f"value={mapping['value']!r}"
    return label, payload, vector, -value, ~value


def comparisons(left, right):
    return (
        left is not right,
        left in right,
        left < len(right),
        bool(left and right),
    )


def call_examples(function, value):
    return function(value, 3, scale=2, extra=4)


def all_binary_operations(left, right):
    return (
        left + right,
        left & right,
        left // right,
        left << right,
        left @ right,
        left * right,
        left % right,
        left | right,
        left**right,
        left >> right,
        left - right,
        left / right,
        left ^ right,
    )


def all_inplace_operations(value, other):
    value += other
    value &= other
    value //= other
    value <<= other
    value @= other
    value *= other
    value %= other
    value |= other
    value **= other
    value >>= other
    value -= other
    value /= other
    value ^= other
    return value


def all_comparisons(left, right, container):
    return (
        left < right,
        left <= right,
        left == right,
        left != right,
        left > right,
        left >= right,
        left in container,
        left not in container,
        left is right,
        left is not right,
        left < right <= container,
    )
