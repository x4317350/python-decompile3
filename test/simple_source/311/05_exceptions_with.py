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


def multiple_handlers(value):
    try:
        return 10 / value
    except TypeError:
        return "type"
    except ZeroDivisionError:
        return "zero"
    except:  # noqa: E722 - bare-handler bytecode is part of this corpus
        return "other"


def cleanup_only(events):
    try:
        events.append("try")
    finally:
        events.append("finally")
    return events


def finally_return(value):
    try:
        marker = value
    finally:
        return marker


def finally_break(values):
    seen = []
    for value in values:
        try:
            seen.append(value)
        finally:
            break
    return seen


def finally_continue(values):
    seen = []
    for value in values:
        try:
            seen.append(value)
        finally:
            continue
    return seen


def use_context(resource):
    with resource as active:
        return active.value


def record_context(resource, events):
    with resource as active:
        events.append(active.value)
    return events


def use_context_without_target(resource, events):
    with resource:
        events.append("body")
    return events


def context_failure(resource):
    with resource as active:
        return active.fail()


def use_two_contexts(first, second):
    with first as left, second as right:
        return left.value + right.value


def use_nested_context(first, second):
    with first as left:
        with second as right:
            return left.value + right.value


async def use_async_context(resource):
    async with resource as active:
        return active.value


async def async_record_context(resource, events):
    async with resource as active:
        events.append(active.value)
    return events


async def consume_async(iterator):
    values = []
    async for value in iterator:
        values.append(value)
    return values


async def consume_until_negative(iterator):
    values = []
    async for value in iterator:
        if value < 0:
            break
        values.append(value)
    else:
        values.append("exhausted")
    return values
