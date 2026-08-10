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


def first_or_default(values, default):
    result = default
    for value in values:
        result = value
        break
    return result


def nested_first(groups, events):
    for group in groups:
        for item in group:
            events.append(item)
            break
        events.append("group")


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


def extended_comprehension_filter(values):
    return [
        value
        for value in values
        if value == 0
        or value == 1
        or value == 2
        or value == 3
        or value == 4
        or value == 5
        or value == 6
        or value == 7
        or value == 8
        or value == 9
        or value == 10
        or value == 11
        or value == 12
        or value == 13
        or value == 14
        or value == 15
        or value == 16
        or value == 17
        or value == 18
        or value == 19
        or value == 20
        or value == 21
        or value == 22
        or value == 23
        or value == 24
        or value == 25
        or value == 26
        or value == 27
        or value == 28
        or value == 29
        or value == 30
        or value == 31
        or value == 32
        or value == 33
        or value == 34
        or value == 35
        or value == 36
        or value == 37
        or value == 38
        or value == 39
        or value == 40
        or value == 41
        or value == 42
        or value == 43
        or value == 44
        or value == 45
        or value == 46
        or value == 47
        or value == 48
        or value == 49
        or value == 50
        or value == 51
        or value == 52
        or value == 53
        or value == 54
        or value == 55
        or value == 56
        or value == 57
        or value == 58
        or value == 59
        or value == 60
        or value == 61
        or value == 62
        or value == 63
        or value == 64
        or value == 65
        or value == 66
        or value == 67
        or value == 68
        or value == 69
        or value == 70
        or value == 71
        or value == 72
        or value == 73
        or value == 74
        or value == 75
        or value == 76
        or value == 77
        or value == 78
        or value == 79
        or value == 80
        or value == 81
        or value == 82
        or value == 83
        or value == 84
        or value == 85
        or value == 86
        or value == 87
        or value == 88
        or value == 89
        or value == 90
        or value == 91
        or value == 92
        or value == 93
        or value == 94
        or value == 95
        or value == 96
        or value == 97
        or value == 98
        or value == 99
        or value == 100
        or value == 101
        or value == 102
        or value == 103
        or value == 104
        or value == 105
        or value == 106
        or value == 107
        or value == 108
        or value == 109
        or value == 110
        or value == 111
        or value == 112
        or value == 113
        or value == 114
        or value == 115
        or value == 116
        or value == 117
        or value == 118
        or value == 119
        or value == 120
        or value == 121
        or value == 122
        or value == 123
        or value == 124
        or value == 125
        or value == 126
        or value == 127
        or value == 128
        or value == 129
        or value == 130
        or value == 131
        or value == 132
        or value == 133
        or value == 134
        or value == 135
        or value == 136
        or value == 137
        or value == 138
        or value == 139
        or value == 140
        or value == 141
        or value == 142
        or value == 143
        or value == 144
        or value == 145
        or value == 146
        or value == 147
        or value == 148
        or value == 149
        or value == 150
        or value == 151
        or value == 152
        or value == 153
        or value == 154
        or value == 155
        or value == 156
        or value == 157
        or value == 158
        or value == 159
        or value == 160
        or value == 161
        or value == 162
        or value == 163
        or value == 164
        or value == 165
        or value == 166
        or value == 167
        or value == 168
        or value == 169
        or value == 170
        or value == 171
        or value == 172
        or value == 173
        or value == 174
        or value == 175
        or value == 176
        or value == 177
        or value == 178
        or value == 179
    ]


def make_prefixed(first, rest):
    def prefixed():
        yield first
        yield from rest

    return prefixed


generator_lambda = lambda value: (yield value)  # noqa: E731
