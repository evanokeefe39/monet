"""Form-schema envelope builders for HITL interrupts.

Every interrupt in the graph layer emits a ``{prompt, fields, context}``
envelope that the TUI (or any consumer) renders generically. The
builders here are the single source of truth for each envelope shape so
graphs stay wire-compatible with their renderers.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from monet._ports import artifact_view_url

if TYPE_CHECKING:
    from monet.types import ApprovalAction, ArtifactPointer


@dataclass(frozen=True)
class ApprovalDecision:
    """Parsed approve/revise/reject response from a plan-approval interrupt."""

    action: ApprovalAction
    feedback: str | None


def build_plan_approval_form(
    *,
    work_brief_pointer: ArtifactPointer,
    routing_skeleton: dict[str, Any] | None,
    prompt: str | None = None,
) -> dict[str, Any]:
    """Build the form-schema payload for the plan-approval interrupt.

    When ``prompt`` is None, a default summary is rendered from the
    routing skeleton so the reviewer sees what they are approving. The
    plan summary lives in the prompt (not the fields) so every consumer
    — TUI, web UI, REPL — gets the same human-readable plan view for
    free from the shared form-schema renderer.
    """
    if prompt is None:
        prompt = _render_plan_prompt(work_brief_pointer, routing_skeleton)
    return {
        "prompt": prompt,
        "fields": [
            {
                "name": "action",
                "type": "radio",
                "label": "Decision",
                "options": [
                    {"value": "approve", "label": "Approve"},
                    {"value": "revise", "label": "Revise with feedback"},
                    {"value": "reject", "label": "Reject"},
                ],
            },
            {
                "name": "feedback",
                "type": "textarea",
                "label": "Feedback (required for revise)",
                "required": False,
            },
        ],
        "context": {
            "work_brief_pointer": work_brief_pointer,
            "routing_skeleton": routing_skeleton,
        },
    }


def _render_plan_prompt(
    work_brief_pointer: ArtifactPointer,
    routing_skeleton: dict[str, Any] | None,
) -> str:
    """Render a compact plan summary for the approval prompt."""
    lines: list[str] = []
    if isinstance(routing_skeleton, dict):
        goal = str(routing_skeleton.get("goal") or "").strip()
        if goal:
            lines.append(f"Plan: {goal}")
        nodes = routing_skeleton.get("nodes")
        if isinstance(nodes, list) and nodes:
            lines.append(f"{len(nodes)} step{'s' if len(nodes) != 1 else ''}:")
            for n in nodes[:8]:
                if not isinstance(n, dict):
                    continue
                deps = n.get("depends_on") or []
                dep_str = f" ← {', '.join(deps)}" if deps else ""
                lines.append(
                    f"  - {n.get('id')}: "
                    f"{n.get('agent_id')}/{n.get('command')}{dep_str}"
                )
            if len(nodes) > 8:
                lines.append(f"  … +{len(nodes) - 8} more")
    artifact_id = work_brief_pointer.get("artifact_id", "")
    if artifact_id:
        lines.append(f"Work brief: {artifact_view_url(artifact_id)}")
    if not lines:
        return "Approve this plan?"
    lines.append("")
    lines.append("Approve this plan?")
    return "\n".join(lines)


def parse_approval_decision(decision: Any) -> ApprovalDecision:
    """Parse a resume payload into a typed ApprovalDecision.

    Unknown, missing, or non-dict payloads fall back to ``reject`` so the
    caller has a single uniform path. The caller enforces
    revise-requires-feedback.
    """
    if not isinstance(decision, dict):
        return ApprovalDecision(action="reject", feedback=None)
    raw_action = decision.get("action")
    feedback_raw = decision.get("feedback")
    feedback = str(feedback_raw).strip() if feedback_raw else None
    if raw_action in ("approve", "revise", "reject"):
        return ApprovalDecision(action=raw_action, feedback=feedback)
    return ApprovalDecision(action="reject", feedback=feedback)


def build_execution_interrupt_form(
    *,
    last_result: dict[str, Any],
    reason: str = "Blocking signal from node execution",
) -> dict[str, Any]:
    """Build the form-schema payload for the execution retry/abort interrupt."""
    return {
        "prompt": "Execution paused — retry or abort?",
        "fields": [
            {
                "name": "action",
                "type": "radio",
                "label": "Decision",
                "options": [
                    {"value": "retry", "label": "Retry"},
                    {"value": "abort", "label": "Abort"},
                ],
            },
            {
                "name": "feedback",
                "type": "textarea",
                "label": "Reason (optional)",
                "required": False,
            },
        ],
        "context": {
            "reason": reason,
            "last_result": last_result,
        },
    }
