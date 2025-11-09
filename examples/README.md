# Examples

Example scripts demonstrating common purreal usage patterns.

## Quick Start

### `stress_test.py`
Simple stress test for quick validation:

```bash
python examples/stress_test.py 500
# or
./test.bat stress
```

Tests 500 concurrent connections executing 10 operations each.

### `monitor_pool.py`
Real-time pool monitoring during load:

```bash
python examples/monitor_pool.py
# or
./test.bat monitor
```

Shows live statistics about connection pool behavior.

### `load_test.py`
Comprehensive load test with detailed metrics:

```bash
python examples/load_test.py
# or
./test.bat load
```

**Warning:** Takes several minutes to complete.

## Usage Patterns

All examples follow this basic pattern:

```python
from purreal import SurrealDBConnectionPool

# Create pool
pool = SurrealDBConnectionPool(
    uri="ws://localhost:8000/rpc",
    credentials={"username": "root", "password": "root"},
    namespace="test",
    database="test",
    min_connections=10,
    max_connections=50,
    reset_on_return=False,  # Critical for performance
)

# Initialize
await pool.initialize()

# Use connections
async with pool.acquire() as conn:
    result = await conn.query("SELECT * FROM users")

# Cleanup
await pool.close()
```

## Next Steps

- Review [README.md](../README.md) for full API documentation
- Run [benchmarks](../benchmarks/) to validate performance
- Check [PERFORMANCE_TESTING.md](../PERFORMANCE_TESTING.md) for tuning guidance
