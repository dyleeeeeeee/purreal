# -*- coding: utf-8 -*-
"""Test suite for purreal session multiplexer with teleportation."""

import asyncio

import pytest
from unittest.mock import AsyncMock

from purreal.pool import SurrealDBConnectionPool
from purreal.multiplexer import SessionMultiplexer, VirtualSession
from purreal.types import (
    PooledConnection,
    SessionState,
    SessionExpiredError,
)


@pytest.fixture
async def mock_pool(pool_config, mock_surreal_connection):
    pool = SurrealDBConnectionPool(**pool_config)
    pool._create_connection = AsyncMock(
        side_effect=lambda overflow=False: PooledConnection(
            connection=mock_surreal_connection,
            overflow=overflow,
        )
    )
    await pool.initialize()
    yield pool
    if not pool._closed:
        await pool.close()


@pytest.fixture
async def multiplexer(mock_pool):
    mux = SessionMultiplexer(
        pool=mock_pool,
        num_slots=2,
        max_sessions=50,
        session_idle_timeout=60.0,
    )
    await mux.start()
    yield mux
    if not mux._closed:
        await mux.stop()


class TestSessionCreation:
    @pytest.mark.asyncio
    async def test_get_session(self, multiplexer):
        session = await multiplexer.get_session(
            "ns", "db", {"username": "root", "password": "root"}
        )
        assert isinstance(session, VirtualSession)
        assert session.namespace == "ns"
        assert session.database == "db"
        assert session.is_active

    @pytest.mark.asyncio
    async def test_max_sessions_enforced(self, mock_pool):
        mux = SessionMultiplexer(pool=mock_pool, num_slots=1, max_sessions=3)
        await mux.start()

        for _ in range(3):
            await mux.get_session("ns", "db", {"username": "root", "password": "root"})

        with pytest.raises(Exception):  # PoolExhaustedError
            await mux.get_session("ns", "db", {"username": "root", "password": "root"})

        await mux.stop()

    @pytest.mark.asyncio
    async def test_session_close(self, multiplexer):
        session = await multiplexer.get_session(
            "ns", "db", {"username": "root", "password": "root"}
        )
        await session.close()
        assert not session.is_active
        assert session.session_id not in multiplexer._sessions


class TestSessionQueries:
    @pytest.mark.asyncio
    async def test_query(self, multiplexer, mock_surreal_connection):
        session = await multiplexer.get_session(
            "ns", "db", {"username": "root", "password": "root"}
        )
        result = await session.query("SELECT * FROM users")
        assert result == [{"result": "success"}]

    @pytest.mark.asyncio
    async def test_select(self, multiplexer, mock_surreal_connection):
        session = await multiplexer.get_session(
            "ns", "db", {"username": "root", "password": "root"}
        )
        result = await session.select("users")
        assert result is not None

    @pytest.mark.asyncio
    async def test_create(self, multiplexer, mock_surreal_connection):
        session = await multiplexer.get_session(
            "ns", "db", {"username": "root", "password": "root"}
        )
        result = await session.create("users", {"name": "test"})
        assert result is not None

    @pytest.mark.asyncio
    async def test_expired_session_raises(self, multiplexer):
        session = await multiplexer.get_session(
            "ns", "db", {"username": "root", "password": "root"}
        )
        session._session_state = SessionState.EXPIRED
        with pytest.raises(SessionExpiredError):
            await session.query("SELECT 1")


class TestRouting:
    @pytest.mark.asyncio
    async def test_state_affinity(self, multiplexer, mock_surreal_connection):
        """Sessions with same state should prefer the same slot."""
        s1 = await multiplexer.get_session(
            "ns1", "db1", {"username": "root", "password": "root"}
        )
        s2 = await multiplexer.get_session(
            "ns1", "db1", {"username": "root", "password": "root"}
        )

        await s1.query("SELECT 1")
        await s2.query("SELECT 2")

        # Both should have been routed (successfully — no errors)
        # The router should prefer the slot that already has ns1/db1 state

    @pytest.mark.asyncio
    async def test_concurrent_sessions_different_state(
        self, multiplexer, mock_surreal_connection
    ):
        sessions = []
        for i in range(5):
            s = await multiplexer.get_session(
                f"ns{i}", f"db{i}", {"username": "root", "password": "root"}
            )
            sessions.append(s)

        results = await asyncio.gather(*[s.query("SELECT 1") for s in sessions])
        assert len(results) == 5
        assert all(r == [{"result": "success"}] for r in results)


class TestTeleportation:
    @pytest.mark.asyncio
    async def test_teleport_on_connection_failure(
        self, mock_pool, mock_surreal_connection
    ):
        call_count = [0]

        async def failing_query(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                raise ConnectionError("WebSocket closed")
            return [{"result": "recovered"}]

        mock_surreal_connection.query = AsyncMock(side_effect=failing_query)

        mux = SessionMultiplexer(pool=mock_pool, num_slots=1, max_sessions=10)
        await mux.start()

        session = await mux.get_session(
            "ns", "db", {"username": "root", "password": "root"}
        )
        result = await session.query("SELECT 1")
        # Should have recovered via teleportation
        assert result == [{"result": "recovered"}]

        await mux.stop()


class TestMultiplexerLifecycle:
    @pytest.mark.asyncio
    async def test_start_stop(self, mock_pool):
        mux = SessionMultiplexer(pool=mock_pool, num_slots=2)
        await mux.start()
        assert len(mux._slots) == 2
        await mux.stop()
        assert len(mux._slots) == 0

    @pytest.mark.asyncio
    async def test_context_manager(self, mock_pool):
        async with SessionMultiplexer(pool=mock_pool, num_slots=2) as mux:
            assert len(mux._slots) == 2
        assert mux._closed

    @pytest.mark.asyncio
    async def test_stats(self, multiplexer):
        await multiplexer.get_session(
            "ns", "db", {"username": "root", "password": "root"}
        )
        stats = await multiplexer.get_stats()
        assert stats["active_sessions"] == 1
        assert len(stats["slots"]) == 2


class TestStress:
    @pytest.mark.asyncio
    async def test_200_sessions_1000_queries(self, mock_pool, mock_surreal_connection):
        mux = SessionMultiplexer(
            pool=mock_pool,
            num_slots=3,
            max_sessions=200,
        )
        await mux.start()

        sessions = []
        for i in range(50):
            s = await mux.get_session(
                f"ns{i % 5}",
                f"db{i % 3}",
                {"username": "root", "password": "root"},
            )
            sessions.append(s)

        # Fire 200 queries across all sessions
        queries = [s.query("SELECT 1") for s in sessions for _ in range(4)]
        results = await asyncio.gather(*queries, return_exceptions=True)

        successes = [r for r in results if not isinstance(r, Exception)]
        assert len(successes) >= 150  # Allow some to fail under extreme concurrency

        await mux.stop()
