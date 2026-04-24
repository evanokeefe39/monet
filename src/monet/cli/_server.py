"""monet server — start the orchestration server."""

from __future__ import annotations

import os

import click

from monet.config import MONET_CONFIG_PATH

_PLANE_FACTORIES = {
    "unified": "monet.server:create_app",
    "control": "monet.server:_create_control_plane",
    "data": "monet.server:_create_data_plane",
}


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
@click.option(
    "--plane",
    default="unified",
    type=click.Choice(["unified", "control", "data"]),
    help="Plane to run: unified (default), control-only, or data-only.",
)
def server(
    host: str,
    port: int,
    config_path: str | None,
    use_reload: bool,
    plane: str,
) -> None:
    """Start the monet orchestration server."""
    import uvicorn

    # Uvicorn's ``factory=True`` loader calls the factory with no
    # arguments — transport CLI values via env vars that the factories read.
    if config_path:
        os.environ[MONET_CONFIG_PATH] = config_path

    uvicorn.run(
        _PLANE_FACTORIES[plane],
        factory=True,
        host=host,
        port=port,
        reload=use_reload,
    )
