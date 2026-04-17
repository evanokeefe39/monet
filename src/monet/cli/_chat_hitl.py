"""HITL interrupt parsing for the monet chat TUI.

The TUI renders any pending interrupt as transcript text and reads the
user's next prompt submission as the resume payload. No modal, no focus
games — the prompt Input is the one widget we trust to receive keystrokes
reliably across terminals.

Two form shapes are supported:
- Approval form (approve / revise <feedback> / reject)
- Single-field free-text form
"""

from __future__ import annotations

from typing import Any

from monet.types import ApprovalAction, InterruptEnvelope


def _hidden_defaults(form: dict[str, Any]) -> dict[str, Any]:
    """Carry-through defaults for ``hidden`` fields in *form*."""
    out: dict[str, Any] = {}
    for f in form.get("fields") or []:
        if isinstance(f, dict) and f.get("type") == "hidden":
            name = str(f.get("name") or "")
            if name:
                out[name] = f.get("default")
    return out


def _visible_fields(form: dict[str, Any]) -> list[dict[str, Any]]:
    """Fields that need user input (everything except ``hidden``)."""
    return [
        f
        for f in form.get("fields") or []
        if isinstance(f, dict) and f.get("type") != "hidden"
    ]


def is_approval_form(form: dict[str, Any]) -> bool:
    """True when *form* carries an ``action`` radio with approve/reject options."""
    envelope = InterruptEnvelope.from_interrupt_values(form)
    return envelope.is_approval_form() if envelope is not None else False


def format_form_prompt(form: dict[str, Any]) -> list[str]:
    """Render *form* as transcript lines telling the user how to respond."""
    lines: list[str] = []
    prompt = str(form.get("prompt") or "Please respond:").strip()
    if prompt:
        lines.append(f"[info] {prompt}")
    if is_approval_form(form):
        lines.append("[info] Reply: approve | revise <feedback> | reject")
        return lines
    visible = _visible_fields(form)
    if len(visible) == 1:
        f = visible[0]
        label = str(f.get("label") or f.get("name") or "answer")
        lines.append(f"[info] Reply with your {label.lower()}.")
        return lines
    lines.append("[info] Reply with one line per field, in this order:")
    for f in visible:
        label = str(f.get("label") or f.get("name") or "?")
        lines.append(f"[info]   {label}")
    return lines


def parse_approval_reply(text: str) -> dict[str, Any] | None:
    """Turn ``approve|reject|revise <feedback>`` into a resume payload.

    Returns ``None`` when the reply doesn't match a recognised action so
    a typo never silently becomes a revise with the typo as feedback.
    """
    head, _, rest = text.strip().partition(" ")
    head_l = head.lower()
    action: ApprovalAction
    if head_l in {"a", "approve", "y", "yes", "ok"}:
        action = "approve"
        return {"action": action, "feedback": ""}
    if head_l in {"r", "reject", "n", "no", "abort"}:
        action = "reject"
        return {"action": action, "feedback": ""}
    if head_l in {"revise", "rev", "edit"}:
        action = "revise"
        return {"action": action, "feedback": rest.strip()}
    return None


def parse_text_reply(form: dict[str, Any], text: str) -> dict[str, Any] | None:
    """Convert raw user text into a resume payload for *form*.

    Returns ``None`` when *form* has an unrecognised shape or the reply
    doesn't match a recognised pattern — the caller re-prompts.
    """
    payload: dict[str, Any] = dict(_hidden_defaults(form))
    if is_approval_form(form):
        approval = parse_approval_reply(text)
        if approval is None:
            return None
        payload.update(approval)
        for f in _visible_fields(form):
            name = str(f.get("name") or "")
            if name and name not in payload:
                payload[name] = f.get("default")
        return payload
    visible = _visible_fields(form)
    if len(visible) == 1:
        f = visible[0]
        name = str(f.get("name") or "")
        if not name:
            return None
        payload[name] = text.strip()
        return payload
    return None
