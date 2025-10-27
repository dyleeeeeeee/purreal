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


@pytest.fixture(scope="session")
def event_loop_policy():
    """Set the event loop policy for all async tests."""
    return asyncio.get_event_loop_policy()


@pytest.fixture(scope="function")
def event_loop():
    """Create an event loop for each test function."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


# ============================================================================
# Cleanup Hooks
# ============================================================================

@pytest.fixture(autouse=True)
async def cleanup_tasks():
    """Clean up any lingering async tasks after each test."""
    yield
    
    # Cancel any pending tasks
    tasks = [t for t in asyncio.all_tasks() if not t.done()]
    for task in tasks:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
