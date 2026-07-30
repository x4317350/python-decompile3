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


def none_control(value, items):
    while value is None:
        if not items:
            break
        value = items.pop()

    for item in items:
        if item is None:
            continue
        if item is not None:
            value = item
    return value


def not_none_loop(value):
    while value is not None:
        value = None
    return value


def boolean_values(left, right):
    return left or right, left and right


def choose(value):
    if value:
        result = "yes"
    else:
        result = "no"
    return result


def nested_loops(rows):
    total = 0
    for row in rows:
        for value in row:
            if value < 0:
                break
            if value == 0:
                continue
            total += value
        else:
            total += 1
    return total


def while_continue(limit):
    value = 0
    total = 0
    while value < limit:
        value += 1
        if value % 2:
            continue
        total += value
    return total
