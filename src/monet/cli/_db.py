"""Database migration commands.

The ``monet db`` group wraps :mod:`monet.artifacts._migrations`. The
artifact index is the only monet-owned relational schema today; the
deployments table (``server/_deployment.py``) is rebuilt each boot and
does not yet have migration coverage.
"""

from __future__ import annotations

import os

import click

from monet.artifacts._migrations import (
    apply_migrations,
    check_at_head,
    current_revision,
    head_revision,
    stamp_head,
)


def _resolve_db_url(override: str | None) -> str:
    if override:
        return override
    env = os.environ.get("MONET_ARTIFACTS_DB_URL")
    if env:
        return env
    return "sqlite+aiosqlite:///.artifacts/index.db"


@click.group()
def db() -> None:
    """Artifact index schema migration commands."""


@db.command()
@click.option("--db-url", default=None, help="Override MONET_ARTIFACTS_DB_URL.")
def migrate(db_url: str | None) -> None:
    """Apply all pending migrations (alembic upgrade head)."""
    url = _resolve_db_url(db_url)
    click.echo(f"Applying migrations against {url}")
    apply_migrations(url)
    click.echo(f"At head: {head_revision()}")


@db.command()
@click.option("--db-url", default=None, help="Override MONET_ARTIFACTS_DB_URL.")
def current(db_url: str | None) -> None:
    """Show the current alembic revision applied to the DB."""
    url = _resolve_db_url(db_url)
    rev = current_revision(url)
    head = head_revision()
    if rev is None:
        click.echo("Database has no alembic_version table (never migrated).")
    else:
        click.echo(f"Current: {rev}")
    click.echo(f"Head:    {head}")


@db.command()
@click.option("--db-url", default=None, help="Override MONET_ARTIFACTS_DB_URL.")
def stamp(db_url: str | None) -> None:
    """Mark the DB as being at head without running migrations.

    Use this when adopting alembic on an existing database that was
    created by legacy ``Base.metadata.create_all``.
    """
    url = _resolve_db_url(db_url)
    click.echo(f"Stamping {url} at head")
    stamp_head(url)


@db.command()
@click.option("--db-url", default=None, help="Override MONET_ARTIFACTS_DB_URL.")
def check(db_url: str | None) -> None:
    """Exit non-zero if the DB is not at head. For deploy gating."""
    url = _resolve_db_url(db_url)
    if check_at_head(url):
        click.echo("OK: database at head.")
        return
    rev = current_revision(url)
    head = head_revision()
    click.echo(
        f"Database not at head: current={rev}, head={head}. "
        f"Run `monet db migrate` to apply pending migrations.",
        err=True,
    )
    raise SystemExit(1)
