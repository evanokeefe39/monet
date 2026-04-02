"""Stub agent implementations for the social media content example.

All agents return realistic mock data. No LLM calls. Each agent calls
emit_progress() at entry and exit to demonstrate intra-node streaming.
Artificial delays (asyncio.sleep) differentiate fast/deep in Langfuse traces.
"""

from __future__ import annotations

import asyncio
import json
import random
from typing import Any

from monet import agent, emit_progress

# ---------------------------------------------------------------------------
# Work brief templates — the primary plan and 3 revision variants
# ---------------------------------------------------------------------------

_BASE_WORK_BRIEF: dict[str, Any] = {
    "goal": "Create social media content about AI in marketing",
    "in_scope": ["Twitter post", "LinkedIn article", "Instagram caption"],
    "out_of_scope": ["Paid ad copy", "Email newsletter"],
    "quality_criteria": {
        "each_platform": "platform-appropriate tone and length",
        "factual_accuracy": "claims supported by research",
    },
    "constraints": {"audience": "B2B marketers", "tone": "professional"},
    "phases": [
        {
            "name": "Research & Draft",
            "waves": [
                {
                    "items": [
                        {
                            "agent_id": "sm-researcher",
                            "command": "deep",
                            "task": (
                                "Research AI in marketing trends, "
                                "audience data, competitor content"
                            ),
                        }
                    ]
                },
                {
                    "items": [
                        {
                            "agent_id": "sm-writer",
                            "command": "deep",
                            "task": "Write Twitter post about AI in marketing",
                        },
                        {
                            "agent_id": "sm-writer",
                            "command": "deep",
                            "task": ("Write LinkedIn article about AI in marketing"),
                        },
                        {
                            "agent_id": "sm-writer",
                            "command": "deep",
                            "task": ("Write Instagram caption about AI in marketing"),
                        },
                    ]
                },
            ],
        },
        {
            "name": "QA & Publish",
            "waves": [
                {
                    "items": [
                        {
                            "agent_id": "sm-qa",
                            "command": "fast",
                            "task": "Review Twitter post against brief",
                        },
                        {
                            "agent_id": "sm-qa",
                            "command": "fast",
                            "task": "Review LinkedIn article against brief",
                        },
                        {
                            "agent_id": "sm-qa",
                            "command": "fast",
                            "task": "Review Instagram caption against brief",
                        },
                    ]
                },
                {
                    "items": [
                        {
                            "agent_id": "sm-publisher",
                            "command": "publish",
                            "task": "Publish Twitter post",
                        },
                        {
                            "agent_id": "sm-publisher",
                            "command": "publish",
                            "task": "Publish LinkedIn article",
                        },
                        {
                            "agent_id": "sm-publisher",
                            "command": "publish",
                            "task": "Publish Instagram caption",
                        },
                    ]
                },
            ],
        },
    ],
    "assumptions": ["Topic is non-controversial", "All platforms are active"],
}


def _make_revision_variant(index: int) -> dict[str, Any]:
    """Create a subtly different work brief variant for re-planning."""
    brief = json.loads(json.dumps(_BASE_WORK_BRIEF))
    if index == 0:
        # Variant A: swap phase order — draft first without dedicated research
        brief["phases"][0]["name"] = "Draft & Iterate"
        brief["phases"][0]["waves"] = brief["phases"][0]["waves"][1:]
        brief["assumptions"].append("Skipping dedicated research per feedback")
    elif index == 1:
        # Variant B: add extra research wave for competitor analysis
        extra_wave = {
            "items": [
                {
                    "agent_id": "sm-researcher",
                    "command": "deep",
                    "task": "Deep competitor content analysis across platforms",
                }
            ]
        }
        brief["phases"][0]["waves"].insert(1, extra_wave)
        brief["assumptions"].append("Added competitor deep-dive per feedback")
    else:
        # Variant C: focus on LinkedIn and Twitter only, drop Instagram
        brief["in_scope"] = ["Twitter post", "LinkedIn article"]
        for phase in brief["phases"]:
            for wave in phase["waves"]:
                wave["items"] = [
                    item
                    for item in wave["items"]
                    if "Instagram" not in item.get("task", "")
                ]
        brief["assumptions"].append("Narrowed to Twitter + LinkedIn per feedback")
    return brief


