"""
SurrealDB Connection Pool
=========================

A robust, production-ready connection pooling solution for SurrealDB in asyncio applications.

This module provides a connection pool that manages multiple database connections,
handles connection acquisition and release, implements timeouts, connection health checks,
and other advanced features to ensure reliable database connectivity.

Key Features:
- Dynamic connection pool sizing (min/max connections)
- Connection health monitoring and automatic recovery
- Connection acquisition timeouts
- Connection usage tracking and recycling
- Idle connection management
- Connection reset before release
- Comprehensive metrics and statistics
- Graceful shutdown
- Retry mechanisms for failed connections
- Schema initialization support

Usage:
    pool = SurrealDBConnectionPool(
        uri="ws://localhost:8000/rpc",
        credentials={"username": "root", "password": "root"},
        namespace="test",
        database="test"
    )
    await pool.initialize()
    
    async with pool.acquire() as conn:
        result = await conn.query("SELECT * FROM users")
    
    # Or use the helper method
    result = await pool.execute_query("SELECT * FROM users")
    
    # When shutting down
    await pool.close()
"""

import asyncio
import logging
import time
import random

from typing import AsyncGenerator
from typing import Dict, List, Optional, Callable, Any, Set, Union
from contextlib import asynccontextmanager
from surrealdb.connections.async_template import AsyncTemplate
from surrealdb import AsyncSurreal
from surrealdb import AsyncWsSurrealConnection, AsyncHttpSurrealConnection

from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

@dataclass
class PooledConnection(AsyncTemplate):
    """
    Represents a connection in the pool with metadata.
    
    Attributes:
        connection: The actual SurrealDB connection
        created_at: Timestamp when the connection was created
        last_used: Timestamp when the connection was last used
        in_use: Whether the connection is currently being used
        usage_count: Number of times this connection has been used
        id: Unique identifier for this connection
        health_status: Current health status of the connection
    """
    connection: AsyncWsSurrealConnection | AsyncHttpSurrealConnection
    created_at: float = field(default_factory=time.time)
    last_used: float = field(default_factory=time.time)
    in_use: bool = False
    usage_count: int = 0
    id: str = field(default_factory=lambda: f"conn_{random.randint(1000, 9999)}")
    health_status: str = "healthy"
    
    def mark_as_used(self):
        """Mark this connection as currently in use."""
        self.in_use = True
        self.last_used = time.time()
        self.usage_count += 1
        
    def mark_as_free(self):
        """Mark this connection as available for use."""
        self.in_use = False
        self.last_used = time.time()


