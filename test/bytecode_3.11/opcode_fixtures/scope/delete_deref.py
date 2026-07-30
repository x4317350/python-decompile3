"""Generate DELETE_DEREF from a nonlocal deletion."""


def make_deleter():
    value = "present"

    def delete_value():
        nonlocal value
        del value

    return delete_value
