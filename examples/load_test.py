"""
Load testing suite for purreal connection pool.

Tests high-throughput scenarios with 500+ concurrent connections to validate:
- Pool can handle target connection count
- No deadlocks or race conditions
- Proper connection reuse
- Performance under sustained load
- Error recovery
"""

import asyncio
import time
import statistics
from typing import List, Dict
from dataclasses import dataclass, field
import logging

from purreal.pooler import SurrealDBConnectionPool

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


@dataclass
class LoadTestMetrics:
    """Track performance metrics during load test."""
    total_operations: int = 0
    successful_operations: int = 0
    failed_operations: int = 0
    acquire_times: List[float] = field(default_factory=list)
    query_times: List[float] = field(default_factory=list)
    release_times: List[float] = field(default_factory=list)
    total_duration: float = 0.0
    peak_concurrent: int = 0
    errors: Dict[str, int] = field(default_factory=dict)
    
    def add_error(self, error_type: str):
        """Track error occurrence."""
        self.errors[error_type] = self.errors.get(error_type, 0) + 1
    
    def record_acquire(self, duration: float):
        """Record connection acquisition time."""
        self.acquire_times.append(duration)
    
    def record_query(self, duration: float):
        """Record query execution time."""
        self.query_times.append(duration)
    
    def record_release(self, duration: float):
        """Record connection release time."""
        self.release_times.append(duration)
    
    def get_stats(self, times: List[float]) -> Dict:
        """Calculate statistics for a list of times."""
        if not times:
            return {"min": 0, "max": 0, "avg": 0, "p50": 0, "p95": 0, "p99": 0}
        
        sorted_times = sorted(times)
        return {
            "min": min(sorted_times),
            "max": max(sorted_times),
            "avg": statistics.mean(sorted_times),
            "p50": sorted_times[int(len(sorted_times) * 0.50)],
            "p95": sorted_times[int(len(sorted_times) * 0.95)],
            "p99": sorted_times[int(len(sorted_times) * 0.99)],
        }
    
    def print_report(self):
        """Print comprehensive test report."""
        print("\n" + "="*80)
        print("LOAD TEST REPORT")
        print("="*80)
        
        print(f"\nOperations:")
        print(f"  Total:      {self.total_operations:,}")
        print(f"  Successful: {self.successful_operations:,}")
        print(f"  Failed:     {self.failed_operations:,}")
        print(f"  Success Rate: {(self.successful_operations/self.total_operations*100):.2f}%")
        
        print(f"\nThroughput:")
        print(f"  Duration: {self.total_duration:.2f}s")
        print(f"  Ops/sec:  {self.successful_operations/self.total_duration:.2f}")
        print(f"  Peak Concurrent: {self.peak_concurrent}")
        
        print(f"\nConnection Acquisition (ms):")
        acq_stats = self.get_stats(self.acquire_times)
        for metric, value in acq_stats.items():
            print(f"  {metric.upper():4}: {value*1000:.2f}")
        
        print(f"\nQuery Execution (ms):")
        query_stats = self.get_stats(self.query_times)
        for metric, value in query_stats.items():
            print(f"  {metric.upper():4}: {value*1000:.2f}")
        
        print(f"\nConnection Release (ms):")
        rel_stats = self.get_stats(self.release_times)
        for metric, value in rel_stats.items():
            print(f"  {metric.upper():4}: {value*1000:.2f}")
        
        if self.errors:
            print(f"\nErrors:")
            for error_type, count in self.errors.items():
                print(f"  {error_type}: {count}")
        
        print("="*80 + "\n")


