"""
Purreal - Production-grade async connection pooler for SurrealDB.

Provides exclusive connection leasing and sophisticated lifecycle management
to prevent ConcurrencyError issues in high-concurrency Python applications.
"""

from .pooler import (
    SurrealDBConnectionPool,
    PooledConnection,
    SurrealDBPoolManager,
)

__version__ = "0.1.0"
__all__ = [
    "SurrealDBConnectionPool",
    "PooledConnection",
    "SurrealDBPoolManager",
]