#!/usr/bin/env python3
"""CLI agent that emits ndjson to stdout.

Demonstrates the CLI wrapping pattern from the spec without npm or Node.js.
Reads task from --task arg, emits progress events, then a final result.
"""

from __future__ import annotations

import argparse
import json
import sys
import time


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task", required=True)
    parser.add_argument("--run-id", default="")
    parser.add_argument("--effort", default="high")
    args = parser.parse_args()

    # Emit progress events
    steps = ["gathering", "processing", "synthesizing"]
    for i, step in enumerate(steps):
        event = {
            "type": "progress",
            "step": step,
            "current": i + 1,
            "total": len(steps),
        }
        print(json.dumps(event), flush=True)
        time.sleep(0.05)  # Simulate work

    # Emit final result
    result = {
        "type": "result",
        "output": f"CLI analysis of: {args.task}",
        "confidence": 0.85,
    }
    print(json.dumps(result), flush=True)


if __name__ == "__main__":
    main()
    sys.exit(0)
