"""
Database Shutdown Utilities
===========================

Utilities for gracefully shutting down database connections.
"""

import asyncio
import logging
from .pooler import SurrealDBPoolManager

logger = logging.getLogger(__name__)

async def close_pools():
    """
    Close all database connection pools gracefully.
    
    This function should be called during application shutdown.
    """
    try:
        pool_manager = SurrealDBPoolManager()
        await pool_manager._close_pools()
        logger.info("All database connection pools closed successfully")
    except Exception as e:
        logger.error(f"Error closing database connection pools: {e}")