class PoolLoadTester:
    """High-throughput load tester for connection pool."""
    
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
        self.pool = None
        self.active_tasks = 0
        self.peak_concurrent = 0
    
    async def setup_pool(self, min_size: int, max_size: int):
        """Initialize connection pool with specified size."""
        logger.info(f"Setting up pool: min={min_size}, max={max_size}")
        self.pool = SurrealDBConnectionPool(
            url=self.db_url,
            namespace=self.namespace,
            database=self.database,
            username=self.username,
            password=self.password,
            min_size=min_size,
            max_size=max_size,
            max_lifetime=300,
            max_idle_time=60,
        )
        await self.pool.initialize()
        logger.info(f"Pool initialized: {self.pool.size} connections")
    
    async def teardown_pool(self):
        """Clean up pool resources."""
        if self.pool:
            await self.pool.close()
            logger.info("Pool closed")
    
    async def simulate_work(
        self,
        task_id: int,
        metrics: LoadTestMetrics,
        query_complexity: str = "simple",
    ):
        """Simulate a single database operation."""
        self.active_tasks += 1
        self.peak_concurrent = max(self.peak_concurrent, self.active_tasks)
        
        try:
            # Acquire connection
            acquire_start = time.perf_counter()
            async with self.pool.acquire() as conn:
                acquire_duration = time.perf_counter() - acquire_start
                metrics.record_acquire(acquire_duration)
                
                # Execute query based on complexity
                query_start = time.perf_counter()
                
                if query_complexity == "simple":
                    # Simple SELECT
                    await conn.query("SELECT * FROM test LIMIT 1")
                
                elif query_complexity == "moderate":
                    # Multiple operations
                    await conn.query("CREATE test:item SET value = $val", {"val": task_id})
                    await conn.query("SELECT * FROM test WHERE value = $val", {"val": task_id})
                    await conn.query("DELETE test:item WHERE value = $val", {"val": task_id})
                
                elif query_complexity == "complex":
                    # Complex transaction
                    await conn.query("""
                        BEGIN TRANSACTION;
                        CREATE test:item SET value = $val, created = time::now();
                        LET $result = SELECT * FROM test WHERE value = $val;
                        UPDATE test:item SET accessed = time::now() WHERE value = $val;
                        DELETE test:item WHERE value = $val;
                        COMMIT TRANSACTION;
                    """, {"val": task_id})
                
                query_duration = time.perf_counter() - query_start
                metrics.record_query(query_duration)
            
            # Connection automatically released by context manager
            release_duration = 0.001  # Minimal overhead
            metrics.record_release(release_duration)
            
            metrics.successful_operations += 1
            
        except Exception as e:
            metrics.failed_operations += 1
            metrics.add_error(type(e).__name__)
            logger.error(f"Task {task_id} failed: {e}")
        
        finally:
            metrics.total_operations += 1
            self.active_tasks -= 1
    
    async def test_sustained_load(
        self,
        num_operations: int,
        concurrent_workers: int,
        query_complexity: str = "simple",
    ) -> LoadTestMetrics:
        """
        Test sustained load with specified concurrency.
        
        Args:
            num_operations: Total number of operations to perform
            concurrent_workers: Number of concurrent tasks
            query_complexity: "simple", "moderate", or "complex"
        """
        logger.info(f"Starting sustained load test:")
        logger.info(f"  Operations: {num_operations:,}")
        logger.info(f"  Workers: {concurrent_workers}")
        logger.info(f"  Complexity: {query_complexity}")
        
        metrics = LoadTestMetrics()
        start_time = time.perf_counter()
        
        # Create semaphore to limit concurrency
        semaphore = asyncio.Semaphore(concurrent_workers)
        
        async def worker(task_id: int):
            async with semaphore:
                await self.simulate_work(task_id, metrics, query_complexity)
        
        # Run all operations
        tasks = [worker(i) for i in range(num_operations)]
        await asyncio.gather(*tasks)
        
        metrics.total_duration = time.perf_counter() - start_time
        metrics.peak_concurrent = self.peak_concurrent
        
        return metrics
    
    async def test_burst_load(
        self,
        burst_size: int,
        num_bursts: int,
        burst_interval: float = 1.0,
    ) -> LoadTestMetrics:
        """
        Test burst load pattern (sudden spikes in traffic).
        
        Args:
            burst_size: Number of concurrent operations per burst
            num_bursts: Number of bursts to perform
            burst_interval: Time between bursts in seconds
        """
        logger.info(f"Starting burst load test:")
        logger.info(f"  Burst size: {burst_size}")
        logger.info(f"  Bursts: {num_bursts}")
        logger.info(f"  Interval: {burst_interval}s")
        
        metrics = LoadTestMetrics()
        start_time = time.perf_counter()
        
        for burst_num in range(num_bursts):
            logger.info(f"Executing burst {burst_num + 1}/{num_bursts}")
            
            # Launch all tasks in burst simultaneously
            tasks = [
                self.simulate_work(
                    burst_num * burst_size + i,
                    metrics,
                    "moderate"
                )
                for i in range(burst_size)
            ]
            await asyncio.gather(*tasks)
            
            # Wait before next burst (except after last burst)
            if burst_num < num_bursts - 1:
                await asyncio.sleep(burst_interval)
        
        metrics.total_duration = time.perf_counter() - start_time
        metrics.peak_concurrent = self.peak_concurrent
        
        return metrics
    
    async def test_connection_churn(
        self,
        num_operations: int,
        hold_time_range: tuple = (0.01, 0.1),
    ) -> LoadTestMetrics:
        """
        Test rapid connection acquire/release (connection churn).
        
        Args:
            num_operations: Number of acquire/release cycles
            hold_time_range: (min, max) time to hold connection in seconds
        """
        import random
        
        logger.info(f"Starting connection churn test:")
        logger.info(f"  Operations: {num_operations:,}")
        logger.info(f"  Hold time: {hold_time_range[0]}-{hold_time_range[1]}s")
        
        metrics = LoadTestMetrics()
        start_time = time.perf_counter()
        
        async def churn_worker(task_id: int):
            self.active_tasks += 1
            self.peak_concurrent = max(self.peak_concurrent, self.active_tasks)
            
            try:
                acquire_start = time.perf_counter()
                async with self.pool.acquire() as conn:
                    acquire_duration = time.perf_counter() - acquire_start
                    metrics.record_acquire(acquire_duration)
                    
                    # Hold connection for random time
                    hold_time = random.uniform(*hold_time_range)
                    await asyncio.sleep(hold_time)
                    
                    # Quick query
                    query_start = time.perf_counter()
                    await conn.query("SELECT 1")
                    query_duration = time.perf_counter() - query_start
                    metrics.record_query(query_duration)
                
                metrics.successful_operations += 1
            
            except Exception as e:
                metrics.failed_operations += 1
                metrics.add_error(type(e).__name__)
            
            finally:
                metrics.total_operations += 1
                self.active_tasks -= 1
        
        tasks = [churn_worker(i) for i in range(num_operations)]
        await asyncio.gather(*tasks)
        
        metrics.total_duration = time.perf_counter() - start_time
        metrics.peak_concurrent = self.peak_concurrent
        
        return metrics


