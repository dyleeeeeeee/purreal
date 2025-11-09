# Purreal Performance Testing Guide

## Quick Start

```bash
# Run high-throughput stress test
./test.bat throughput

# Or directly
python tests/test_high_throughput.py
```

## What Gets Tested

### 1. Sustained Load Test (50 workers, 10 seconds)
**Purpose:** Validate continuous concurrent query execution under steady load

**Metrics:**
- Queries per second (QPS)
- Latency distribution (p50, p95, p99)
- Connection pool scaling behavior
- Error rate under sustained pressure

**Expected Performance:**
- QPS: 500-2000+ (depends on hardware & SurrealDB config)
- p95 latency: <50ms for simple queries
- Success rate: >99%

### 2. Burst Load Test (100 queries × 5 bursts)
**Purpose:** Test pool behavior under sudden traffic spikes

**Metrics:**
- Peak connection handling
- Queue management efficiency
- Latency degradation during bursts
- Recovery time between bursts

**Expected Behavior:**
- Pool scales to `max_connections` (50)
- Queuing handles overflow gracefully
- No acquisition timeouts
- Connections released promptly

### 3. Connection Churn Test (100 cycles × 20 connections)
**Purpose:** Validate rapid acquire/release patterns

**Metrics:**
- Connection lifecycle performance
- Pool locking overhead
- Memory stability
- No connection leaks

**Expected Results:**
- 2000 total acquire/release cycles
- Consistent latency across cycles
- Pool returns to baseline after test
- Zero connection errors

## Performance Benchmarks

### Minimal Configuration
```python
pool = SurrealDBConnectionPool(
    uri="ws://localhost:8000/rpc",
    credentials={"username": "root", "password": "root"},
    namespace="test",
    database="test",
    min_connections=10,
    max_connections=50,
    reset_on_return=False,  # Critical for performance
)
```

### Expected Results (Local SurrealDB)

| Test | Queries | Duration | QPS | p95 Latency | Success Rate |
|------|---------|----------|-----|-------------|--------------|
| Sustained (50 workers) | 5000-15000 | 10s | 500-1500 | <50ms | >99% |
| Burst (100×5) | 500 | ~5s | 100-200 | <100ms | >99% |
| Churn (100×20) | 2000 | ~10s | 200-400 | <50ms | 100% |

*Actual performance varies based on hardware, network, and SurrealDB configuration*

## Interpreting Results

### Good Performance Indicators ✅
- **QPS > 500**: Pool is handling concurrent load efficiently
- **p95 < 50ms**: Low latency, minimal queuing
- **Success rate > 99%**: Stable under stress
- **Peak connections ≤ max_connections**: Pool sizing correct
- **Acquisition timeouts = 0**: No deadlocks or blocking

### Warning Signs ⚠️
- **QPS < 100**: Possible bottleneck (check SurrealDB, network, or pool config)
- **p95 > 500ms**: High latency, likely queue congestion
- **Success rate < 95%**: Connection errors, review logs
- **Acquisition timeouts > 0**: Pool too small or connections not releasing

### Red Flags 🚨
- **Success rate < 80%**: Critical issue, check connection stability
- **Acquisition timeouts > 10%**: Severe blocking, investigate connection lifecycle
- **Pool errors increasing**: Memory leak or connection corruption
- **QPS degrading over time**: Resource exhaustion

## Customizing Tests

### Test Longer Duration
```python
metrics = await tester.sustained_load_test(
    num_workers=100,
    duration_seconds=60,  # 1 minute
    query_type="simple"
)
```

### Test Complex Queries
```python
metrics = await tester.sustained_load_test(
    num_workers=50,
    duration_seconds=30,
    query_type="crud"  # or "complex"
)
```

### Adjust Pool Size
```python
await tester.setup_pool(
    min_connections=20,
    max_connections=100,
    acquisition_timeout=30.0
)
```

### Larger Bursts
```python
metrics = await tester.burst_load_test(
    burst_size=500,      # 500 concurrent queries
    num_bursts=10,       # 10 bursts
    delay_between_bursts=2.0
)
```

## Production Recommendations

### Pool Configuration
```python
# For web servers (moderate traffic)
pool = SurrealDBConnectionPool(
    min_connections=10,
    max_connections=50,
    acquisition_timeout=15.0,
    reset_on_return=False,
)

# For high-traffic APIs
pool = SurrealDBConnectionPool(
    min_connections=25,
    max_connections=100,
    acquisition_timeout=30.0,
    reset_on_return=False,
)

# For background workers (burst patterns)
pool = SurrealDBConnectionPool(
    min_connections=5,
    max_connections=75,
    acquisition_timeout=60.0,
    reset_on_return=False,
)
```

### Key Tuning Parameters

**`max_connections`**: Set to 2-3x peak concurrent request rate
- Too low: Acquisition timeouts
- Too high: SurrealDB resource exhaustion

**`min_connections`**: Set to average concurrent load
- Reduces connection creation overhead
- Maintains consistent performance

**`acquisition_timeout`**: Set based on acceptable request timeout
- Web APIs: 5-15s
- Background jobs: 30-120s
- Critical paths: Use dedicated pools

**`reset_on_return`**: Always `False` for production
- Eliminates 5s blocking per connection release
- Connections maintain namespace/database state

## Troubleshooting

### Low QPS
1. Check SurrealDB resource usage (CPU, memory, network)
2. Increase `max_connections`
3. Verify network latency to SurrealDB
4. Profile query complexity

### High Latency
1. Check pool queue length during test
2. Increase `max_connections` if saturated
3. Optimize query patterns
4. Consider SurrealDB indexing

### Timeouts
1. Verify `acquisition_timeout` is appropriate
2. Check if `max_connections` is too low
3. Investigate slow queries blocking connections
4. Monitor SurrealDB connection limits

### Memory Growth
1. Ensure `reset_on_return=False`
2. Check for connection leaks (monitor pool stats)
3. Verify SurrealDB memory usage
4. Review `max_connections` setting

## Monitoring in Production

```python
# Periodic health check
stats = await pool.get_stats()

# Alert if:
if stats['acquisition_timeouts'] > 0:
    logger.warning("Pool experiencing acquisition timeouts!")

if stats['in_use_connections'] / stats['current_connections'] > 0.9:
    logger.warning("Pool utilization > 90%, consider scaling")

if stats['connection_errors'] > 10:
    logger.error("High connection error rate!")
```

## Next Steps

1. **Run baseline test**: `./test.bat throughput`
2. **Compare with your requirements**: Match QPS to expected load
3. **Tune pool configuration**: Adjust based on results
4. **Test with production queries**: Use `query_type="complex"`
5. **Validate under load**: Run extended duration tests
6. **Monitor in staging**: Observe real-world patterns

---

**Always Works™ Validation:**
- ✅ Run throughput test before deploying to production
- ✅ Verify QPS meets requirements under sustained load
- ✅ Confirm p95 latency within acceptable bounds
- ✅ Validate zero acquisition timeouts
- ✅ Monitor pool stats in production
