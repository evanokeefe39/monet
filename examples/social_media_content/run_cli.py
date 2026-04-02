"""Convenience script to run the CLI without PYTHONPATH gymnastics.

Usage: uv run python examples/social_media_content/run_cli.py
"""

import sys
from pathlib import Path

# Add project root to path so `examples` is importable
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from examples.social_media_content.cli import cli_main

cli_main()
