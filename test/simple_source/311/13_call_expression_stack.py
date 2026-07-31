"""Stage 4 call, expression-region, and logical-stack coverage."""


def formatted(value):
    return f"{value:.1e}"


def nested_choice(left, right):
    return 0 if left == right else 1 if left > right else -1


def selected_pattern(implementation, version, old, current):
    return (
        old
        if (
            implementation == "cpython"
            and version < (3, 11, 5)
        )
        or (
            implementation == "pypy"
            and version < (3, 11, 13)
        )
        or version < (3, 11)
        else current
    )


def callback_argument(register, value):
    return register(lambda: value)


def ordered_call(receiver, mark):
    return receiver.method(
        mark("positional", 1),
        keyword=mark("keyword", 2),
    )


def chain_loop(values, events):
    index = 0
    while 0 <= index < len(values):
        value = values[index]
        events.append(value)
        if not value:
            break
        index += 1
    return index, events


def loop_return_finally(functions, cleanup):
    try:
        for function in functions:
            result = function()
            if result:
                return result
    finally:
        cleanup()
    return None


def nested_finally_except(function, cleanup):
    try:
        try:
            return function()
        finally:
            cleanup()
    except ValueError as error:
        return "error", error.args
