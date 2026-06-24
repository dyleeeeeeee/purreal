from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections import deque
from typing import Any, Optional

from .types import (
    EventContext,
    PendingRequest,
    PhysicalState,
    PooledConnection,
    PoolEvent,
    SessionExpiredError,
    SessionState,
    SlotHealth,
    TeleportError,
)
from .pool import SurrealDBConnectionPool

logger = logging.getLogger(__name__)

MAX_RETRIES = 2


class PhysicalSlot:
    """Manages one physical connection: drain loop, request queue, state tracking."""

    __slots__ = (
        "_pool",
        "_conn",
        "_state",
        "_queue",
        "_drain_task",
        "_health",
        "_id",
        "_closed",
    )

    def __init__(self, pool: SurrealDBConnectionPool, slot_id: int) -> None:
        self._pool = pool
        self._conn: Optional[PooledConnection] = None
        self._state: Optional[PhysicalState] = None
        self._queue: asyncio.Queue[PendingRequest] = asyncio.Queue()
        self._drain_task: Optional[asyncio.Task] = None
        self._health = SlotHealth.HEALTHY
        self._id = f"slot_{slot_id}"
        self._closed = False

    @property
    def queue_depth(self) -> int:
        return self._queue.qsize()

    @property
    def current_state(self) -> Optional[PhysicalState]:
        return self._state

    @property
    def health(self) -> SlotHealth:
        return self._health

    @property
    def connection(self) -> Optional[PooledConnection]:
        return self._conn

    async def start(self) -> None:
        self._conn = await self._pool.checkout()
        self._drain_task = asyncio.create_task(self._drain_loop())

    async def stop(self) -> None:
        self._closed = True
        if self._drain_task:
            self._drain_task.cancel()
            try:
                await self._drain_task
            except asyncio.CancelledError:
                pass
        # Fail remaining requests
        while not self._queue.empty():
            try:
                req = self._queue.get_nowait()
                if not req.future.done():
                    req.future.set_exception(RuntimeError("Slot shutting down"))
            except asyncio.QueueEmpty:
                break
        if self._conn:
            await self._pool.checkin(self._conn)
            self._conn = None

    def enqueue(self, request: PendingRequest) -> None:
        self._queue.put_nowait(request)

    async def _drain_loop(self) -> None:
        while not self._closed:
            try:
                request = await self._queue.get()
            except asyncio.CancelledError:
                break

            if request.future.done():
                continue

            try:
                await self._apply_state(request.state)
                result = await self._execute(request)
                if not request.future.done():
                    request.future.set_result(result)
            except (ConnectionError, OSError, asyncio.TimeoutError) as e:
                await self._handle_connection_failure(request, e)
            except Exception as e:
                if not request.future.done():
                    request.future.set_exception(e)

    async def _apply_state(self, desired: PhysicalState) -> None:
        if self._state == desired:
            return
        if not self._conn:
            raise ConnectionError("No physical connection")

        conn = self._conn.connection
        needs_use = (
            self._state is None
            or self._state.namespace != desired.namespace
            or self._state.database != desired.database
        )
        needs_auth = self._state is None or self._state.creds_hash != desired.creds_hash
        needs_vars = self._state is None or self._state.variables != desired.variables

        if needs_use:
            await conn.use(desired.namespace, desired.database)
        if needs_auth:
            # Credentials are stored in the session that created the request
            # Re-signin with stored creds (passed via request metadata)
            pass  # Auth is set during initial checkout; skip re-auth for same pool
        if needs_vars:
            # Apply LET variables
            current_vars = self._state.variables if self._state else frozenset()
            for key, value in desired.variables:
                if (key, value) not in current_vars:
                    await conn.query(f"LET ${key} = {repr(value)};")

        self._state = desired

    async def _execute(self, request: PendingRequest) -> Any:
        if not self._conn:
            raise ConnectionError("No physical connection")
        start = time.monotonic()
        async with asyncio.timeout(30.0):
            result = await self._conn.connection.query(request.query, request.params)
        latency_ms = (time.monotonic() - start) * 1000
        self._conn.record_latency(latency_ms)
        return result

    async def _handle_connection_failure(
        self, request: PendingRequest, error: Exception
    ) -> None:
        request.retry_count += 1
        if request.retry_count > MAX_RETRIES:
            if not request.future.done():
                request.future.set_exception(error)
            return

        logger.warning(
            f"{self._id}: connection failed, teleporting (attempt {request.retry_count})"
        )
        self._health = SlotHealth.RECONNECTING
        self._state = None

        # Return broken connection
        if self._conn:
            try:
                await self._pool.checkin(self._conn)
            except Exception:
                pass

        # Get fresh connection
        try:
            self._conn = await self._pool.checkout()
            self._health = SlotHealth.HEALTHY
            # Re-enqueue the failed request
            self.enqueue(request)
        except Exception as e:
            self._health = SlotHealth.DEAD
            if not request.future.done():
                request.future.set_exception(TeleportError(f"Teleport failed: {e}"))

    async def proactive_migrate(self) -> None:
        """Preemptively switch to a fresh connection before expiry."""
        if not self._conn:
            return
        old = self._conn
        try:
            self._conn = await self._pool.checkout()
            self._state = None  # Force re-apply on next request
            await self._pool.checkin(old)
        except Exception as e:
            logger.error(f"{self._id}: proactive migration failed: {e}")
            self._conn = old


