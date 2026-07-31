"""Stage 7 CPython 3.11 with-statement control-transfer shapes."""


class TraceContext:
    def __init__(self, events, name, value=0, suppress=False):
        self.events = events
        self.name = name
        self.value = value
        self.suppress = suppress

    def __enter__(self):
        self.events.append(("enter", self.name))
        return self

    def __exit__(self, kind, value, traceback):
        exception_name = None if kind is None else kind.__name__
        self.events.append(("exit", self.name, exception_name))
        return self.suppress

    def mark(self, value):
        self.events.append(("mark", self.name, value))
        return value


class AsyncTraceContext(TraceContext):
    async def __aenter__(self):
        self.events.append(("aenter", self.name))
        return self

    async def __aexit__(self, kind, value, traceback):
        exception_name = None if kind is None else kind.__name__
        self.events.append(("aexit", self.name, exception_name))
        return self.suppress

    async def mark_async(self, value):
        self.events.append(("async-mark", self.name, value))
        return value


def multi_statement_return(manager, value):
    with manager as active:
        active.mark("before")
        adjusted = value + active.value
        if adjusted < 0:
            active.mark("negative")
            return -1
        active.mark("return")
        return adjusted * 2


def loop_transfers(factory, values):
    result = []
    for value in values:
        with factory(value) as active:
            active.mark("loop")
            if value < 0:
                continue
            if value == 0:
                break
            result.append(value)
    else:
        result.append("else")
    return result


def generator_transfer(manager):
    with manager as active:
        active.mark("before-yield")
        received = yield active.value
        active.mark(("received", received))
        yield received
    return "done"


def multiple_contexts(left, right):
    with left as first, right as second:
        first.mark("left")
        second.mark("right")
        return first.value + second.value


def nested_contexts(outer, inner):
    with outer as first:
        first.mark("outer")
        with inner as second:
            second.mark("inner")
            return first.value * second.value


def suppressed_exception(manager):
    events = manager.events
    with manager as active:
        active.mark("raise")
        raise ValueError("stage7")
    events.append(("continued", manager.name))
    return "suppressed"


def with_inside_try(manager, should_fail):
    try:
        with manager as active:
            active.mark("try")
            if should_fail:
                raise LookupError("missing")
            result = active.value
    except LookupError:
        result = "handled"
    finally:
        manager.events.append(("finally", manager.name))
    return result


async def async_return(manager, value):
    async with manager as active:
        await active.mark_async("before")
        adjusted = value + active.value
        return adjusted


async def async_loop_transfers(factory, values):
    result = []
    for value in values:
        async with factory(value) as active:
            await active.mark_async("loop")
            if value < 0:
                continue
            if value == 0:
                break
            result.append(value)
    return result
