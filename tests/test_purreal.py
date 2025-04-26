import unittest
from unittest.mock import AsyncMock, MagicMock
import asyncio
from purreal.src.pooler import SurrealDBConnectionPool


class TestSurrealDBConnectionPool(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.mock_connection = MagicMock()
        self.mock_connection.connection = MagicMock()
        self.mock_connection.connection.use = AsyncMock()
        self.mock_connection.connection.close = AsyncMock()  # Fix for 'await' issue
        self.mock_connection.mark_as_used = MagicMock()
        self.mock_connection.mark_as_unused = MagicMock()
        self.mock_connection.health_status = "healthy"
        self.mock_connection.in_use = False
        self.mock_connection.id = "mock_connection_id"
        self.mock_connection.usage_count = 0  # Fix for TypeError

        self.pool = SurrealDBConnectionPool(
            uri="ws://localhost:800/rpc",
            credentials={"username": "root", "password": "root"},
            namespace="test",
            database="test",
            min_connections=2,
            max_connections=5,
        )
        self.pool._create_connection = AsyncMock(return_value=self.mock_connection)
        self.pool._connection_waiters = []
        self.pool._pool = [self.mock_connection]  # Fix for ValueError

    async def test_release_connection(self):
        """Test releasing a connection back to the pool."""
        conn = self.mock_connection
        conn.in_use = True

        await self.pool._release_connection(conn)
        self.assertFalse(conn.in_use)
        conn.mark_as_unused.assert_called_once()

    async def test_release_unhealthy_connection(self):
        """Test releasing an unhealthy connection."""
        conn = self.mock_connection
        conn.health_status = "unhealthy"

        await self.pool._release_connection(conn)
        self.assertNotIn(conn, self.pool._pool)

    async def test_handle_error_while_waiting_for_connection(self):
        """Test handling errors while waiting for a connection."""
        waiter = asyncio.Future()
        self.pool._connection_waiters.append(waiter)

        # Simulate an error while waiting
        waiter.set_exception(Exception("Test error"))

        with self.assertRaises(Exception) as context:
            await self.pool._acquire_connection()

        self.assertEqual(str(context.exception), "Test error")
        self.assertNotIn(waiter, self.pool._connection_waiters)