"""Typed JSON boundary primitives shared by request, snapshot and deletion receipts."""
from __future__ import annotations

import json
from collections.abc import Mapping, Sequence

from .choices import ChoiceError
from .store import JsonLoader, JsonValue


def decode(document: str, loader: JsonLoader = json.loads) -> JsonValue:
    return loader(document)


def mapping(value: JsonValue) -> Mapping[str, JsonValue]:
    match value:
        case dict() as result:
            return result
        case _:
            raise ChoiceError("json_object")


def sequence(value: JsonValue) -> Sequence[JsonValue]:
    match value:
        case list() as result:
            return result
        case _:
            raise ChoiceError("json_list")


def text(value: JsonValue) -> str:
    match value:
        case str() as result:
            return result
        case _:
            raise ChoiceError("json_string")
