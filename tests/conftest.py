# -*- coding: utf-8 -*-
"""
Shared pytest configuration and fixtures for purreal tests.
"""

import asyncio
import sys
from unittest.mock import MagicMock

import pytest


# ============================================================================
# Mock SurrealDB Module
# ============================================================================

# Mock surrealdb before any imports
mock_surrealdb = MagicMock()
mock_surrealdb.AsyncSurreal = MagicMock()
mock_surrealdb.AsyncWsSurrealConnection = MagicMock()
mock_surrealdb.AsyncHttpSurrealConnection = MagicMock()
sys.modules['surrealdb'] = mock_surrealdb


# ============================================================================
# Pytest Configuration
# ============================================================================

def pytest_configure(config):
    """Configure pytest with custom markers and settings."""
    config.addinivalue_line("markers", "unit: Unit tests")
    config.addinivalue_line("markers", "integration: Integration tests")
    config.addinivalue_line("markers", "performance: Performance tests")
    config.addinivalue_line("markers", "slow: Slow-running tests")


# ============================================================================
# Cleanup Hooks
# ============================================================================

@pytest.fixture(autouse=True)
async def cleanup_tasks():
    """Clean up any lingering async tasks after each test."""
    yield

    current = asyncio.current_task()
    tasks = [t for t in asyncio.all_tasks() if not t.done() and t is not current]
    for task in tasks:
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass
