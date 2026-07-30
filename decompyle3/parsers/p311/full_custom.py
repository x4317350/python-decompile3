"""Phase-3 grammar policy for CPython 3.11.

The legacy Spark grammars are deliberately not mixed into Parser311: their
CALL_FUNCTION, SETUP_*, POP_BLOCK, and jump productions describe bytecode
protocols that no longer exist in CPython 3.11.
"""


class Python311FullCustom:
    """Marker mixin documenting the isolated 3.11 grammar vocabulary."""

    inherits_legacy_call_rules = False
    inherits_legacy_exception_rules = False
    inherits_legacy_jump_rules = False