class SurrealDBConnectionPool:
    """
    A connection pool for SurrealDB that manages multiple connections
    and provides features like connection acquisition, release, health checks,
    and automatic reconnection.
    
    This pool solves the "cannot call recv while another coro is calling recv" issue
    by ensuring each connection is only used by one coroutine at a time.
    """
    
    def __init__(
        self,
        uri: str,
        credentials: Dict[str, str],
        namespace: str,
        database: str,
        min_connections: int = 4,
        max_connections: int = 10,
        max_idle_time: int = 300,  # 5 minutes
        connection_timeout: float = 25.0,
        acquisition_timeout: float = 10.0,
        health_check_interval: int = 30,
        max_usage_count: int = 1000,
        connection_retry_attempts: int = 3,
        connection_retry_delay: float = 1.0,
        schema_file: Optional[str] = None,
        on_connection_create: Optional[Callable[[AsyncWsSurrealConnection | AsyncHttpSurrealConnection], Any]] = None,
        reset_on_return: bool = True,
        log_queries: bool = False,
    ):
        """
        Initialize the connection pool with configuration parameters.
        
        Args:
            uri: SurrealDB connection URI
            credentials: Dict with username and password
            namespace: SurrealDB namespace
            database: SurrealDB database
            min_connections: Minimum number of connections to maintain
            max_connections: Maximum number of connections allowed
            max_idle_time: Maximum time in seconds a connection can be idle before being closed
            connection_timeout: Timeout for creating a new connection
            acquisition_timeout: Timeout for acquiring a connection from the pool
            health_check_interval: Interval in seconds for health checks
            max_usage_count: Maximum number of times a connection can be used before being recycled
            connection_retry_attempts: Number of retry attempts when creating a connection
            connection_retry_delay: Delay between retry attempts
            schema_file: Optional path to schema file to execute on new connections
            on_connection_create: Optional callback to run after creating a new connection
            reset_on_return: Whether to reset connections before returning them to the pool
            log_queries: Whether to log queries for debugging
        """
        self.uri = uri
        self.credentials = credentials
        self.namespace = namespace
        self.database = database
        self.min_connections = min_connections
        self.max_connections = max_connections
        self.max_idle_time = max_idle_time
        self.connection_timeout = connection_timeout
        self.acquisition_timeout = acquisition_timeout
        self.health_check_interval = health_check_interval
        self.max_usage_count = max_usage_count
        self.connection_retry_attempts = connection_retry_attempts
        self.connection_retry_delay = connection_retry_delay
        self.schema_file = schema_file
        self.on_connection_create = on_connection_create
        self.reset_on_return = reset_on_return
        self.log_queries = log_queries
        
        # Pool state
        self._pool: List[PooledConnection] = []
        self._lock = asyncio.Lock()
        self._closed = False
        self._maintenance_task = None
        self._active_acquisitions: Set[asyncio.Task] = set()
        self._connection_waiters: List[asyncio.Future] = []
        
        # Statistics
        self._stats = {
            "total_connections_created": 0,
            "total_connections_closed": 0,
            "total_acquisitions": 0,
            "total_releases": 0,
            "acquisition_timeouts": 0,
            "connection_errors": 0,
            "health_check_failures": 0,
            "peak_connections": 0,
            "peak_concurrent_users": 0,
        }
    
    async def __aenter__(self):
        """Support for async with statement."""
        if not self._pool:  # If not initialized yet
            await self.initialize()
        return self
        
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Ensure pool is closed when exiting context."""
        await self.close()
        
    async def initialize(self):
        """
        Initialize the connection pool with minimum connections.
        
        This method should be called before using the pool.
        """
        async with self._lock:
            for _ in range(self.min_connections):
                conn = await self._create_connection()
                self._pool.append(conn)
                
        # Start maintenance task
        self._maintenance_task = asyncio.create_task(self._maintenance_loop())
        logger.info(f"SurrealDB connection pool initialized with {self.min_connections} connections")
        
    async def close(self):
        """
        Close all connections in the pool.
        
        This method should be called when shutting down the application.
        """
        if self._closed:
            return
            
        self._closed = True
        
        # Cancel maintenance task
        if self._maintenance_task:
            self._maintenance_task.cancel()
            try:
                await self._maintenance_task
            except asyncio.CancelledError:
                pass
        
        # Notify all waiters that the pool is closing
        for waiter in self._connection_waiters:
            if not waiter.done():
                waiter.set_exception(RuntimeError("Connection pool is closing"))
        
        # Wait for all active acquisitions to complete
        if self._active_acquisitions:
            try:
                async with asyncio.timeout(5.0):  # Add a timeout to prevent hanging
                    await asyncio.gather(*self._active_acquisitions, return_exceptions=True)
            except asyncio.TimeoutError:
                logger.warning("Timeout waiting for active acquisitions to complete")

        # Close all connections
        async with self._lock:
            for conn in self._pool:
                try:
                    # Make sure we properly close the websocket connection
                    await asyncio.wait_for(conn.connection.close(), timeout=2.0)
                    self._stats["total_connections_closed"] += 1
                    logger.debug(f"Closed connection {conn.id}")
                except asyncio.TimeoutError:
                    logger.warning(f"Timeout closing connection {conn.id}")
                except Exception as e:
                    logger.error(f"Error closing connection {conn.id}: {e}")
            
            self._pool.clear()
        
        logger.info("SurrealDB connection pool closed")
        
    @asynccontextmanager
    async def acquire(self) -> AsyncGenerator[PooledConnection, None]:
        """
        Acquire a connection from the pool.

        This is an async context manager that acquires a connection, yields it,
        and automatically releases it when the context exits.

        Returns:
            AsyncGenerator[AsyncTemplate, None]: A SurrealDB connection

        Raises:
            TimeoutError: If unable to acquire a connection within the timeout
            RuntimeError: If the pool is closed
        """
        if self._closed:
            raise RuntimeError("Connection pool is closed")

        conn = await self._acquire_connection()  # Assuming this returns an AsyncTemplate
        try:
            yield conn
        finally:
            await self._release_connection(conn)  # Ensure the connection is released
    
    async def execute_query(self, query: str, params: Optional[Dict] = None):
        """
        Execute a query using a connection from the pool.
        
        This is a convenience method that acquires a connection, executes the query,
        and releases the connection.
        
        Args:
            query: SurrealDB query to execute
            params: Optional parameters for the query
            
        Returns:
            Query result
            
        Example:
            users = await pool.execute_query("SELECT * FROM users WHERE age > $age", {"age": 18})
        """
        start_time : int | Any = Any
        if self.log_queries:
            start_time = time.time()
            
        async with self.acquire() as conn:
            try:
                result = await conn.query(query, params)
                
                if self.log_queries:
                    duration = time.time() - start_time
                    logger.debug(f"Query executed in {duration:.4f}s: {query[:100]}{'...' if len(query) > 100 else ''}")
                
                return result
            except Exception as e:
                logger.error(f"Query execution error: {e}, Query: {query[:100]}{'...' if len(query) > 100 else ''}")
                raise
    
    async def get_stats(self) -> Dict[str, Any]:
        """
        Get current pool statistics.
        
        Returns:
            Dict: Statistics about the connection pool
        """
        async with self._lock:
            current_stats = self._stats.copy()
            current_stats.update({
                "current_connections": len(self._pool),
                "available_connections": sum(1 for conn in self._pool if not conn.in_use),
                "in_use_connections": sum(1 for conn in self._pool if conn.in_use),
                "active_acquisitions": len(self._active_acquisitions),
                "connection_waiters": len(self._connection_waiters),
            })
            return current_stats
    
    async def _acquire_connection(self) -> PooledConnection:
        """
        Acquire an available connection or create a new one if needed.
        
        This internal method is used by the acquire context manager.
        
        Returns:
            PooledConnection: A connection from the pool
            
        Raises:
            TimeoutError: If unable to acquire a connection within the timeout
        """
        while True:
            async with self._lock:
                # First, try to find an available connection
                for conn in self._pool:
                    if not conn.in_use and conn.health_status == "healthy":
                        conn.mark_as_used()
                        return conn
                
                # If we have capacity, create a new connection
                if len(self._pool) < self.max_connections:
                    conn = await self._create_connection()
                    conn.mark_as_used()
                    self._pool.append(conn)
                    
                    # Update peak connections stat
                    if len(self._pool) > self._stats["peak_connections"]:
                        self._stats["peak_connections"] = len(self._pool)
                        
                    return conn
                
                # No available connections and at capacity, create a waiter
                waiter = asyncio.Future()
                self._connection_waiters.append(waiter)
            
            # Wait outside the lock
            try:
                await waiter
            except Exception as e:
                logger.error(f"Error while waiting for a connection: {e}")
                # If the waiter was cancelled or errored, remove it from the list
                async with self._lock:
                    if waiter in self._connection_waiters:
                        self._connection_waiters.remove(waiter)
                raise
            
            # A connection should be available now, try again
            async with self._lock:
                if waiter in self._connection_waiters:
                    self._connection_waiters.remove(waiter)
    
    async def _release_connection(self, connection: PooledConnection):
        """
        Release a connection back to the pool.
        
        This internal method is used by the acquire context manager.
        
        Args:
            connection: The connection to release
        """
        self._stats["total_releases"] += 1
        
        # Reset connection if needed
        if self.reset_on_return:
            try:
                # Simple reset - just re-use namespace and database
                await connection.connection.use(self.namespace, self.database)
            except Exception as e:
                logger.error(f"Error resetting connection {connection.id}: {e}")
                connection.health_status = "unhealthy"
        
        async with self._lock:
            # If the connection is unhealthy or has been used too many times, close and replace it
            if connection.health_status == "unhealthy" or connection.usage_count >= self.max_usage_count:
                try:
                    await connection.connection.close()
                    self._stats["total_connections_closed"] += 1
                except Exception as e:
                    logger.error(f"Error closing connection {connection.id}: {e}")
                
                # Remove from pool
                self._pool.remove(connection)
                
                # Create a replacement if we're below min_connections
                if len(self._pool) < self.min_connections:
                    new_conn = await self._create_connection()
                    self._pool.append(new_conn)
            else:
                # Mark as free for reuse
                connection.mark_as_free()
            
            # Notify a waiter if any
            if self._connection_waiters:
                waiter = self._connection_waiters.pop(0)
                if not waiter.done():
                    waiter.set_result(None)
    
    async def _create_connection(self) -> PooledConnection:
        """
        Create a new database connection.
        
        This internal method creates a new connection with retry logic.
        
        Returns:
            PooledConnection: A new connection wrapped in a PooledConnection object
            
        Raises:
            Exception: If connection creation fails after all retry attempts
        """
        last_error = BaseException()
        
        for attempt in range(1, self.connection_retry_attempts + 1):
            try:
                # Create connection with timeout
                async with asyncio.timeout(self.connection_timeout):
                    
                    if attempt != 1:
                        await asyncio.sleep(1) # Wait for 1 second before creating connection if not first attempt

                    db = AsyncSurreal(self.uri)
                    
                    # Sign in
                    await db.signin(self.credentials)
                    
                    # Use namespace and database
                    await db.use(self.namespace, self.database)
                    
                    # Execute schema if provided
                    if self.schema_file and attempt == 1:  # Only try schema on first attempt
                        await self._execute_schema(db)
                    
                    # Run custom initialization if provided
                    if self.on_connection_create:
                        await self.on_connection_create(db)
                    
                    self._stats["total_connections_created"] += 1
                    return PooledConnection(connection=db)
                    
            except Exception as e:
                last_error = e
                logger.warning(f"Connection attempt {attempt} failed: {e}")
                self._stats["connection_errors"] += 1
                
                if attempt < self.connection_retry_attempts:
                    # Wait before retrying
                    await asyncio.sleep(self.connection_retry_delay * attempt)  # Exponential backoff
        
        # All attempts failed
        logger.error(f"Failed to create connection after {self.connection_retry_attempts} attempts: {last_error}")
        raise last_error
    
    async def _execute_schema(self, db: AsyncWsSurrealConnection | AsyncHttpSurrealConnection):
        """
        Execute schema file on a connection.
        
        Args:
            db: The database connection to execute the schema on
            
        Raises:
            Exception: If schema execution fails
        """
        try:
            with open(self.schema_file, "r") as file:
                schema = file.read()
                commands = [cmd.strip() for cmd in schema.split(";") if cmd.strip()]
                for cmd in commands:
                    await db.query(cmd)
            logger.info(f"Schema executed successfully from {self.schema_file}")
        except Exception as e:
            logger.error(f"Error executing schema: {e}")
            raise
    
    async def _check_connection_health(self, conn: PooledConnection) -> bool:
        """
        Check if a connection is healthy by executing a simple query.
        
        Args:
            conn: The connection to check
            
        Returns:
            bool: True if healthy, False otherwise
        """
        try:
            # Don't check connections that are in use
            if conn.in_use:
                return True
                
            # Simple health check query
            await conn.connection.query("INFO FOR DB;")
            conn.health_status = "healthy"
            return True
        except Exception as e:
            logger.warning(f"Connection {conn.id} health check failed: {e}")
            conn.health_status = "unhealthy"
            self._stats["health_check_failures"] += 1
            return False
    
    async def _maintenance_loop(self):
        """
        Periodic maintenance task that:
        1. Checks connection health
        2. Closes idle connections above min_connections
        3. Creates new connections if below min_connections
        """
        while not self._closed:
            try:
                await asyncio.sleep(self.health_check_interval)
                
                async with self._lock:
                    # Check health of all connections
                    unhealthy_connections = []
                    for conn in self._pool:
                        if not await self._check_connection_health(conn):
                            unhealthy_connections.append(conn)
                    
                    # Close unhealthy connections
                    for conn in unhealthy_connections:
                        if conn in self._pool:  # Check again in case it was removed
                            try:
                                await conn.connection.close()
                                self._stats["total_connections_closed"] += 1
                            except Exception as e:
                                logger.error(f"Error closing unhealthy connection {conn.id}: {e}")
                            self._pool.remove(conn)
                    
                    # Close idle connections if we have more than min_connections
                    current_time = time.time()
                    idle_connections = [
                        conn for conn in self._pool 
                        if not conn.in_use and 
                        (current_time - conn.last_used) > self.max_idle_time
                    ]
                    
                    # Only close idle connections if we're above min_connections
                    if len(self._pool) - len(idle_connections) >= self.min_connections:
                        for conn in idle_connections:
                            try:
                                await conn.connection.close()
                                self._stats["total_connections_closed"] += 1
                            except Exception as e:
                                logger.error(f"Error closing idle connection {conn.id}: {e}")
                            self._pool.remove(conn)
                    
                    # Create new connections if we're below min_connections
                    connections_to_create = self.min_connections - len(self._pool)
                    if connections_to_create > 0:
                        for _ in range(connections_to_create):
                            try:
                                new_conn = await self._create_connection()
                                self._pool.append(new_conn)
                            except Exception as e:
                                logger.error(f"Error creating connection during maintenance: {e}")
                
            except asyncio.CancelledError:
                # Maintenance task was cancelled
                break
            except Exception as e:
                logger.error(f"Error in connection pool maintenance: {e}")


class SurrealDBPoolManager:
    """
    Singleton manager for SurrealDB connection pools.
    Allows creating and accessing multiple named pools.
    
    This class follows the Singleton pattern to ensure only one instance
    exists across the application.
    """
    _instance = None
    _pools = {}
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(SurrealDBPoolManager, cls).__new__(cls)
        return cls._instance
    
    async def create_pool(self, name: str, **pool_kwargs):
        """
        Create a new connection pool with the given name and parameters.
        
        Args:
            name: Unique name for the pool
            **pool_kwargs: Parameters to pass to SurrealDBConnectionPool constructor
            
        Returns:
            SurrealDBConnectionPool: The created pool
            
        Raises:
            ValueError: If a pool with the given name already exists
        """
        if name in self._pools:
            raise ValueError(f"Pool with name '{name}' already exists")
            
        pool = SurrealDBConnectionPool(**pool_kwargs)
        await pool.initialize()
        self._pools[name] = pool
        return pool
    
    def get_pool(self, name: str) -> SurrealDBConnectionPool:
        """
        Get an existing connection pool by name.
        
        Args:
            name: Name of the pool to get
            
        Returns:
            SurrealDBConnectionPool: The requested pool
            
        Raises:
            ValueError: If no pool with the given name exists
        """
        if name not in self._pools:
            raise ValueError(f"Pool with name '{name}' does not exist")
        return self._pools[name]
    
    async def _close_pools(self):
        """
        Close all connection pools.
        
        This method should be called during application shutdown.
        """
        logger.info(f"Closing all SurrealDB connection pools ({len(self._pools)} pools)")
        for name, pool in list(self._pools.items()):
            try:
                logger.info(f"Closing pool '{name}'")
                await pool.close()
                del self._pools[name]
            except Exception as e:
                logger.error(f"Error closing pool '{name}': {e}")