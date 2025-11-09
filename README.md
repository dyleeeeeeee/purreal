# Purreal

## Quick Test (After Git Pull)

```bash
# 1. Start SurrealDB
surreal start --bind 0.0.0.0:8000 --user root --pass root

# 2. Install purreal
pip install -e .

# 3. Test connectivity (takes 5 seconds)
python tests/test_connectivity.py

# 4. Test 500 concurrent connections (optional)
python tests/stress_test_simple.py 500
```

**Quick test runner:**
```bash
# Linux/Mac
./test.sh

# Windows
test.bat
```

See [QUICKSTART.md](QUICKSTART.md) for full setup guide.

## Production-Grade SurrealDB Connection Pooling

[![License](https://www.gnu.org/graphics/gplv3-with-text-136x68.png)](https://opensource.org/licenses/GNU)
[![Python Version](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![Code Style: Black](https://img.shields.io/badge/code%20style-black-000000.svg)](https://github.com/psf/black)

## Overview

Purreal is a **production-grade async connection pooler** for SurrealDB that solves the critical `websockets.exceptions.ConcurrencyError: cannot call recv while another coro is calling recv` issue in high-concurrency Python applications.

By ensuring **exclusive connection leasing** and sophisticated **connection lifecycle management**, Purreal enables your async applications to safely handle thousands of concurrent database operations without race conditions or connection conflicts.

## Why Purreal?

**The Problem:** SurrealDB's Python client connections cannot safely handle concurrent operations from multiple coroutines. Attempting to share a single connection results in `ConcurrencyError` crashes.

**The Solution:** Purreal provides:
- **Exclusive connection leasing** - each coroutine gets its own connection
- **Automatic pool management** - connections created/destroyed based on demand
- **Robust error handling** - connections auto-replaced on failure
- **Production-ready** - battle-tested with comprehensive logging and stats

## Key Features

### Core Pooling
*   **Exclusive Connection Leasing:** Prevents `ConcurrencyError` by ensuring only one coroutine uses a connection at a time
*   **Dynamic Pool Sizing:** Auto-scales between `min_connections` and `max_connections` based on load
*   **Connection Queueing:** Fair FIFO queue when pool exhausted, with configurable timeout
*   **Async Context Manager:** Safe `async with` pattern guarantees connection return

### Reliability & Health
*   **Automatic Health Checks:** Background maintenance loop validates connection health
*   **Connection Lifecycle Management:** Tracks usage count, age, and health status
*   **Intelligent Retry Logic:** Exponential backoff with jitter for transient failures
*   **Graceful Degradation:** Continues operating even when some connections fail

### Production Features
*   **Connection State Reset:** Optional `reset_on_return` to clear session state
*   **Schema Initialization:** Auto-execute `.surql` schema files on connection creation
*   **Comprehensive Stats:** Track acquisitions, timeouts, errors, peak usage
*   **Detailed Logging:** Debug-level connection lifecycle tracing
*   **Query Logging:** Optional request/response logging for debugging

## Installation

```bash
pip install purreal
```

**Requirements:**
- Python 3.11+
- surrealdb >= 0.3.0


## Quick Start

### Basic Usage

```python
import asyncio
from purreal import SurrealDBConnectionPool

async def main():
    # Initialize pool
    pool = SurrealDBConnectionPool(
        uri="ws://localhost:8000/rpc",
        credentials={"username": "root", "password": "root"},
        namespace="test",
        database="test",
        min_connections=5,
        max_connections=20,
    )
    
    # Use async context manager (handles init + cleanup)
    async with pool:
        # Acquire exclusive connection
        async with pool.acquire() as conn:
            result = await conn.query("SELECT * FROM users")
            print(result)

asyncio.run(main())
```

### Production Example with Error Handling

```python
import asyncio
import logging
from purreal import SurrealDBConnectionPool

logging.basicConfig(level=logging.INFO)

async def process_user_batch(pool, user_ids):
    """Process multiple users concurrently using the pool."""
    async def process_user(user_id):
        try:
            async with pool.acquire() as conn:
                # Each coroutine gets exclusive connection access
                user = await conn.query(f"SELECT * FROM user:{user_id}")
                await conn.query(
                    f"UPDATE user:{user_id} SET last_accessed = time::now()"
                )
                return user
        except asyncio.TimeoutError:
            logging.warning(f"Timeout acquiring connection for user {user_id}")
            return None
        except Exception as e:
            logging.error(f"Error processing user {user_id}: {e}")
            return None
    
    # Process all users concurrently - pool handles queueing
    results = await asyncio.gather(*[process_user(uid) for uid in user_ids])
    return [r for r in results if r is not None]

async def main():
    pool = SurrealDBConnectionPool(
        uri="wss://mydb.surreal.cloud",
        credentials={"username": "admin", "password": "secure_pass"},
        namespace="production",
        database="app",
        min_connections=10,
        max_connections=50,
        acquisition_timeout=30.0,  # Wait up to 30s for connection
        health_check_interval=60.0,  # Check health every 60s
        log_queries=True,  # Log all queries (disable in prod)
        schema_file="schema.surql",  # Auto-execute on new connections
    )
    
    async with pool:
        # Simulate high-concurrency workload
        user_ids = [f"user_{i}" for i in range(100)]
        results = await process_user_batch(pool, user_ids)
        
        # Check pool stats
        stats = await pool.get_stats()
        logging.info(f"Pool stats: {stats}")

asyncio.run(main())
```


## Configuration Options

### Required Parameters
- **`uri`** (str): SurrealDB connection URI (e.g., `ws://localhost:8000/rpc`, `wss://cloud.surreal.io`)
- **`credentials`** (dict): Authentication credentials `{"username": "...", "password": "..."}`
- **`namespace`** (str): SurrealDB namespace
- **`database`** (str): SurrealDB database name

### Pool Sizing
- **`min_connections`** (int, default: 4): Minimum connections maintained in pool
- **`max_connections`** (int, default: 10): Maximum connections allowed
- **`acquisition_timeout`** (float, default: 10.0): Seconds to wait for available connection

### Connection Lifecycle
- **`max_idle_time`** (float, default: 300.0): Seconds before idle connection is recycled
- **`max_usage_count`** (int, default: 1000): Max queries before connection recycled
- **`connection_timeout`** (float, default: 25.0): Seconds to establish new connection
- **`connection_retry_attempts`** (int, default: 3): Retries for failed connections
- **`connection_retry_delay`** (float, default: 1.0): Base delay between retries (exponential backoff)

### Health & Maintenance
- **`health_check_interval`** (float, default: 30.0): Seconds between health checks
- **`reset_on_return`** (bool, default: True): Reset connection state on release

### Advanced
- **`schema_file`** (str, optional): Path to `.surql` file to execute on new connections
- **`on_connection_create`** (callable, optional): Async callback when connection created
- **`log_queries`** (bool, default: False): Log all queries (useful for debugging)



## API Reference

### `SurrealDBConnectionPool`

#### Initialization
```python
pool = SurrealDBConnectionPool(
    uri, credentials, namespace, database,
    min_connections=4, max_connections=10,
    acquisition_timeout=10.0, **kwargs
)
```

#### Context Manager (Recommended)
```python
async with pool:  # Calls initialize() and close() automatically
    async with pool.acquire() as conn:
        await conn.query("SELECT * FROM table")
```

#### Methods

**`async with acquire() -> SurrealConnection`**
- Acquires exclusive connection from pool
- **Must** use with `async with` to ensure proper release
- Raises `asyncio.TimeoutError` if pool exhausted for `acquisition_timeout` seconds
- Raises `RuntimeError` if pool closed or not initialized

**`async initialize()`**
- Creates minimum connections and starts maintenance loop
- Safe to call multiple times (idempotent)
- Automatically called by `async with pool`

**`async close()`**
- Gracefully closes all connections
- Cancels maintenance loop
- Notifies waiting coroutines
- Safe to call multiple times
- Automatically called by `async with pool` exit

**`async get_stats() -> dict`**
- Returns pool statistics:
  ```python
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
      "current_pool_size": int,
      "current_available": int,
      "current_in_use": int,
      "current_waiters": int,
  }
  ```

**`async execute_query(query: str, params: dict = None) -> Any`**
- Convenience method: acquires connection, executes query, releases
- Equivalent to:
  ```python
  async with pool.acquire() as conn:
      return await conn.query(query, params)
  ```

## Best Practices

### ✅ DO

```python
# Use async context managers for guaranteed cleanup
async with pool:
    async with pool.acquire() as conn:
        await conn.query("SELECT * FROM users")

# Set appropriate pool sizes for your workload
pool = SurrealDBConnectionPool(
    min_connections=10,  # Based on baseline load
    max_connections=50,  # Based on peak load
)

# Handle timeouts gracefully
try:
    async with pool.acquire() as conn:
        result = await conn.query("SLOW QUERY")
except asyncio.TimeoutError:
    logger.warning("Pool exhausted, consider scaling")
```

### ❌ DON'T

```python
# Don't share connections between coroutines
conn = await pool.acquire()  # Missing async with!
await asyncio.gather(
    conn.query("SELECT 1"),  # ❌ ConcurrencyError!
    conn.query("SELECT 2"),
)

# Don't forget to close the pool
pool = SurrealDBConnectionPool(...)
await pool.initialize()
# ... use pool ...
# ❌ Missing: await pool.close()

# Don't set pool sizes too small
pool = SurrealDBConnectionPool(
    min_connections=1,   # ❌ Too small for production
    max_connections=2,   # ❌ Will bottleneck quickly
)
```

## Troubleshooting

### `ConcurrencyError: cannot call recv`
**Cause:** Sharing a connection between coroutines  
**Solution:** Always use `async with pool.acquire()` - never store or share the connection object

### `asyncio.TimeoutError` during acquisition
**Cause:** Pool exhausted (all connections in use)  
**Solutions:**
- Increase `max_connections`
- Increase `acquisition_timeout`
- Reduce query execution time
- Check for connection leaks (not releasing connections)

### Connections failing health checks
**Cause:** Network issues, database restart, or connection timeout  
**Solution:** Purreal auto-replaces unhealthy connections. Check logs for patterns:
```python
# Enable debug logging to diagnose
logging.getLogger('purreal').setLevel(logging.DEBUG)
```

### Memory usage growing
**Cause:** Connections not being released  
**Solution:** Verify all `pool.acquire()` uses are in `async with` blocks

## Performance Tips

### Pool Sizing
```python
# Calculate based on your workload
concurrent_requests = 100  # Peak concurrent users
avg_query_time = 0.1       # Average query time in seconds
connections_needed = concurrent_requests * avg_query_time

pool = SurrealDBConnectionPool(
    min_connections=int(connections_needed * 0.5),  # 50% for baseline
    max_connections=int(connections_needed * 2),    # 200% for peaks
)
```

### Connection Lifecycle
```python
# Tune based on your database workload
pool = SurrealDBConnectionPool(
    max_usage_count=10000,      # Higher for read-heavy workloads
    max_idle_time=600,          # Longer for stable connections
    health_check_interval=60,   # More frequent for critical apps
)
```

### Monitoring
```python
# Periodically check pool health
async def monitor_pool(pool):
    while True:
        stats = await pool.get_stats()
        if stats['acquisition_timeouts'] > 10:
            logger.warning(f"High timeout rate: {stats}")
        if stats['in_use_connections'] / stats['current_connections'] > 0.8:
            logger.warning("Pool utilization above 80%")
        await asyncio.sleep(60)

asyncio.create_task(monitor_pool(pool))
```

## Known Limitations

### Burst Load > max_connections

**Issue:** When burst traffic exceeds `max_connections`, waiting tasks may timeout instead of queuing properly.

**Example:**
- Pool: `max_connections=50`
- Burst: 100 concurrent requests
- Result: 50 succeed, 50 timeout (instead of queuing)

**Workaround:**
```python
# Size pool for peak burst load
pool = SurrealDBConnectionPool(
    max_connections=150,  # 1.5x expected peak
    ...
)

# Or use semaphore to limit concurrency
semaphore = asyncio.Semaphore(50)

async def limited_query():
    async with semaphore:
        async with pool.acquire() as conn:
            await conn.query(...)
```

**Status:** Known issue in v0.1.0. See [KNOWN_ISSUES.md](KNOWN_ISSUES.md) for details.

**Impact:** Sustained load and gradual ramp-up work fine. Only affects sudden bursts >> pool size.

## Contributing

We welcome contributions! Please feel free to submit a Pull Request.

### Guidelines
1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Make your changes
4. Write tests for your changes (`pytest tests/`)
5. Ensure all tests pass (`pytest`)
6. Submit a pull request

### Development Setup
```bash
git clone https://github.com/dyleeeeeeee/purreal.git
cd purreal
pip install -e ".[dev]"
pytest
```

## License

This project is licensed under the GNU General Public License v3 (GPLv3) - see the [LICENSE](LICENSE) file for details.

## Acknowledgments

- Solves the critical `ConcurrencyError` issue affecting SurrealDB Python applications
- Built for production use in high-concurrency async applications
- Thanks to the SurrealDB team for an excellent database
