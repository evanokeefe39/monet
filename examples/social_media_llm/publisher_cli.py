"""Standalone CLI script for content publishing.

Emits the monet event vocabulary as JSON lines to stdout:
  {"type": "progress", ...}
  {"type": "artifact", ...}
  {"type": "result", "output": "..."}

Run by publisher.py via asyncio.create_subprocess_exec.
"""

from __future__ import annotations

import argparse
import json
import sys


def _emit(event: dict[str, object]) -> None:
    """Write a JSON event to stdout and flush."""
    print(json.dumps(event))
    sys.stdout.flush()


def _detect_platform(task: str) -> str:
    """Detect platform from task string."""
    task_lower = task.lower()
    for platform in ("twitter", "linkedin", "instagram"):
        if platform in task_lower:
            return platform
    return "unknown"


def _format_content(task: str, platform: str) -> str:
    """Format content as publication-ready markdown."""
    lines = [
        f"# {platform.title()} Publication",
        "",
        f"**Platform:** {platform}",
        f"**Task:** {task}",
        "",
        "---",
        "",
        "## Content",
        "",
        f"[Content for {platform} about: {task}]",
        "",
        "## Metadata",
        "",
        f"- Platform: {platform}",
        "- Status: formatted",
        "- Ready for review: yes",
    ]
    return "\n".join(lines)


def main() -> None:
    """Run the publisher CLI."""
    parser = argparse.ArgumentParser(description="Format and publish content")
    parser.add_argument("--task", required=True, help="Task description")
    args = parser.parse_args()

    platform = _detect_platform(args.task)

    _emit({"type": "progress", "status": "formatting", "platform": platform})

    formatted = _format_content(args.task, platform)

    _emit(
        {
            "type": "artifact",
            "content": formatted,
            "content_type": "text/markdown",
            "summary": f"Formatted {platform} publication",
            "confidence": 1.0,
            "completeness": "complete",
        }
    )

    _emit(
        {
            "type": "result",
            "output": json.dumps(
                {
                    "status": "published",
                    "platform": platform,
                    "url": f"https://{platform}.example.com/post/mock-12345",
                }
            ),
        }
    )


if __name__ == "__main__":
    main()