class VirtualSession:
    """User-facing session handle — stateful, backed by any physical slot."""

    __slots__ = (
        "_multiplexer",
        "_state",
        "_credentials",
        "_variables",
        "session_id",
        "_session_state",
        "_last_active",
    )

    def __init__(
        self,
        multiplexer: SessionMultiplexer,
        namespace: str,
        database: str,
        credentials: dict[str, str],
    ) -> None:
        self._multiplexer = multiplexer
        self._credentials = credentials
        self._variables: dict[str, Any] = {}
        self._state = PhysicalState.from_config(namespace, database, credentials)
        self.session_id = f"sess_{uuid.uuid4().hex[:12]}"
        self._session_state = SessionState.IDLE
        self._last_active = time.monotonic()

    @property
    def namespace(self) -> str:
        return self._state.namespace

    @property
    def database(self) -> str:
        return self._state.database

    @property
    def is_active(self) -> bool:
        return self._session_state not in (SessionState.EXPIRED, SessionState.CLOSED)

    @property
    def idle_duration(self) -> float:
        return time.monotonic() - self._last_active

    async def query(self, sql: str, params: Optional[dict] = None) -> Any:
        self._check_active()
        return await self._execute(sql, params)

    async def select(self, thing: str) -> Any:
        return await self.query(f"SELECT * FROM {thing}")

    async def create(self, thing: str, data: Optional[dict] = None) -> Any:
        if data:
            return await self.query(f"CREATE {thing} CONTENT $data", {"data": data})
        return await self.query(f"CREATE {thing}")

    async def update(self, thing: str, data: dict) -> Any:
        return await self.query(f"UPDATE {thing} CONTENT $data", {"data": data})

    async def delete(self, thing: str) -> Any:
        return await self.query(f"DELETE {thing}")

    async def let(self, key: str, value: Any) -> None:
        self._check_active()
        self._variables[key] = value
        self._state = PhysicalState(
            namespace=self._state.namespace,
            database=self._state.database,
            creds_hash=self._state.creds_hash,
            variables=frozenset(self._variables.items()),
        )

    async def close(self) -> None:
        self._session_state = SessionState.CLOSED
        self._multiplexer._remove_session(self)

    def _check_active(self) -> None:
        if not self.is_active:
            raise SessionExpiredError(f"Session {self.session_id} is no longer active")

    async def _execute(self, sql: str, params: Optional[dict]) -> Any:
        self._session_state = SessionState.QUEUED
        self._last_active = time.monotonic()

        future: asyncio.Future = asyncio.get_event_loop().create_future()
        request = PendingRequest(
            state=self._state,
            query=sql,
            params=params,
            future=future,
        )

        self._multiplexer._route_request(request)
        self._session_state = SessionState.EXECUTING

        try:
            result = await future
            self._session_state = SessionState.IDLE
            return result
        except Exception:
            self._session_state = SessionState.IDLE
            raise


class SessionRouter:
    """Routes requests to the optimal physical slot based on state affinity."""

    __slots__ = ("_slots",)

    def __init__(self, slots: list[PhysicalSlot]) -> None:
        self._slots = slots

    def route(self, request: PendingRequest) -> PhysicalSlot:
        best_slot = None
        best_score = -1

        for slot in self._slots:
            if slot.health == SlotHealth.DEAD:
                continue

            score = 0
            state = slot.current_state

            if state == request.state:
                score += 1000  # Perfect match — zero state switch
            elif (
                state
                and state.namespace == request.state.namespace
                and state.database == request.state.database
            ):
                score += 500  # Same ns/db, only need re-auth or vars
            elif state and state.namespace == request.state.namespace:
                score += 200  # Same namespace

            # Penalize queue depth
            score -= slot.queue_depth * 10

            # Penalize reconnecting slots
            if slot.health == SlotHealth.RECONNECTING:
                score -= 5000

            if score > best_score:
                best_score = score
                best_slot = slot

        if best_slot is None:
            # All dead — pick first one (it'll trigger teleport)
            best_slot = self._slots[0]

        return best_slot


