"""Environment validation for monet CLI commands."""

from __future__ import annotations

from pathlib import Path

import click

# Keys that qualify as LLM provider configuration.
_LLM_KEYS = ("GEMINI_API_KEY", "GROQ_API_KEY")


def check_env() -> None:
    """Fail fast with a helpful message if .env is missing or incomplete.

    Checks for a ``.env`` file in the current directory and verifies
    that at least one LLM provider key is configured.

    Raises:
        click.ClickException: When ``.env`` is missing or no LLM key is set.
    """
    env_path = Path.cwd() / ".env"

    if not env_path.exists():
        raise click.ClickException(
            "No .env found in current directory.\n"
            "Copy .env.example to .env and fill in at least one LLM provider key.\n"
            f"Required: {' or '.join(_LLM_KEYS)}"
        )

    # Parse .env for key presence (simple KEY=VALUE parsing).
    env_vars: dict[str, str] = {}
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            key, _, value = line.partition("=")
            env_vars[key.strip()] = value.strip()

    if not any(env_vars.get(k) for k in _LLM_KEYS):
        raise click.ClickException(
            "No LLM provider configured. Set at least one in .env:\n"
            + "\n".join(f"  {k}=your-key" for k in _LLM_KEYS)
        )
