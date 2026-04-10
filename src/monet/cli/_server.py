"""monet server — start the orchestration server."""

from __future__ import annotations

import os

import click


@click.command()
@click.option(
    "--host",
    default="0.0.0.0",
    help="Host to bind to.",
)
@click.option(
    "--port",
    default=8000,
    type=int,
    help="Port to listen on.",
)
@click.option(
    "--config",
    "config_path",
    default=None,
    type=click.Path(exists=True),
    help="Path to monet.toml.",
)
@click.option(
    "--reload",
    "use_reload",
    is_flag=True,
    help="Enable auto-reload for development.",
)
def server(host: str, port: int, config_path: str | None, use_reload: bool) -> None:
    """Start the monet orchestration server."""
    import uvicorn

    if config_path:
        os.environ["MONET_CONFIG_PATH"] = config_path

    uvicorn.run(
        "monet.server:create_app",
        factory=True,
        host=host,
        port=port,
        reload=use_reload,
    )
