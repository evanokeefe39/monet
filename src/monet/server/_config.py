"""Pool topology configuration — re-exports from monet.config._pools.

Canonical location is now ``monet.config.PoolConfig`` and
``monet.config.load_pool_config``. This module re-exports for
backward compatibility within the server package.
"""

from monet.config._pools import PoolConfig, load_pool_config

# Preserve the old name used by orchestration._invoke
load_config = load_pool_config

__all__ = ["PoolConfig", "load_config", "load_pool_config"]
