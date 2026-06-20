# Purreal

**Adaptive async connection pooler and session multiplexer for SurrealDB.**

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](https://www.gnu.org/licenses/gpl-3.0)
[![Tests](https://github.com/dyleeeeeeee/purreal/actions/workflows/test.yml/badge.svg)](https://github.com/dyleeeeeeee/purreal/actions)

---

## What It Does

Purreal solves the `websockets.exceptions.ConcurrencyError` that crashes SurrealDB Python apps under concurrent load. It goes further: predictive pre-warming, self-tuning pool size, session multiplexing across physical connections, and transparent failover (session teleportation) when connections die.

```python
import asyncio
from purreal import SurrealDBConnectionPool, SessionMultiplexer

async def main():
    async with SurrealDBConnectionPool(
        uri="ws://localhost:8000/rpc",
        credentials={"username": "root", "password": "root"},
        namespace="test",
        database="test",
    ) as pool:
        # Simple: exclusive connection leasing
        async with pool.acquire() as conn:
            result = await conn.query("SELECT * FROM users")

        # Advanced: 200 virtual sessions on 3 physical connections
        async with SessionMultiplexer(pool, num_slots=3) as mux:
            session = await mux.get_session("app", "prod", {"username": "root", "password": "root"})
            users = await session.query("SELECT * FROM users")

asyncio.run(main())
```

---

## Installation

```bash
pip install purreal
```

Requires Python 3.11+ and `surrealdb >= 0.3.0`.

---

## Architecture

```
User Code
    │
    ▼
SessionMultiplexer ──► VirtualSession.query(...)
    │
    │  SessionRouter (state-affinity scoring)
    ▼
PhysicalSlot [0..N] ──► drain loop (serial execution per slot)
    │
    ▼
SurrealDBConnectionPool ──► Semaphore + LifoQueue, O(1) acquire
    │
    │  DemandPredictor ─── pre-warms before spikes
    │  LatencyOracle ───── detects degraded connections
    │  AdaptiveScaler ──── self-tunes pool size
    │  CircuitBreaker ──── isolates failing connections
    ▼
SurrealDB Server (WebSocket)
```

---

## Features

| Feature | Description |
|---------|-------------|
| **Exclusive leasing** | One coroutine per connection — no ConcurrencyError |
| **Adaptive pool sizing** | Self-tunes based on p95 acquisition latency |
| **Predictive pre-warming** | Pre-creates connections before demand spikes |
| **Session multiplexing** | M virtual sessions on N physical connections |
| **Session teleportation** | Transparent retry on connection failure |
| **Proactive migration** | Moves sessions off expiring connections before they die |
| **Circuit breakers** | Isolates failing connections, auto-recovers |
| **Latency oracle** | 3σ outlier detection for degraded connections |
| **Overflow/burst** | Temporary connections for traffic spikes |
| **Pre-ping validation** | Validates stale connections before handing out |
| **Leak detection** | Warns with stack trace if connection held too long |
| **Bounded waiters** | Load shedding when queue depth exceeds limit |
| **Graceful drain** | Pause/resume/drain for zero-downtime operations |
| **Event hooks** | Subscribe to pool lifecycle events |
| **Max-lifetime with jitter** | Prevents thundering herd on rotation |

---

## API Reference

### `SurrealDBConnectionPool`

The core connection pool. Manages physical WebSocket connections to SurrealDB.

#### Constructor

```python
from purreal import SurrealDBConnectionPool, PoolConfig

# Option 1: Direct kwargs (backward-compatible)
pool = SurrealDBConnectionPool(
    uri="ws://localhost:8000/rpc",
    credentials={"username": "root", "password": "root"},
    namespace="test",
    database="test",
    pool_size=5,              # Steady-state connections (default: 5)
    max_overflow=10,          # Extra connections for bursts (default: 10)
    acquisition_timeout=5.0,  # Seconds to wait for connection (default: 5.0)
    connection_timeout=10.0,  # Seconds to establish connection (default: 10.0)
    max_lifetime=1800.0,      # Max connection age in seconds (default: 1800)
    max_idle_time=300.0,      # Idle timeout in seconds (default: 300)
    max_usage_count=10000,    # Queries before recycling (default: 10000)
    health_check_interval=30.0,
    pre_ping=True,            # Validate stale connections on acquire (default: True)
    pre_ping_bypass_window=1.0,  # Skip validation if used within N seconds
    prewarm=True,             # Predictive pre-warming before demand spikes (default: True)
    adaptive=True,            # Latency-driven adaptive scaling (default: True)
    leak_detection_threshold=60.0,  # Warn if held longer than N seconds
    max_waiters=100,          # Reject if more than N tasks waiting
    connection_retry_attempts=3,
    connection_retry_delay=1.0,
    schema_file="schema.surql",     # Execute on each new connection
    on_connection_create=my_callback,  # Async callback on creation
    log_queries=False,
)

# Option 2: PoolConfig object
config = PoolConfig(
    uri="ws://localhost:8000/rpc",
    credentials={"username": "root", "password": "root"},
    namespace="test",
    database="test",
    pool_size=10,
    max_overflow=20,
)
pool = SurrealDBConnectionPool(config=config)
```

#### Lifecycle

```python
# Context manager (recommended)
async with SurrealDBConnectionPool(...) as pool:
    ...  # auto-initializes, auto-closes

# Manual
pool = SurrealDBConnectionPool(...)
await pool.initialize()  # Create initial connections, start background tasks
# ... use pool ...
await pool.close()       # Graceful shutdown
```

#### Methods

| Method | Description |
|--------|-------------|
| `async with pool.acquire() as conn` | Get exclusive connection. Raises `TimeoutError` or `PoolExhaustedError` |
| `await pool.execute_query(sql, params)` | Convenience: acquire → query → release |
| `await pool.get_stats()` | Returns dict with all pool metrics |
| `await pool.checkout()` | Long-term lease (returns `PooledConnection`) |
| `await pool.checkin(conn)` | Return long-term lease |
| `pool.pause()` | Stop handing out connections (raises `PoolPausedError`) |
| `pool.resume()` | Re-enable acquisitions |
| `await pool.drain(timeout=30)` | Wait for in-flight, then close |
| `await pool.initialize()` | Idempotent init |
| `await pool.close()` | Idempotent shutdown |

#### Stats

```python
stats = await pool.get_stats()
# Returns:
{
    "total_connections_created": int,
    "total_connections_closed": int,
    "total_acquisitions": int,
    "total_releases": int,
    "acquisition_timeouts": int,
    "connection_errors": int,
    "health_check_failures": int,
    "peak_connections": int,
    "peak_waiters": int,
    "current_connections": int,
    "available_connections": int,
    "in_use_connections": int,
    "connection_waiters": int,
    "overflow_connections": int,
    "p50_acquire_ms": float,
    "p95_acquire_ms": float,
    "p99_acquire_ms": float,
    "total_teleports": int,
    "total_pre_warms": int,
}
```

#### Events

```python
from purreal import PoolEvent

pool.events.on(PoolEvent.ACQUIRE, lambda ctx: print(f"Acquired {ctx.connection_id}"))
pool.events.on(PoolEvent.TIMEOUT, lambda ctx: alert("Pool exhausted!"))
pool.events.on(PoolEvent.HEALTH_FAIL, my_async_handler)
pool.events.on(PoolEvent.PRE_WARM, lambda ctx: logger.info("Pre-warmed connection"))
```

Available events: `ACQUIRE`, `RELEASE`, `CREATE`, `DESTROY`, `HEALTH_FAIL`, `TIMEOUT`, `OVERFLOW_CREATE`, `OVERFLOW_DESTROY`, `DRAIN_START`, `DRAIN_COMPLETE`, `PRE_WARM`, `SCALE_UP`, `SCALE_DOWN`, `CIRCUIT_OPEN`, `CIRCUIT_CLOSE`, `TELEPORT`

---

### `SessionMultiplexer`

Multiplexes M virtual sessions over N physical connections with state-aware routing and transparent teleportation.

#### Constructor

```python
from purreal import SessionMultiplexer

mux = SessionMultiplexer(
    pool=pool,                    # Required: the underlying connection pool
    num_slots=3,                  # Physical connections to maintain (default: 3)
    max_sessions=200,             # Cap on concurrent virtual sessions (default: 200)
    session_idle_timeout=300.0,   # Reap idle sessions after N seconds (default: 300)
    max_queue_per_slot=50,        # Max pending requests per slot (default: 50)
)
```

#### Lifecycle

```python
# Context manager
async with SessionMultiplexer(pool, num_slots=3) as mux:
    session = await mux.get_session("ns", "db", creds)
    await session.query("SELECT * FROM users")

# Manual
mux = SessionMultiplexer(pool, num_slots=3)
await mux.start()
# ... use ...
await mux.stop()
```

#### Methods

| Method | Description |
|--------|-------------|
| `await mux.get_session(ns, db, credentials)` | Create a virtual session |
| `await mux.get_stats()` | Returns session count and slot status |
| `await mux.start()` | Initialize slots and background tasks |
| `await mux.stop()` | Drain and shutdown |

---

### `VirtualSession`

User-facing session handle. Tracks namespace, database, credentials, and variables. Not bound to any physical connection — transparently routes queries to the best available slot.

#### Methods

| Method | Description |
|--------|-------------|
| `await session.query(sql, params=None)` | Execute arbitrary SurrealQL |
| `await session.select(thing)` | `SELECT * FROM {thing}` |
| `await session.create(thing, data=None)` | `CREATE {thing} CONTENT ...` |
| `await session.update(thing, data)` | `UPDATE {thing} CONTENT ...` |
| `await session.delete(thing)` | `DELETE {thing}` |
| `await session.let(key, value)` | Set a session variable (`LET $key = value`) |
| `await session.close()` | Close session, free resources |

#### Properties

| Property | Type | Description |
|----------|------|-------------|
| `session.session_id` | `str` | Unique identifier |
| `session.namespace` | `str` | Current namespace |
| `session.database` | `str` | Current database |
| `session.is_active` | `bool` | True if not expired/closed |
| `session.idle_duration` | `float` | Seconds since last activity |

---

### `EventBus`

Lightweight pub/sub for pool lifecycle events.

```python
from purreal import EventBus, PoolEvent

bus = EventBus()

# Sync handler
bus.on(PoolEvent.ACQUIRE, lambda ctx: metrics.increment("pool.acquire"))

# Async handler
async def on_timeout(ctx):
    await alerting.send(f"Pool timeout at {ctx.timestamp}")
bus.on(PoolEvent.TIMEOUT, on_timeout)

# Remove handler
bus.off(PoolEvent.TIMEOUT, on_timeout)

# Clear all
bus.clear()
```

Handlers never block the pool's hot path. Exceptions in handlers are logged and swallowed.

---

### `DemandPredictor`

Predicts future connection demand from time-of-day patterns and real-time EWMA.

```python
from purreal import DemandPredictor

predictor = DemandPredictor(alpha=0.3)  # EWMA smoothing factor
predictor.record_acquisition()          # Call on each acquire
predicted = predictor.predict_demand(horizon_seconds=10.0)  # How many conns needed
predictor.decay(factor=0.95)            # Age historical data
```

Used internally by the pool's housekeeping loop. Exposed for custom scheduling logic.

The forecast is bounded by **recent** demand: the EWMA term decays toward zero as
the pool sits idle, so a pool seeing ~0 acquisitions pre-warms ~0 connections
rather than warming toward `max_connections`.

#### Disabling speculative growth

Both background growth strategies are opt-out via the pool constructor / `PoolConfig`:

| Flag | Default | What it controls |
|------|---------|------------------|
| `prewarm` | `True` | Predictive pre-warming (`DemandPredictor`-driven) ahead of demand spikes |
| `adaptive` | `True` | Latency-driven adaptive scaling (`AdaptiveScaler`, `LatencyOracle`-driven) |

```python
# Strictly demand-driven (0.1.x-style): grow only under acquire pressure,
# shrink idle connections back to min_idle, never pre-warm or scale on latency.
pool = SurrealDBConnectionPool(..., prewarm=False, adaptive=False)
```

Use this for pools that idle for long stretches and serve occasional bursts, or
when many pools share one SurrealDB node and background connection churn matters
more than shaving acquire latency off the first request of a spike. `pre_ping`,
circuit breakers, leak detection, and the multiplexer are unaffected.

---

### `LatencyOracle`

Per-connection latency tracking with 3σ outlier detection.

```python
from purreal import LatencyOracle

oracle = LatencyOracle()
oracle.record("conn_abc", 5.2)          # Record RTT in ms
oracle.is_degraded("conn_abc")          # True if 3σ outlier
oracle.pool_p50()                       # Pool-wide p50 latency
oracle.pool_p95()                       # Pool-wide p95 latency
oracle.pool_p99()                       # Pool-wide p99 latency
oracle.remove_connection("conn_abc")    # Cleanup
```

---

### `CircuitBreaker`

Per-connection failure isolation with automatic recovery.

```python
from purreal import CircuitBreaker

cb = CircuitBreaker(threshold=5, cooldown=30.0)
cb.record_failure()     # Increment failure count
cb.record_success()     # Decrement / close circuit
cb.allows_request       # True if CLOSED or HALF_OPEN
cb.state                # CircuitState.CLOSED / OPEN / HALF_OPEN
cb.reset()              # Force close
```

States: `CLOSED` (normal) → `OPEN` (after N failures, rejects for cooldown period) → `HALF_OPEN` (allows one request to test recovery) → `CLOSED`

---

### `AdaptiveScaler`

Emits pool scaling decisions based on latency targets.

```python
from purreal import AdaptiveScaler

scaler = AdaptiveScaler(
    target_p95_ms=5.0,
    low_utilization_threshold=0.3,
    min_pool=2,
    max_pool=50,
    cooldown_seconds=5.0,
)
decision = scaler.decide(
    current_p95_ms=8.0,
    current_size=10,
    idle_count=2,
)
# Returns: +1 (grow), -1 (shrink), or 0 (hold)
```

---

### `SurrealDBPoolManager`

Singleton for managing multiple named pools.

```python
from purreal import SurrealDBPoolManager

manager = SurrealDBPoolManager()

# Create named pools
await manager.create_pool("primary", uri="ws://primary:8000/rpc", ...)
await manager.create_pool("replica", uri="ws://replica:8000/rpc", ...)

# Retrieve
pool = manager.get_pool("primary")

# Shutdown all
await manager.close_all_pools()
```

---

### Exceptions

| Exception | When |
|-----------|------|
| `PoolExhaustedError` | Waiter queue at max capacity |
| `PoolPausedError` | Pool is paused via `pool.pause()` |
| `SessionExpiredError` | Session timed out or was closed |
| `asyncio.TimeoutError` | Acquisition timeout exceeded |
| `RuntimeError` | Pool closed or not initialized |

---

## Usage Patterns

### Basic Pool Usage

```python
async with SurrealDBConnectionPool(
    uri="ws://localhost:8000/rpc",
    credentials={"username": "root", "password": "root"},
    namespace="test",
    database="test",
) as pool:
    async with pool.acquire() as conn:
        await conn.query("CREATE user SET name = 'Alice'")
```

### High-Concurrency Batch Processing

```python
async def process_batch(pool, items):
    async def process_one(item):
        async with pool.acquire() as conn:
            return await conn.query(f"UPDATE item:{item['id']} SET processed = true")

    return await asyncio.gather(*[process_one(i) for i in items])
```

### Multi-Tenant with Session Multiplexer

```python
async with SessionMultiplexer(pool, num_slots=5, max_sessions=500) as mux:
    # Each tenant gets isolated session (different ns/db)
    tenant_a = await mux.get_session("tenant_a", "prod", creds)
    tenant_b = await mux.get_session("tenant_b", "prod", creds)

    # Queries route to optimal physical connections
    await tenant_a.query("SELECT * FROM orders")
    await tenant_b.query("SELECT * FROM orders")  # Different namespace
```

### Graceful Deployment (Zero-Downtime Drain)

```python
# On SIGTERM:
pool.pause()                    # Stop accepting new requests
await pool.drain(timeout=30)    # Wait for in-flight to finish
```

### Monitoring with Events

```python
from purreal import PoolEvent

pool.events.on(PoolEvent.TIMEOUT, lambda ctx: metrics.inc("pool.timeouts"))
pool.events.on(PoolEvent.SCALE_UP, lambda ctx: logger.info("Pool grew"))
pool.events.on(PoolEvent.TELEPORT, lambda ctx: logger.warn(f"Session teleported: {ctx.metadata}"))
pool.events.on(PoolEvent.HEALTH_FAIL, lambda ctx: pagerduty.alert(ctx.connection_id))
```

### Session Variables (LET)

```python
session = await mux.get_session("app", "prod", creds)
await session.let("user_id", "user:alice")
await session.query("SELECT * FROM orders WHERE author = $user_id")
```

---

## Configuration Guide

### Pool Sizing

| Workload | `pool_size` | `max_overflow` | Notes |
|----------|-------------|----------------|-------|
| Low traffic (< 10 req/s) | 2-3 | 5 | Minimal resources |
| Medium (10-100 req/s) | 5-10 | 15 | Good balance |
| High (100-1000 req/s) | 10-20 | 30 | Production standard |
| Extreme (1000+ req/s) | 20-50 | 50 | Use multiplexer too |

### Multiplexer Sizing

| Scenario | `num_slots` | `max_sessions` |
|----------|-------------|----------------|
| Single-tenant app | 2-3 | 50 |
| Multi-tenant SaaS | 5-10 | 500 |
| Microservice with many DBs | 3-5 per DB | 200 |

---

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| `ConcurrencyError` | Sharing connection between coroutines | Always use `async with pool.acquire()` |
| `TimeoutError` on acquire | Pool exhausted | Increase `pool_size` or `max_overflow` |
| `PoolExhaustedError` | Too many tasks waiting | Increase `max_waiters` or add backpressure upstream |
| `PoolPausedError` | Pool is draining | Wait for `resume()` or deployment to complete |
| `SessionExpiredError` | Session idle too long | Reduce `session_idle_timeout` or keep sessions active |
| LEAK warnings in logs | Connection held > threshold | Check your code for missing `async with` or long operations |
| Frequent connection churn | `max_lifetime` too low | Increase to 1800+ seconds |

Enable debug logging:
```python
import logging
logging.getLogger("purreal").setLevel(logging.DEBUG)
```

---

## Development

```bash
git clone https://github.com/dyleeeeeeee/purreal.git
cd purreal
pip install -e ".[dev]"
pytest                    # 69 tests
python -m build           # Build wheel
```

---

## License

GPL-3.0. See [LICENSE](LICENSE).
