"""Pool topology configuration from monet.toml + environment variables."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, cast

from monet.config import (
    default_config_path,
    pool_auth_env,
    pool_url_env,
    read_str,
    read_toml,
)

if TYPE_CHECKING:
    from pathlib import Path

__all__ = ["PoolConfig", "load_config"]

_VALID_POOL_TYPES = frozenset({"local", "pull", "push"})


@dataclass(frozen=True)
class PoolConfig:
    """Configuration for a single agent pool.

    Attributes:
        name: Pool identifier, matching the TOML section key.
        type: Pool type — ``local`` (sidecar), ``pull`` (remote),
            or ``push`` (cloud forwarding).
        lease_ttl: Seconds a claimed task lease is valid. Defaults to 300.
        url: Endpoint URL for pull/push pools. Resolved from env
            (``MONET_POOL_{NAME}_URL``) if not in the TOML file.
        auth: Auth token/secret for pull/push pools. Resolved from env
            (``MONET_POOL_{NAME}_AUTH``) if not in the TOML file.
    """

    name: str
    type: Literal["local", "pull", "push"]
    lease_ttl: int = 300
    url: str | None = None
    auth: str | None = None


def load_config(path: Path | None = None) -> dict[str, PoolConfig]:
    """Load pool configuration from monet.toml + environment.

    If *path* is ``None``, looks for ``monet.toml`` in the current working
    directory. If the file does not exist, returns a default configuration
    with a single ``local`` pool.

    For each pool, infrastructure values are resolved from environment
    variables following the pattern::

        MONET_POOL_{NAME}_URL  -> url
        MONET_POOL_{NAME}_AUTH -> auth

    where ``{NAME}`` is the uppercased pool name.

    Args:
        path: Explicit path to a ``monet.toml`` file. When ``None``,
            defaults to ``Path.cwd() / "monet.toml"``.

    Returns:
        Mapping of pool name to its ``PoolConfig``.

    Raises:
        ValueError: If a pool type is not one of ``local``, ``pull``,
            ``push``, or if a ``push`` pool has no URL configured.
    """
    resolved = path if path is not None else default_config_path()

    if not resolved.exists():
        return {"local": PoolConfig(name="local", type="local")}

    raw = read_toml(resolved)

    pools_section: dict[str, dict[str, object]] = raw.get("pools", {})
    result: dict[str, PoolConfig] = {}

    for name, pool_data in pools_section.items():
        pool_type = pool_data.get("type")
        if pool_type not in _VALID_POOL_TYPES:
            raise ValueError(
                f"Invalid pool type {pool_type!r} for pool {name!r}. "
                f"Must be one of: {', '.join(sorted(_VALID_POOL_TYPES))}"
            )

        lease_ttl = pool_data.get("lease_ttl", 300)

        url = pool_data.get("url") or read_str(pool_url_env(name))
        auth = pool_data.get("auth") or read_str(pool_auth_env(name))

        # Push pools require a URL — either in config or environment.
        if pool_type == "push" and url is None:
            raise ValueError(
                f"Push pool {name!r} requires a URL. Set it in monet.toml "
                f"or via the {pool_url_env(name)} environment variable."
            )

        result[name] = PoolConfig(
            name=name,
            type=cast('Literal["local", "pull", "push"]', pool_type),
            lease_ttl=int(cast("int", lease_ttl)),
            url=cast("str | None", url),
            auth=cast("str | None", auth),
        )

    return result
