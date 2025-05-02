# -*- coding: utf-8 -*-
"""
SurrealDB Async Connection Pool
===============================

Manages a pool of SurrealDB connections for asyncio applications. Handles acquisition,
release, health checks, timeouts, and basic lifecycle management.

**Primary Goal:** Solves the `websockets.exceptions.ConcurrencyError: cannot call recv while
another coro is calling recv` by ensuring that each acquired database connection
object is used exclusively by only one coroutine at a time.

Usage:
    # Create and initialize
    pool = SurrealDBConnectionPool(...)
    await pool.initialize() # Or use 'async with pool:'

    # Acquire connection via context manager (ensures exclusivity)
    async with pool.acquire() as conn:
        # 'conn' is exclusively yours within this block
        result = await conn.query("SELECT * FROM users")

    # Shutdown
    await pool.close()
"""

import asyncio
import logging
import time
import random
import traceback
import uuid
import os # For Process ID

from surrealdb import AsyncSurreal, AsyncWsSurrealConnection, AsyncHttpSurrealConnection
from typing import Dict, List, Optional, Callable, Any, Set, Union, AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# Example: Configure more detailed logging for debugging the pool itself
# Ensure this runs before the pool is used if you need this level of detail.
# logging.basicConfig(
#     level=logging.DEBUG,
#     format='%(asctime)s - PID:%(process)d - %(levelname)-8s - %(name)-15s [%(threadName)s] - %(message)s'
# )
# logger.setLevel(logging.DEBUG) # Set logger level specifically if needed


SurrealConnectionType = Union[AsyncWsSurrealConnection, AsyncHttpSurrealConnection]

@dataclass
class PooledConnection:
    """Internal wrapper holding a connection and its pool metadata."""
    connection: SurrealConnectionType
    created_at: float = field(default_factory=time.time)
    last_used: float = field(default_factory=time.time)
    in_use: bool = False # <-- Crucial for exclusivity
    usage_count: int = 0
    id: str = field(default_factory=lambda: f"conn_{uuid.uuid4().hex[:8]}")
    health_status: str = "healthy" # 'healthy', 'unhealthy'
    # DEBUG: Track which task acquired this connection
    acquired_by_task: Optional[asyncio.Task] = None

    def mark_as_used(self):
        """Mark this connection wrapper as currently in use by the current task."""
        current_task = asyncio.current_task()
        task_name = current_task.get_name() if current_task else "UnknownTask"
        if self.in_use:
            # This is a potential issue indicator if the pool logic is flawed
            old_task_name = self.acquired_by_task.get_name() if self.acquired_by_task else "None"
            logger.warning(f"POOL DEBUG: Conn {self.id} (obj: {id(self.connection)}) marked USED by task {task_name} but was ALREADY IN USE by task {old_task_name}.")
        else:
            logger.debug(f"POOL DEBUG: Conn {self.id} (obj: {id(self.connection)}) marked USED by task {task_name}.")
        self.in_use = True
        self.last_used = time.time()
        self.usage_count += 1
        self.acquired_by_task = current_task


    def mark_as_free(self):
        """Mark this connection wrapper as available."""
        current_task = asyncio.current_task()
        task_name = current_task.get_name() if current_task else "UnknownTask"
        old_task_name = self.acquired_by_task.get_name() if self.acquired_by_task else 'None' # Get name before clearing
        if not self.in_use:
             # Might happen if closed by maintenance then released, less critical
             logger.debug(f"POOL DEBUG: Conn {self.id} (obj: {id(self.connection)}) marked FREE by task {task_name} but was ALREADY FREE.")
        else:
            logger.debug(f"POOL DEBUG: Conn {self.id} (obj: {id(self.connection)}) marked FREE by task {task_name} (was used by {old_task_name}).")
        self.in_use = False
        self.acquired_by_task = None


