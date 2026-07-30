"""Generate DELETE_GLOBAL from a declared global deletion."""


value = "present"


def delete_value():
    global value
    del value
