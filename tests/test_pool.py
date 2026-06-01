# -*- coding: utf-8 -*-
"""Test suite for purreal adaptive connection pool."""

import asyncio
import time

import pytest
from unittest.mock import AsyncMock, patch

from purreal.pool import SurrealDBConnectionPool, SurrealDBPoolManager
from purreal.types import PooledConnection, PoolConfig, PoolExhaustedError, PoolPausedError


@pytest.fixture
async def mock_pool(pool_config, mock_surreal_connection):
	pool = SurrealDBConnectionPool(**pool_config)
	pool._create_connection = AsyncMock(
		side_effect=lambda overflow=False: PooledConnection(
			connection=mock_surreal_connection, overflow=overflow,
		)
	)
	await pool.initialize()
	yield pool
	if not pool._closed:
		await pool.close()


class TestPoolInitialization:
	@pytest.mark.asyncio
	async def test_pool_initializes(self, mock_pool):
		assert mock_pool._initialized
		assert not mock_pool._closed
		assert len(mock_pool._all_connections) >= 1

	@pytest.mark.asyncio
	async def test_double_init(self, mock_pool):
		size = len(mock_pool._all_connections)
		await mock_pool.initialize()
		assert len(mock_pool._all_connections) == size

	@pytest.mark.asyncio
	async def test_context_manager(self, pool_config, mock_surreal_connection):
		pool = SurrealDBConnectionPool(**pool_config)
		pool._create_connection = AsyncMock(
			side_effect=lambda overflow=False: PooledConnection(
				connection=mock_surreal_connection, overflow=overflow,
			)
		)
		async with pool as p:
			assert p._initialized
		assert p._closed

	@pytest.mark.asyncio
	async def test_invalid_config(self):
		with pytest.raises((ValueError, TypeError)):
			SurrealDBConnectionPool(
				uri="ws://localhost",
				credentials={},
				namespace="test",
				database="test",
				pool_size=0,
				max_overflow=-1,
			)


class TestConnectionAcquisition:
	@pytest.mark.asyncio
	async def test_acquire_returns_connection(self, mock_pool, mock_surreal_connection):
		async with mock_pool.acquire() as conn:
			assert conn is mock_surreal_connection

	@pytest.mark.asyncio
	async def test_acquire_releases_back(self, mock_pool):
		async with mock_pool.acquire():
			pass
		stats = await mock_pool.get_stats()
		assert stats["total_releases"] >= 1

	@pytest.mark.asyncio
	async def test_concurrent_acquisitions(self, mock_pool):
		async def acquire_and_hold():
			async with mock_pool.acquire() as conn:
				await asyncio.sleep(0.01)
				return conn

		results = await asyncio.gather(*[acquire_and_hold() for _ in range(5)])
		assert len(results) == 5

	@pytest.mark.asyncio
	async def test_acquisition_timeout(self, pool_config, mock_surreal_connection):
		pool_config["pool_size"] = 1
		pool_config["max_overflow"] = 0
		pool_config["acquisition_timeout"] = 0.3
		pool = SurrealDBConnectionPool(**pool_config)
		pool._create_connection = AsyncMock(
			side_effect=lambda overflow=False: PooledConnection(
				connection=mock_surreal_connection, overflow=overflow,
			)
		)
		await pool.initialize()

		async with pool.acquire():
			with pytest.raises(asyncio.TimeoutError):
				async with pool.acquire():
					pass
		await pool.close()

	@pytest.mark.asyncio
	async def test_acquire_on_closed_pool(self, mock_pool):
		await mock_pool.close()
		with pytest.raises(RuntimeError, match="closed"):
			async with mock_pool.acquire():
				pass

	@pytest.mark.asyncio
	async def test_bounded_waiters(self, pool_config, mock_surreal_connection):
		pool_config["pool_size"] = 1
		pool_config["max_overflow"] = 0
		pool_config["max_waiters"] = 2
		pool_config["acquisition_timeout"] = 0.5
		pool = SurrealDBConnectionPool(**pool_config)
		pool._create_connection = AsyncMock(
			side_effect=lambda overflow=False: PooledConnection(
				connection=mock_surreal_connection, overflow=overflow,
			)
		)
		await pool.initialize()

		# Hold the only connection
		async with pool.acquire():
			# Fill up waiters (they'll timeout but we test exhaustion)
			# Since pool has 1 conn held + max_waiters=2, third waiter should get exhausted
			tasks = []
			for _ in range(3):
				async def try_acquire():
					try:
						async with pool.acquire():
							await asyncio.sleep(5)
					except (asyncio.TimeoutError, PoolExhaustedError):
						return "rejected"
					return "acquired"
				tasks.append(asyncio.create_task(try_acquire()))

			await asyncio.sleep(0.1)
			# At least one should be rejected
			for t in tasks:
				t.cancel()
				try:
					await t
				except asyncio.CancelledError:
					pass
		await pool.close()


