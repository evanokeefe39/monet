"""Writer implementation — pure LangChain, zero monet imports.

Generates platform-specific social media content using Gemini Flash.
"""

from __future__ import annotations

import os

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_google_genai import ChatGoogleGenerativeAI

_PLATFORM_PROMPTS = {
    "twitter": """Write a Twitter/X post (max 280 characters). Be concise,
use a hook, include 2-3 relevant hashtags. Professional but engaging tone.""",
    "linkedin": """Write a LinkedIn post (300-1200 words). Professional tone,
include a hook, structured with short paragraphs, end with a question or
call-to-action. Include relevant hashtags at the end.""",
    "instagram": """Write an Instagram caption (max 2200 characters). Start with
a hook, use emoji sparingly, include a call-to-action, and end with
8-12 relevant hashtags on a separate line.""",
}


def _detect_platform(task: str) -> str:
    """Detect platform from task description."""
    task_lower = task.lower()
    for platform in ("twitter", "linkedin", "instagram"):
        if platform in task_lower:
            return platform
    return "twitter"


async def run_writer(task: str, platform: str | None = None) -> str:
    """Generate platform-specific content. Returns content string."""
    if platform is None:
        platform = _detect_platform(task)

    model = ChatGoogleGenerativeAI(
        model="gemini-2.5-flash",
        google_api_key=os.environ["GEMINI_API_KEY"],
        temperature=0.7,
    )

    platform_prompt = _PLATFORM_PROMPTS.get(platform, _PLATFORM_PROMPTS["twitter"])

    response = await model.ainvoke(
        [
            SystemMessage(
                content=f"You are a social media content writer.\n\n{platform_prompt}"
            ),
            HumanMessage(content=f"Write content about: {task}"),
        ]
    )
    return str(response.content)
