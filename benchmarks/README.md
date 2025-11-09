# Benchmarks

Performance benchmarks for validating purreal under high-throughput conditions.

## Available Benchmarks

### `high_throughput.py`
Comprehensive stress test suite:
- **Sustained load**: 50 workers × 10 seconds
- **Burst load**: 100 queries × 5 bursts  
- **Connection churn**: 2000 acquire/release cycles

```bash
python benchmarks/high_throughput.py
# or
./test.bat throughput
```

**Metrics tracked:**
- Queries per second (QPS)
- Latency percentiles (p50, p95, p99)
- Success rate
- Pool statistics

### `benchmark_configs.py`
Compare different pool configurations to find optimal settings for your workload.

```bash
python benchmarks/benchmark_configs.py
# or
./test.bat benchmark
```

Tests various `min_connections` and `max_connections` combinations.

## Interpreting Results

### Good Performance ✅
- QPS > 500
- p95 latency < 50ms
- Success rate > 99%
- Zero acquisition timeouts

### Warning Signs ⚠️
- QPS < 100
- p95 latency > 500ms
- Success rate < 95%
- Acquisition timeouts > 0

See [PERFORMANCE_TESTING.md](../PERFORMANCE_TESTING.md) for detailed guidance.