class TestOverflow:
	@pytest.mark.asyncio
	async def test_overflow_connections_created(self, pool_config, mock_surreal_connection):
		pool_config["pool_size"] = 2
		pool_config["max_overflow"] = 3
		pool = SurrealDBConnectionPool(**pool_config)
		pool._create_connection = AsyncMock(
			side_effect=lambda overflow=False: PooledConnection(
				connection=mock_surreal_connection, overflow=overflow,
			)
		)
		await pool.initialize()

		# Acquire more than pool_size
		conns = []
		for _ in range(4):
			conn = await pool.checkout()
			conns.append(conn)

		# Should have overflow connections
		overflow_count = sum(1 for c in conns if c.overflow)
		assert overflow_count >= 1

		for conn in conns:
			await pool.checkin(conn)
		await pool.close()


class TestPrePing:
	@pytest.mark.asyncio
	async def test_pre_ping_validates_stale(self, pool_config, mock_surreal_connection):
		pool_config["pre_ping"] = True
		pool_config["pre_ping_bypass_window"] = 0.0  # Always validate
		pool = SurrealDBConnectionPool(**pool_config)
		pool._create_connection = AsyncMock(
			side_effect=lambda overflow=False: PooledConnection(
				connection=mock_surreal_connection, overflow=overflow,
			)
		)
		await pool.initialize()

		# First acquire should trigger validation
		async with pool.acquire():
			pass

		assert mock_surreal_connection.query.called
		await pool.close()


class TestMaxLifetime:
	@pytest.mark.asyncio
	async def test_expired_connections_recycled(self, pool_config, mock_surreal_connection):
		pool_config["max_lifetime"] = 0.1  # Very short
		pool = SurrealDBConnectionPool(**pool_config)
		pool._create_connection = AsyncMock(
			side_effect=lambda overflow=False: PooledConnection(
				connection=mock_surreal_connection,
				max_lifetime=0.1,
				overflow=overflow,
			)
		)
		await pool.initialize()
		initial_calls = pool._create_connection.call_count

		await asyncio.sleep(0.15)  # Let connections expire

		# Acquiring should trigger new connection creation (old ones expired)
		async with pool.acquire():
			pass

		assert pool._create_connection.call_count > initial_calls
		await pool.close()


class TestGracefulDrain:
	@pytest.mark.asyncio
	async def test_pause_blocks_acquire(self, mock_pool):
		mock_pool.pause()
		with pytest.raises(PoolPausedError):
			async with mock_pool.acquire():
				pass

	@pytest.mark.asyncio
	async def test_resume_unblocks(self, mock_pool):
		mock_pool.pause()
		mock_pool.resume()
		async with mock_pool.acquire():
			pass

	@pytest.mark.asyncio
	async def test_drain_waits_for_in_flight(self, mock_pool):
		released = asyncio.Event()

		async def hold_and_release():
			async with mock_pool.acquire():
				await asyncio.sleep(0.2)
			released.set()

		task = asyncio.create_task(hold_and_release())
		await asyncio.sleep(0.05)
		await mock_pool.drain(timeout=5.0)
		assert released.is_set()
		task.cancel()
		try:
			await task
		except asyncio.CancelledError:
			pass


