"""CPython 3.11 branch and loop control-flow corpus."""


def classify(value):
    if value < 0:
        return "negative"
    if value == 0:
        return "zero"
    if value % 2:
        return "positive odd"
    return "positive even"


def nested_conditions(left, right, fallback):
    if left and (right or fallback):
        result = left if right else fallback
    elif not left and right:
        result = right
    else:
        result = None
    return result


def loops(values):
    total = 0
    for index, value in enumerate(values):
        if value == 0:
            continue
        if value < 0:
            break
        total += index * value
    else:
        total += 100

    count = 0
    while count < 3:
        count += 1
        if total > 1000:
            break
    else:
        total += count

    return total