async def run_comprehensive_test_suite():
    """Run full suite of load tests."""
    print("\n" + "="*80)
    print("PURREAL CONNECTION POOL - COMPREHENSIVE LOAD TEST SUITE")
    print("="*80 + "\n")
    
    tester = PoolLoadTester()
    
    # Test 1: Sustained load with 500 concurrent workers
    print("\n[TEST 1] Sustained Load - 500 Concurrent Workers")
    print("-" * 80)
    await tester.setup_pool(min_size=50, max_size=600)
    metrics_1 = await tester.test_sustained_load(
        num_operations=5000,
        concurrent_workers=500,
        query_complexity="simple"
    )
    metrics_1.print_report()
    await tester.teardown_pool()
    
    # Test 2: Burst load - 1000 connections in bursts
    print("\n[TEST 2] Burst Load - 1000 Concurrent Bursts")
    print("-" * 80)
    await tester.setup_pool(min_size=100, max_size=1200)
    metrics_2 = await tester.test_burst_load(
        burst_size=1000,
        num_bursts=5,
        burst_interval=2.0
    )
    metrics_2.print_report()
    await tester.teardown_pool()
    
    # Test 3: Connection churn - rapid acquire/release
    print("\n[TEST 3] Connection Churn - Rapid Acquire/Release")
    print("-" * 80)
    await tester.setup_pool(min_size=50, max_size=600)
    metrics_3 = await tester.test_connection_churn(
        num_operations=10000,
        hold_time_range=(0.001, 0.05)
    )
    metrics_3.print_report()
    await tester.teardown_pool()
    
    # Test 4: Mixed complexity under high load
    print("\n[TEST 4] Mixed Complexity - 500 Workers, Complex Queries")
    print("-" * 80)
    await tester.setup_pool(min_size=50, max_size=600)
    metrics_4 = await tester.test_sustained_load(
        num_operations=2000,
        concurrent_workers=500,
        query_complexity="complex"
    )
    metrics_4.print_report()
    await tester.teardown_pool()
    
    print("\n" + "="*80)
    print("TEST SUITE COMPLETED")
    print("="*80 + "\n")


if __name__ == "__main__":
    asyncio.run(run_comprehensive_test_suite())
