"""Convenience script to run the LLM CLI without PYTHONPATH gymnastics.

Usage: uv run python examples/social_media_llm/run_cli.py [topic]
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from examples.social_media_llm.cli import cli_main

cli_main()
