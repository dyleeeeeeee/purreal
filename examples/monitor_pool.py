"""
Real-time pool monitoring during load tests.

Shows live statistics about connection pool behavior.
"""

import asyncio
import time
from dataclasses import dataclass
from typing import Optional
from purreal.pooler import SurrealDBConnectionPool


@dataclass
class PoolSnapshot:
    """Snapshot of pool state at a point in time."""
    timestamp: float
    total_connections: int
    available_connections: int
    in_use_connections: int
    waiting_tasks: int = 0
    
    @property
    def utilization(self) -> float:
        """Connection utilization percentage."""
        if self.total_connections == 0:
            return 0.0
        return (self.in_use_connections / self.total_connections) * 100


class PoolMonitor:
    """Monitor connection pool in real-time."""
    
    def __init__(self, pool: SurrealDBConnectionPool, interval: float = 0.5):
        self.pool = pool
        self.interval = interval
        self.snapshots = []
        self.monitoring = False
        self._monitor_task: Optional[asyncio.Task] = None
    
    def start(self):
        """Start monitoring."""
        if not self.monitoring:
            self.monitoring = True
            self._monitor_task = asyncio.create_task(self._monitor_loop())
    
    def stop(self):
        """Stop monitoring."""
        self.monitoring = False
        if self._monitor_task:
            self._monitor_task.cancel()
    
    async def _monitor_loop(self):
        """Continuous monitoring loop."""
        try:
            while self.monitoring:
                snapshot = PoolSnapshot(
                    timestamp=time.time(),
                    total_connections=self.pool.size,
                    available_connections=self.pool.available,
                    in_use_connections=self.pool.size - self.pool.available,
                )
                self.snapshots.append(snapshot)
                
                # Print live stats
                self._print_live_stats(snapshot)
                
                await asyncio.sleep(self.interval)
        
        except asyncio.CancelledError:
            pass
    
    def _print_live_stats(self, snapshot: PoolSnapshot):
        """Print live statistics."""
        bar_length = 40
        used_bars = int((snapshot.in_use_connections / max(snapshot.total_connections, 1)) * bar_length)
        available_bars = bar_length - used_bars
        
        bar = "█" * used_bars + "░" * available_bars
        
        print(
            f"\r[{bar}] "
            f"Total: {snapshot.total_connections:4} | "
            f"Used: {snapshot.in_use_connections:4} | "
            f"Free: {snapshot.available_connections:4} | "
            f"Util: {snapshot.utilization:5.1f}%",
            end="",
            flush=True
        )
    
    def print_summary(self):
        """Print summary statistics."""
        if not self.snapshots:
            print("\nNo data collected")
            return
        
        print("\n\n" + "="*80)
        print("MONITORING SUMMARY")
        print("="*80)
        
        max_total = max(s.total_connections for s in self.snapshots)
        max_used = max(s.in_use_connections for s in self.snapshots)
        avg_util = sum(s.utilization for s in self.snapshots) / len(self.snapshots)
        max_util = max(s.utilization for s in self.snapshots)
        
        print(f"\nConnections:")
        print(f"  Peak Total:     {max_total}")
        print(f"  Peak In-Use:    {max_used}")
        print(f"\nUtilization:")
        print(f"  Average:        {avg_util:.1f}%")
        print(f"  Peak:           {max_util:.1f}%")
        print(f"\nSnapshots:        {len(self.snapshots)}")
        print("="*80 + "\n")


async def monitored_load_test():
    """Run load test with real-time monitoring."""
    print("\n" + "="*80)
    print("MONITORED LOAD TEST")
    print("="*80 + "\n")
    
    # Setup pool
    pool = SurrealDBConnectionPool(
        url="ws://localhost:8000/rpc",
        namespace="test",
        database="test",
        username="root",
        password="root",
        min_size=50,
        max_size=600,
        max_lifetime=300,
        max_idle_time=60,
    )
    
    await pool.initialize()
    print(f"Pool initialized: {pool.size} connections\n")
    
    # Start monitoring
    monitor = PoolMonitor(pool, interval=0.1)
    monitor.start()
    
    # Simulate load
    async def worker(worker_id: int):
        for _ in range(10):
            async with pool.acquire() as conn:
                await conn.query("SELECT 1")
                await asyncio.sleep(0.01)  # Simulate work
    
    print("Running 500 concurrent workers...\n")
    tasks = [worker(i) for i in range(500)]
    await asyncio.gather(*tasks)
    
    # Stop monitoring
    monitor.stop()
    await asyncio.sleep(0.5)  # Let final snapshot print
    
    # Print summary
    monitor.print_summary()
    
    await pool.close()


if __name__ == "__main__":
    asyncio.run(monitored_load_test())
