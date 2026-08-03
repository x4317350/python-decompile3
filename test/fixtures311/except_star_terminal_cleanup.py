"""CPython 3.11 terminal except-star cleanup protocol fixtures."""


def terminal_empty(group):
    try:
        raise group
    except* ValueError:
        pass


def terminal_named(group):
    try:
        raise group
    except* ValueError as error:  # noqa: F841
        pass


def terminal_nonempty(group, events):
    try:
        raise group
    except* ValueError:
        events.append("value")


def terminal_raise(group):
    try:
        raise group
    except* TypeError:
        raise


def terminal_multiple(group, events):
    try:
        raise group
    except* ValueError:
        pass
    except* TypeError:
        events.append("type")


async def terminal_async(group):
    try:
        raise group
    except* ValueError:
        pass


def terminal_generator(group):
    try:
        yield "ready"
        raise group
    except* ValueError:
        pass
