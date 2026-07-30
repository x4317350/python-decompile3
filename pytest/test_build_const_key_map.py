import pytest
from xdis.version_info import PYTHON_VERSION_TRIPLE

# decompyle3
from validate import validate_decompile

TESTS = (
    "{0.: 'a', -1: 'b'}",  # BUILD_MAP
    "{'a':'b'}",  # BUILD_MAP
    "{0: 1}",  # BUILD_MAP
    "{b'0':1, b'2':3}",  # BUILD_CONST_KEY_MAP
    "{0: 1, 2: 3}",  # BUILD_CONST_KEY_MAP
    "{'a':'b','c':'d'}",  # BUILD_CONST_KEY_MAP
    "{0: 1, 2: 3}",  # BUILD_CONST_KEY_MAP
    "{'a': 1, 'b': 2}",  # BUILD_CONST_KEY_MAP
    "{'a':'b','c':'d'}",  # BUILD_CONST_KEY_MAP
    "{0.0:'b',0.1:'d'}",  # BUILD_CONST_KEY_MAP
)

SUPPORTED_VERSIONS = {(3, 7), (3, 8), (3, 11)}


@pytest.mark.parametrize("text", TESTS)
@pytest.mark.skipif(
    PYTHON_VERSION_TRIPLE[:2] not in SUPPORTED_VERSIONS,
    reason="requires a supported decompyle3 bytecode version",
)
def test_build_const_key_map(text):
    validate_decompile(text)


if __name__ == "__main__":
    for test in TESTS:
        validate_decompile(test)
