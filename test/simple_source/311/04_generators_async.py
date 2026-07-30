"""CPython 3.11 generator, coroutine, and async-comprehension corpus."""


def numbers(limit):
    for value in range(limit):
        yield value


def delegating(values):
    yield from values


def echo(values):
    for value in values:
        received = yield value
        if received is not None:
            yield received


async def async_numbers(limit):
    for value in range(limit):
        yield value


async def consume(iterator):
    return [value async for value in iterator if value % 2]


async def await_value(awaitable):
    result = await awaitable
    return result


async def async_filtered(iterator):
    return {
        value * 2
        async for value in iterator
        if value > 0
        if value % 2
    }


async def async_transform(awaitables):
    for awaitable in awaitables:
        yield await awaitable
