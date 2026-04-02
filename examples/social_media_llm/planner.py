"""Planner implementation — pure LangChain, zero monet imports.

Provides triage classification and work brief generation using Gemini Flash.
"""

from __future__ import annotations

import json
import os

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_google_genai import ChatGoogleGenerativeAI

_TRIAGE_SYSTEM = """You are a content request classifier. Given a user's request,
classify it as one of: simple, bounded, or complex.

Return JSON only:
{
  "complexity": "simple" | "bounded" | "complex",
  "suggested_agents": ["sm-researcher", "sm-writer", "sm-qa", "sm-publisher"],
  "requires_planning": true | false
}

Rules:
- "simple": greeting, FAQ, or off-topic — no content generation needed
- "bounded": explicitly asks for ONE specific platform only, no research needed
- "complex": anything involving content creation, research, or multiple platforms

When in doubt, classify as "complex". Most content requests are complex.
"""

_PLAN_SYSTEM = """You are a content planning agent. Given a topic and optional
feedback, produce a structured work brief as JSON.

The work brief MUST follow this exact schema:
{
  "goal": "one sentence describing the outcome",
  "in_scope": ["list of deliverables"],
  "out_of_scope": ["list of exclusions"],
  "quality_criteria": {"criterion_name": "description"},
  "constraints": {"constraint_name": "value"},
  "is_sensitive": false,
  "phases": [
    {
      "name": "Phase Name",
      "waves": [
        {
          "items": [
            {"agent_id": "sm-researcher", "command": "deep",
             "task": "task description"},
            {"agent_id": "sm-writer", "command": "deep", "task": "task description"}
          ]
        }
      ]
    }
  ],
  "assumptions": ["list of assumptions"]
}

IMPORTANT: Use ONLY these exact agent_id/command combinations:
- {"agent_id": "sm-researcher", "command": "deep"} for research
- {"agent_id": "sm-writer", "command": "deep"} for writing
- {"agent_id": "sm-qa", "command": "fast"} for quality review
- {"agent_id": "sm-publisher", "command": "publish"} for publishing

Always include exactly 2 phases:
1. "Research & Draft" — wave 1: sm-researcher/deep,
   wave 2: sm-writer/deep (one per platform)
2. "QA & Publish" — wave 1: sm-qa/fast per piece, wave 2: sm-publisher/publish per piece

Set is_sensitive to true if the topic involves politics, health claims,
financial advice, or other sensitive areas requiring human review.
"""


def _strip_code_fences(text: str) -> str:
    """Strip markdown code fences from LLM output."""
    if "```json" in text:
        text = text.split("```json")[1].split("```")[0]
    elif "```" in text:
        text = text.split("```")[1].split("```")[0]
    return text.strip()


async def run_planner_triage(task: str) -> str:
    """Classify a content request. Returns JSON string."""
    model = ChatGoogleGenerativeAI(
        model="gemini-2.5-flash",
        google_api_key=os.environ["GEMINI_API_KEY"],
        temperature=0.1,
    )
    response = await model.ainvoke(
        [
            SystemMessage(content=_TRIAGE_SYSTEM),
            HumanMessage(content=task),
        ]
    )
    return _strip_code_fences(str(response.content))


async def run_planner_plan(task: str, feedback: str | None = None) -> dict[str, object]:
    """Generate a structured work brief. Returns parsed dict."""
    model = ChatGoogleGenerativeAI(
        model="gemini-2.5-flash",
        google_api_key=os.environ["GEMINI_API_KEY"],
        temperature=0.3,
    )
    prompt = task
    if feedback:
        prompt += f"\n\nHuman feedback on previous plan: {feedback}"

    response = await model.ainvoke(
        [
            SystemMessage(content=_PLAN_SYSTEM),
            HumanMessage(content=prompt),
        ]
    )
    content = _strip_code_fences(str(response.content))
    return json.loads(content)
