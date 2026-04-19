"""Wire compatibility: assert Python types serialize to the required JSON keys.

For stream events (RunStarted, NodeUpdate, etc.) we instantiate the Python
dataclass, convert to a dict, apply the agent_id→agent wire rename where
relevant, then assert all required keys from wire_schema.json are present.

For query-response types (HealthResponse, Capability, ArtifactItem) we build
the canonical dict directly from server/client models.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import pytest

from monet.client._events import (
    AgentProgress,
    Interrupt,
    NodeUpdate,
    RunComplete,
    RunFailed,
    RunStarted,
    SignalEmitted,
)
from monet.server._routes import ArtifactListItem

SCHEMA_PATH = Path(__file__).parent / "wire_schema.json"


def _load_schema() -> dict[str, list[str]]:
    raw = json.loads(SCHEMA_PATH.read_text())
    return {k: v for k, v in raw.items() if not k.startswith("_")}


SCHEMA = _load_schema()


def _canonical_wire(name: str) -> dict:
    """Return a minimal valid wire dict for the given type name."""
    if name == "RunStarted":
        return dataclasses.asdict(
            RunStarted(run_id="r1", graph_id="g1", thread_id="t1")
        )
    if name == "NodeUpdate":
        return dataclasses.asdict(NodeUpdate(run_id="r1", node="n1", update={}))
    if name == "AgentProgress":
        d = dataclasses.asdict(AgentProgress(run_id="r1", agent_id="ag1", status="ok"))
        d["agent"] = d.pop("agent_id")
        return d
    if name == "SignalEmitted":
        d = dataclasses.asdict(
            SignalEmitted(run_id="r1", agent_id="ag1", signal_type="s1")
        )
        d["agent"] = d.pop("agent_id")
        return d
    if name == "Interrupt":
        return dataclasses.asdict(Interrupt(run_id="r1", tag="review"))
    if name == "RunComplete":
        return dataclasses.asdict(RunComplete(run_id="r1"))
    if name == "RunFailed":
        return dataclasses.asdict(RunFailed(run_id="r1", error="oops"))
    if name == "HealthResponse":
        # Shape: GET /api/v1/health
        return {"status": "ok", "workers": 1, "queued": 0}
    if name == "Capability":
        # Shape: GET /api/v1/agents entry
        return {"agent_id": "ag1", "command": "run"}
    if name == "ArtifactItem":
        item = ArtifactListItem(
            artifact_id="a1",
            key="work_brief",
            content_type="application/json",
            content_length=42,
            summary="",
            created_at="2026-01-01T00:00:00Z",
        )
        return json.loads(item.model_dump_json())
    raise ValueError(f"unknown type: {name}")


@pytest.mark.parametrize("type_name,required_keys", SCHEMA.items())
def test_required_keys_present(type_name: str, required_keys: list[str]) -> None:
    wire = _canonical_wire(type_name)
    missing = [k for k in required_keys if k not in wire]
    assert not missing, (
        f"{type_name}: wire dict missing required keys {missing}. Got: {sorted(wire)}"
    )
