# -*- coding: utf-8 -*-
"""
Comprehensive test suite for purreal connection pooler.
Uses pytest for top-tier testing standards with proper fixtures, parametrization, and coverage.
"""

import asyncio
import pytest
import time
from typing import Dict, Any
from unittest.mock import AsyncMock, MagicMock, patch, call
from collections import deque

from purreal.pooler import (
    SurrealDBConnectionPool,
    PooledConnection,
    SurrealDBPoolManager,
)


# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture
def mock_surreal_connection():
    """Create a mock SurrealDB connection."""
    conn = AsyncMock()
    conn.query = AsyncMock(return_value=[{"result": "success"}])
    conn.use = AsyncMock(return_value=None)
    conn.signin = AsyncMock(return_value=None)
    conn.close = AsyncMock(return_value=None)
    return conn


@pytest.fixture
def pool_config():
    """Standard pool configuration for testing."""
    return {
        "uri": "ws://localhost:8000/rpc",
        "credentials": {"username": "root", "password": "root"},
        "namespace": "test",
        "database": "test",
        "min_connections": 2,
        "max_connections": 5,
        "max_idle_time": 300.0,
        "connection_timeout": 5.0,
        "acquisition_timeout": 10.0,
        "health_check_interval": 30.0,
        "max_usage_count": 1000,
        "connection_retry_attempts": 3,
        "connection_retry_delay": 0.1,  # Short for testing
        "reset_on_return": True,
        "log_queries": False,
    }


@pytest.fixture
async def mock_pool(pool_config, mock_surreal_connection):
    """Create a pool with mocked connection creation."""
    pool = SurrealDBConnectionPool(**pool_config)
    
    # Mock connection creation
    async def mock_create():
        return PooledConnection(connection=mock_surreal_connection)
    
    pool._create_connection = AsyncMock(side_effect=mock_create)
    
    await pool.initialize()
    
    yield pool
    
    # Cleanup
    if not pool._closed:
        await pool.close()


@pytest.fixture
def pooled_connection(mock_surreal_connection):
    """Create a pooled connection instance."""
    return PooledConnection(connection=mock_surreal_connection)


# ============================================================================
# PooledConnection Tests
# ============================================================================

class TestPooledConnection:
    """Test suite for PooledConnection wrapper class."""

    @pytest.mark.asyncio
    async def test_initial_state(self, pooled_connection):
        """Test initial state of pooled connection."""
        assert not pooled_connection.in_use
        assert pooled_connection.usage_count == 0
        assert pooled_connection.health_status == "healthy"
        assert pooled_connection.acquired_by_task is None
        assert pooled_connection.id.startswith("conn_")

    @pytest.mark.asyncio
    async def test_mark_as_used(self, pooled_connection):
        """Test marking connection as used increments counters."""
        initial_time = pooled_connection.last_used
        await asyncio.sleep(0.01)  # Small delay to ensure time difference
        
        pooled_connection.mark_as_used()
        
        assert pooled_connection.in_use
        assert pooled_connection.usage_count == 1
        assert pooled_connection.last_used > initial_time
        assert pooled_connection.acquired_by_task is not None

    @pytest.mark.asyncio
    async def test_mark_as_used_multiple_times(self, pooled_connection):
        """Test multiple mark_as_used calls increment usage count."""
        for i in range(1, 6):
            pooled_connection.mark_as_used()
            assert pooled_connection.usage_count == i

    @pytest.mark.asyncio
    async def test_mark_as_free(self, pooled_connection):
        """Test marking connection as free clears state."""
        pooled_connection.mark_as_used()
        pooled_connection.mark_as_free()
        
        assert not pooled_connection.in_use
        assert pooled_connection.acquired_by_task is None

    @pytest.mark.asyncio
    async def test_health_status_tracking(self, pooled_connection):
        """Test health status can be updated."""
        assert pooled_connection.health_status == "healthy"
        
        pooled_connection.health_status = "unhealthy"
        assert pooled_connection.health_status == "unhealthy"


# ============================================================================
# SurrealDBConnectionPool Tests
# ============================================================================

