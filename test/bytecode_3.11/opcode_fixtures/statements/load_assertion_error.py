"""Generate LOAD_ASSERTION_ERROR from an assert statement."""


def require_value(value):
    assert value, "value is required"
