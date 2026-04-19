"""Tests for _result_parser — structured output parsing."""

from __future__ import annotations

from monet.orchestration._result_parser import ParseFailure, parse_json_output
from monet.types import AgentResult


def _result(output: str | dict[str, object] | None = None) -> AgentResult:
    return AgentResult(success=True, output=output)


def test_dict_output_passthrough() -> None:
    """Dict output returned as-is, no parsing needed."""
    data = {"verdict": "pass", "confidence": 0.9}
    parsed = parse_json_output(_result(data))
    assert parsed == data


def test_valid_json_string_parsed() -> None:
    """Valid JSON string is parsed to dict."""
    parsed = parse_json_output(_result('{"verdict": "pass"}'))
    assert not isinstance(parsed, ParseFailure)
    assert parsed["verdict"] == "pass"


def test_invalid_json_returns_parse_failure() -> None:
    """Non-JSON string returns ParseFailure."""
    parsed = parse_json_output(_result("not json at all"))
    assert isinstance(parsed, ParseFailure)
    assert "not json at all" in parsed.raw


def test_empty_output_returns_parse_failure() -> None:
    """None/empty output returns ParseFailure."""
    assert isinstance(parse_json_output(_result(None)), ParseFailure)
    assert isinstance(parse_json_output(_result("")), ParseFailure)
    assert isinstance(parse_json_output(_result("   ")), ParseFailure)


def test_non_dict_json_returns_parse_failure() -> None:
    """JSON that parses to a non-dict type returns ParseFailure."""
    parsed = parse_json_output(_result("[1, 2, 3]"))
    assert isinstance(parsed, ParseFailure)
    assert "[1, 2, 3]" in parsed.raw
