"""
Benchmark different pool configurations to find optimal settings.

Helps determine best min_size, max_size, and other parameters for your workload.
"""

import asyncio
import time
from dataclasses import dataclass
from typing import List
from purreal.pooler import SurrealDBConnectionPool


@dataclass
class PoolConfig:
    """Pool configuration to test."""
    name: str
    min_size: int
    max_size: int
    max_lifetime: int = 300
    max_idle_time: int = 60


@dataclass
class BenchmarkResult:
    """Results from benchmarking a configuration."""
    config: PoolConfig
    duration: float
    operations: int
    successful: int
    failed: int
    avg_acquire_time: float
    p95_acquire_time: float
    
    @property
    def ops_per_sec(self) -> float:
        return self.successful / self.duration if self.duration > 0 else 0
    
    @property
    def success_rate(self) -> float:
        return (self.successful / self.operations * 100) if self.operations > 0 else 0


class PoolConfigBenchmark:
    """Benchmark multiple pool configurations."""
    
    def __init__(
        self,
        db_url: str = "ws://localhost:8000/rpc",
        namespace: str = "test",
        database: str = "test",
        username: str = "root",
        password: str = "root",
    ):
        self.db_url = db_url
        self.namespace = namespace
        self.database = database
        self.username = username
        self.password = password
    
    async def benchmark_config(
        self,
        config: PoolConfig,
        num_workers: int,
        operations_per_worker: int,
    ) -> BenchmarkResult:
        """Benchmark a single pool configuration."""
        print(f"\nTesting: {config.name}")
        print(f"  min_size={config.min_size}, max_size={config.max_size}")
        
        # Create pool
        pool = SurrealDBConnectionPool(
            url=self.db_url,
            namespace=self.namespace,
            database=self.database,
            username=self.username,
            password=self.password,
            min_size=config.min_size,
            max_size=config.max_size,
            max_lifetime=config.max_lifetime,
            max_idle_time=config.max_idle_time,
        )
        
        await pool.initialize()
        
        # Track metrics
        successful = 0
        failed = 0
        acquire_times = []
        
        async def worker(worker_id: int):
            nonlocal successful, failed
            
            for _ in range(operations_per_worker):
                try:
                    acquire_start = time.perf_counter()
                    async with pool.acquire() as conn:
                        acquire_time = time.perf_counter() - acquire_start
                        acquire_times.append(acquire_time)
                        
                        # Simple query
                        await conn.query("SELECT 1")
                        successful += 1
                
                except Exception as e:
                    failed += 1
        
        # Run benchmark
        start_time = time.perf_counter()
        tasks = [worker(i) for i in range(num_workers)]
        await asyncio.gather(*tasks)
        duration = time.perf_counter() - start_time
        
        # Calculate stats
        acquire_times.sort()
        avg_acquire = sum(acquire_times) / len(acquire_times) if acquire_times else 0
        p95_acquire = acquire_times[int(len(acquire_times) * 0.95)] if acquire_times else 0
        
        await pool.close()
        
        return BenchmarkResult(
            config=config,
            duration=duration,
            operations=successful + failed,
            successful=successful,
            failed=failed,
            avg_acquire_time=avg_acquire,
            p95_acquire_time=p95_acquire,
        )
    
    async def run_comparison(
        self,
        configs: List[PoolConfig],
        num_workers: int = 500,
        operations_per_worker: int = 10,
    ):
        """Compare multiple configurations."""
        print("\n" + "="*80)
        print("POOL CONFIGURATION BENCHMARK")
        print("="*80)
        print(f"\nTest Parameters:")
        print(f"  Workers: {num_workers}")
        print(f"  Operations per worker: {operations_per_worker}")
        print(f"  Total operations: {num_workers * operations_per_worker:,}")
        
        results = []
        for config in configs:
            result = await self.benchmark_config(config, num_workers, operations_per_worker)
            results.append(result)
            
            # Brief pause between tests
            await asyncio.sleep(1)
        
        # Print comparison table
        self._print_comparison(results)
    
    def _print_comparison(self, results: List[BenchmarkResult]):
        """Print comparison table."""
        print("\n" + "="*80)
        print("RESULTS COMPARISON")
        print("="*80 + "\n")
        
        # Header
        print(f"{'Config':<25} {'Ops/s':>10} {'Success':>8} {'Avg Acq':>10} {'P95 Acq':>10}")
        print("-" * 80)
        
        # Sort by ops/sec
        results.sort(key=lambda r: r.ops_per_sec, reverse=True)
        
        # Results
        for result in results:
            print(
                f"{result.config.name:<25} "
                f"{result.ops_per_sec:>10.0f} "
                f"{result.success_rate:>7.1f}% "
                f"{result.avg_acquire_time*1000:>9.2f}ms "
                f"{result.p95_acquire_time*1000:>9.2f}ms"
            )
        
        # Winner
        winner = results[0]
        print("\n" + "="*80)
        print(f"WINNER: {winner.config.name}")
        print(f"  min_size={winner.config.min_size}, max_size={winner.config.max_size}")
        print(f"  {winner.ops_per_sec:.0f} ops/sec, {winner.p95_acquire_time*1000:.2f}ms P95")
        print("="*80 + "\n")


