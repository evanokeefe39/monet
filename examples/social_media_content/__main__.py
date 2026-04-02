"""Allow running as: PYTHONPATH=. uv run python -m examples.social_media_content

Or more conveniently via the helper script:
    uv run python examples/social_media_content/run_cli.py
"""

from .cli import cli_main

cli_main()
