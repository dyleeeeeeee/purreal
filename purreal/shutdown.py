from __future__ import annotations

import logging
from .pool import SurrealDBPoolManager

logger = logging.getLogger(__name__)


async def close_pools() -> None:
	"""Close all database connection pools gracefully."""
	try:
		manager = SurrealDBPoolManager()
		await manager.close_all_pools()
		logger.info("All database connection pools closed successfully")
	except Exception as e:
		logger.error(f"Error closing database connection pools: {e}")
