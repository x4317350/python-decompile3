"""CPython 3.11 ordinary except-handler return fixtures."""


def bare_return(iterator, events):
    try:
        value = next(iterator)
    except StopIteration:
        return
    events.append(("after", value))
    return "continued"


def explicit_none_return(iterator, events):
    try:
        value = next(iterator)
    except StopIteration:
        return None
    events.append(("after", value))
    return "continued"


def named_return(iterator, events):
    try:
        value = next(iterator)
    except StopIteration as error:
        events.append(("handled", type(error).__name__))
        return
    events.append(("after", value))
    return "continued"


def real_pass(iterator, events):
    try:
        value = next(iterator)
    except StopIteration:
        value = "stopped"
    events.append(("after", value))
    return "continued"


def empty_pass(iterator, events):
    try:
        next(iterator)
    except StopIteration:
        pass
    events.append("after")


def terminal_pass(iterator):
    try:
        next(iterator)
    except StopIteration:
        pass


def return_value(iterator, events):
    try:
        value = next(iterator)
    except StopIteration:
        return "stopped"
    events.append(("after", value))
    return "continued"


def multiple_handlers(mode, events):
    try:
        if mode == "stop":
            next(iter(()))
        elif mode == "value":
            raise ValueError("value")
        elif mode == "key":
            raise KeyError("key")
    except StopIteration:
        return
    except ValueError:
        events.append("value")
    except KeyError:
        return "key"
    events.append("after")
    return "continued"


def nested_return(iterator, events):
    try:
        try:
            value = next(iterator)
        except StopIteration:
            return
    except RuntimeError:
        events.append("runtime")
    events.append(("after", value))
    return "continued"


def return_with_else(iterator, events):
    try:
        value = next(iterator)
    except StopIteration:
        return
    else:
        events.append(("else", value))
    events.append("after")
    return "continued"


def return_inside_terminal_if(enabled, iterator, events):
    if enabled:
        try:
            value = next(iterator)
        except StopIteration:
            return
        events.append(("after", value))


def return_in_loop(iterator, events):
    while True:
        try:
            value = next(iterator)
        except StopIteration:
            return
        events.append(("loop", value))
        break
    events.append("continued")
    return "done"


def return_in_for_loop(iterator, events):
    for marker in (1,):
        try:
            value = next(iterator)
        except StopIteration:
            return
        events.append(("loop", marker, value))
    events.append("continued")
    return "done"


class RecordingContext:
    def __init__(self, events):
        self.events = events

    def __enter__(self):
        self.events.append("enter")
        return self

    def __exit__(self, kind, value, traceback):
        self.events.append("exit")
        return False


def return_inside_with(iterator, events):
    with RecordingContext(events):
        try:
            value = next(iterator)
        except StopIteration as error:
            events.append(("handled", type(error).__name__))
            return
        events.append(("body", value))
    events.append("continued")
    return "done"


def return_after_nested_handler(iterator, events):
    try:
        value = next(iterator)
    except StopIteration as error:
        events.append(("handled", type(error).__name__))
        try:
            raise RuntimeError("nested")
        except RuntimeError:
            events.append("nested")
        return
    events.append(("continued", value))
    return "done"
