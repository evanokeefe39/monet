"""Shared backend resolution utilities for config-driven subsystems.

Three subsystems (artifacts, queue, progress) share the same pattern:
a config ref selects either a built-in backend or a user-supplied
dotted-path factory.  This module extracts the shared mechanics.
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

from monet.config._env import ConfigError

__all__ = ["resolve_backend", "validate_dotted_path"]


def validate_dotted_path(ref: str, env_var_name: str) -> None:
    """Fail fast if *ref* is not a resolvable ``module.path:factory`` string.

    Args:
        ref: Dotted-path string to validate.
        env_var_name: Config key shown in error messages.

    Raises:
        ConfigError: When *ref* is malformed, the module is missing, or the
            named attribute does not exist on the module.
    """
    if ":" not in ref:
        raise ConfigError(
            env_var_name,
            ref,
            "a dotted path of the form 'module.path:factory'",
        )
    module_part, _, factory = ref.rpartition(":")
    if not module_part or not factory:
        raise ConfigError(
            env_var_name,
            ref,
            "a dotted path of the form 'module.path:factory'",
        )
    try:
        mod = importlib.import_module(module_part)
    except ModuleNotFoundError as exc:
        raise ConfigError(
            env_var_name,
            ref,
            f"an importable module (ModuleNotFoundError: {exc})",
        ) from None
    if not hasattr(mod, factory):
        raise ConfigError(
            env_var_name,
            ref,
            f"a callable named '{factory}' on module '{module_part}'",
        )


def resolve_backend(
    *,
    config_ref: str | None,
    env_var_name: str,
    default_factory: Callable[[], Any],
    protocol: type | None = None,
) -> Any:
    """Instantiate a backend from a dotted-path ref or a built-in factory.

    Args:
        config_ref: Optional ``module.path:factory`` string from config.
            When set, the module is imported and the factory called with no
            arguments.  Validation is assumed to have passed already via
            :func:`validate_dotted_path`.
        env_var_name: Config key shown in error messages.
        default_factory: Called with no arguments when *config_ref* is None.
        protocol: When set, the result must satisfy ``isinstance(result,
            protocol)``; otherwise :exc:`ConfigError` is raised.

    Returns:
        The instantiated backend object.

    Raises:
        ConfigError: When the result does not satisfy *protocol*.
    """
    if config_ref:
        mod_path, _, factory_name = config_ref.rpartition(":")
        mod = importlib.import_module(mod_path)
        result = getattr(mod, factory_name)()
    else:
        result = default_factory()
    if protocol is not None and not isinstance(result, protocol):
        raise ConfigError(
            env_var_name,
            config_ref or "(default)",
            f"an object satisfying {protocol.__qualname__}",
        )
    return result
