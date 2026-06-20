from __future__ import annotations

import asyncio
import time
import hashlib
import traceback
from collections import deque
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Awaitable, Callable, Optional, Union

from surrealdb import AsyncSurreal, AsyncWsSurrealConnection, AsyncHttpSurrealConnection


SurrealConnectionType = Union[AsyncWsSurrealConnection, AsyncHttpSurrealConnection]
EventHandler = Callable[["EventContext"], Union[Awaitable[None], None]]


class PoolEvent(Enum):
	ACQUIRE = auto()
	RELEASE = auto()
	CREATE = auto()
	DESTROY = auto()
	HEALTH_FAIL = auto()
	TIMEOUT = auto()
	OVERFLOW_CREATE = auto()
	OVERFLOW_DESTROY = auto()
	DRAIN_START = auto()
	DRAIN_COMPLETE = auto()
	PRE_WARM = auto()
	SCALE_UP = auto()
	SCALE_DOWN = auto()
	CIRCUIT_OPEN = auto()
	CIRCUIT_CLOSE = auto()
	TELEPORT = auto()


class ConnectionState(Enum):
	IDLE = auto()
	IN_USE = auto()
	HEALTH_CHECK = auto()
	CLOSING = auto()
	CLOSED = auto()


class SessionState(Enum):
	IDLE = auto()
	QUEUED = auto()
	EXECUTING = auto()
	EXPIRED = auto()
	CLOSED = auto()


class SlotHealth(Enum):
	HEALTHY = auto()
	DEGRADED = auto()
	RECONNECTING = auto()
	DEAD = auto()


class CircuitState(Enum):
	CLOSED = auto()
	OPEN = auto()
	HALF_OPEN = auto()


class PoolExhaustedError(Exception):
	pass


class PoolPausedError(Exception):
	pass


class SessionExpiredError(Exception):
	pass


class TeleportError(Exception):
	pass


@dataclass
class PoolConfig:
	uri: str
	credentials: dict[str, str]
	namespace: str
	database: str
	pool_size: int = 5
	max_overflow: int = 10
	min_idle: int = 2
	max_lifetime: float = 1800.0
	max_idle_time: float = 300.0
	connection_timeout: float = 10.0
	acquisition_timeout: float = 5.0
	health_check_interval: float = 30.0
	housekeeping_interval: float = 5.0
	max_usage_count: int = 10000
	connection_retry_attempts: int = 3
	connection_retry_delay: float = 1.0
	pre_ping: bool = True
	pre_ping_bypass_window: float = 1.0
	prewarm: bool = True
	adaptive: bool = True
	leak_detection_threshold: float = 60.0
	max_waiters: int = 100
	max_lifetime_jitter: float = 0.025
	schema_file: Optional[str] = None
	on_connection_create: Optional[Callable] = None
	log_queries: bool = False

	@property
	def max_connections(self) -> int:
		return self.pool_size + self.max_overflow


@dataclass
class PooledConnection:
	__slots__ = (
		"connection", "created_at", "expires_at", "last_used",
		"state", "usage_count", "id", "overflow",
		"checkout_stack", "checkout_time", "latency_samples",
	)
	connection: SurrealConnectionType
	created_at: float
	expires_at: float
	last_used: float
	state: ConnectionState
	usage_count: int
	id: str
	overflow: bool
	checkout_stack: Optional[str]
	checkout_time: Optional[float]
	latency_samples: deque

	def __init__(
		self,
		connection: SurrealConnectionType,
		max_lifetime: float = 1800.0,
		jitter: float = 0.025,
		overflow: bool = False,
	):
		import random
		import uuid
		now = time.monotonic()
		self.connection = connection
		self.created_at = now
		self.expires_at = now + max_lifetime + (max_lifetime * random.uniform(-jitter, jitter))
		self.last_used = now
		self.state = ConnectionState.IDLE
		self.usage_count = 0
		self.id = f"conn_{uuid.uuid4().hex[:8]}"
		self.overflow = overflow
		self.checkout_stack = None
		self.checkout_time = None
		self.latency_samples = deque(maxlen=100)

	@property
	def is_expired(self) -> bool:
		return time.monotonic() >= self.expires_at

	@property
	def idle_duration(self) -> float:
		return time.monotonic() - self.last_used

	def record_use(self) -> None:
		self.last_used = time.monotonic()
		self.usage_count += 1
		self.state = ConnectionState.IN_USE
		self.checkout_time = time.monotonic()
		self.checkout_stack = "".join(traceback.format_stack()[:-1])

	def record_release(self) -> None:
		self.last_used = time.monotonic()
		self.state = ConnectionState.IDLE
		self.checkout_stack = None
		self.checkout_time = None

	def record_latency(self, ms: float) -> None:
		self.latency_samples.append(ms)

	def __hash__(self) -> int:
		return hash(self.id)

	def __eq__(self, other) -> bool:
		if isinstance(other, PooledConnection):
			return self.id == other.id
		return NotImplemented


@dataclass(frozen=True, slots=True)
class PhysicalState:
	namespace: str
	database: str
	creds_hash: str
	variables: frozenset = frozenset()

	@classmethod
	def from_config(
		cls,
		namespace: str,
		database: str,
		credentials: dict[str, str],
		variables: Optional[dict[str, Any]] = None,
	) -> PhysicalState:
		raw = str(sorted(credentials.items())).encode()
		h = hashlib.sha256(raw).hexdigest()[:16]
		vs = frozenset((variables or {}).items())
		return cls(namespace=namespace, database=database, creds_hash=h, variables=vs)


@dataclass
class PendingRequest:
	__slots__ = ("state", "query", "params", "future", "created_at", "retry_count")
	state: PhysicalState
	query: str
	params: Optional[dict]
	future: asyncio.Future
	created_at: float
	retry_count: int

	def __init__(
		self,
		state: PhysicalState,
		query: str,
		params: Optional[dict],
		future: asyncio.Future,
	):
		self.state = state
		self.query = query
		self.params = params
		self.future = future
		self.created_at = time.monotonic()
		self.retry_count = 0


@dataclass(frozen=True, slots=True)
class EventContext:
	event: PoolEvent
	timestamp: float
	connection_id: Optional[str] = None
	duration_ms: Optional[float] = None
	metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class PoolStats:
	total_created: int = 0
	total_closed: int = 0
	total_acquisitions: int = 0
	total_releases: int = 0
	total_timeouts: int = 0
	total_errors: int = 0
	total_health_failures: int = 0
	total_teleports: int = 0
	total_pre_warms: int = 0
	peak_connections: int = 0
	peak_waiters: int = 0
	current_size: int = 0
	idle_count: int = 0
	in_use_count: int = 0
	overflow_count: int = 0
	waiter_count: int = 0
	p50_acquire_ms: float = 0.0
	p95_acquire_ms: float = 0.0
	p99_acquire_ms: float = 0.0
