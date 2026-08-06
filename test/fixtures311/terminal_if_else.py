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


def terminal_and_no_else(left, right, events):
    if left and right:
        events.append("both")


def terminal_or_no_else(left, right, events):
    if left or right:
        events.append("either")


def terminal_nested_no_else(left, right, key, events):
    if left and right:
        if key:
            events.append("hit")


def terminal_nested_short_circuit_no_else(
    first,
    second,
    third,
    fourth,
    events,
):
    if first and second:
        if third and fourth:
            events.append("hit")


def terminal_many_and_no_else(first, second, third, events):
    if first and second and third:
        events.append("all")


def terminal_mixed_no_else(first, second, third, events):
    if (first and second) or third:
        events.append("selected")


def terminal_not_no_else(flag, events):
    if not flag:
        events.append("false")


def terminal_empty_if(flag):
    if flag:
        pass


def terminal_empty_and(first, second):
    if first and second:
        pass


def terminal_empty_many_and(first, second, third):
    if first and second and third:
        pass


def terminal_empty_or(first, second):
    if first or second:
        pass


def terminal_empty_not(flag):
    if not flag:
        pass


def terminal_empty_mixed(first, second, third):
    if (first and second) or third:
        pass


def terminal_empty_condition(first, second):
    if first() and second():
        pass


def terminal_return_condition(first, second):
    if first() and second():
        return


def terminal_short_circuit_statement_and(obj):
    obj and obj.binding(False)


def terminal_short_circuit_statement_or(obj):
    obj or obj.binding(False)


def terminal_short_circuit_statement_many(first, second, final):
    first() and second() and final()


def terminal_before_no_else(left, right, events):
    events.append("before")
    if left and right:
        events.append("both")
        events.append("done")


def independent_terminal_ifs(first, second, events):
    if first:
        events.append("first")
    if second:
        events.append("second")


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
