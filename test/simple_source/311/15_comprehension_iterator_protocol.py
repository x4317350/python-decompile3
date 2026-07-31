"""Stage 6 comprehension, iterator, and generator protocol coverage."""


def incremental_literals(values, mapping, tail):
    return (
        [*values, tail],
        {*values, tail},
        {**mapping, "tail": tail},
    )


def extended_for(values):
    result = []
    for value in values:
        mapping = {
            "k0": 0,
            "k1": 1,
            "k2": 2,
            "k3": 3,
            "k4": 4,
            "k5": 5,
            "k6": 6,
            "k7": 7,
            "k8": 8,
            "k9": 9,
            "k10": 10,
            "k11": 11,
            "k12": 12,
            "k13": 13,
            "k14": 14,
            "k15": 15,
            "k16": 16,
            "k17": 17,
            "k18": 18,
            "k19": 19,
            "k20": 20,
            "k21": 21,
            "k22": 22,
            "k23": 23,
            "k24": 24,
            "k25": 25,
            "k26": 26,
            "k27": 27,
            "k28": 28,
            "k29": 29,
            "k30": 30,
            "k31": 31,
            "k32": 32,
            "k33": 33,
            "k34": 34,
            "k35": 35,
            "k36": 36,
            "k37": 37,
            "k38": 38,
            "k39": 39,
            "k40": 40,
            "k41": 41,
            "k42": 42,
            "k43": 43,
            "k44": 44,
            "k45": 45,
            "k46": 46,
            "k47": 47,
            "k48": 48,
            "k49": 49,
            "k50": 50,
            "k51": 51,
            "k52": 52,
            "k53": 53,
            "k54": 54,
            "k55": 55,
            "k56": 56,
            "k57": 57,
            "k58": 58,
            "k59": 59,
            "k60": 60,
            "k61": 61,
            "k62": 62,
            "k63": 63,
            "k64": 64,
            "k65": 65,
            "k66": 66,
            "k67": 67,
            "k68": 68,
            "k69": 69,
        }
        result.append(mapping.get(value, value))
    return result


def first_or_error(values):
    for value in values:
        return value
    raise LookupError("empty")


def comprehension_shapes(values, records, parts):
    conditional = {
        value: "even" if value % 2 == 0 else "odd"
        for value in values
    }
    filtered = [
        value
        for value in values
        if value < 0 or value % 2 == 0
    ]
    selected = [
        record
        for record in records
        if (
            record["compare"]
            if record["hash"] is None
            else record["hash"]
        )
    ]
    valid_parts = all(1 <= len(part) <= 2 for part in parts)
    return conditional, filtered, selected, valid_parts


def make_prefixed(first, rest):
    def prefixed():
        yield first
        yield from rest

    return prefixed


generator_lambda = lambda value: (yield value)  # noqa: E731
