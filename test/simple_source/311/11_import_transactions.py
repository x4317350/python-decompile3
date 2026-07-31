import json.decoder as json_decoder
import xml.etree.ElementTree
import xml.etree.ElementTree as element_tree
from collections import (
    defaultdict as mapping_factory,
    deque,
)
from urllib.parse import quote as encode
from urllib.parse import unquote


def imported_values():
    return (
        json_decoder.JSONDecoder().decode('{"value": 7}')["value"],
        xml.etree.ElementTree.fromstring("<root />").tag,
        element_tree.fromstring("<child />").tag,
        list(deque((1, 2, 3))),
        mapping_factory(list, {"key": [4]})["key"],
        encode("a value"),
        unquote("a%20value"),
    )
