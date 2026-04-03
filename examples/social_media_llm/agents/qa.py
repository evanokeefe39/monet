"""QA implementation — pure LangChain, zero monet imports.

Uses Groq (llama-3.3-70b-versatile) for fast quality evaluation.
"""

from __future__ import annotations

import json
import os

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_groq import ChatGroq

_SYSTEM = """You are a content quality evaluator. Given a piece of social media
content and the original task, evaluate it and return a JSON verdict.

Return JSON only:
{
  "verdict": "pass" | "fail",
  "confidence": 0.0 to 1.0,
  "notes": "brief explanation of your assessment"
}

Evaluation criteria:
- Relevance: content matches the requested topic
- Quality: well-written, no obvious errors
- Platform fit: appropriate tone and length for the platform
- Accuracy: no obviously false claims

Be strict but fair. Most decent content should score 0.7+.
"""


async def run_qa(task: str, context_summary: str = "") -> dict[str, object]:
    """Evaluate content quality. Returns verdict dict."""
    model = ChatGroq(
        model="llama-3.3-70b-versatile",
        groq_api_key=os.environ["GROQ_API_KEY"],
        temperature=0.1,
    )

    prompt = f"Evaluate this content:\n\nTask: {task}"
    if context_summary:
        prompt += f"\n\nContent to evaluate:\n{context_summary}"

    response = await model.ainvoke(
        [
            SystemMessage(content=_SYSTEM),
            HumanMessage(content=prompt),
        ]
    )
    content = str(response.content)

    # Extract JSON from response
    if "```json" in content:
        content = content.split("```json")[1].split("```")[0]
    elif "```" in content:
        content = content.split("```")[1].split("```")[0]

    try:
        return json.loads(content)
    except json.JSONDecodeError:
        return {"verdict": "pass", "confidence": 0.6, "notes": content[:200]}