async def run_standard_benchmark():
    """Run benchmark with standard configurations."""
    
    configs = [
        # Conservative
        PoolConfig(
            name="Conservative",
            min_size=20,
            max_size=400,
        ),
        
        # Balanced
        PoolConfig(
            name="Balanced",
            min_size=50,
            max_size=600,
        ),
        
        # Aggressive
        PoolConfig(
            name="Aggressive",
            min_size=100,
            max_size=800,
        ),
        
        # High Min
        PoolConfig(
            name="High Min Pool",
            min_size=200,
            max_size=600,
        ),
        
        # High Max
        PoolConfig(
            name="High Max Pool",
            min_size=50,
            max_size=1000,
        ),
        
        # Minimal
        PoolConfig(
            name="Minimal",
            min_size=10,
            max_size=550,
        ),
    ]
    
    benchmark = PoolConfigBenchmark()
    await benchmark.run_comparison(
        configs=configs,
        num_workers=500,
        operations_per_worker=10,
    )


async def find_optimal_config():
    """
    Iteratively find optimal min_size and max_size.
    
    Tests different combinations to find best performance.
    """
    print("\n" + "="*80)
    print("FINDING OPTIMAL CONFIGURATION")
    print("="*80 + "\n")
    
    benchmark = PoolConfigBenchmark()
    
    # Test different min_size values with fixed max_size
    print("\nPhase 1: Finding optimal min_size")
    print("-" * 80)
    
    min_size_configs = [
        PoolConfig(f"min={m}", min_size=m, max_size=600)
        for m in [10, 25, 50, 75, 100, 150]
    ]
    
    min_results = []
    for config in min_size_configs:
        result = await benchmark.benchmark_config(config, num_workers=500, operations_per_worker=10)
        min_results.append(result)
        print(f"  {config.name}: {result.ops_per_sec:.0f} ops/sec")
        await asyncio.sleep(1)
    
    # Find best min_size
    best_min = max(min_results, key=lambda r: r.ops_per_sec)
    print(f"\nBest min_size: {best_min.config.min_size}")
    
    # Test different max_size values with optimal min_size
    print("\nPhase 2: Finding optimal max_size")
    print("-" * 80)
    
    max_size_configs = [
        PoolConfig(f"max={m}", min_size=best_min.config.min_size, max_size=m)
        for m in [400, 500, 600, 700, 800, 1000]
    ]
    
    max_results = []
    for config in max_size_configs:
        result = await benchmark.benchmark_config(config, num_workers=500, operations_per_worker=10)
        max_results.append(result)
        print(f"  {config.name}: {result.ops_per_sec:.0f} ops/sec")
        await asyncio.sleep(1)
    
    # Find best max_size
    best_max = max(max_results, key=lambda r: r.ops_per_sec)
    print(f"\nBest max_size: {best_max.config.max_size}")
    
    # Final recommendation
    print("\n" + "="*80)
    print("RECOMMENDED CONFIGURATION")
    print("="*80)
    print(f"""
pool = SurrealDBConnectionPool(
    min_size={best_min.config.min_size},
    max_size={best_max.config.max_size},
    max_lifetime=300,
    max_idle_time=60,
)
""")
    print(f"Expected performance: {best_max.ops_per_sec:.0f} ops/sec")
    print("="*80 + "\n")


if __name__ == "__main__":
    import sys
    
    if len(sys.argv) > 1 and sys.argv[1] == "--find-optimal":
        asyncio.run(find_optimal_config())
    else:
        asyncio.run(run_standard_benchmark())
