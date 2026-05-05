from __future__ import annotations

import pytest

from monet.adapter._jsonpath import assign, extract


def test_extract_flat() -> None:
    assert extract({"foo": "bar"}, "$.foo") == "bar"


def test_extract_nested() -> None:
    obj = {"a": {"b": {"c": 42}}}
    assert extract(obj, "$.a.b.c") == 42


def test_extract_array_index() -> None:
    obj = {"items": [10, 20, 30]}
    assert extract(obj, "$.items[1]") == 20


def test_extract_nested_array() -> None:
    obj = {"choices": [{"message": {"content": "hello"}}]}
    assert extract(obj, "$.choices[0].message.content") == "hello"


def test_extract_bad_path() -> None:
    with pytest.raises(ValueError, match="start with"):
        extract({}, "field")


def test_assign_flat() -> None:
    obj: dict = {}
    result = assign(obj, "$.key", "val")
    assert result == {"key": "val"}
    assert result is obj


def test_assign_nested_creates_dicts() -> None:
    obj: dict = {}
    assign(obj, "$.a.b.c", 99)
    assert obj == {"a": {"b": {"c": 99}}}


def test_assign_existing_nested() -> None:
    obj = {"a": {"x": 1}}
    assign(obj, "$.a.y", 2)
    assert obj == {"a": {"x": 1, "y": 2}}
