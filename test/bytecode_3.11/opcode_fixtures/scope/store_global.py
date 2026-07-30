"""Generate STORE_GLOBAL from a declared global assignment."""


value = None


def store_value(new_value):
    global value
    value = new_value
