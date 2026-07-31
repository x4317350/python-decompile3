"""CPython 3.11 match case-boundary and shared-join corpus."""


# This is the smallest relevant shape from the old tarfile.py false positive:
# several callable values in a constant-key dict followed by an ordinary
# comparison.  It must remain ordinary module code, not a match statement.
PAX_NUMBER_FIELDS = {
    "atime": float,
    "ctime": float,
    "mtime": float,
    "uid": int,
    "gid": int,
    "size": int,
}
PLATFORM_NAME = "posix"
ENCODING = "utf-8" if PLATFORM_NAME == "nt" else "filesystem"


def boundary(value, events):
    match value:
        case 0:
            if events:
                events.append("nested-true")
            else:
                events.append("nested-false")
        case int() as number if number > 0:
            events.append(("positive", number))
        case None:
            raise ValueError("none")
        case "stop":
            return "stopped"
        case _:
            events.append("other")
    events.append("after")
    return tuple(events)


def nested_boundary(value, events):
    match value:
        case ("outer", payload):
            match payload:
                case 1:
                    events.append("inner-one")
                case _:
                    events.append("inner-other")
            events.append("outer-end")
        case _:
            events.append("fallback")
    return tuple(events)


def refutable_fallthrough(value, events):
    match value:
        case "hit":
            events.append("hit")
    events.append("after")
    return tuple(events)


def conditional_exit(value, flag, events):
    match value:
        case "mixed":
            if flag:
                events.append("return")
                return ("early", tuple(events))
            events.append("continued")
        case "raise":
            if flag:
                events.append("raise")
                raise LookupError("boom")
            events.append("safe")
        case _:
            events.append("fallback")
    events.append("after")
    return ("done", tuple(events))
