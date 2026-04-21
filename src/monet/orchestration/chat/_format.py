"""Chat-message rendering for agent results and execution summary."""

from __future__ import annotations

from typing import Any

from monet._ports import artifact_view_url as _artifact_url
from monet.orchestration._planner_outcome import format_signal_reasons

from ._state import (
    ChatState,  # noqa: TC001 — runtime import for LangGraph get_type_hints()
)


def _format_agent_result(result: Any, *, label: str) -> dict[str, str]:
    """Render an :class:`AgentResult` as an assistant chat message."""
    if result is None:
        return {"role": "assistant", "content": f"[{label}] no result."}
    success = getattr(result, "success", True)
    output = getattr(result, "output", None)
    artifacts = getattr(result, "artifacts", ()) or ()
    if not success:
        signals = getattr(result, "signals", []) or []
        reason = "; ".join(format_signal_reasons(signals))
        content = f"[{label}] failed"
        if reason:
            content += f": {reason}"
        return {
            "role": "assistant",
            "content": _append_artifact_links(content, artifacts),
        }
    if output is None:
        body = f"[{label}] complete."
    elif isinstance(output, dict):
        body = _summarise_dict_output(label, output)
    else:
        body = f"[{label}] {output}"
    return {
        "role": "assistant",
        "content": _append_artifact_links(body, artifacts),
    }


def _append_artifact_links(content: str, artifacts: Any) -> str:
    """Append markdown artifact links for every artifact with an ``artifact_id``."""
    links: list[str] = []
    for artifact in artifacts or ():
        if not isinstance(artifact, dict):
            continue
        artifact_id = str(artifact.get("artifact_id") or "").strip()
        if not artifact_id:
            continue
        key = str(artifact.get("key") or "").strip() or artifact_id[:8]
        links.append(f"- [{key}]({_artifact_url(artifact_id)})")
    if not links:
        return content
    return content + "\n\n" + "\n".join(links)


def _summarise_dict_output(label: str, output: dict[str, Any]) -> str:
    """Render a structured agent output as a compact, readable summary."""
    skeleton = output.get("routing_skeleton")
    if isinstance(skeleton, dict):
        goal = skeleton.get("goal") or "(no goal)"
        nodes = skeleton.get("nodes")
        n_nodes = len(nodes) if isinstance(nodes, list) else 0
        lines = [f"[{label}] {goal}"]
        lines.append(f"  • {n_nodes} agent step{'s' if n_nodes != 1 else ''}")
        if isinstance(nodes, list):
            for n in nodes[:8]:
                if not isinstance(n, dict):
                    continue
                deps = n.get("depends_on") or []
                dep_str = f" ← {', '.join(deps)}" if deps else ""
                lines.append(
                    f"    - {n.get('id')}: "
                    f"{n.get('agent_id')}/{n.get('command')}{dep_str}"
                )
            if len(nodes) > 8:
                lines.append(f"    … +{len(nodes) - 8} more")
        brief = output.get("work_brief_artifact_id")
        if brief:
            lines.append(f"  • [work_brief]({_artifact_url(str(brief))})")
        return "\n".join(lines)

    for key in ("summary", "goal", "task", "verdict", "result", "content"):
        value = output.get(key)
        if isinstance(value, str) and value.strip():
            return f"[{label}] {value.strip()}"

    import json

    try:
        rendered = json.dumps(output, indent=2, default=str)
    except (TypeError, ValueError):
        rendered = str(output)
    return f"[{label}]\n{rendered}"


async def execution_summary_node(state: ChatState) -> dict[str, Any]:
    """Render the execution subgraph's ``wave_results`` as one chat message.

    Without this the user sees only ``[progress]`` lines while
    execution runs and silence at the end. The summary lists each
    completed node's agent + status so the chat transcript records
    what just happened.
    """
    waves = state.get("wave_results") or []
    if not waves:
        return {
            "messages": [
                {
                    "role": "assistant",
                    "content": "**Execution finished** — no results recorded.",
                }
            ]
        }
    lines = ["**Execution finished:**"]
    for entry in waves:
        if not isinstance(entry, dict):
            continue
        node_id = str(entry.get("node_id") or "?")
        agent_id = str(entry.get("agent_id") or "?")
        success = bool(entry.get("success", True))
        artifacts = entry.get("artifacts") or []
        artifact_link = ""
        if isinstance(artifacts, list | tuple):
            for a in artifacts:
                if isinstance(a, dict) and a.get("artifact_id"):
                    aid = str(a["artifact_id"])
                    key = str(a.get("key") or "").strip() or aid[:8]
                    artifact_link = f" — [{key}]({_artifact_url(aid)})"
                    break
        if success:
            lines.append(f"- ok `{node_id}` ({agent_id}){artifact_link}")
        else:
            lines.append(f"- **fail** `{node_id}` ({agent_id})")
    return {"messages": [{"role": "assistant", "content": "\n".join(lines)}]}
