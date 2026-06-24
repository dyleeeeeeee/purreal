# -*- coding: utf-8 -*-
"""Test suite for predictive pre-warming, latency oracle, circuit breaker."""

import time

from purreal.predictive import (
    AdaptiveScaler,
    CircuitBreaker,
    DemandPredictor,
    LatencyOracle,
)
from purreal.types import CircuitState


class TestDemandPredictor:
    def test_initial_prediction_zero(self):
        p = DemandPredictor()
        assert p.predict_demand(10.0) == 0

    def test_records_increase_prediction(self):
        p = DemandPredictor()
        for _ in range(10):
            p.record_acquisition()
        prediction = p.predict_demand(10.0)
        assert prediction > 0

    def test_ewma_responds_to_burst(self):
        p = DemandPredictor(alpha=0.5)
        # Simulate rapid burst
        for _ in range(20):
            p.record_acquisition()
        pred = p.predict_demand(5.0)
        assert pred > 0

    def test_decay_reduces_historical(self):
        p = DemandPredictor()
        p._ring[0] = 100.0
        p.decay(0.5)
        assert p._ring[0] == 50.0

    def test_ewma_decays_when_idle(self):
        # A burst (~20 acquisitions/s) drives the forecast up...
        p = DemandPredictor(alpha=0.5)
        for _ in range(20):
            p.record_acquisition()
            p._last_tick -= 0.05  # space arrivals ~50ms apart
        assert p.predict_demand(10.0) > 0
        # ...but once the pool sits idle, a stale burst must not keep the
        # forecast pinned high (this drove the pre-warm/reap/warm churn).
        p._last_tick -= 600.0  # simulate 10 minutes with no acquisitions
        assert p.predict_demand(10.0) == 0


class TestLatencyOracle:
    def test_empty_not_degraded(self):
        o = LatencyOracle()
        assert not o.is_degraded("conn_1")

    def test_normal_not_degraded(self):
        o = LatencyOracle()
        for i in range(20):
            o.record("conn_1", 5.0 + (i % 3) * 0.5)
            o.record("conn_2", 5.0 + (i % 3) * 0.5)
        assert not o.is_degraded("conn_1")

    def test_outlier_detected(self):
        o = LatencyOracle()
        # Normal connections
        for i in range(50):
            o.record("conn_1", 5.0)
            o.record("conn_2", 5.0)
        # Degraded connection
        for i in range(10):
            o.record("conn_3", 500.0)
        assert o.is_degraded("conn_3")

    def test_p95(self):
        o = LatencyOracle()
        for i in range(100):
            o.record("conn_1", float(i))
        p95 = o.pool_p95()
        assert 90 <= p95 <= 99

    def test_remove_connection(self):
        o = LatencyOracle()
        o.record("conn_1", 5.0)
        o.remove_connection("conn_1")
        assert "conn_1" not in o._samples


class TestCircuitBreaker:
    def test_starts_closed(self):
        cb = CircuitBreaker(threshold=3, cooldown=1.0)
        assert cb.state == CircuitState.CLOSED
        assert cb.allows_request

    def test_opens_on_failures(self):
        cb = CircuitBreaker(threshold=3, cooldown=1.0)
        cb.record_failure()
        cb.record_failure()
        cb.record_failure()
        assert cb.state == CircuitState.OPEN
        assert not cb.allows_request

    def test_half_open_after_cooldown(self):
        cb = CircuitBreaker(threshold=3, cooldown=0.1)
        for _ in range(3):
            cb.record_failure()
        assert cb.state == CircuitState.OPEN

        import time

        time.sleep(0.15)
        assert cb.state == CircuitState.HALF_OPEN
        assert cb.allows_request

    def test_closes_on_success_in_half_open(self):
        cb = CircuitBreaker(threshold=3, cooldown=0.1)
        for _ in range(3):
            cb.record_failure()
        time.sleep(0.15)
        assert cb.state == CircuitState.HALF_OPEN
        cb.record_success()
        assert cb.state == CircuitState.CLOSED

    def test_reset(self):
        cb = CircuitBreaker(threshold=3)
        for _ in range(3):
            cb.record_failure()
        cb.reset()
        assert cb.state == CircuitState.CLOSED


class TestAdaptiveScaler:
    def test_hold_when_stable(self):
        s = AdaptiveScaler(target_p95_ms=5.0, cooldown_seconds=0.0)
        decision = s.decide(current_p95_ms=3.0, current_size=5, idle_count=2)
        assert decision == 0

    def test_scale_up_on_high_latency(self):
        s = AdaptiveScaler(target_p95_ms=5.0, cooldown_seconds=0.0, max_pool=10)
        decision = s.decide(current_p95_ms=10.0, current_size=5, idle_count=1)
        assert decision == 1

    def test_scale_down_on_low_util(self):
        s = AdaptiveScaler(
            target_p95_ms=5.0,
            cooldown_seconds=0.0,
            low_utilization_threshold=0.5,
            min_pool=2,
        )
        decision = s.decide(current_p95_ms=1.0, current_size=10, idle_count=8)
        assert decision == -1

    def test_respects_cooldown(self):
        s = AdaptiveScaler(target_p95_ms=5.0, cooldown_seconds=10.0)
        d1 = s.decide(current_p95_ms=10.0, current_size=5, idle_count=1)
        assert d1 == 1
        d2 = s.decide(current_p95_ms=10.0, current_size=5, idle_count=1)
        assert d2 == 0  # Cooldown prevents another decision

    def test_respects_max_pool(self):
        s = AdaptiveScaler(target_p95_ms=5.0, cooldown_seconds=0.0, max_pool=5)
        decision = s.decide(current_p95_ms=10.0, current_size=5, idle_count=0)
        assert decision == 0  # Already at max