class TestConnectionPoolInitialization:
    """Test pool initialization and configuration."""

    @pytest.mark.asyncio
    async def test_pool_initialization(self, pool_config, mock_surreal_connection):
        """Test pool initializes with correct number of connections."""
        pool = SurrealDBConnectionPool(**pool_config)
        pool._create_connection = AsyncMock(
            return_value=PooledConnection(connection=mock_surreal_connection)
        )
        
        await pool.initialize()
        
        assert pool._initialized
        assert not pool._closed
        assert len(pool._pool) == pool_config["min_connections"]
        assert pool._maintenance_task is not None
        
        await pool.close()

    @pytest.mark.asyncio
    async def test_double_initialization(self, mock_pool):
        """Test pool can be safely initialized multiple times."""
        initial_pool_size = len(mock_pool._pool)
        await mock_pool.initialize()
        
        assert len(mock_pool._pool) == initial_pool_size  # No duplicate connections

    @pytest.mark.asyncio
    async def test_invalid_configuration(self):
        """Test pool raises errors for invalid configuration."""
        with pytest.raises(ValueError, match="min_connections must be positive"):
            SurrealDBConnectionPool(
                uri="ws://localhost:8000",
                credentials={},
                namespace="test",
                database="test",
                min_connections=0,
            )

        with pytest.raises(ValueError, match="max_connections cannot be less than min_connections"):
            SurrealDBConnectionPool(
                uri="ws://localhost:8000",
                credentials={},
                namespace="test",
                database="test",
                min_connections=10,
                max_connections=5,
            )

    @pytest.mark.asyncio
    async def test_context_manager(self, pool_config, mock_surreal_connection):
        """Test pool works as async context manager."""
        pool = SurrealDBConnectionPool(**pool_config)
        pool._create_connection = AsyncMock(
            return_value=PooledConnection(connection=mock_surreal_connection)
        )
        
        async with pool as p:
            assert p._initialized
            assert not p._closed
        
        assert p._closed


class TestConnectionAcquisition:
    """Test connection acquisition and release."""

    @pytest.mark.asyncio
    async def test_acquire_context_manager(self, mock_pool, mock_surreal_connection):
        """Test acquiring connection via async context manager."""
        async with mock_pool.acquire() as conn:
            assert conn is mock_surreal_connection
            # Verify at least one connection is in use
            assert any(c.in_use for c in mock_pool._pool)

        # After release, no connections should be in use
        assert not any(c.in_use for c in mock_pool._pool)

    @pytest.mark.asyncio
    async def test_concurrent_acquisitions(self, mock_pool):
        """Test multiple concurrent connection acquisitions."""
        async def acquire_and_hold():
            async with mock_pool.acquire() as conn:
                await asyncio.sleep(0.01)
                return conn

        # Acquire multiple connections concurrently
        results = await asyncio.gather(
            acquire_and_hold(),
            acquire_and_hold(),
            acquire_and_hold(),
        )

        assert len(results) == 3
        # All connections should be released
        assert not any(c.in_use for c in mock_pool._pool)

    @pytest.mark.asyncio
    async def test_acquisition_timeout(self, pool_config, mock_surreal_connection):
        """Test acquisition times out when pool is exhausted."""
        pool_config["min_connections"] = 1
        pool_config["max_connections"] = 1
        pool_config["acquisition_timeout"] = 0.5
        
        pool = SurrealDBConnectionPool(**pool_config)
        pool._create_connection = AsyncMock(
            return_value=PooledConnection(connection=mock_surreal_connection)
        )
        await pool.initialize()

        async def hold_connection():
            async with pool.acquire():
                await asyncio.sleep(2.0)  # Hold longer than timeout

        # Start task that holds the only connection
        task = asyncio.create_task(hold_connection())
        await asyncio.sleep(0.1)  # Let it acquire

        # This should timeout
        with pytest.raises(asyncio.TimeoutError):
            async with pool.acquire():
                pass

        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        
        await pool.close()

    @pytest.mark.asyncio
    async def test_acquire_on_closed_pool(self, mock_pool):
        """Test acquiring from closed pool raises error."""
        await mock_pool.close()
        
        with pytest.raises(RuntimeError, match="Connection pool is closed"):
            async with mock_pool.acquire():
                pass

    @pytest.mark.asyncio
    async def test_waiter_queue_operations(self, mock_pool):
        """Test waiter queue uses deque for O(1) operations."""
        assert isinstance(mock_pool._connection_waiters, deque)


class TestConnectionRelease:
    """Test connection release and cleanup."""

    @pytest.mark.asyncio
    async def test_connection_reset_on_release(self, mock_pool, mock_surreal_connection):
        """Test connection reset is skipped (workaround for blocking .use() issue)."""
        async with mock_pool.acquire() as conn:
            pass  # Just acquire and release

        # Verify use() was NOT called (reset disabled to prevent deadlock)
        # TODO: Re-enable when .use() blocking issue is fixed
        mock_surreal_connection.use.assert_not_called()

    @pytest.mark.asyncio
    async def test_unhealthy_connection_closed(self, mock_pool):
        """Test unhealthy connections are closed on release."""
        async with mock_pool.acquire() as conn:
            # Find the pooled connection and mark as unhealthy
            for pc in mock_pool._pool:
                if pc.in_use:
                    pc.health_status = "unhealthy"
                    break

        # Wait for async close task to complete
        await asyncio.sleep(0.1)
        
        # Connection should be removed from pool
        assert all(c.health_status == "healthy" for c in mock_pool._pool)

    @pytest.mark.asyncio
    async def test_max_usage_count_closes_connection(self, pool_config, mock_surreal_connection):
        """Test connection is closed when max usage count is reached."""
        pool_config["max_usage_count"] = 3
        
        pool = SurrealDBConnectionPool(**pool_config)
        pool._create_connection = AsyncMock(
            return_value=PooledConnection(connection=mock_surreal_connection)
        )
        await pool.initialize()

        # Use connection multiple times
        for _ in range(4):
            async with pool.acquire():
                pass

        await asyncio.sleep(0.1)  # Let cleanup happen

        # Verify connection was cycled
        assert mock_surreal_connection.close.called
        
        await pool.close()


