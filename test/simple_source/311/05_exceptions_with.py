"""CPython 3.11 exception-table and context-manager corpus."""


def guarded_division(numerator, denominator):
    events = []
    try:
        result = numerator / denominator
    except ZeroDivisionError as error:
        events.append(type(error).__name__)
        result = None
    else:
        events.append("success")
    finally:
        events.append("finished")
    return result, events


def nested_exception(value):
    try:
        try:
            return int(value)
        except TypeError:
            return 0
    except ValueError:
        return -1


def use_context(resource):
    with resource as active:
        return active.value


def use_two_contexts(first, second):
    with first as left, second as right:
        return left.value + right.value


async def use_async_context(resource):
    async with resource as active:
        return active.value
