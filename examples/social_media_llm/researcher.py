"""Researcher implementation — pure LangChain, zero monet imports.

Uses create_react_agent with Gemini Flash + Tavily for web research.
"""

from __future__ import annotations

import os

from langchain_community.tools.tavily_search import TavilySearchResults
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_google_genai import ChatGoogleGenerativeAI
from langgraph.prebuilt import create_react_agent

_SYSTEM = """You are a research agent specializing in social media content.
Your job is to gather relevant information about a topic using web search.

Research process:
1. Search for the topic to find current trends and data
2. Search for audience insights relevant to the topic
3. Search for competitor content on the same topic
4. Synthesize findings into a structured research brief

Return your findings as a markdown document with sections:
- Key Statistics
- Audience Insights
- Trending Themes
- Competitor Content Gaps
- Recommended Hashtags
"""


async def run_researcher(task: str, trace_id: str = "") -> str:
    """Run web research and return markdown synthesis."""
    model = ChatGoogleGenerativeAI(
        model="gemini-2.5-flash",
        google_api_key=os.environ["GEMINI_API_KEY"],
        temperature=0.3,
    )
    tavily = TavilySearchResults(
        max_results=5,
        tavily_api_key=os.environ["TAVILY_API_KEY"],
    )

    agent = create_react_agent(model, [tavily])
    result = await agent.ainvoke(
        {
            "messages": [
                SystemMessage(content=_SYSTEM),
                HumanMessage(
                    content=f"Research this topic for social media content: {task}"
                ),
            ]
        }
    )

    # Extract the final AI message content
    messages = result.get("messages", [])
    for msg in reversed(messages):
        if hasattr(msg, "content") and msg.content and not hasattr(msg, "tool_calls"):
            return str(msg.content)

    return "Research completed but no synthesis produced."