class TestQueryExecution:
    """Test query execution through pool."""

    @pytest.mark.asyncio
    async def test_execute_query_success(self, mock_pool, mock_surreal_connection):
        """Test successful query execution."""
        result = await mock_pool.execute_query("SELECT * FROM users")
        
        assert result == [{"result": "success"}]
        mock_surreal_connection.query.assert_called_once()

    @pytest.mark.asyncio
    async def test_execute_query_with_params(self, mock_pool, mock_surreal_connection):
        """Test query execution with parameters."""
        params = {"id": "user:123"}
        await mock_pool.execute_query("SELECT * FROM $id", params)
        
        mock_surreal_connection.query.assert_called_once_with("SELECT * FROM $id", params)

    @pytest.mark.asyncio
    async def test_execute_query_error_handling(self, mock_pool, mock_surreal_connection):
        """Test query execution handles errors properly."""
        mock_surreal_connection.query.side_effect = Exception("Query failed")
        
        with pytest.raises(Exception, match="Query failed"):
            await mock_pool.execute_query("INVALID QUERY")


class TestPoolStatistics:
    """Test pool statistics tracking."""

    @pytest.mark.asyncio
    async def test_get_stats_structure(self, mock_pool):
        """Test stats contain all expected keys."""
        stats = await mock_pool.get_stats()
        
        expected_keys = {
            "total_connections_created",
            "total_connections_closed",
            "total_acquisitions",
            "total_releases",
            "acquisition_timeouts",
            "connection_errors",
            "health_check_failures",
            "peak_connections",
            "peak_waiters",
            "current_connections",
            "available_connections",
            "in_use_connections",
            "connection_waiters",
        }
        
        assert set(stats.keys()) == expected_keys

    @pytest.mark.asyncio
    async def test_stats_track_acquisitions(self, mock_pool):
        """Test stats track acquisition counts."""
        initial_stats = await mock_pool.get_stats()
        initial_acquisitions = initial_stats["total_acquisitions"]
        
        async with mock_pool.acquire():
            pass
        
        final_stats = await mock_pool.get_stats()
        assert final_stats["total_acquisitions"] == initial_acquisitions + 1
        assert final_stats["total_releases"] == initial_stats["total_releases"] + 1

    @pytest.mark.asyncio
    async def test_stats_track_peak_values(self, mock_pool):
        """Test stats track peak connections and waiters."""
        stats = await mock_pool.get_stats()
        
        assert stats["peak_connections"] >= stats["current_connections"]
        assert stats["peak_waiters"] >= stats["connection_waiters"]


class TestPoolClosure:
    """Test pool shutdown and cleanup."""

    @pytest.mark.asyncio
    async def test_close_pool(self, mock_pool):
        """Test pool closes cleanly."""
        await mock_pool.close()
        
        assert mock_pool._closed
        assert not mock_pool._initialized
        assert len(mock_pool._pool) == 0

    @pytest.mark.asyncio
    async def test_double_close(self, mock_pool):
        """Test closing pool multiple times is safe."""
        await mock_pool.close()
        await mock_pool.close()  # Should not raise

    @pytest.mark.asyncio
    async def test_close_cancels_waiters(self, pool_config, mock_surreal_connection):
        """Test closing pool cancels pending waiters."""
        pool_config["min_connections"] = 1
        pool_config["max_connections"] = 1
        
        pool = SurrealDBConnectionPool(**pool_config)
        pool._create_connection = AsyncMock(
            return_value=PooledConnection(connection=mock_surreal_connection)
        )
        await pool.initialize()

        async def try_acquire():
            try:
                async with pool.acquire():
                    await asyncio.sleep(10)
            except (RuntimeError, asyncio.TimeoutError):
                pass  # Expected when pool closes or times out

        # Hold the connection
        task1 = asyncio.create_task(try_acquire())
        await asyncio.sleep(0.1)
        
        # Try to acquire (will wait)
        task2 = asyncio.create_task(try_acquire())
        await asyncio.sleep(0.1)
        
        # Close pool
        await pool.close()
        
        # Cleanup tasks
        for task in [task1, task2]:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, RuntimeError):
                pass


