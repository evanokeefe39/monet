"""Reference QA agent — quality evaluation with signal emission."""

from __future__ import annotations

import functools
import json
import os
from pathlib import Path
from typing import Any

from monet import Signal, SignalType, agent, emit_progress, emit_signal
from monet.exceptions import SemanticError

from .._prompts import extract_text, make_env

_env = make_env(Path(__file__).parent)


@functools.cache
def _get_model(model_string: str) -> Any:
    from langchain.chat_models import init_chat_model  # type: ignore[import-not-found]

    return init_chat_model(model_string)


def _model_string() -> str:
    return os.environ.get("MONET_QA_MODEL", "groq:llama-3.3-70b-versatile")


qa = agent("qa")


@qa(command="fast")
async def qa_fast(task: str, context: list[dict[str, Any]] | None = None) -> str:
    """Evaluate content quality. May emit LOW_CONFIDENCE / REVISION_SUGGESTED.

    The artifact(s) being evaluated are passed via ``context`` — typically the
    full text of upstream wave outputs, with catalogue artifacts already
    resolved by the orchestrator.
    """
    emit_progress({"status": "evaluating", "agent": "qa"})

    prompt = _env.get_template("fast.j2").render(task=task, context=context or [])
    model = _get_model(_model_string())
    response = await model.ainvoke([{"role": "user", "content": prompt}])
    raw = extract_text(response).strip()
    if raw.startswith("```"):
        lines = raw.split("\n")
        body = lines[1:-1] if lines[-1].startswith("```") else lines[1:]
        raw = "\n".join(body).strip()

    try:
        verdict = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SemanticError(
            type="parse_error",
            message=f"QA model returned invalid JSON: {exc}",
        ) from exc

    confidence = float(verdict.get("confidence", 0.7))
    notes = str(verdict.get("notes", ""))

    if confidence < 0.5:
        emit_signal(
            Signal(
                type=SignalType.LOW_CONFIDENCE,
                reason=f"QA confidence {confidence:.2f}: {notes}",
                metadata={"confidence": confidence},
            )
        )

    if verdict.get("verdict") == "fail":
        emit_signal(
            Signal(
                type=SignalType.REVISION_SUGGESTED,
                reason=f"QA failed: {notes}",
                metadata={"verdict": "fail", "confidence": confidence},
            )
        )

    return json.dumps(verdict)
