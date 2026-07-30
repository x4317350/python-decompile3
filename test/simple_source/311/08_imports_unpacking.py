"""CPython 3.11 import, unpacking, and formatting corpus."""

import math as mathematics
from collections import deque as Queue


def formatting(value):
    root = mathematics.sqrt(value)
    queue = Queue([value, root])
    return f"{value=}, {root:.3f}", tuple(queue)


def unpacking(*groups, **metadata):
    items = [*groups]
    merged = {**metadata, "count": len(items)}
    head, *middle, tail = items
    return head, middle, tail, merged
