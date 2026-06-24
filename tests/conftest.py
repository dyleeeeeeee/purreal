# -*- coding: utf-8 -*-
"""Shared pytest configuration and fixtures for purreal tests."""

import asyncio
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

# Mock surrealdb before any imports
mock_surrealdb = MagicMock()
mock_surrealdb.AsyncSurreal = MagicMock()
mock_surrealdb.AsyncWsSurrealConnection = MagicMock()
mock_surrealdb.AsyncHttpSurrealConnection = MagicMock()
sys.modules["surrealdb"] = mock_surrealdb


def pytest_configure(config):
    config.addinivalue_line("markers", "unit: Unit tests")
    config.addinivalue_line("markers", "integration: Integration tests")
    config.addinivalue_line("markers", "performance: Performance tests")
    config.addinivalue_line("markers", "slow: Slow-running tests")


@pytest.fixture(autouse=True)
async def cleanup_tasks():
    yield
    current = asyncio.current_task()
    tasks = [t for t in asyncio.all_tasks() if not t.done() and t is not current]
    for task in tasks:
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass


@pytest.fixture
def mock_surreal_connection():
    conn = AsyncMock()
    conn.query = AsyncMock(return_value=[{"result": "success"}])
    conn.use = AsyncMock(return_value=None)
    conn.signin = AsyncMock(return_value=None)
    conn.close = AsyncMock(return_value=None)
    return conn


@pytest.fixture
def pool_config():
    return {
        "uri": "ws://localhost:8000/rpc",
        "credentials": {"username": "root", "password": "root"},
        "namespace": "test",
        "database": "test",
        "pool_size": 3,
        "max_overflow": 5,
        "connection_timeout": 5.0,
        "acquisition_timeout": 5.0,
        "health_check_interval": 30.0,
        "max_usage_count": 1000,
        "connection_retry_attempts": 1,
        "connection_retry_delay": 0.01,
        "pre_ping": False,
        "log_queries": False,
    }