class TestQueryExecution:
	@pytest.mark.asyncio
	async def test_execute_query(self, mock_pool, mock_surreal_connection):
		result = await mock_pool.execute_query("SELECT * FROM users")
		assert result == [{"result": "success"}]

	@pytest.mark.asyncio
	async def test_execute_query_with_params(self, mock_pool, mock_surreal_connection):
		await mock_pool.execute_query("SELECT * FROM $id", {"id": "user:1"})
		mock_surreal_connection.query.assert_called_with("SELECT * FROM $id", {"id": "user:1"})


class TestPoolStats:
	@pytest.mark.asyncio
	async def test_stats_structure(self, mock_pool):
		stats = await mock_pool.get_stats()
		assert "total_acquisitions" in stats
		assert "p95_acquire_ms" in stats
		assert "total_pre_warms" in stats
		assert "overflow_connections" in stats

	@pytest.mark.asyncio
	async def test_stats_track_acquisitions(self, mock_pool):
		async with mock_pool.acquire():
			pass
		stats = await mock_pool.get_stats()
		assert stats["total_acquisitions"] >= 1
		assert stats["total_releases"] >= 1


class TestPoolClosure:
	@pytest.mark.asyncio
	async def test_close(self, mock_pool):
		await mock_pool.close()
		assert mock_pool._closed

	@pytest.mark.asyncio
	async def test_double_close(self, mock_pool):
		await mock_pool.close()
		await mock_pool.close()


class TestPoolManager:
	@pytest.mark.asyncio
	async def test_singleton(self):
		m1 = SurrealDBPoolManager()
		m2 = SurrealDBPoolManager()
		assert m1 is m2

	@pytest.mark.asyncio
	async def test_create_and_get(self, pool_config, mock_surreal_connection):
		manager = SurrealDBPoolManager()
		manager._pools.clear()

		with patch.object(SurrealDBConnectionPool, '_create_connection',
			side_effect=lambda self, overflow=False: PooledConnection(
				connection=mock_surreal_connection, overflow=overflow
			)):
			pool = await manager.create_pool("test_mgr_pool", **pool_config)
			assert manager.get_pool("test_mgr_pool") is pool
		await manager.close_all_pools()

	@pytest.mark.asyncio
	async def test_get_nonexistent(self):
		manager = SurrealDBPoolManager()
		with pytest.raises(KeyError):
			manager.get_pool("nonexistent")

	@pytest.mark.asyncio
	async def test_duplicate_pool(self, pool_config, mock_surreal_connection):
		manager = SurrealDBPoolManager()
		manager._pools.clear()

		with patch.object(SurrealDBConnectionPool, '_create_connection',
			side_effect=lambda self, overflow=False: PooledConnection(
				connection=mock_surreal_connection, overflow=overflow
			)):
			await manager.create_pool("dup_test", **pool_config)
			with pytest.raises(ValueError):
				await manager.create_pool("dup_test", **pool_config)
		await manager.close_all_pools()


class TestPerformance:
	@pytest.mark.asyncio
	@pytest.mark.parametrize("concurrent_tasks", [10, 50, 100])
	async def test_concurrent_load(self, pool_config, mock_surreal_connection, concurrent_tasks):
		pool_config["pool_size"] = 10
		pool_config["max_overflow"] = 20
		pool = SurrealDBConnectionPool(**pool_config)
		pool._create_connection = AsyncMock(
			side_effect=lambda overflow=False: PooledConnection(
				connection=mock_surreal_connection, overflow=overflow,
			)
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
		assert duration < concurrent_tasks * 0.05
		await pool.close()