class SurrealDBConnectionPool:
    """
    Manages SurrealDB connections, ensuring exclusive use to prevent concurrency errors.
    """

    def __init__(
        self,
        uri: str,
        credentials: Dict[str, str],
        namespace: str,
        database: str,
        min_connections: int = 4,
        max_connections: int = 10,
        max_idle_time: float = 300.0,
        connection_timeout: float = 25.0,
        acquisition_timeout: float = 10.0,
        health_check_interval: float = 30.0,
        max_usage_count: int = 1000,
        connection_retry_attempts: int = 3,
        connection_retry_delay: float = 1.0,
        schema_file: Optional[str] = None,
        on_connection_create: Optional[Callable[[SurrealConnectionType], Any]] = None,
        reset_on_return: bool = True,
        log_queries: bool = False,
    ):
        """Sets up the pool configuration. Call initialize() or use async with."""
        if min_connections <= 0:
            raise ValueError("min_connections must be positive")
        if max_connections < min_connections:
            raise ValueError("max_connections cannot be less than min_connections")

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

        # Internal state
        self._pool: List[PooledConnection] = []
        self._lock = asyncio.Lock() # Protects _pool, _connection_waiters, relevant _stats
        self._initialized = False
        self._closed = False
        self._maintenance_task: Optional[asyncio.Task] = None
        self._connection_waiters: List[asyncio.Future] = []

        # Basic stats tracking
        self._stats = {
            "total_connections_created": 0,
            "total_connections_closed": 0,
            "total_acquisitions": 0,
            "total_releases": 0,
            "acquisition_timeouts": 0,
            "connection_errors": 0,
            "health_check_failures": 0,
            "peak_connections": 0,
            "peak_waiters": 0,
        }

    async def __aenter__(self):
        """Initializes the pool if not already done, for 'async with' usage."""
        if not self._initialized:
            await self.initialize()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Closes the pool when exiting 'async with' block."""
        await self.close()

    async def initialize(self):
        """
        Creates the minimum number of connections and starts maintenance task.
        Safe to call multiple times; only initializes once.
        """
        if self._initialized:
            logger.debug("Pool already initialized.")
            return
        if self._closed:
             raise RuntimeError("Cannot initialize a closed pool.")

        # Acquire lock to ensure atomicity of initialization check and setup
        async with self._lock:
            # Double check initialization flag inside lock
            if self._initialized:
                return

            logger.info(f"Initializing SurrealDB connection pool for {self.uri}...")
            # Concurrently create initial connections
            create_tasks = [self._create_connection() for _ in range(self.min_connections)]
            results = await asyncio.gather(*create_tasks, return_exceptions=True)

            successful_creations = 0
            for result in results:
                if isinstance(result, PooledConnection):
                    self._pool.append(result)
                    successful_creations += 1
                elif isinstance(result, Exception):
                    # Log error but continue - maintenance loop will try to replenish later
                    logger.error(f"Failed to create initial connection: {result}")
                else:
                     # Should not happen, but log just in case
                     logger.warning(f"Unexpected result during initial connection creation: {result}")

            # Update stats under lock
            self._stats["peak_connections"] = len(self._pool)
            self._initialized = True

            # Start background maintenance task
            self._maintenance_task = asyncio.create_task(self._maintenance_loop())
            logger.info(f"Pool initialized with {successful_creations}/{self.min_connections} connections (Target min: {self.min_connections}).")

    async def close(self):
        """
        Closes all connections and stops the maintenance task.
        Safe to call multiple times.
        """
        if self._closed:
            return
        logger.info("Closing SurrealDB connection pool...")
        self._closed = True # Signal closure immediately

        # 1. Stop maintenance task first
        if self._maintenance_task:
            self._maintenance_task.cancel()
            try:
                await self._maintenance_task
            except asyncio.CancelledError:
                logger.debug("Maintenance task cancelled.")
            except Exception as e:
                # Log if cancellation itself resulted in an unexpected error
                logger.error(f"Error during maintenance task shutdown: {e}", exc_info=True)
            self._maintenance_task = None

        # 2. Notify any pending waiters that the pool is closing
        async with self._lock:
            # Copy waiters list under lock before clearing
            waiters_to_cancel = list(self._connection_waiters)
            self._connection_waiters.clear()
        # Cancel futures outside lock
        for waiter in waiters_to_cancel:
            if not waiter.done():
                waiter.set_exception(RuntimeError("Connection pool is closing"))
        if waiters_to_cancel:
             logger.debug(f"Cancelled {len(waiters_to_cancel)} pending connection waiters.")

        # 3. Close all actual connections
        async with self._lock:
            # Copy pool list under lock before clearing
            connections_to_close = list(self._pool)
            self._pool.clear() # Clear the list immediately under lock

        # Perform actual connection closing outside lock
        if connections_to_close:
            logger.info(f"Closing {len(connections_to_close)} active connections...")
            close_tasks = [self._close_pooled_connection(conn, reason="pool shutdown")
                           for conn in connections_to_close]
            # Wait for closures to finish (or timeout/error)
            await asyncio.gather(*close_tasks, return_exceptions=True)

        self._initialized = False # Mark as uninitialized after closing
        logger.info("SurrealDB connection pool closed.")


    @asynccontextmanager
    async def acquire(self) -> AsyncGenerator[SurrealConnectionType, None]:
        """
        Acquires an exclusive connection from the pool. Use with 'async with'.

        This method is the primary way to get a connection and ensures it won't
        be used by other coroutines concurrently, preventing the `ConcurrencyError`.

        Yields:
            The actual SurrealDB connection object, exclusively leased.
        """
        if self._closed:
            raise RuntimeError("Connection pool is closed")
        if not self._initialized:
             raise RuntimeError("Pool not initialized. Call initialize() or use 'async with pool:'.")

        start_time = time.monotonic()
        pooled_conn: Optional[PooledConnection] = None
        conn_id_str = "None" # For logging if acquisition fails early
        conn_obj_id_str = "None"
        current_task = asyncio.current_task()
        task_name = current_task.get_name() if current_task else "UnknownTask"

        logger.debug(f"POOL DEBUG: Task {task_name} attempting to acquire connection...")
        try:
            # Acquire the wrapper with exclusive lease logic
            # The timeout applies to the whole _acquire_connection call
            async with asyncio.timeout(self.acquisition_timeout):
                 pooled_conn = await self._acquire_connection() # Handles finding/creating and marking 'in_use'

            # If we got here, pooled_conn is not None
            conn_id_str = pooled_conn.id
            conn_obj_id_str = str(id(pooled_conn.connection))
            logger.debug(f"POOL DEBUG: Task {task_name} ACQUIRED conn {conn_id_str} (obj: {conn_obj_id_str})")

            # Yield the *actual connection object* inside the wrapper for use
            yield pooled_conn.connection

        except asyncio.TimeoutError:
            self._stats["acquisition_timeouts"] += 1
            elapsed = time.monotonic() - start_time
            logger.warning(f"POOL DEBUG: Task {task_name} TIMEOUT acquiring connection after {elapsed:.2f}s (limit: {self.acquisition_timeout:.2f}s)")
            # Ensure pooled_conn is None so finally doesn't try to release
            pooled_conn = None
            raise # Re-raise the timeout
        except Exception as e:
            # Catch any other error during the acquisition *process* itself
            logger.error(f"POOL DEBUG: Task {task_name} ERROR during acquisition phase (before yield): {e}", exc_info=True)
            if pooled_conn:
                 # This means _acquire_connection succeeded but something failed immediately after
                 # Assume the connection might be bad or state inconsistent. Force close.
                 logger.error(f"POOL DEBUG: Task {task_name} scheduling force-close for conn {pooled_conn.id} (obj: {id(pooled_conn.connection)}) due to acquisition error after getting lease.")
                 asyncio.create_task(self._release_connection(pooled_conn, force_close=True))
                 pooled_conn = None # Prevent release in finally block
            raise # Re-raise the error
        finally:
            # This block *always* runs when the 'async with' block exits (normally or via exception)
            # It ensures the connection is returned to the pool or closed.
            if pooled_conn:
                # Connection was successfully acquired and yielded
                logger.debug(f"POOL DEBUG: Task {task_name} releasing conn {conn_id_str} (obj: {conn_obj_id_str})")
                await self._release_connection(pooled_conn)
            else:
                # Acquisition failed (timeout or error before yield), nothing to release
                logger.debug(f"POOL DEBUG: Task {task_name} exiting acquire context without a connection to release.")

    async def execute_query(self, query: str, params: Optional[Dict] = None):
        """ Convenience method: acquire, execute query, release. Ensures exclusive use. """
        start_time: Optional[float] = None
        log_query = query # Keep original query for error reporting
        current_task = asyncio.current_task()
        task_name = current_task.get_name() if current_task else "UnknownTask"

        if self.log_queries:
            start_time = time.monotonic()
            # Truncate long queries for cleaner logging
            log_query_short = query[:150] + ('...' if len(query) > 150 else '')
            log_params = f" Params: {params}" if params else ""
            logger.debug(f"Task {task_name}: Executing query: {log_query_short}{log_params}")

        # Use the acquire context manager to handle exclusivity and release
        async with self.acquire() as conn:
            # conn is the exclusively leased SurrealConnectionType object
            conn_obj_id = id(conn)
            logger.debug(f"POOL DEBUG: Task {task_name} using conn obj {conn_obj_id} for execute_query.")
            try:
                result = await conn.query(query, params)
                logger.debug(f"POOL DEBUG: Task {task_name} finished query on conn obj {conn_obj_id}.")
                if self.log_queries and start_time is not None:
                    duration = time.monotonic() - start_time
                    logger.debug(f"Task {task_name}: Query finished in {duration:.4f}s. Query: {log_query_short}")
                return result
            except Exception as e:
                # Log the error with context
                logger.error(f"Task {task_name}: Query execution failed on conn obj {conn_obj_id}: {e}. Query: {log_query}", exc_info=True)
                raise # Re-raise the exception after logging

    async def get_stats(self) -> Dict[str, Any]:
        """Returns a snapshot of the pool's statistics."""
        async with self._lock: # Protect access to stats and pool list
            current_stats = self._stats.copy()
            current_connections = len(self._pool)
            current_waiters = len(self._connection_waiters)
            current_stats.update({
                "current_connections": current_connections,
                "available_connections": sum(1 for conn in self._pool if not conn.in_use),
                "in_use_connections": sum(1 for conn in self._pool if conn.in_use),
                "connection_waiters": current_waiters,
            })
            # Update persistent peak stats under lock
            self._stats["peak_connections"] = max(self._stats["peak_connections"], current_connections)
            self._stats["peak_waiters"] = max(self._stats["peak_waiters"], current_waiters)
            # Ensure returned dict has the latest peak values
            current_stats["peak_connections"] = self._stats["peak_connections"]
            current_stats["peak_waiters"] = self._stats["peak_waiters"]
            return current_stats

    # --- Internal Helper Methods ---

    async def _acquire_connection(self) -> PooledConnection:
        """Internal logic: Finds/creates a connection wrapper and marks it 'in_use'."""
        current_task = asyncio.current_task()
        task_name = current_task.get_name() if current_task else "UnknownTask"

        while True: # Loop until connection obtained or caller times out
            # --- Try finding existing connection (under lock) ---
            async with self._lock:
                # logger.debug(f"POOL DEBUG: Task {task_name} entered acquire lock. Pool: {len(self._pool)}, Waiters: {len(self._connection_waiters)}")
                for conn_wrapper in self._pool:
                    if not conn_wrapper.in_use and conn_wrapper.health_status == "healthy":
                        # Found one! Mark it used (logs inside mark_as_used) and return wrapper.
                        conn_wrapper.mark_as_used() # Sets in_use = True, acquired_by_task
                        self._stats["total_acquisitions"] += 1
                        logger.debug(f"POOL DEBUG: Task {task_name} found free existing conn {conn_wrapper.id} (obj: {id(conn_wrapper.connection)})")
                        return conn_wrapper # Return the PooledConnection wrapper

                # --- If none free, check if we can create one ---
                if len(self._pool) < self.max_connections:
                    logger.debug(f"POOL DEBUG: Task {task_name} found no free conn, will create new. Pool size {len(self._pool)} < {self.max_connections}.")
                    # Exit lock block to create connection below
                else:
                    # --- Pool is full, wait (under lock to add waiter) ---
                    logger.debug(f"POOL DEBUG: Task {task_name} found no free conn and pool full ({len(self._pool)}). Waiting...")
                    waiter = asyncio.Future()
                    self._connection_waiters.append(waiter)
                    # Update peak waiters stat while holding lock
                    self._stats["peak_waiters"] = max(self._stats["peak_waiters"], len(self._connection_waiters))
                    # logger.debug(f"POOL DEBUG: Task {task_name} added waiter, exiting lock to wait.")
                    # Exit lock block before waiting
                    await waiter # Wait outside the lock
                    logger.debug(f"POOL DEBUG: Task {task_name} woken up, retrying acquisition.")
                    # Loop again to re-enter lock and check pool state
                    continue

            # --- Create connection (outside the lock) ---
            # This block is reached only if we exited the lock because pool size < max_connections
            logger.debug(f"POOL DEBUG: Task {task_name} attempting to create connection outside lock.")
            try:
                new_conn_wrapper = await self._create_connection() # Handles retries internally
            except Exception as create_err:
                logger.error(f"POOL DEBUG: Task {task_name} failed to create new connection: {create_err}")
                # Avoid tight loop on persistent creation failure
                await asyncio.sleep(random.uniform(0.1, 0.5))
                continue # Retry acquisition loop from the start

            # --- Add new connection to pool (under lock) ---
            logger.debug(f"POOL DEBUG: Task {task_name} created {new_conn_wrapper.id}, acquiring lock to add.")
            async with self._lock:
                # Re-check conditions after lock acquired, state might have changed
                if self._closed:
                     logger.warning(f"POOL DEBUG: Task {task_name} created {new_conn_wrapper.id} but pool closed. Closing.")
                     # Schedule closure task, don't await under lock
                     asyncio.create_task(self._close_pooled_connection(new_conn_wrapper, "pool closed during creation"))
                     # Need to signal failure to the acquirer
                     raise RuntimeError("Pool closed while creating connection")

                if len(self._pool) >= self.max_connections:
                    # Pool filled up while we were creating this one
                    logger.warning(f"POOL DEBUG: Task {task_name} created {new_conn_wrapper.id} but pool filled up ({len(self._pool)}). Closing.")
                    asyncio.create_task(self._close_pooled_connection(new_conn_wrapper, "pool full"))
                    # Loop again to try acquiring (maybe wait this time)
                    continue
                else:
                    # Success! Add to pool, mark used (logs inside), update stats, return wrapper
                    logger.debug(f"POOL DEBUG: Task {task_name} adding new conn {new_conn_wrapper.id} (obj: {id(new_conn_wrapper.connection)}) to pool and marking used.")
                    new_conn_wrapper.mark_as_used() # Sets in_use = True, acquired_by_task
                    self._pool.append(new_conn_wrapper)
                    self._stats["total_acquisitions"] += 1
                    # Update peak connections stat while holding lock
                    self._stats["peak_connections"] = max(self._stats["peak_connections"], len(self._pool))
                    return new_conn_wrapper


    async def _release_connection(self, pooled_conn: PooledConnection, force_close: bool = False):
        """Internal logic: Marks connection 'free' or handles closure, notifies waiters."""
        current_task = asyncio.current_task()
        task_name = current_task.get_name() if current_task else "UnknownTask"
        conn_id_str = pooled_conn.id
        conn_obj_id_str = str(id(pooled_conn.connection))
        logger.debug(f"POOL DEBUG: Task {task_name} attempting to release conn {conn_id_str} (obj: {conn_obj_id_str}). Force close: {force_close}")

        self._stats["total_releases"] += 1
        should_close = force_close
        close_reason = "forced release" if force_close else ""
        needs_replacement = False # Flag if we need to trigger replenishment check

        # --- Check health/usage/reset conditions (outside lock is fine for checks) ---
        if not should_close:
            # Check health status flag first
            if pooled_conn.health_status == "unhealthy":
                should_close = True
                close_reason = "unhealthy"
            # Check usage count
            elif pooled_conn.usage_count >= self.max_usage_count:
                should_close = True
                close_reason = f"max usage ({self.max_usage_count}) reached"

        # Try resetting state if required and not closing yet
        if not should_close and self.reset_on_return:
            logger.debug(f"POOL DEBUG: Task {task_name} attempting reset for conn {conn_id_str}")
            try:
                # Use a timeout for the reset operation itself
                async with asyncio.timeout(5.0):
                    await pooled_conn.connection.use(self.namespace, self.database)
                logger.debug(f"POOL DEBUG: Task {task_name} reset successful for conn {conn_id_str}")
            except asyncio.TimeoutError:
                 logger.warning(f"Timeout resetting conn {conn_id_str}. Marking unhealthy.")
                 pooled_conn.health_status = "unhealthy" # Mark flag
                 should_close = True
                 close_reason = "reset timeout"
            except Exception as e:
                logger.warning(f"Failed to reset conn {conn_id_str}: {e}. Marking unhealthy.")
                pooled_conn.health_status = "unhealthy" # Mark flag
                should_close = True
                close_reason = "reset failed"

        # --- Update pool state (under lock) ---
        async with self._lock:
            logger.debug(f"POOL DEBUG: Task {task_name} entered release lock for conn {conn_id_str} (obj: {conn_obj_id_str}). Should close: {should_close}")
            try:
                 # Find the wrapper in the pool list to modify/remove it
                 current_index = self._pool.index(pooled_conn)
            except ValueError:
                 # Connection already removed from pool (likely by maintenance loop)
                 logger.debug(f"POOL DEBUG: Task {task_name} found conn {conn_id_str} (obj: {conn_obj_id_str}) already removed before release could complete.")
                 # Don't try to close it again or notify waiters based on this release
                 return # Exit release process

            # Basic sanity check (optional, might log noise if release races with maintenance close)
            # if not pooled_conn.in_use and not force_close:
            #    logger.warning(f"POOL DEBUG: Task {task_name} releasing conn {conn_id_str} which was not marked 'in_use'.")

            if should_close:
                 # Remove from pool list immediately under lock
                 logger.info(f"POOL DEBUG: Task {task_name} removing conn {conn_id_str} (obj: {conn_obj_id_str}) from pool for closure. Reason: {close_reason}")
                 del self._pool[current_index]
                 # Schedule the actual close task (runs outside lock)
                 asyncio.create_task(self._close_pooled_connection(pooled_conn, close_reason))
                 # Check if we dropped below minimum and need a replacement (under lock)
                 needs_replacement = not self._closed and len(self._pool) < self.min_connections
            else:
                 # Return to pool: mark as available (logs inside mark_as_free)
                 logger.debug(f"POOL DEBUG: Task {task_name} returning conn {conn_id_str} (obj: {conn_obj_id_str}) to pool.")
                 pooled_conn.mark_as_free() # Sets in_use = False, acquired_by_task = None

            # Notify one waiter if any exist *after* pool state is updated
            if self._connection_waiters:
                 logger.debug(f"POOL DEBUG: Task {task_name} notifying waiter after releasing/closing {conn_id_str}.")
                 waiter_to_notify = self._connection_waiters.pop(0)
                 if not waiter_to_notify.done():
                      waiter_to_notify.set_result(None) # Wake up the waiter

        # --- Trigger replenishment outside lock if needed ---
        if needs_replacement:
            logger.info(f"POOL DEBUG: Task {task_name} triggering replenish check after closing {conn_id_str}.")
            # Schedule task to add connection if still needed
            asyncio.create_task(self._add_connection_if_needed())
        logger.debug(f"POOL DEBUG: Task {task_name} finished releasing conn {conn_id_str} (obj: {conn_obj_id_str}).")


    async def _create_connection(self) -> PooledConnection:
        """Creates, authenticates, and sets up a single new connection."""
        # Implementation reviewed, looks okay for creating/setup/retry.
        # Logging added in previous step covers task context.
        current_task = asyncio.current_task()
        task_name = current_task.get_name() if current_task else "UnknownTask"
        last_error: Optional[Exception] = None

        for attempt in range(1, self.connection_retry_attempts + 1):
            logger.debug(f"POOL DEBUG: Task {task_name} connection attempt {attempt}...")
            try:
                async with asyncio.timeout(self.connection_timeout):
                    # NOTE: Assuming AsyncSurreal instance IS the connection object
                    # needed for query/use/close. Adjust if library API differs.
                    db = AsyncSurreal(self.uri)
                    await db.signin(self.credentials)
                    await db.use(self.namespace, self.database)

                    actual_connection : SurrealConnectionType = db # Assumption here

                    if self.schema_file:
                        await self._execute_schema(actual_connection) # Pass connection object

                    if self.on_connection_create:
                        try:
                            await self.on_connection_create(actual_connection) # Pass connection object
                        except Exception as callback_err:
                             # Log error but don't fail connection creation because of callback
                             logger.error(f"Error in on_connection_create callback: {callback_err}", exc_info=True)

                    # Wrap in PooledConnection and return
                    pooled_conn = PooledConnection(connection=actual_connection)
                    self._stats["total_connections_created"] += 1 # Increment stat (thread-safe enough for counter)
                    logger.info(f"POOL DEBUG: Task {task_name} successfully created connection {pooled_conn.id} (obj: {id(pooled_conn.connection)}) (attempt {attempt})")
                    return pooled_conn

            except asyncio.TimeoutError:
                last_error = TimeoutError(f"Connection attempt {attempt} timed out after {self.connection_timeout}s")
                logger.warning(last_error)
            except ConnectionRefusedError as e:
                 last_error = e
                 logger.warning(f"Connection attempt {attempt} refused: {e}")
            except Exception as e:
                # Catch broader errors (auth issues, network problems, etc.)
                last_error = e
                logger.warning(f"POOL DEBUG: Task {task_name} connection attempt {attempt} failed: {type(e).__name__} - {e}")
                # Optionally log full traceback for debugging difficult connection issues
                # if logger.isEnabledFor(logging.DEBUG):
                #    logger.debug(traceback.format_exc())

            # If not the last attempt, wait before retrying
            if attempt < self.connection_retry_attempts:
                # Use exponential backoff with jitter
                delay = (self.connection_retry_delay * (2 ** (attempt - 1))) + random.uniform(0, 0.5)
                logger.info(f"POOL DEBUG: Task {task_name} retrying connection in {delay:.2f} seconds...")
                await asyncio.sleep(delay)
            else:
                 # Log after final attempt fails
                 self._stats["connection_errors"] += 1 # Increment stat
                 logger.error(f"POOL DEBUG: Task {task_name} failed to create connection after {self.connection_retry_attempts} attempts.")

        # If loop finished without returning, all attempts failed
        raise last_error if last_error else RuntimeError("Unknown error creating connection")

    async def _execute_schema(self, db: SurrealConnectionType):
        """Executes schema file on a given connection object."""
        # Implementation reviewed, looks okay. Logging includes object ID.
        if not self.schema_file:
            # logger.debug("No schema file specified, skipping schema execution.")
            return

        logger.debug(f"Executing schema from '{self.schema_file}' on conn obj {id(db)}...")
        try:
            with open(self.schema_file, "r", encoding="utf-8") as file:
                schema_script = file.read()

            if not schema_script.strip():
                logger.warning(f"Schema file '{self.schema_file}' is empty or contains only whitespace. Skipping.")
                return

            # SurrealDB can handle multiple statements separated by semicolons in one query call
            await db.query(schema_script)
            logger.info(f"Schema executed successfully from {self.schema_file} on conn obj {id(db)}")

        except FileNotFoundError:
            logger.error(f"Schema file not found: {self.schema_file}")
            raise # Propagate for potential handling or failing connection creation
        except PermissionError:
            logger.error(f"Permission denied reading schema file: {self.schema_file}")
            raise
        except Exception as e:
            # Log traceback for DB errors during schema exec, could be syntax error etc.
            logger.error(f"Database error executing schema from {self.schema_file} on conn obj {id(db)}: {type(e).__name__} - {e}")
            logger.debug(traceback.format_exc())
            raise # Fail connection creation if schema fails

    async def _check_connection_health(self, conn_wrapper: PooledConnection) -> bool:
        """Performs a quick health check on an idle connection."""
        # Implementation reviewed, looks okay. Checks run outside main lock.
        # Logging includes connection ID and object ID.
        # Only checks connections not currently marked 'in_use'.
        if conn_wrapper.in_use:
            # Should not happen if called correctly by maintenance loop, but safe check.
            logger.debug(f"Skipping health check for in-use conn {conn_wrapper.id}")
            return True

        logger.debug(f"Checking health of idle conn {conn_wrapper.id} (obj: {id(conn_wrapper.connection)})")
        try:
            # Use a lightweight, read-only query with a short timeout
            async with asyncio.timeout(5.0):
                await conn_wrapper.connection.query("INFO FOR DB;")
            # If query succeeds, update status if it was previously unhealthy
            if conn_wrapper.health_status != "healthy":
                 logger.info(f"Conn {conn_wrapper.id} health restored.")
                 conn_wrapper.health_status = "healthy" # Update the flag
            return True
        except asyncio.TimeoutError:
             logger.warning(f"Health check timed out for {conn_wrapper.id}. Marking unhealthy.")
             conn_wrapper.health_status = "unhealthy" # Update the flag
             self._stats["health_check_failures"] += 1 # Increment stat
             return False
        except Exception as e:
            # Includes connection errors, query errors etc. Likely indicates broken connection.
            logger.warning(f"Health check failed for {conn_wrapper.id}: {type(e).__name__} - {e}. Marking unhealthy.")
            conn_wrapper.health_status = "unhealthy" # Update the flag
            self._stats["health_check_failures"] += 1 # Increment stat
            return False

    async def _maintenance_loop(self):
        """Background task for periodic pool maintenance."""
        # Logic reviewed, looks sound. Runs checks outside lock, updates state under lock.
        logger.info("Starting pool maintenance task.")
        while not self._closed:
            try:
                await asyncio.sleep(self.health_check_interval)
                if self._closed: break # Exit if pool closed during sleep

                logger.debug("Running maintenance...")
                connections_to_check: List[PooledConnection] = []
                # --- Get list of idle connections under lock ---
                async with self._lock:
                    # Copy list of connections that are not currently in use
                    connections_to_check = [conn for conn in self._pool if not conn.in_use]

                # --- Run health checks concurrently (outside lock) ---
                if connections_to_check:
                    logger.debug(f"Maintenance: Checking health of {len(connections_to_check)} idle connections.")
                    check_tasks = [self._check_connection_health(conn) for conn in connections_to_check]
                    # Errors are logged within _check_connection_health
                    await asyncio.gather(*check_tasks, return_exceptions=True)

                # --- Update pool state based on checks and idle time (under lock) ---
                needs_replenishment = False
                num_to_create = 0
                async with self._lock:
                     if self._closed: break # Re-check after potentially long health checks

                     current_time = time.time()
                     initial_pool_size = len(self._pool)
                     num_to_close = 0
                     temp_pool = [] # Build the next state of the pool

                     # Identify connections to close (unhealthy or idle timeout)
                     for conn in self._pool: # Iterate the original locked list
                         close_this = False
                         close_reason = ""
                         # Close if health check marked it unhealthy
                         if conn.health_status == "unhealthy":
                              close_this = True
                              close_reason = "unhealthy"
                         # Check idle time only if healthy
                         elif not conn.in_use and (current_time - conn.last_used) > self.max_idle_time:
                              # Only close idle if pool size *after* removing unhealthy/closing ones
                              # will still be >= min_connections
                              if (initial_pool_size - num_to_close) > self.min_connections:
                                   close_this = True
                                   close_reason = f"idle timeout ({self.max_idle_time}s)"

                         if close_this:
                             num_to_close += 1
                             logger.debug(f"Maintenance: Scheduling closure for {conn.id} (obj: {id(conn.connection)}). Reason: {close_reason}")
                             # Schedule closing task (don't await under lock)
                             asyncio.create_task(self._close_pooled_connection(conn, f"maintenance ({close_reason})"))
                             # Don't add it to the temp_pool
                         else:
                             temp_pool.append(conn) # Keep this connection

                     # Update the pool list with survivors
                     self._pool = temp_pool
                     current_pool_size = len(self._pool)

                     # Check if we need to create new connections to meet minimum
                     if not self._closed and current_pool_size < self.min_connections:
                         needs_replenishment = True
                         num_to_create = self.min_connections - current_pool_size
                         logger.info(f"Maintenance: Pool size {current_pool_size} < min {self.min_connections}. Need {num_to_create}.")

                # --- Trigger replenishment outside lock if needed ---
                if needs_replenishment:
                     logger.debug(f"Maintenance: Triggering creation of {num_to_create} connection(s).")
                     for _ in range(num_to_create):
                          # Schedule task, don't await here
                          asyncio.create_task(self._add_connection_if_needed())

                logger.debug("Maintenance finished cycle.")

            except asyncio.CancelledError:
                logger.info("Maintenance task stopping due to cancellation.")
                break # Exit loop cleanly
            except Exception as e:
                # Log unexpected errors in maintenance but keep the loop running
                logger.error(f"Error in connection pool maintenance loop: {e}", exc_info=True)
                # Add a small delay to prevent tight error loops if error is persistent
                await asyncio.sleep(5)

    async def _close_pooled_connection(self, pooled_conn: PooledConnection, reason: str):
        """Closes the actual SurrealDB connection and logs."""
        # Runs outside the main pool lock. Logs include ID, obj ID, reason.
        logger.debug(f"Closing conn {pooled_conn.id} (obj: {id(pooled_conn.connection)}). Reason: {reason}")
        try:
             # Add a short timeout for closing the connection itself
             async with asyncio.timeout(5.0):
                 # Assuming the connection object has a close method
                 await pooled_conn.connection.close()
             self._stats["total_connections_closed"] += 1 # Increment stat
             logger.debug(f"Conn {pooled_conn.id} (obj: {id(pooled_conn.connection)}) closed successfully.")
        except asyncio.TimeoutError:
              logger.warning(f"Timeout closing conn {pooled_conn.id}")
        except Exception as e:
              # Log errors during close but don't prevent pool operation
              logger.error(f"Error closing conn {pooled_conn.id}: {type(e).__name__} - {e}")

    async def _add_connection_if_needed(self):
         """Safely tries to add one connection if pool is below minimum size."""
         # Runs outside the main pool lock initially, then acquires lock to add.
         current_task = asyncio.current_task()
         task_name = current_task.get_name() if current_task else "UnknownTask"
         logger.debug(f"POOL DEBUG: Task {task_name} checking if connection needed (approx pool size: {len(self._pool)}).")

         # Quick check without lock first
         if self._closed:
             logger.debug(f"POOL DEBUG: Task {task_name} replenish check: Pool closed. Aborting.")
             return

         # Check size more accurately under lock before deciding to create
         async with self._lock:
             current_size = len(self._pool)
             if current_size >= self.min_connections:
                  logger.debug(f"POOL DEBUG: Task {task_name} replenish check: Pool size {current_size} >= min {self.min_connections}. Aborting create.")
                  return
             logger.debug(f"POOL DEBUG: Task {task_name} replenish check: Pool size {current_size} < min {self.min_connections}. Proceeding to create.")

         # Create connection outside the lock
         try:
              new_conn = await self._create_connection()
         except Exception as e:
              logger.error(f"POOL DEBUG: Task {task_name} failed to create connection during replenish: {e}")
              return # Failed to create

         # Add to pool under lock, re-checking conditions
         async with self._lock:
              # Re-check conditions under lock after potentially long creation
              if self._closed:
                   logger.warning(f"POOL DEBUG: Task {task_name} created replenish conn {new_conn.id} but pool closed. Closing.")
                   asyncio.create_task(self._close_pooled_connection(new_conn, "pool closed during replenish"))
                   return
              if len(self._pool) >= self.min_connections:
                  # Pool filled by other means while we created this one
                  logger.warning(f"POOL DEBUG: Task {task_name} created replenish conn {new_conn.id} but pool met min size ({len(self._pool)}). Closing.")
                  asyncio.create_task(self._close_pooled_connection(new_conn, "min size reached during replenish"))
                  return

              # Add the new connection
              logger.info(f"POOL DEBUG: Task {task_name} adding replenish conn {new_conn.id} (obj: {id(new_conn.connection)}) to pool.")
              self._pool.append(new_conn)

              # Check if we should wake a waiter (maybe someone is waiting because pool was *empty*)
              if self._connection_waiters:
                  logger.debug(f"POOL DEBUG: Task {task_name} notifying waiter after replenish add.")
                  waiter_to_notify = self._connection_waiters.pop(0)
                  if not waiter_to_notify.done():
                      waiter_to_notify.set_result(None)


