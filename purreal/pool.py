from __future__ import annotations

import asyncio
import logging
import random
import time
import traceback
from collections import deque
from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator, Optional

from surrealdb import AsyncSurreal

from .types import (
    ConnectionState,
    EventContext,
    PoolConfig,
    PooledConnection,
    PoolEvent,
    PoolExhaustedError,
    PoolPausedError,
    PoolStats,
    SurrealConnectionType,
)
from .events import EventBus
from .predictive import (
    AdaptiveScaler,
    CircuitBreaker,
    DemandPredictor,
    LatencyOracle,
)

logger = logging.getLogger(__name__)


class SurrealDBConnectionPool:
    def __init__(
        self,
        uri: str = "",
        credentials: Optional[dict[str, str]] = None,
        namespace: str = "",
        database: str = "",
        config: Optional[PoolConfig] = None,
        # Legacy kwargs for backward compat
        min_connections: Optional[int] = None,
        max_connections: Optional[int] = None,
        max_idle_time: Optional[float] = None,
        connection_timeout: Optional[float] = None,
        acquisition_timeout: Optional[float] = None,
        health_check_interval: Optional[float] = None,
        max_usage_count: Optional[int] = None,
        connection_retry_attempts: Optional[int] = None,
        connection_retry_delay: Optional[float] = None,
        schema_file: Optional[str] = None,
        on_connection_create=None,
        reset_on_return: bool = True,
        log_queries: bool = False,
        # New params
        pool_size: Optional[int] = None,
        max_overflow: Optional[int] = None,
        pre_ping: bool = True,
        pre_ping_bypass_window: Optional[float] = None,
        prewarm: bool = True,
        adaptive: bool = True,
        max_lifetime: Optional[float] = None,
        leak_detection_threshold: Optional[float] = None,
        max_waiters: Optional[int] = None,
    ):
        if config:
            self._config = config
        else:
            ps = pool_size or min_connections or 5
            mo = (
                max_overflow
                if max_overflow is not None
                else (
                    (max_connections - ps)
                    if max_connections and max_connections > ps
                    else 10
                )
            )
            self._config = PoolConfig(
                uri=uri,
                credentials=credentials or {},
                namespace=namespace,
                database=database,
                pool_size=ps,
                max_overflow=mo,
                min_idle=min_connections or 2,
                max_lifetime=max_lifetime or 1800.0,
                max_idle_time=max_idle_time or 300.0,
                connection_timeout=connection_timeout or 10.0,
                acquisition_timeout=acquisition_timeout or 5.0,
                health_check_interval=health_check_interval or 30.0,
                max_usage_count=max_usage_count or 10000,
                connection_retry_attempts=connection_retry_attempts or 3,
                connection_retry_delay=connection_retry_delay or 1.0,
                pre_ping=pre_ping,
                pre_ping_bypass_window=(
                    pre_ping_bypass_window
                    if pre_ping_bypass_window is not None
                    else 1.0
                ),
                prewarm=prewarm,
                adaptive=adaptive,
                leak_detection_threshold=leak_detection_threshold or 60.0,
                max_waiters=max_waiters or 100,
                schema_file=schema_file,
                on_connection_create=on_connection_create,
                log_queries=log_queries,
            )

        if self._config.pool_size < 1:
            raise ValueError("pool_size must be at least 1")
        if self._config.max_overflow < 0:
            raise ValueError("max_overflow must be non-negative")

        self._idle_queue: asyncio.LifoQueue[PooledConnection] = asyncio.LifoQueue()
        self._semaphore = asyncio.Semaphore(self._config.max_connections)
        self._all_connections: set[PooledConnection] = set()
        self._checked_out: set[PooledConnection] = set()
        self._lock = asyncio.Lock()
        self._waiter_count = 0
        self._paused = False
        self._closed = False
        self._initialized = False
        self._stats = PoolStats()
        self._acquire_latencies: deque[float] = deque(maxlen=1000)

        # 3050 components
        self.events = EventBus()
        self._predictor = DemandPredictor()
        self._oracle = LatencyOracle()
        self._scaler = AdaptiveScaler(
            min_pool=self._config.min_idle,
            max_pool=self._config.max_connections,
        )
        self._breakers: dict[str, CircuitBreaker] = {}

        self._housekeeping_task: Optional[asyncio.Task] = None
        self._leak_detection_task: Optional[asyncio.Task] = None

    # ── Backward-compat properties ──

    @property
    def max_connections(self) -> int:
        return self._config.max_connections

    @property
    def min_connections(self) -> int:
        return self._config.min_idle

    @property
    def uri(self) -> str:
        return self._config.uri

    # ── Lifecycle ──

    async def __aenter__(self):
        if not self._initialized:
            await self.initialize()
        return self

    async def __aexit__(self, *args):
        await self.close()

    async def initialize(self) -> None:
        if self._initialized:
            return
        if self._closed:
            raise RuntimeError("Cannot initialize a closed pool")

        async with self._lock:
            if self._initialized:
                return

            tasks = [self._create_connection() for _ in range(self._config.min_idle)]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            for result in results:
                if isinstance(result, PooledConnection):
                    self._all_connections.add(result)
                    self._idle_queue.put_nowait(result)
                elif isinstance(result, Exception):
                    logger.error(f"Failed to create initial connection: {result}")

            self._stats.current_size = len(self._all_connections)
            self._stats.peak_connections = self._stats.current_size
            self._initialized = True

        self._housekeeping_task = asyncio.create_task(self._housekeeping_loop())
        self._leak_detection_task = asyncio.create_task(self._leak_detection_loop())
        logger.info(
            f"Pool initialized: {len(self._all_connections)} connections "
            f"(target: {self._config.pool_size}+{self._config.max_overflow} overflow)"
        )

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._paused = True

        if self._housekeeping_task:
            self._housekeeping_task.cancel()
            try:
                await self._housekeeping_task
            except asyncio.CancelledError:
                pass
        if self._leak_detection_task:
            self._leak_detection_task.cancel()
            try:
                await self._leak_detection_task
            except asyncio.CancelledError:
                pass

        # Drain: wait briefly for in-flight to return
        deadline = time.monotonic() + 5.0
        while self._checked_out and time.monotonic() < deadline:
            await asyncio.sleep(0.1)

        async with self._lock:
            conns = list(self._all_connections)
            self._all_connections.clear()
            self._checked_out.clear()

        close_tasks = [self._close_connection(c) for c in conns]
        await asyncio.gather(*close_tasks, return_exceptions=True)
        self._initialized = False
        logger.info("Pool closed")

    async def drain(self, timeout: float = 30.0) -> None:
        """Wait for all checked-out connections to return, then close pool."""
        self._paused = True
        await self.events.emit(
            EventContext(event=PoolEvent.DRAIN_START, timestamp=time.monotonic())
        )
        deadline = time.monotonic() + timeout
        while self._checked_out and time.monotonic() < deadline:
            await asyncio.sleep(0.1)
        await self.close()
        await self.events.emit(
            EventContext(event=PoolEvent.DRAIN_COMPLETE, timestamp=time.monotonic())
        )

    def pause(self) -> None:
        self._paused = True

    def resume(self) -> None:
        self._paused = False

    # ── Acquire / Release ──

    @asynccontextmanager
    async def acquire(self) -> AsyncGenerator[SurrealConnectionType, None]:
        if self._closed:
            raise RuntimeError("Connection pool is closed")
        if not self._initialized:
            raise RuntimeError("Pool not initialized")
        if self._paused:
            raise PoolPausedError("Pool is paused")

        start = time.monotonic()
        conn = await self._acquire_connection()
        elapsed_ms = (time.monotonic() - start) * 1000
        self._acquire_latencies.append(elapsed_ms)
        self._predictor.record_acquisition()

        try:
            yield conn.connection
        finally:
            await asyncio.shield(self._release_connection(conn))

    async def checkout(self) -> PooledConnection:
        """Long-term lease without context manager. Caller must call checkin()."""
        if self._closed:
            raise RuntimeError("Connection pool is closed")
        if not self._initialized:
            raise RuntimeError("Pool not initialized")
        return await self._acquire_connection()

    async def checkin(self, conn: PooledConnection) -> None:
        """Return a long-term leased connection."""
        await self._release_connection(conn)

    async def execute_query(self, query: str, params: Optional[dict] = None) -> Any:
        async with self.acquire() as conn:
            start = time.monotonic()
            result = await conn.query(query, params)
            latency_ms = (time.monotonic() - start) * 1000
            self._oracle.record("__pool_query__", latency_ms)
            return result

    # ── Stats ──

    async def get_stats(self) -> dict[str, Any]:
        async with self._lock:
            idle = self._idle_queue.qsize()
            in_use = len(self._checked_out)
            overflow = sum(1 for c in self._all_connections if c.overflow)

        self._stats.current_size = len(self._all_connections)
        self._stats.idle_count = idle
        self._stats.in_use_count = in_use
        self._stats.overflow_count = overflow
        self._stats.waiter_count = self._waiter_count

        if self._acquire_latencies:
            s = sorted(self._acquire_latencies)
            self._stats.p50_acquire_ms = s[len(s) // 2]
            self._stats.p95_acquire_ms = s[int(len(s) * 0.95)]
            self._stats.p99_acquire_ms = s[min(int(len(s) * 0.99), len(s) - 1)]

        return {
            "total_connections_created": self._stats.total_created,
            "total_connections_closed": self._stats.total_closed,
            "total_acquisitions": self._stats.total_acquisitions,
            "total_releases": self._stats.total_releases,
            "acquisition_timeouts": self._stats.total_timeouts,
            "connection_errors": self._stats.total_errors,
            "health_check_failures": self._stats.total_health_failures,
            "peak_connections": self._stats.peak_connections,
            "peak_waiters": self._stats.peak_waiters,
            "current_connections": self._stats.current_size,
            "available_connections": self._stats.idle_count,
            "in_use_connections": self._stats.in_use_count,
            "connection_waiters": self._stats.waiter_count,
            "overflow_connections": self._stats.overflow_count,
            "p50_acquire_ms": self._stats.p50_acquire_ms,
            "p95_acquire_ms": self._stats.p95_acquire_ms,
            "p99_acquire_ms": self._stats.p99_acquire_ms,
            "total_teleports": self._stats.total_teleports,
            "total_pre_warms": self._stats.total_pre_warms,
        }

    # ── Internal: Acquire ──

    async def _acquire_connection(self) -> PooledConnection:
        if self._waiter_count >= self._config.max_waiters:
            raise PoolExhaustedError(
                f"Pool exhausted: {self._waiter_count} waiters at limit"
            )

        self._waiter_count += 1
        try:
            async with asyncio.timeout(self._config.acquisition_timeout):
                await self._semaphore.acquire()
        except asyncio.TimeoutError:
            self._stats.total_timeouts += 1
            await self.events.emit(
                EventContext(event=PoolEvent.TIMEOUT, timestamp=time.monotonic())
            )
            raise
        finally:
            self._waiter_count -= 1

        # Try to get an idle connection (LIFO)
        conn = await self._try_get_idle()
        if conn:
            return conn

        # Create new (possibly overflow)
        conn = await self._create_and_register(overflow=self._at_steady_state())
        return conn

    async def _try_get_idle(self) -> Optional[PooledConnection]:
        while not self._idle_queue.empty():
            try:
                conn = self._idle_queue.get_nowait()
            except asyncio.QueueEmpty:
                break

            # Check expired
            if conn.is_expired or conn.usage_count >= self._config.max_usage_count:
                await self._destroy_connection(conn)
                continue

            # Check circuit breaker
            breaker = self._breakers.get(conn.id)
            if breaker and not breaker.allows_request:
                await self._destroy_connection(conn)
                continue

            # Pre-ping if stale
            if (
                self._config.pre_ping
                and conn.idle_duration > self._config.pre_ping_bypass_window
            ):
                if not await self._validate_connection(conn):
                    await self._destroy_connection(conn)
                    continue

            conn.record_use()
            async with self._lock:
                self._checked_out.add(conn)
            self._stats.total_acquisitions += 1
            await self.events.emit(
                EventContext(
                    event=PoolEvent.ACQUIRE,
                    timestamp=time.monotonic(),
                    connection_id=conn.id,
                )
            )
            return conn
        return None

    def _at_steady_state(self) -> bool:
        return len(self._all_connections) >= self._config.pool_size

    async def _create_and_register(self, overflow: bool = False) -> PooledConnection:
        conn = await self._create_connection(overflow=overflow)
        conn.record_use()
        async with self._lock:
            self._all_connections.add(conn)
            self._checked_out.add(conn)
            self._stats.peak_connections = max(
                self._stats.peak_connections, len(self._all_connections)
            )
        self._breakers[conn.id] = CircuitBreaker()
        self._stats.total_acquisitions += 1

        event = PoolEvent.OVERFLOW_CREATE if overflow else PoolEvent.CREATE
        await self.events.emit(
            EventContext(event=event, timestamp=time.monotonic(), connection_id=conn.id)
        )
        return conn

    # ── Internal: Release ──

    async def _release_connection(self, conn: PooledConnection) -> None:
        self._stats.total_releases += 1
        conn.record_release()

        should_destroy = (
            conn.is_expired
            or conn.usage_count >= self._config.max_usage_count
            or self._closed
        )

        # Overflow connections are always destroyed on release
        if conn.overflow:
            should_destroy = True

        async with self._lock:
            self._checked_out.discard(conn)

        if should_destroy:
            await self._destroy_connection(conn)
        else:
            self._idle_queue.put_nowait(conn)

        self._semaphore.release()
        await self.events.emit(
            EventContext(
                event=PoolEvent.RELEASE,
                timestamp=time.monotonic(),
                connection_id=conn.id,
            )
        )

    # ── Internal: Connection Lifecycle ──

    async def _create_connection(self, overflow: bool = False) -> PooledConnection:
        last_error: Optional[Exception] = None
        for attempt in range(1, self._config.connection_retry_attempts + 1):
            try:
                async with asyncio.timeout(self._config.connection_timeout):
                    db = AsyncSurreal(self._config.uri)
                    await db.signin(self._config.credentials)
                    await db.use(self._config.namespace, self._config.database)

                    if self._config.schema_file:
                        await self._execute_schema(db)
                    if self._config.on_connection_create:
                        try:
                            await self._config.on_connection_create(db)
                        except Exception as e:
                            logger.error(f"on_connection_create callback error: {e}")

                    conn = PooledConnection(
                        connection=db,
                        max_lifetime=self._config.max_lifetime,
                        jitter=self._config.max_lifetime_jitter,
                        overflow=overflow,
                    )
                    self._stats.total_created += 1
                    return conn
            except asyncio.TimeoutError:
                last_error = TimeoutError(f"Connection attempt {attempt} timed out")
            except Exception as e:
                last_error = e
                logger.warning(f"Connection attempt {attempt} failed: {e}")

            if attempt < self._config.connection_retry_attempts:
                delay = self._config.connection_retry_delay * (2 ** (attempt - 1))
                delay += random.uniform(0, 0.5)
                await asyncio.sleep(delay)

        self._stats.total_errors += 1
        raise last_error or RuntimeError("Failed to create connection")

    async def _validate_connection(self, conn: PooledConnection) -> bool:
        try:
            async with asyncio.timeout(2.0):
                await conn.connection.query("INFO FOR DB;")
            breaker = self._breakers.get(conn.id)
            if breaker:
                breaker.record_success()
            return True
        except Exception:
            self._stats.total_health_failures += 1
            breaker = self._breakers.get(conn.id)
            if breaker:
                breaker.record_failure()
            await self.events.emit(
                EventContext(
                    event=PoolEvent.HEALTH_FAIL,
                    timestamp=time.monotonic(),
                    connection_id=conn.id,
                )
            )
            return False

    async def _destroy_connection(self, conn: PooledConnection) -> None:
        async with self._lock:
            self._all_connections.discard(conn)
            self._checked_out.discard(conn)
        self._breakers.pop(conn.id, None)
        self._oracle.remove_connection(conn.id)
        await self._close_connection(conn)
        self._stats.total_closed += 1
        event = PoolEvent.OVERFLOW_DESTROY if conn.overflow else PoolEvent.DESTROY
        await self.events.emit(
            EventContext(event=event, timestamp=time.monotonic(), connection_id=conn.id)
        )

    async def _close_connection(self, conn: PooledConnection) -> None:
        try:
            async with asyncio.timeout(5.0):
                await conn.connection.close()
        except Exception as e:
            logger.debug(f"Error closing {conn.id}: {e}")

    async def _execute_schema(self, db) -> None:
        if not self._config.schema_file:
            return
        with open(self._config.schema_file, "r") as f:
            schema = f.read()
        if schema.strip():
            await db.query(schema)

    # ── Background Tasks ──

    async def _housekeeping_loop(self) -> None:
        while not self._closed:
            try:
                await asyncio.sleep(self._config.housekeeping_interval)
                if self._closed:
                    break
                await self._run_housekeeping()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Housekeeping error: {e}")
                await asyncio.sleep(1)

    async def _run_housekeeping(self) -> None:
        idle = self._idle_queue.qsize()
        size = len(self._all_connections)

        # Adaptive scaling (latency-driven). Disabled with adaptive=False.
        if self._config.adaptive:
            p95 = self._oracle.pool_p95()
            decision = self._scaler.decide(p95, size, idle)

            if decision > 0:
                try:
                    conn = await self._create_connection()
                    async with self._lock:
                        self._all_connections.add(conn)
                    self._idle_queue.put_nowait(conn)
                    self._breakers[conn.id] = CircuitBreaker()
                    self._stats.current_size = len(self._all_connections)
                    await self.events.emit(
                        EventContext(
                            event=PoolEvent.SCALE_UP,
                            timestamp=time.monotonic(),
                            connection_id=conn.id,
                        )
                    )
                except Exception as e:
                    logger.warning(f"Scale-up failed: {e}")
            elif decision < 0 and idle > self._config.min_idle:
                try:
                    conn = self._idle_queue.get_nowait()
                    await self._destroy_connection(conn)
                    self._semaphore.release()
                    await self.events.emit(
                        EventContext(
                            event=PoolEvent.SCALE_DOWN,
                            timestamp=time.monotonic(),
                            connection_id=conn.id,
                        )
                    )
                except asyncio.QueueEmpty:
                    pass

        # Predictive pre-warming. Disabled with prewarm=False. The forecast is
        # bounded by recent demand, so a near-idle pool pre-warms ~0 (see
        # DemandPredictor.predict_demand).
        if self._config.prewarm:
            predicted = self._predictor.predict_demand(horizon_seconds=10.0)
            deficit = predicted - idle
            if deficit > 0 and size < self._config.max_connections:
                to_create = min(deficit, self._config.max_connections - size, 3)
                for _ in range(to_create):
                    try:
                        conn = await self._create_connection()
                        async with self._lock:
                            self._all_connections.add(conn)
                        self._idle_queue.put_nowait(conn)
                        self._breakers[conn.id] = CircuitBreaker()
                        self._stats.total_pre_warms += 1
                        await self.events.emit(
                            EventContext(
                                event=PoolEvent.PRE_WARM,
                                timestamp=time.monotonic(),
                                connection_id=conn.id,
                            )
                        )
                    except Exception:
                        break

        # Evict idle beyond min_idle that exceed idle timeout
        evicted = 0
        temp = []
        while not self._idle_queue.empty() and not self._closed:
            try:
                conn = self._idle_queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            if (
                conn.idle_duration > self._config.max_idle_time
                and len(self._all_connections) - evicted > self._config.min_idle
            ):
                await self._destroy_connection(conn)
                self._semaphore.release()
                evicted += 1
            elif conn.is_expired:
                await self._destroy_connection(conn)
                self._semaphore.release()
                evicted += 1
            else:
                temp.append(conn)
        for c in temp:
            self._idle_queue.put_nowait(c)

        # Rotate degraded connections
        for conn_id in list(self._breakers.keys()):
            if self._oracle.is_degraded(conn_id):
                logger.info(f"Connection {conn_id} degraded, scheduling rotation")

        # Replenish to min_idle
        current_idle = self._idle_queue.qsize()
        if current_idle < self._config.min_idle and not self._closed:
            deficit = self._config.min_idle - current_idle
            for _ in range(deficit):
                if len(self._all_connections) >= self._config.max_connections:
                    break
                try:
                    conn = await self._create_connection()
                    async with self._lock:
                        self._all_connections.add(conn)
                    self._idle_queue.put_nowait(conn)
                    self._breakers[conn.id] = CircuitBreaker()
                except Exception:
                    break

        # Decay predictor daily patterns slightly
        self._predictor.decay(0.99)
        self._stats.current_size = len(self._all_connections)

    async def _leak_detection_loop(self) -> None:
        while not self._closed:
            try:
                await asyncio.sleep(30)
                if self._closed:
                    break
                now = time.monotonic()
                async with self._lock:
                    leaked = [
                        c
                        for c in self._checked_out
                        if c.checkout_time
                        and (now - c.checkout_time)
                        > self._config.leak_detection_threshold
                    ]
                for conn in leaked:
                    logger.warning(
                        f"LEAK DETECTED: Connection {conn.id} held for "
                        f"{now - conn.checkout_time:.1f}s\n"
                        f"Checkout stack:\n{conn.checkout_stack}"
                    )
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Leak detection error: {e}")


class SurrealDBPoolManager:
    _instance: Optional[SurrealDBPoolManager] = None
    _pools: dict[str, SurrealDBConnectionPool] = {}
    _lock = asyncio.Lock()

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    async def create_pool(self, name: str, **kwargs) -> SurrealDBConnectionPool:
        async with self._lock:
            if name in self._pools:
                raise ValueError(f"Pool '{name}' already exists")
            pool = SurrealDBConnectionPool(**kwargs)
            await pool.initialize()
            self._pools[name] = pool
            return pool

    def get_pool(self, name: str) -> SurrealDBConnectionPool:
        try:
            return self._pools[name]
        except KeyError:
            raise KeyError(
                f"Pool with name '{name}' does not exist. Call create_pool first."
            )

    async def close_all_pools(self) -> None:
        async with self._lock:
            pools = dict(self._pools)
            self._pools.clear()
        tasks = [p.close() for p in pools.values()]
        await asyncio.gather(*tasks, return_exceptions=True)
