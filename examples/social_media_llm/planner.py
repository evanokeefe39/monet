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
- "simple": greeting or FAQ, no content generation needed
- "bounded": single platform, single piece of content
- "complex": multi-platform content requiring research, writing, QA, publishing
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

Always include exactly 2 phases:
1. "Research & Draft" — wave 1: researcher, wave 2: writers (one per platform)
2. "QA & Publish" — wave 1: QA per piece, wave 2: publisher per piece

Set is_sensitive to true if the topic involves politics, health claims,
financial advice, or other sensitive areas requiring human review.
"""


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
    return str(response.content)


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
    content = str(response.content)

    # Extract JSON from response (may be wrapped in markdown code blocks)
    if "```json" in content:
        content = content.split("```json")[1].split("```")[0]
    elif "```" in content:
        content = content.split("```")[1].split("```")[0]

    return json.loads(content)
