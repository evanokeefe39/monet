"""Environment validation for monet CLI commands."""

from __future__ import annotations

import os
from pathlib import Path

import click

from monet.config._env import GEMINI_API_KEY, GROQ_API_KEY

# Keys that qualify as LLM provider configuration.
_LLM_KEYS: tuple[str, ...] = (GEMINI_API_KEY, GROQ_API_KEY)

# Aegra's Postgres connection string. Required so the bundled
# ``.monet/docker-compose.yml`` credentials match what Aegra dials; without
# it Aegra falls back to ``postgres:postgres@localhost/aegra`` and the
# migration step fails with a password-authentication error.
_DATABASE_URL_KEY = "DATABASE_URL"


def check_env() -> None:
    """Fail fast with a helpful message if .env is missing or incomplete.

    Checks for a ``.env`` file in the current directory and verifies
    that at least one LLM provider key is configured plus ``DATABASE_URL``
    is set (either in ``.env`` or the process environment).

    Raises:
        click.ClickException: When ``.env`` is missing, no LLM key is set,
            or ``DATABASE_URL`` is unset in both ``.env`` and the environment.
    """
    env_path = Path.cwd() / ".env"

    if not env_path.exists():
        raise click.ClickException(
            "No .env found in current directory.\n"
            "Copy .env.example to .env and fill in at least one LLM provider key.\n"
            f"Required: {' or '.join(_LLM_KEYS)}"
        )

    env_vars = _parse_env_file(env_path)

    if not any(env_vars.get(k) for k in _LLM_KEYS):
        raise click.ClickException(
            "No LLM provider configured. Set at least one in .env:\n"
            + "\n".join(f"  {k}=your-key" for k in _LLM_KEYS)
        )

    if not env_vars.get(_DATABASE_URL_KEY) and not os.environ.get(_DATABASE_URL_KEY):
        raise click.ClickException(
            "DATABASE_URL is not set. Aegra needs it to reach the bundled "
            "Postgres container.\n"
            "Add this line to .env (matches .monet/docker-compose.yml defaults):\n"
            "  DATABASE_URL=postgresql://monet:monet_secret@localhost:5432/monet"
        )


def _parse_env_file(env_path: Path) -> dict[str, str]:
    """Parse a ``KEY=value`` env file, stripping quotes from values."""
    env_vars: dict[str, str] = {}
    for raw in env_path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        env_vars[key.strip()] = value.strip().strip('"').strip("'")
    return env_vars
