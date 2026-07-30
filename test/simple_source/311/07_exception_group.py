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
