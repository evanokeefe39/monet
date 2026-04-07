"""Reference QA agent — quality evaluation with signal emission."""

from __future__ import annotations

import functools
import json
import os
from typing import Any

from monet import Signal, SignalType, agent, emit_progress, emit_signal
from monet.exceptions import SemanticError

_QA_PROMPT = """You are a content QA agent. Evaluate the content/task.

Return ONLY valid JSON:
{
  "verdict": "pass" | "marginal" | "fail",
  "confidence": 0.0-1.0,
  "notes": "short rationale"
}

Content/task: {task}
"""


@functools.cache
def _get_model(model_string: str) -> Any:
    from langchain.chat_models import init_chat_model  # type: ignore[import-not-found]

    return init_chat_model(model_string)


def _model_string() -> str:
    return os.environ.get("MONET_QA_MODEL", "groq:llama-3.3-70b-versatile")


@agent(agent_id="qa", command="fast")
async def qa_fast(task: str) -> str:
    """Evaluate content quality. May emit LOW_CONFIDENCE / REVISION_SUGGESTED."""
    emit_progress({"status": "evaluating", "agent": "qa"})

    model = _get_model(_model_string())
    prompt = _QA_PROMPT.replace("{task}", task)
    response = await model.ainvoke([{"role": "user", "content": prompt}])
    content = response.content if hasattr(response, "content") else str(response)
    if isinstance(content, list):
        content = "".join(str(p) for p in content)
    raw = content.strip()
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
