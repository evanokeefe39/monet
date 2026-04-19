"""CapabilityIndex + Capability wire validation tests."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from monet.server._capabilities import Capability, CapabilityIndex


def _cap(agent_id: str, command: str, pool: str = "local") -> Capability:
    return Capability(agent_id=agent_id, command=command, pool=pool, description="")


def test_upsert_two_workers_one_capability_both_tracked() -> None:
    idx = CapabilityIndex()
    idx.upsert_worker("w1", "local", [_cap("a", "run")])
    idx.upsert_worker("w2", "local", [_cap("a", "run")])
    caps = idx.capabilities()
    assert len(caps) == 1
    assert caps[0]["worker_ids"] == ["w1", "w2"]


def test_drop_worker_keeps_capability_when_other_serves_it() -> None:
    idx = CapabilityIndex()
    idx.upsert_worker("w1", "local", [_cap("a", "run")])
    idx.upsert_worker("w2", "local", [_cap("a", "run")])
    pruned = idx.drop_worker("w1")
    assert pruned == []
    assert idx.is_available("a", "run")
    assert idx.capabilities()[0]["worker_ids"] == ["w2"]


def test_drop_worker_removes_orphan_capability() -> None:
    idx = CapabilityIndex()
    idx.upsert_worker("w1", "local", [_cap("a", "run")])
    pruned = idx.drop_worker("w1")
    assert pruned == [("a", "run")]
    assert not idx.is_available("a", "run")


def test_upsert_drops_stale_caps_for_same_worker() -> None:
    idx = CapabilityIndex()
    idx.upsert_worker("w1", "local", [_cap("a", "run"), _cap("b", "run")])
    idx.upsert_worker("w1", "local", [_cap("a", "run")])
    assert idx.is_available("a", "run")
    assert not idx.is_available("b", "run")


def test_invalid_empty_agent_id_rejected() -> None:
    with pytest.raises(ValidationError):
        Capability(agent_id="", command="run", pool="local")


def test_invalid_pool_charset_rejected() -> None:
    with pytest.raises(ValidationError):
        Capability(agent_id="a", command="run", pool="Bad Pool!")


def test_invalid_length_rejected() -> None:
    with pytest.raises(ValidationError):
        Capability(agent_id="a" * 65, command="run", pool="local")


def test_slash_commands_merges_reserved() -> None:
    idx = CapabilityIndex()
    idx.upsert_worker("w1", "local", [_cap("planner", "deep")])
    assert idx.slash_commands() == ["/plan", "/planner:deep"]


def test_worker_for_pool() -> None:
    idx = CapabilityIndex()
    idx.upsert_worker("w1", "gpu", [_cap("a", "run", pool="gpu")])
    assert idx.worker_for_pool("w1", "gpu")
    assert not idx.worker_for_pool("w1", "cpu")
    assert not idx.worker_for_pool("w2", "gpu")


def test_get_pool() -> None:
    idx = CapabilityIndex()
    idx.upsert_worker("w1", "gpu", [_cap("a", "run", pool="gpu")])
    assert idx.get_pool("a", "run") == "gpu"
    assert idx.get_pool("missing", "run") is None