_REVISION_VARIANTS = [_make_revision_variant(i) for i in range(3)]


# ---------------------------------------------------------------------------
# Planner agents
# ---------------------------------------------------------------------------


@agent(agent_id="sm-planner", command="fast")
async def planner_triage(task: str) -> str:
    """Classify incoming message complexity for routing."""
    emit_progress({"type": "started", "agent_id": "sm-planner", "command": "fast"})
    await asyncio.sleep(0.1)
    result = json.dumps(
        {
            "complexity": "complex",
            "suggested_agents": [
                "sm-researcher",
                "sm-writer",
                "sm-qa",
                "sm-publisher",
            ],
            "requires_planning": True,
        }
    )
    emit_progress({"type": "completed", "agent_id": "sm-planner", "command": "fast"})
    return result


@agent(agent_id="sm-planner", command="plan")
async def planner_plan(task: str, context: list[Any] | None = None) -> str:
    """Build a structured work brief for social media content generation."""
    emit_progress({"type": "started", "agent_id": "sm-planner", "command": "plan"})
    await asyncio.sleep(0.3)

    # Check if there's human feedback in context — if so, pick a revision variant.
    # Feedback arrives as InstructionEntry with summary="Human feedback".
    has_feedback = False
    if context:
        for entry in context:
            entry_type = getattr(entry, "type", None) or (
                entry.get("type") if isinstance(entry, dict) else None
            )
            if entry_type == "instruction":
                has_feedback = True
                break

    brief = random.choice(_REVISION_VARIANTS) if has_feedback else _BASE_WORK_BRIEF

    emit_progress({"type": "completed", "agent_id": "sm-planner", "command": "plan"})
    return json.dumps(brief)


# ---------------------------------------------------------------------------
# Researcher agents
# ---------------------------------------------------------------------------


@agent(agent_id="sm-researcher", command="fast")
async def researcher_fast(task: str) -> str:
    """Quick context gathering to inform planning decisions."""
    emit_progress({"type": "started", "agent_id": "sm-researcher", "command": "fast"})
    await asyncio.sleep(0.1)
    result = (
        "Topic: AI in marketing | "
        "Audience: B2B marketers, 25-45 | "
        "Trending: #AIMarketing, #ContentAutomation | "
        "Competitor gap: No one covering ethical AI angle"
    )
    emit_progress({"type": "completed", "agent_id": "sm-researcher", "command": "fast"})
    return result


@agent(agent_id="sm-researcher", command="deep")
async def researcher_deep(task: str) -> str:
    """Thorough research across all available sources."""
    emit_progress({"type": "started", "agent_id": "sm-researcher", "command": "deep"})
    await asyncio.sleep(0.5)
    result = json.dumps(
        {
            "audience_data": {
                "primary": "B2B marketers aged 25-45",
                "platforms": {
                    "twitter": "High engagement with threads and data visuals",
                    "linkedin": "Thought leadership, long-form preferred",
                    "instagram": "Behind-the-scenes, carousel infographics",
                },
            },
            "trending_hashtags": [
                "#AIMarketing",
                "#ContentAutomation",
                "#MarTech",
                "#B2BMarketing",
                "#AIContent",
            ],
            "competitor_analysis": {
                "gap": "Ethical AI angle underserved",
                "saturated": "Generic AI productivity tips",
                "opportunity": "Data-backed case studies",
            },
            "key_statistics": [
                "73% of marketers now use AI tools (2025 HubSpot)",
                "AI-generated content gets 2.3x more engagement when human-edited",
                "B2B buyers consume 13+ pieces of content before purchasing",
            ],
        }
    )
    emit_progress({"type": "completed", "agent_id": "sm-researcher", "command": "deep"})
    return result


# ---------------------------------------------------------------------------
# Writer agent
# ---------------------------------------------------------------------------

