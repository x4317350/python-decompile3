"""CPython 3.11 except-star empty-body protocol fixtures."""


def empty_handler(group):
    try:
        raise group
    except* ValueError:
        pass
    return group


def empty_named_handler(group):
    try:
        raise group
    except* ValueError as error:  # noqa: F841
        pass
    return group


def nonempty_handler(group, events):
    try:
        raise group
    except* ValueError:
        events.append("handled")
    return events
