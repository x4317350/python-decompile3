"""CPython 3.11 ExceptionGroup and except-star corpus."""


def handle_group():
    handled = []
    try:
        raise ExceptionGroup(
            "multiple failures",
            [ValueError("bad value"), TypeError("bad type")],
        )
    except* ValueError as errors:
        handled.append(("value", len(errors.exceptions)))
    except* TypeError as errors:
        handled.append(("type", len(errors.exceptions)))
    return handled


def mark_values(group):
    handled = False
    try:
        raise group
    except* ValueError:
        handled = True
    return handled


def split_group(group):
    handled = []
    try:
        if group is not None:
            raise group
    except* ValueError as errors:
        handled.append(("value", len(errors.exceptions)))
    return handled
