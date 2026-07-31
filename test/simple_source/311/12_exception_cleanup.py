def return_from_handler(value):
    try:
        raise LookupError("missing")
    except LookupError:
        return value


def translate_error(value, events):
    try:
        return int(value)
    except (TypeError, ValueError) as error:
        events.append(type(error).__name__)
        raise RuntimeError("converted") from error


def reraised_error(events):
    try:
        raise ValueError("original")
    except ValueError:
        events.append("handler")
        raise


def nested_handler_return(value):
    try:
        raise RuntimeError("outer")
    except RuntimeError:
        try:
            raise LookupError("inner")
        except LookupError:
            return value


def nested_finally(events, fail):
    try:
        try:
            events.append("body")
            if fail:
                raise ValueError("boom")
            return "ok"
        finally:
            events.append("inner")
    finally:
        events.append("outer")


def cleanup_generator(events, fail):
    try:
        try:
            events.append("body")
            if fail:
                raise ValueError("generator")
        finally:
            events.append("inner")
        yield "value"
    finally:
        try:
            events.append("outer")
        except LookupError:
            pass


def handler_break(values):
    results = []
    index = 0
    while True:
        try:
            value = values[index]
        except IndexError:
            if results:
                break
            raise
        results.append(value)
        index += 1
        if value is None:
            break
    return results
