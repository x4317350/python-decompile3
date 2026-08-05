def terminal_if_else(flag, events):
    if flag:
        events.append("left")
    else:
        events.append("right")


def terminal_if_elif(value, events):
    if value == 1:
        events.append("one")
    elif value == 2:
        events.append("two")
    else:
        events.append("other")


def terminal_nested(value, events):
    if value > 0:
        if value % 2:
            events.append("positive odd")
        else:
            events.append("positive even")
    else:
        events.append("not positive")


def plain_terminal_if(flag, events):
    if flag:
        events.append("hit")


def early_return(flag, events):
    if flag:
        return
    events.append("after")


def terminal_explicit_returns(value):
    if value:
        return "truthy"
    else:
        return "falsey"


def terminal_condition(predicate, events):
    if predicate():
        events.append("true")
    else:
        events.append("false")


def terminal_multi_elif(value, events):
    if value == 0:
        events.append("zero")
    elif value == 1:
        events.append("one")
    elif value == 2:
        events.append("two")
    else:
        events.append("other")
        events.append("done")


def terminal_nested_elif(value, events):
    events.append("start")
    if value < 0:
        events.append("negative")
    elif value > 0:
        if value % 2:
            events.append("positive odd")
        else:
            events.append("positive even")
    else:
        events.append("zero")


def terminal_short_circuit(left, right, events):
    if left and right:
        events.append("both")
    else:
        events.append("not both")


def terminal_membership(value, events):
    if value in {"left", "right"}:
        events.append("member")
    else:
        events.append("other")


def terminal_reversed(flag, events):
    if not flag:
        events.append("false")
    else:
        events.append("true")


def terminal_reversed_layout(flag, events):
    if flag:
        pass
    else:
        events.append("false")


def terminal_mixed_return(flag, events):
    if flag:
        return "truthy"
    events.append("falsey")


def joined_if_else(flag, events):
    if flag:
        events.append("left")
    else:
        events.append("right")
    events.append("joined")
