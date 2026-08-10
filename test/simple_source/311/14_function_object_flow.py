"""Stage 5 delayed function-object and decorator protocol coverage."""


EVENTS = []


def record(name):
    def decorator(function):
        EVENTS.append(("decorate", name))

        def wrapper(*args, **kwargs):
            EVENTS.append(("call", name))
            return function(*args, **kwargs)

        wrapper.original = function
        return wrapper

    return decorator


@record("outer")
@record("inner")
def decorated(value: int = 2, *, scale: int = 3) -> int:
    return value * scale


class DescriptorDemo:
    def __init__(self, value):
        self.value = value

    @staticmethod
    def add(left, right=1):
        return left + right

    @classmethod
    def owner_name(cls):
        return cls.__name__

    @property
    def doubled(self):
        return self.value * 2


def default_callback(callback=lambda value: value + 1):
    return callback(2)


def build_lambdas(base):
    holder = DescriptorDemo(base)
    holder.transform = lambda value=1: base + value
    mapping = {
        "scale": lambda value: base * value,
        "identity": lambda value: value,
    }
    sequence = (
        lambda: base,
        [lambda value: base - value],
    )
    assigned = {}
    assigned["offset"] = lambda value: base + value
    return holder, mapping, sequence, assigned


def make_lazy_resolver(offset):
    events = []
    resolve = None

    def then(value):
        nonlocal resolve
        resolve = resolve or (
            lambda item: (events.append(item), item + offset)[1]
        )
        return resolve(value)

    return then, events