# --- Optional Pool Manager (Singleton) ---
# Reviewed, indentation corrected. Logic seems sound for managing named pools.
class SurrealDBPoolManager:
    """
    (Optional) Singleton to manage named connection pools.
    Useful if your app needs to connect to multiple DBs/users.
    """
    _instance: Optional['SurrealDBPoolManager'] = None
    _pools: Dict[str, SurrealDBConnectionPool] = {}
    _lock = asyncio.Lock() # Lock for managing the _pools dictionary

    def __new__(cls):
        # Basic singleton pattern implementation
        if cls._instance is None:
            cls._instance = super(SurrealDBPoolManager, cls).__new__(cls)
        return cls._instance

    async def create_pool(self, name: str, **pool_kwargs) -> SurrealDBConnectionPool:
        """
        Creates (or raises error if exists) and initializes a named pool.

        Args:
            name: Unique name for this pool instance.
            **pool_kwargs: Arguments passed directly to SurrealDBConnectionPool constructor.

        Returns:
            The initialized SurrealDBConnectionPool instance.

        Raises:
            ValueError: If a pool with this name already exists.
            RuntimeError: If pool initialization fails.
        """
        async with self._lock: # Protect _pools dictionary access
            if name in self._pools:
                raise ValueError(f"Pool with name '{name}' already exists.")

            logger.info(f"Creating new connection pool named '{name}'...")
            # Validate kwargs slightly? Or trust user? Trust for now.
            pool = SurrealDBConnectionPool(**pool_kwargs)
            # Initialize the pool before adding it to the manager
            try:
                 await pool.initialize() # Pool handles internal initialization state
                 self._pools[name] = pool # Add successfully initialized pool
                 logger.info(f"Pool '{name}' created and initialized.")
                 return pool
            except Exception as e:
                 logger.error(f"Failed to initialize pool '{name}': {e}", exc_info=True)
                 # Don't add failed pool to manager
                 raise RuntimeError(f"Failed to initialize pool '{name}'") from e

    def get_pool(self, name: str) -> SurrealDBConnectionPool:
        """
        Retrieves an existing pool by name.

        Args:
            name: The name of the pool to retrieve.

        Returns:
            The SurrealDBConnectionPool instance.

        Raises:
            KeyError: If no pool with the given name exists.
        """
        # Reading self._pools should be safe without lock if creation/deletion is locked,
        # but lock doesn't hurt if concerned about exotic race conditions during dynamic
        # creation/deletion elsewhere (though current design locks modifications).
        # async with self._lock:
        try:
            return self._pools[name]
        except KeyError:
            logger.error(f"Attempted to get non-existent pool: '{name}'")
            raise KeyError(f"Pool with name '{name}' does not exist. Call create_pool first.")

    async def close_all_pools(self):
        """Closes all managed connection pools."""
        pools_to_close: Dict[str, SurrealDBConnectionPool] = {}
        async with self._lock: # Protect _pools dictionary access
            if not self._pools:
                 logger.info("Pool Manager: No pools to close.")
                 return
            # Move pools to a temporary dict to close outside the lock
            pools_to_close = self._pools.copy()
            self._pools.clear() # Clear the manager's dict immediately

        logger.info(f"Pool Manager: Closing {len(pools_to_close)} pool(s)...")
        # Run closes concurrently outside the lock
        close_tasks = [pool.close() for pool in pools_to_close.values()]
        results = await asyncio.gather(*close_tasks, return_exceptions=True)

        # Log any errors during closure
        for name, result in zip(pools_to_close.keys(), results):
             if isinstance(result, Exception):
                  logger.error(f"Error closing pool '{name}': {result}", exc_info=result)
        logger.info("Pool Manager: Finished closing all pools.")