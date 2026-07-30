"""CPython 3.11 generator, coroutine, and async-comprehension corpus."""


def numbers(limit):
    for value in range(limit):
        yield value


def delegating(values):
    yield from values


async def async_numbers(limit):
    for value in range(limit):
        yield value


async def consume(iterator):
    return [value async for value in iterator if value % 2]


async def await_value(awaitable):
    result = await awaitable
    return result