# ============================================================================
# SurrealDBPoolManager Tests
# ============================================================================

class TestPoolManager:
    """Test pool manager singleton."""

    @pytest.mark.asyncio
    async def test_singleton_pattern(self):
        """Test pool manager follows singleton pattern."""
        manager1 = SurrealDBPoolManager()
        manager2 = SurrealDBPoolManager()
        
        assert manager1 is manager2

    @pytest.mark.asyncio
    async def test_create_pool(self, pool_config, mock_surreal_connection):
        """Test creating a named pool."""
        manager = SurrealDBPoolManager()
        
        # Mock the pool's create connection
        with patch.object(SurrealDBConnectionPool, '_create_connection',
                         return_value=PooledConnection(connection=mock_surreal_connection)):
            pool = await manager.create_pool("test_pool", **pool_config)
        
        assert isinstance(pool, SurrealDBConnectionPool)
        assert pool._initialized
        
        await manager.close_all_pools()

    @pytest.mark.asyncio
    async def test_get_pool(self, pool_config, mock_surreal_connection):
        """Test retrieving existing pool."""
        manager = SurrealDBPoolManager()
        manager._pools.clear()  # Clean slate
        
        with patch.object(SurrealDBConnectionPool, '_create_connection',
                         return_value=PooledConnection(connection=mock_surreal_connection)):
            pool1 = await manager.create_pool("test_pool", **pool_config)
            pool2 = manager.get_pool("test_pool")
        
        assert pool1 is pool2
        
        await manager.close_all_pools()

    @pytest.mark.asyncio
    async def test_get_nonexistent_pool(self):
        """Test getting non-existent pool raises error."""
        manager = SurrealDBPoolManager()
        
        with pytest.raises(KeyError, match="does not exist"):
            manager.get_pool("nonexistent_pool")

    @pytest.mark.asyncio
    async def test_create_duplicate_pool(self, pool_config, mock_surreal_connection):
        """Test creating duplicate pool raises error."""
        manager = SurrealDBPoolManager()
        manager._pools.clear()
        
        with patch.object(SurrealDBConnectionPool, '_create_connection',
                         return_value=PooledConnection(connection=mock_surreal_connection)):
            await manager.create_pool("test_pool", **pool_config)
            
            with pytest.raises(ValueError, match="already exists"):
                await manager.create_pool("test_pool", **pool_config)
        
        await manager.close_all_pools()

    @pytest.mark.asyncio
    async def test_close_all_pools(self, pool_config, mock_surreal_connection):
        """Test closing all managed pools."""
        manager = SurrealDBPoolManager()
        manager._pools.clear()
        
        with patch.object(SurrealDBConnectionPool, '_create_connection',
                         return_value=PooledConnection(connection=mock_surreal_connection)):
            await manager.create_pool("pool1", **pool_config)
            await manager.create_pool("pool2", **pool_config)
        
        await manager.close_all_pools()
        
        assert len(manager._pools) == 0


# ============================================================================
# Performance and Stress Tests
# ============================================================================

class TestPoolPerformance:
    """Test pool performance characteristics."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("concurrent_tasks", [10, 50, 100])
    async def test_concurrent_load(self, pool_config, mock_surreal_connection, concurrent_tasks):
        """Test pool handles concurrent load efficiently."""
        pool_config["max_connections"] = 20
        
        pool = SurrealDBConnectionPool(**pool_config)
        pool._create_connection = AsyncMock(
            return_value=PooledConnection(connection=mock_surreal_connection)
        )
        await pool.initialize()

        async def task():
            async with pool.acquire() as conn:
                await conn.query("SELECT 1")

        start = time.monotonic()
        await asyncio.gather(*[task() for _ in range(concurrent_tasks)])
        duration = time.monotonic() - start

        stats = await pool.get_stats()
        assert stats["total_acquisitions"] >= concurrent_tasks
        assert duration < concurrent_tasks * 0.1  # Should be much faster than serial

        await pool.close()

    @pytest.mark.asyncio
    async def test_waiter_queue_efficiency(self, mock_pool):
        """Test waiter queue operations are O(1)."""
        # Simulate many waiters
        for _ in range(1000):
            waiter = asyncio.Future()
            mock_pool._connection_waiters.append(waiter)

        # popleft should be instant
        start = time.monotonic()
        mock_pool._connection_waiters.popleft()
        duration = time.monotonic() - start

        assert duration < 0.001  # Should be near-instant


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
