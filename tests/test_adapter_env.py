from __future__ import annotations

import os

import pytest

from monet.adapter._env import interpolate, interpolate_obj


def test_basic_substitution(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FOO", "bar")
    assert interpolate("${FOO}") == "bar"


def test_default_used_when_missing() -> None:
    os.environ.pop("MISSING_VAR_XYZ", None)
    assert interpolate("${MISSING_VAR_XYZ:fallback}") == "fallback"


def test_env_overrides_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MY_VAR", "real")
    assert interpolate("${MY_VAR:default}") == "real"


def test_missing_no_default_raises() -> None:
    os.environ.pop("ABSENT_VAR_XYZ", None)
    with pytest.raises(KeyError):
        interpolate("${ABSENT_VAR_XYZ}")


def test_nested_in_string(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOST", "localhost")
    assert interpolate("http://${HOST}/path") == "http://localhost/path"


def test_interpolate_obj_recursive(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KEY", "value")
    obj = {"a": "${KEY}", "b": ["${KEY}"], "c": {"d": "${KEY}"}, "e": 42}
    result = interpolate_obj(obj)
    assert result == {"a": "value", "b": ["value"], "c": {"d": "value"}, "e": 42}
