from __future__ import annotations

import math
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Optional

from .types import CircuitState


class DemandPredictor:
	"""Predicts future connection demand from historical patterns + EWMA."""

	BUCKET_SIZE = 5.0  # seconds per bucket
	BUCKETS_PER_DAY = 17280  # 86400 / 5

	__slots__ = ("_ring", "_ewma", "_alpha", "_last_tick")

	def __init__(self, alpha: float = 0.3) -> None:
		self._ring = [0.0] * self.BUCKETS_PER_DAY
		self._ewma = 0.0
		self._alpha = alpha
		self._last_tick = time.monotonic()

	def _bucket_index(self, offset_seconds: float = 0.0) -> int:
		t = time.time() + offset_seconds
		seconds_into_day = t % 86400
		return int(seconds_into_day / self.BUCKET_SIZE) % self.BUCKETS_PER_DAY

	def record_acquisition(self) -> None:
		idx = self._bucket_index()
		self._ring[idx] += 1
		now = time.monotonic()
		dt = max(now - self._last_tick, 1e-6)
		rate = 1.0 / dt
		self._ewma = self._alpha * rate + (1 - self._alpha) * self._ewma
		self._last_tick = now

	def predict_demand(self, horizon_seconds: float = 10.0) -> int:
		"""Predict how many connections will be needed in the next `horizon` seconds."""
		# Historical pattern: average of next N buckets
		buckets_ahead = max(1, int(horizon_seconds / self.BUCKET_SIZE))
		historical = 0.0
		for i in range(1, buckets_ahead + 1):
			idx = self._bucket_index(offset_seconds=i * self.BUCKET_SIZE)
			historical += self._ring[idx]
		historical_rate = historical / buckets_ahead

		# EWMA-based prediction
		ewma_prediction = self._ewma * horizon_seconds

		# Take the higher of the two signals
		return max(int(math.ceil(historical_rate)), int(math.ceil(ewma_prediction)))

	def decay(self, factor: float = 0.95) -> None:
		"""Decay historical data to adapt to changing patterns."""
		for i in range(len(self._ring)):
			self._ring[i] *= factor


class LatencyOracle:
	"""Tracks per-connection latency, detects degraded connections via 3-sigma."""

	__slots__ = ("_samples", "_global_samples")

	def __init__(self) -> None:
		self._samples: dict[str, deque[float]] = {}
		self._global_samples: deque[float] = deque(maxlen=1000)

	def record(self, conn_id: str, latency_ms: float) -> None:
		if conn_id not in self._samples:
			self._samples[conn_id] = deque(maxlen=100)
		self._samples[conn_id].append(latency_ms)
		self._global_samples.append(latency_ms)

	def remove_connection(self, conn_id: str) -> None:
		self._samples.pop(conn_id, None)

	def is_degraded(self, conn_id: str) -> bool:
		"""True if connection's median latency exceeds 3σ above pool mean."""
		samples = self._samples.get(conn_id)
		if not samples or len(samples) < 5:
			return False
		if len(self._global_samples) < 10:
			return False

		conn_median = sorted(samples)[len(samples) // 2]
		global_mean = sum(self._global_samples) / len(self._global_samples)
		variance = sum((x - global_mean) ** 2 for x in self._global_samples) / len(self._global_samples)
		sigma = math.sqrt(variance) if variance > 0 else 0.0

		if sigma == 0:
			return False
		return conn_median > global_mean + 3 * sigma

	def pool_p95(self) -> float:
		if len(self._global_samples) < 2:
			return 0.0
		s = sorted(self._global_samples)
		idx = int(len(s) * 0.95)
		return s[min(idx, len(s) - 1)]

	def pool_p50(self) -> float:
		if len(self._global_samples) < 2:
			return 0.0
		s = sorted(self._global_samples)
		return s[len(s) // 2]

	def pool_p99(self) -> float:
		if len(self._global_samples) < 2:
			return 0.0
		s = sorted(self._global_samples)
		idx = int(len(s) * 0.99)
		return s[min(idx, len(s) - 1)]


class CircuitBreaker:
	"""Per-connection circuit breaker with open/half-open/closed states."""

	__slots__ = (
		"_state", "_failure_count", "_threshold",
		"_cooldown", "_last_failure_time", "_success_count_in_half_open",
	)

	def __init__(self, threshold: int = 5, cooldown: float = 30.0) -> None:
		self._state = CircuitState.CLOSED
		self._failure_count = 0
		self._threshold = threshold
		self._cooldown = cooldown
		self._last_failure_time = 0.0
		self._success_count_in_half_open = 0

	@property
	def state(self) -> CircuitState:
		if self._state == CircuitState.OPEN:
			if time.monotonic() - self._last_failure_time >= self._cooldown:
				self._state = CircuitState.HALF_OPEN
				self._success_count_in_half_open = 0
		return self._state

	@property
	def allows_request(self) -> bool:
		s = self.state
		return s in (CircuitState.CLOSED, CircuitState.HALF_OPEN)

	def record_success(self) -> None:
		if self._state == CircuitState.HALF_OPEN:
			self._success_count_in_half_open += 1
			if self._success_count_in_half_open >= 1:
				self._state = CircuitState.CLOSED
				self._failure_count = 0
		elif self._state == CircuitState.CLOSED:
			self._failure_count = max(0, self._failure_count - 1)

	def record_failure(self) -> None:
		self._failure_count += 1
		self._last_failure_time = time.monotonic()
		if self._failure_count >= self._threshold:
			self._state = CircuitState.OPEN

	def reset(self) -> None:
		self._state = CircuitState.CLOSED
		self._failure_count = 0


@dataclass
class AdaptiveScaler:
	"""Emits pool scaling decisions based on latency targets."""

	target_p95_ms: float = 5.0
	low_utilization_threshold: float = 0.3
	min_pool: int = 2
	max_pool: int = 50
	cooldown_seconds: float = 5.0

	_last_decision_time: float = field(default=0.0, init=False, repr=False)

	def decide(
		self,
		current_p95_ms: float,
		current_size: int,
		idle_count: int,
	) -> int:
		"""Returns +1 (grow), -1 (shrink), or 0 (hold)."""
		now = time.monotonic()
		if now - self._last_decision_time < self.cooldown_seconds:
			return 0

		utilization = 1.0 - (idle_count / max(current_size, 1))

		if current_p95_ms > self.target_p95_ms and current_size < self.max_pool:
			self._last_decision_time = now
			return 1

		if (current_p95_ms < self.target_p95_ms * 0.5
			and utilization < self.low_utilization_threshold
			and current_size > self.min_pool):
			self._last_decision_time = now
			return -1

		return 0
