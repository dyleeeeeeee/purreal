# Purreal Quick Start

Get up and running with purreal in 60 seconds.

## Prerequisites

1. **Python 3.10+**
   ```bash
   python --version
   ```

2. **SurrealDB** running locally
   ```bash
   # Install SurrealDB (if not already installed)
   # macOS/Linux
   curl -sSf https://install.surrealdb.com | sh
   
   # Or use Docker
   docker run -p 8000:8000 surrealdb/surrealdb:latest start
   
   # Start SurrealDB
   surreal start --bind 0.0.0.0:8000 --user root --pass root
   ```

## Installation

```bash
# Clone and install
git clone <your-repo-url>
cd purreal
pip install -e .
```

## Test Your Setup

Run the connectivity test to verify everything works:

```bash
python tests/test_connectivity.py
```

**Expected output:**
```
================================================================================
PURREAL CONNECTIVITY TEST
================================================================================
✓ purreal imported successfully

1. Testing connection to ws://localhost:8000/rpc...
   ✓ Connected successfully
   ✓ Pool initialized with 1 connection(s)

2. Testing basic query...
   ✓ Query executed successfully
   ✓ Result: [{'test': 1}]

3. Testing CRUD operations...
   ✓ CREATE: 1 record(s)
   ✓ READ: 1 record(s)
   ✓ UPDATE: 1 record(s)
   ✓ DELETE: 1 record(s)

4. Testing concurrent access (10 parallel queries)...
   ✓ 10 concurrent queries completed
   ✓ Pool stats: 5 total, 5 available

5. Testing pool scaling (50 concurrent queries)...
   ✓ 50 concurrent queries completed
   ✓ Pool scaled from 5 to 5 connections

✓ Pool closed

================================================================================
SUMMARY
================================================================================
Passed: 5/5
Failed: 0/5

🎉 ALL TESTS PASSED! Your setup is working correctly.
================================================================================
```

## Basic Usage

```python
import asyncio
from purreal.src.pooler import SurrealDBConnectionPool

async def main():
    # Create pool
    pool = SurrealDBConnectionPool(
        url="ws://localhost:8000/rpc",
        namespace="test",
        database="test",
        username="root",
        password="root",
        min_size=5,
        max_size=20,
    )
    
    # Initialize
    await pool.initialize()
    
    # Use connection
    async with pool.acquire() as conn:
        result = await conn.query("SELECT * FROM users")
        print(result)
    
    # Cleanup
    await pool.close()

asyncio.run(main())
```

## Common Issues

### ❌ Connection Failed

**Error:** `Connection failed: Cannot connect to SurrealDB`

**Fix:**
1. Check SurrealDB is running:
   ```bash
   # Should see SurrealDB listening on port 8000
   surreal start --bind 0.0.0.0:8000 --user root --pass root
   ```

2. Verify the URL is correct:
   ```bash
   python tests/test_connectivity.py --url ws://localhost:8000/rpc
   ```

### ❌ Import Error

**Error:** `Failed to import purreal`

**Fix:**
```bash
# Make sure you installed purreal
pip install -e .

# Or install dependencies
pip install -r requirements.txt
```

### ❌ Authentication Failed

**Error:** `Authentication failed`

**Fix:**
```bash
# Test with your credentials
python tests/test_connectivity.py --user myuser --pass mypass

# Or start SurrealDB with known credentials
surreal start --bind 0.0.0.0:8000 --user root --pass root
```

## Next Steps

### Test High Throughput (500+ connections)

```bash
# Quick stress test
python tests/stress_test_simple.py 500

# Watch pool behavior live
python tests/monitor_pool.py

# Find optimal configuration
python tests/benchmark_configs.py --find-optimal

# Full load test suite
python tests/load_test_pool.py
```

See [tests/LOAD_TESTING.md](tests/LOAD_TESTING.md) for comprehensive guide.

### Run Unit Tests

```bash
pytest tests/
```

### Check Examples

```bash
# See example implementations
ls examples/
```

## Troubleshooting

### Enable Debug Logging

```python
import logging
logging.basicConfig(level=logging.DEBUG)
```

### Check SurrealDB Status

```bash
# Check if SurrealDB is running
curl http://localhost:8000/status

# Or test connection
curl -X POST http://localhost:8000/sql \
  -H "Content-Type: application/json" \
  -d '{"query": "SELECT 1"}'
```

### Verify Network

```bash
# Check port is open
telnet localhost 8000

# Or with netcat
nc -zv localhost 8000
```

## Getting Help

1. **Run connectivity test with verbose output:**
   ```bash
   python tests/test_connectivity.py 2>&1 | tee connectivity-log.txt
   ```

2. **Check the logs** for error details

3. **Open an issue** with the log file

## Quick Reference

```bash
# Test connectivity
python tests/test_connectivity.py

# Test 500 concurrent connections
python tests/stress_test_simple.py 500

# Find optimal pool config
python tests/benchmark_configs.py --find-optimal

# Monitor pool in real-time
python tests/monitor_pool.py

# Run all tests
pytest tests/
```

That's it! You're ready to use purreal. 🚀