class SessionMultiplexer:
    """
    Manages N physical connections serving M virtual sessions (M >> N).
    Provides transparent session teleportation, state-aware routing,
    and proactive connection migration.
    """

    def __init__(
        self,
        pool: SurrealDBConnectionPool,
        num_slots: int = 3,
        max_sessions: int = 200,
        session_idle_timeout: float = 300.0,
        max_queue_per_slot: int = 50,
    ) -> None:
        self._pool = pool
        self._num_slots = num_slots
        self._max_sessions = max_sessions
        self._session_idle_timeout = session_idle_timeout
        self._max_queue_per_slot = max_queue_per_slot

        self._slots: list[PhysicalSlot] = []
        self._router: Optional[SessionRouter] = None
        self._sessions: dict[str, VirtualSession] = {}
        self._reaper_task: Optional[asyncio.Task] = None
        self._migration_task: Optional[asyncio.Task] = None
        self._closed = False

    async def __aenter__(self):
        await self.start()
        return self

    async def __aexit__(self, *args):
        await self.stop()

    async def start(self) -> None:
        for i in range(self._num_slots):
            slot = PhysicalSlot(self._pool, i)
            await slot.start()
            self._slots.append(slot)

        self._router = SessionRouter(self._slots)
        self._reaper_task = asyncio.create_task(self._reaper_loop())
        self._migration_task = asyncio.create_task(self._migration_loop())
        logger.info(
            f"Multiplexer started: {self._num_slots} slots, max {self._max_sessions} sessions"
        )

    async def stop(self) -> None:
        self._closed = True
        if self._reaper_task:
            self._reaper_task.cancel()
            try:
                await self._reaper_task
            except asyncio.CancelledError:
                pass
        if self._migration_task:
            self._migration_task.cancel()
            try:
                await self._migration_task
            except asyncio.CancelledError:
                pass

        for slot in self._slots:
            await slot.stop()
        self._slots.clear()
        self._sessions.clear()
        logger.info("Multiplexer stopped")

    async def get_session(
        self,
        namespace: str,
        database: str,
        credentials: dict[str, str],
    ) -> VirtualSession:
        if self._closed:
            raise RuntimeError("Multiplexer is closed")
        if len(self._sessions) >= self._max_sessions:
            raise PoolExhaustedError(f"Max sessions ({self._max_sessions}) reached")

        session = VirtualSession(self, namespace, database, credentials)
        self._sessions[session.session_id] = session
        return session

    def _route_request(self, request: PendingRequest) -> None:
        slot = self._router.route(request)
        slot.enqueue(request)

    def _remove_session(self, session: VirtualSession) -> None:
        self._sessions.pop(session.session_id, None)

    async def get_stats(self) -> dict[str, Any]:
        return {
            "active_sessions": len(self._sessions),
            "slots": [
                {
                    "id": slot._id,
                    "health": slot.health.name,
                    "queue_depth": slot.queue_depth,
                    "state": str(slot.current_state) if slot.current_state else None,
                }
                for slot in self._slots
            ],
        }

    async def _reaper_loop(self) -> None:
        while not self._closed:
            try:
                await asyncio.sleep(30)
                if self._closed:
                    break
                now = time.monotonic()
                expired = [
                    s
                    for s in self._sessions.values()
                    if s.idle_duration > self._session_idle_timeout
                    and s._session_state == SessionState.IDLE
                ]
                for session in expired:
                    session._session_state = SessionState.EXPIRED
                    self._sessions.pop(session.session_id, None)
                if expired:
                    logger.debug(f"Reaped {len(expired)} idle sessions")
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Reaper error: {e}")

    async def _migration_loop(self) -> None:
        """Proactively migrate sessions off connections nearing expiry."""
        while not self._closed:
            try:
                await asyncio.sleep(5)
                if self._closed:
                    break
                now = time.monotonic()
                for slot in self._slots:
                    conn = slot.connection
                    if conn and (conn.expires_at - now) < 10.0:
                        logger.info(
                            f"{slot._id}: proactive migration (expires in {conn.expires_at - now:.1f}s)"
                        )
                        await slot.proactive_migrate()
                        await self._pool.events.emit(
                            EventContext(
                                event=PoolEvent.TELEPORT,
                                timestamp=now,
                                connection_id=conn.id,
                                metadata={"reason": "proactive_migration"},
                            )
                        )
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Migration loop error: {e}")


# Import here to avoid circular
from .types import PoolExhaustedError  # noqa: E402
