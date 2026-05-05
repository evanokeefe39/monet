"""monet adapter SDK — config-based agent onboarding.

Public API:
    serve(config_path, host, port) — load TOML config and run adapter server
    load_config(path) -> AdapterConfig — parse and validate TOML config
    AdapterError(message, code) — wire-format error
"""

from ._config import AdapterConfig, load_config
from ._errors import AdapterError
from ._server import serve

__all__ = ["AdapterConfig", "AdapterError", "load_config", "serve"]
