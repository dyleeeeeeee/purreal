"""
Purreal - Year-3050 async connection pooler and session multiplexer for SurrealDB.

Features:
- Adaptive pool with predictive pre-warming and self-tuning topology
- Session multiplexer with transparent teleportation across physical connections
- Circuit breakers, leak detection, graceful drain, bounded waiters
"""

from .types import (
	PoolConfig,
	PooledConnection,
	PoolEvent,
	PoolExhaustedError,
	PoolPausedError,
	PoolStats,
	SessionExpiredError,
)
from .events import EventBus
from .pool import SurrealDBConnectionPool, SurrealDBPoolManager
from .multiplexer import SessionMultiplexer, VirtualSession
from .predictive import DemandPredictor, LatencyOracle, CircuitBreaker, AdaptiveScaler

__version__ = "0.2.0"
__all__ = [
	"SurrealDBConnectionPool",
	"SurrealDBPoolManager",
	"PooledConnection",
	"PoolConfig",
	"PoolEvent",
	"PoolExhaustedError",
	"PoolPausedError",
	"PoolStats",
	"SessionExpiredError",
	"EventBus",
	"SessionMultiplexer",
	"VirtualSession",
	"DemandPredictor",
	"LatencyOracle",
	"CircuitBreaker",
	"AdaptiveScaler",
]
