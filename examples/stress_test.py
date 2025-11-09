"""
Simple stress test for quick validation of high connection counts.

Run this first to validate your pool can handle target concurrency.
"""

import asyncio
import time
from purreal.pooler import SurrealDBConnectionPool


async def quick_stress_test(
    target_connections: int = 500,
    operations_per_connection: int = 10
):
    """
    Quick test to validate pool handles target connection count.
    
    Args:
        target_connections: Number of concurrent connections to test
        operations_per_connection: Operations per connection
    """
    print(f"\n{'='*80}")
    print(f"QUICK STRESS TEST: {target_connections} concurrent connections")
    print(f"{'='*80}\n")
    
    # Create pool with headroom
    pool = SurrealDBConnectionPool(
        url="ws://localhost:8000/rpc",
        namespace="test",
        database="test",
        username="root",
        password="root",
        min_size=min(50, target_connections // 10),
        max_size=target_connections + 100,  # Headroom for safety
        max_lifetime=300,
        max_idle_time=60,
    )
    
    await pool.initialize()
    print(f"✓ Pool initialized: {pool.size} initial connections\n")
    
    # Track metrics
    successful = 0
    failed = 0
    start_time = time.perf_counter()
    
    async def worker(worker_id: int):
        """Single worker performing multiple operations."""
        nonlocal successful, failed
        
        for op in range(operations_per_connection):
            try:
                async with pool.acquire() as conn:
                    # Simple query to validate connection
                    await conn.query("SELECT 1 as test")
                    successful += 1
                    
            except Exception as e:
                failed += 1
                print(f"✗ Worker {worker_id} op {op} failed: {e}")
    
    # Launch all workers
    print(f"Launching {target_connections} concurrent workers...")
    tasks = [worker(i) for i in range(target_connections)]
    await asyncio.gather(*tasks)
    
    duration = time.perf_counter() - start_time
    total_ops = successful + failed
    
    # Results
    print(f"\n{'='*80}")
    print(f"RESULTS:")
    print(f"  Duration:    {duration:.2f}s")
    print(f"  Operations:  {total_ops:,} total")
    print(f"  Successful:  {successful:,} ({successful/total_ops*100:.1f}%)")
    print(f"  Failed:      {failed:,} ({failed/total_ops*100:.1f}%)")
    print(f"  Throughput:  {successful/duration:.0f} ops/sec")
    print(f"  Pool stats:  {pool.size} connections, {pool.available} available")
    
    if failed == 0:
        print(f"\n✓ SUCCESS: Pool handled {target_connections} concurrent connections!")
    else:
        print(f"\n✗ FAILURE: {failed} operations failed")
    
    print(f"{'='*80}\n")
    
    await pool.close()
    return failed == 0


async def progressive_load_test():
    """
    Test with progressively increasing load to find limits.
    """
    print(f"\n{'='*80}")
    print("PROGRESSIVE LOAD TEST")
    print(f"{'='*80}\n")
    
    connection_counts = [100, 250, 500, 750, 1000]
    
    for count in connection_counts:
        print(f"\nTesting {count} concurrent connections...")
        success = await quick_stress_test(
            target_connections=count,
            operations_per_connection=5
        )
        
        if not success:
            print(f"\n✗ Failed at {count} connections. This is your limit.")
            break
        
        # Brief pause between tests
        await asyncio.sleep(2)
    
    print("\nProgressive test complete.")


if __name__ == "__main__":
    import sys
    
    if len(sys.argv) > 1:
        target = int(sys.argv[1])
        asyncio.run(quick_stress_test(target_connections=target))
    else:
        # Run progressive test
        asyncio.run(progressive_load_test())
