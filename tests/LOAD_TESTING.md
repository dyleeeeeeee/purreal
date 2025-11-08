# Purreal Connection Pool - Load Testing Guide

This guide helps you validate that purreal can handle 500+ concurrent connections in high-throughput applications.

## Quick Start

### 1. Simple Stress Test (Recommended First Step)

Test if your pool can handle a specific number of concurrent connections:

```bash
# Test 500 concurrent connections
python tests/stress_test_simple.py 500

# Or run progressive test (100, 250, 500, 750, 1000)
python tests/stress_test_simple.py
```

**Expected Output:**
```
================================================================================
QUICK STRESS TEST: 500 concurrent connections
================================================================================

✓ Pool initialized: 50 initial connections

Launching 500 concurrent workers...

================================================================================
RESULTS:
  Duration:    2.34s
  Operations:  5000 total
  Successful:  5000 (100.0%)
  Failed:      0 (0.0%)
  Throughput:  2137 ops/sec
  Pool stats:  543 connections, 43 available

✓ SUCCESS: Pool handled 500 concurrent connections!
================================================================================
```

### 2. Monitored Load Test (See What's Happening)

Watch pool behavior in real-time:

```bash
python tests/monitor_pool.py
```

**Live Output:**
```
[████████████████████████████████░░░░░░░░] Total:  543 | Used:  500 | Free:   43 | Util:  92.1%
```

### 3. Comprehensive Load Test (Full Validation)

Run complete test suite with various scenarios:

```bash
python tests/load_test_pool.py
```

This runs:
- Sustained load (500 workers, 5000 operations)
- Burst load (1000 concurrent bursts)
- Connection churn (rapid acquire/release)
- Complex queries under load

**Expected Output:**
```
================================================================================
LOAD TEST REPORT
================================================================================

Operations:
  Total:      5,000
  Successful: 5,000
  Failed:     0
  Success Rate: 100.00%

Throughput:
  Duration: 4.56s
  Ops/sec:  1096.49
  Peak Concurrent: 500

Connection Acquisition (ms):
  MIN:  0.12
  MAX:  45.67
  AVG:  2.34
  P50:  1.89
  P95:  8.92
  P99:  15.43

Query Execution (ms):
  MIN:  0.45
  MAX:  12.34
  AVG:  2.11
  P50:  1.92
  P95:  4.56
  P99:  7.89
```

## What to Look For

### ✅ Success Indicators

1. **100% Success Rate** - No failed operations
2. **Low P95/P99 Latency** - Connection acquisition under 20ms at P95
3. **High Throughput** - >1000 ops/sec for simple queries
4. **Stable Utilization** - Pool doesn't max out (some headroom remains)
5. **No Errors** - Clean logs, no deadlocks or timeouts

### ⚠️ Warning Signs

1. **Failed Operations** - Indicates pool exhaustion or deadlocks
2. **High P99 Latency** - >100ms suggests connection starvation
3. **100% Utilization** - Pool maxed out, need to increase `max_size`
4. **Errors in Logs** - Connection timeouts, deadlocks, race conditions
5. **Memory Growth** - Connections not being released properly

## Tuning Pool Parameters

Based on test results, adjust these parameters:

```python
pool = SurrealDBConnectionPool(
    # Start with 10-20% of max concurrent connections
    min_size=50,          # Increase if frequently creating new connections
    
    # Set 20% higher than peak concurrent connections
    max_size=600,         # Increase if seeing connection exhaustion
    
    # How long connections live before recreation
    max_lifetime=300,     # Decrease if seeing stale connection issues
    
    # How long idle connections stay in pool
    max_idle_time=60,     # Decrease to free resources faster
    
    # How long to wait for connection
    acquire_timeout=30,   # Increase if seeing timeout errors
)
```

## Benchmark Targets

For 500 concurrent connections, aim for:

| Metric | Target | Excellent |
|--------|--------|-----------|
| Success Rate | >99.9% | 100% |
| Throughput | >500 ops/sec | >2000 ops/sec |
| P50 Acquire Time | <5ms | <2ms |
| P95 Acquire Time | <20ms | <10ms |
| P99 Acquire Time | <50ms | <20ms |
| Pool Utilization | 70-90% | 80-85% |

## Common Issues & Fixes

### Issue: Connection Exhaustion

**Symptoms:** Failed operations, timeout errors
**Fix:** Increase `max_size` parameter

```python
max_size=800  # Was 600
```

### Issue: Slow Connection Acquisition

**Symptoms:** High P95/P99 latency
**Fix:** Increase `min_size` to reduce connection creation overhead

```python
min_size=100  # Was 50
```

### Issue: Memory Growth

**Symptoms:** Increasing memory usage over time
**Fix:** Lower `max_idle_time` and `max_lifetime`

```python
max_idle_time=30     # Was 60
max_lifetime=180     # Was 300
```

### Issue: Deadlocks

**Symptoms:** Hung operations, eventual timeouts
**Fix:** Check your code for nested `acquire()` calls

```python
# ❌ BAD - Can cause deadlock
async with pool.acquire() as conn1:
    async with pool.acquire() as conn2:  # Deadlock risk!
        pass

# ✓ GOOD - Sequential or parallel, not nested
async with pool.acquire() as conn:
    result1 = await conn.query("...")

async with pool.acquire() as conn:
    result2 = await conn.query("...")
```

## Advanced Testing

### Test with Production-Like Data

```python
# In load_test_pool.py, modify simulate_work():
await conn.query("""
    CREATE user:test SET 
        email = $email,
        data = $data,
        created = time::now()
""", {
    "email": f"user{task_id}@test.com",
    "data": {"key": "value" * 100}  # Realistic payload size
})
```

### Test Error Recovery

Add fault injection to test resilience:

```python
# Simulate connection failures
if task_id % 100 == 0:
    # Force connection closure
    await conn.close()
    # Pool should handle this gracefully
```

### Test Under System Load

Run while system is under load:

```bash
# Start CPU stress
stress-ng --cpu 4 --timeout 60s &

# Run test
python tests/stress_test_simple.py 500
```

## Performance Profiling

Use Python profiling tools:

```bash
# CPU profiling
python -m cProfile -o profile.stats tests/load_test_pool.py

# Analyze
python -m pstats profile.stats
> sort cumulative
> stats 20
```

## Continuous Testing

Add to CI/CD:

```yaml
# .github/workflows/load-test.yml
- name: Run Load Tests
  run: |
    python tests/stress_test_simple.py 500
    if [ $? -ne 0 ]; then
      echo "Load test failed"
      exit 1
    fi
```

## Getting Help

If tests fail:
1. Check SurrealDB server logs
2. Enable DEBUG logging: `logging.basicConfig(level=logging.DEBUG)`
3. Run monitor tool to see pool behavior
4. Check system resources (CPU, memory, network)
5. Verify SurrealDB server can handle connection count

## Summary

**Start with:** `stress_test_simple.py` to validate basic capacity
**Then run:** `monitor_pool.py` to understand behavior  
**Finally:** `load_test_pool.py` for comprehensive validation

Target: 500+ concurrent connections with 100% success rate and <20ms P95 latency.