_PLATFORM_CONTENT = {
    "twitter": (
        "AI isn't replacing marketers — it's making the great ones unstoppable.\n\n"
        "73% of marketers now use AI tools, but here's what the data actually shows:\n"
        "AI-generated content gets 2.3x more engagement when human-edited.\n\n"
        "The winning formula? AI drafts + human judgment.\n\n"
        "#AIMarketing #MarTech #B2BMarketing"
    ),
    "linkedin": (
        "The AI Marketing Paradox: Why More Automation Demands More Humanity\n\n"
        "After analyzing 500+ B2B campaigns using AI-assisted content creation, "
        "one pattern stands out: the companies seeing the highest ROI aren't the "
        "ones automating the most. They're the ones who've found the right balance.\n\n"
        "Here are 3 findings that challenge conventional wisdom:\n\n"
        "1. AI-generated content gets 2.3x more engagement — but only when "
        "human-edited. Pure AI output actually underperforms manual content by 15%.\n\n"
        "2. The ethical AI angle is massively underserved. Only 3% of marketing "
        "AI content addresses transparency and data ethics — yet those posts "
        "generate 4x the comments.\n\n"
        "3. B2B buyers consume 13+ pieces of content before purchasing. AI lets "
        "you meet that demand without burning out your team.\n\n"
        "The bottom line: AI is a force multiplier, not a replacement. The marketers "
        "who thrive will be those who use AI to do more of what makes them uniquely "
        "human — creative strategy, empathy, and judgment.\n\n"
        "What's your experience with AI in your marketing stack?"
    ),
    "instagram": (
        "AI + Marketing = Better together, not instead of each other.\n\n"
        "Swipe for 3 data-backed insights that changed how we think about "
        "AI in B2B marketing.\n\n"
        "Key takeaway: The best AI-powered campaigns still have humans at the helm.\n\n"
        "#AIMarketing #ContentAutomation #MarTech #B2BMarketing #AIContent "
        "#DigitalMarketing #ContentStrategy #MarketingTips #FutureOfMarketing "
        "#DataDriven"
    ),
}


@agent(agent_id="sm-writer", command="deep")
async def writer_deep(task: str) -> str:
    """Generate platform-specific social media content."""
    emit_progress({"type": "started", "agent_id": "sm-writer", "command": "deep"})
    await asyncio.sleep(0.5)

    # Determine platform from task string
    task_lower = task.lower()
    if "twitter" in task_lower:
        content = _PLATFORM_CONTENT["twitter"]
    elif "linkedin" in task_lower:
        content = _PLATFORM_CONTENT["linkedin"]
    elif "instagram" in task_lower:
        content = _PLATFORM_CONTENT["instagram"]
    else:
        content = f"Content for: {task}"

    emit_progress({"type": "completed", "agent_id": "sm-writer", "command": "deep"})
    return content


# ---------------------------------------------------------------------------
# QA agent
# ---------------------------------------------------------------------------


@agent(agent_id="sm-qa", command="fast")
async def qa_fast(task: str) -> str:
    """Evaluate content against the work brief quality criteria."""
    emit_progress({"type": "started", "agent_id": "sm-qa", "command": "fast"})
    await asyncio.sleep(0.1)
    result = json.dumps(
        {
            "verdict": "pass",
            "notes": "Content meets platform guidelines and quality criteria.",
        }
    )
    emit_progress({"type": "completed", "agent_id": "sm-qa", "command": "fast"})
    return result


# ---------------------------------------------------------------------------
# Publisher agent
# ---------------------------------------------------------------------------


@agent(agent_id="sm-publisher", command="publish")
async def publisher_publish(task: str) -> str:
    """Format and publish content to the target platform."""
    emit_progress({"type": "started", "agent_id": "sm-publisher", "command": "publish"})
    await asyncio.sleep(0.2)

    task_lower = task.lower()
    if "twitter" in task_lower:
        platform = "twitter"
    elif "linkedin" in task_lower:
        platform = "linkedin"
    elif "instagram" in task_lower:
        platform = "instagram"
    else:
        platform = "unknown"

    result = json.dumps(
        {
            "status": "published",
            "platform": platform,
            "url": f"https://{platform}.example.com/post/12345",
            "scheduled_at": "2026-04-02T10:00:00Z",
        }
    )
    emit_progress(
        {"type": "completed", "agent_id": "sm-publisher", "command": "publish"}
    )
    return result
