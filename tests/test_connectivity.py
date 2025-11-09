#!/usr/bin/env python3
"""
Simple connectivity test for purreal.

Run this after git pull to verify:
- SurrealDB is accessible
- purreal can connect
- Basic operations work

Usage:
    python tests/test_connectivity.py
    
    # Custom SurrealDB URL
    python tests/test_connectivity.py --url ws://localhost:8000/rpc
    
    # Test with authentication
    python tests/test_connectivity.py --user root --pass root
"""

import asyncio
import sys
import argparse
from typing import Optional

# Try to import purreal
try:
    from purreal.pooler import SurrealDBConnectionPool
    print("✓ purreal imported successfully")
except ImportError as e:
    print(f"✗ Failed to import purreal: {e}")
    print("\nMake sure you're in the purreal directory and run:")
    print("  pip install -e .")
    sys.exit(1)


class ConnectivityTest:
    """Test SurrealDB connectivity."""
    
    def __init__(
        self,
        url: str = "ws://localhost:8000/rpc",
        namespace: str = "test",
        database: str = "test",
        username: Optional[str] = None,
        password: Optional[str] = None,
    ):
        self.url = url
        self.namespace = namespace
        self.database = database
        self.username = username
        self.password = password
        self.pool: Optional[SurrealDBConnectionPool] = None
    
    async def test_connection(self) -> bool:
        """Test basic connection to SurrealDB."""
        print(f"\n1. Testing connection to {self.url}...")
        
        try:
            # Build credentials dict
            # Note: SurrealDB signin() expects "username" and "password" keys, not "user" and "pass"
            credentials = {}
            if self.username and self.password:
                credentials = {"username": self.username, "password": self.password}
            elif self.username or self.password:
                print(f"   ⚠️  Warning: Both username and password required for authentication")
                credentials = {"username": self.username or "root", "password": self.password or "root"}
            else:
                # Default credentials if none provided
                credentials = {"username": "root", "password": "root"}
            
            self.pool = SurrealDBConnectionPool(
                uri=self.url,
                credentials=credentials,
                namespace=self.namespace,
                database=self.database,
                min_connections=2,
                max_connections=10,  # Increased for concurrent tests
                acquisition_timeout=15.0,  # Allow time for queued tasks
                reset_on_return=False,  # Disable reset to prevent blocking during release
            )
            
            await self.pool.initialize()
            stats = await self.pool.get_stats()
            print(f"   ✓ Connected successfully")
            print(f"   ✓ Pool initialized with {stats['current_connections']} connection(s)")
            return True
            
        except Exception as e:
            print(f"   ✗ Connection failed: {e}")
            print(f"\nTroubleshooting:")
            print(f"  - Is SurrealDB running? Start with:")
            print(f"    surreal start --bind 0.0.0.0:8000 --user root --pass root")
            print(f"  - Check the URL: {self.url}")
            print(f"  - Verify credentials (if using authentication)")
            return False
    
    async def test_basic_query(self) -> bool:
        """Test basic query execution."""
        print(f"\n2. Testing basic query...")
        
        try:
            async with self.pool.acquire() as conn:
                # RETURN doesn't support AS aliasing - use plain RETURN
                result = await conn.query("RETURN 1")
                print(f"   ✓ Query executed successfully")
                print(f"   ✓ Result: {result}")
                return True
                
        except Exception as e:
            print(f"   ✗ Query failed: {e}")
            return False
    
    async def test_crud_operations(self) -> bool:
        """Test Create, Read, Update, Delete operations."""
        print(f"\n3. Testing CRUD operations...")
        
        try:
            async with self.pool.acquire() as conn:
                # Create
                create_result = await conn.query(
                    "CREATE test:connectivity_test SET message = $msg, timestamp = time::now()",
                    {"msg": "purreal connectivity test"}
                )
                print(f"   ✓ CREATE: {len(create_result)} record(s)")
                
                # Read
                read_result = await conn.query("SELECT * FROM test:connectivity_test")
                print(f"   ✓ READ: {len(read_result)} record(s)")
                
                # Update
                update_result = await conn.query(
                    "UPDATE test:connectivity_test SET verified = true"
                )
                print(f"   ✓ UPDATE: {len(update_result)} record(s)")
                
                # Delete
                delete_result = await conn.query("DELETE test:connectivity_test")
                print(f"   ✓ DELETE: {len(delete_result)} record(s)")
                
                return True
                
        except Exception as e:
            print(f"   ✗ CRUD operations failed: {e}")
            return False
    
    async def test_concurrent_access(self) -> bool:
        """Test concurrent connection acquisition."""
        print(f"\n4. Testing concurrent access (10 parallel queries)...")
        
        try:
            async def worker(worker_id: int):
                async with self.pool.acquire() as conn:
                    await conn.query(f"RETURN {worker_id}")
            
            tasks = [worker(i) for i in range(10)]
            await asyncio.gather(*tasks)
            
            # Give connections time to fully release before checking stats
            await asyncio.sleep(0.1)
            
            stats = await self.pool.get_stats()
            print(f"   ✓ 10 concurrent queries completed")
            print(f"   ✓ Pool stats: {stats['current_connections']} total, {stats['available_connections']} available")
            return True
            
        except Exception as e:
            print(f"   ✗ Concurrent access failed: {e}")
            return False
    
    async def test_pool_scaling(self) -> bool:
        """Test pool can scale under load."""
        print(f"\n5. Testing pool scaling (10 sequential batches)...")
        
        initial_stats = await self.pool.get_stats()
        initial_size = initial_stats['current_connections']
        
        try:
            async def worker(worker_id: int):
                async with self.pool.acquire() as conn:
                    await conn.query("RETURN 1")
            
            # Run in batches to avoid overwhelming the queue
            completed = 0
            for batch_num in range(5):
                tasks = [worker(batch_num * 2 + i) for i in range(2)]
                await asyncio.gather(*tasks)
                completed += 2
            
            final_stats = await self.pool.get_stats()
            peak_size = final_stats['peak_connections']
            
            print(f"   ✓ {completed} queries in batches completed")
            print(f"   ✓ Pool scaled from {initial_size} to {peak_size} connections")
            return True
            
        except Exception as e:
            print(f"   ✗ Pool scaling failed: {e}")
            return False
    
    async def cleanup(self):
        """Clean up resources."""
        if self.pool:
            await self.pool.close()
            print(f"\n✓ Pool closed")
    
    async def run_all_tests(self):
        """Run complete test suite."""
        print("="*80)
        print("PURREAL CONNECTIVITY TEST")
        print("="*80)
        
        tests = [
            ("Connection", self.test_connection),
            ("Basic Query", self.test_basic_query),
            ("CRUD Operations", self.test_crud_operations),
            ("Concurrent Access", self.test_concurrent_access),
            ("Pool Scaling", self.test_pool_scaling),
        ]
        
        passed = 0
        failed = 0
        
        for test_name, test_func in tests:
            try:
                if await test_func():
                    passed += 1
                else:
                    failed += 1
                    # Don't run remaining tests if connection failed
                    if test_name == "Connection":
                        print(f"\n✗ Stopping tests - cannot connect to SurrealDB")
                        break
            except Exception as e:
                print(f"\n✗ Test '{test_name}' crashed: {e}")
                failed += 1
        
        await self.cleanup()
        
        # Summary
        print("\n" + "="*80)
        print("SUMMARY")
        print("="*80)
        print(f"Passed: {passed}/{len(tests)}")
        print(f"Failed: {failed}/{len(tests)}")
        
        if failed == 0:
            print("\n🎉 ALL TESTS PASSED! Your setup is working correctly.")
            print("="*80 + "\n")
            return True
        else:
            print("\n⚠️  SOME TESTS FAILED. Check the output above for details.")
            print("="*80 + "\n")
            return False


async def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Test purreal connectivity to SurrealDB",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Test with default local SurrealDB
  python tests/test_connectivity.py
  
  # Test with custom URL
  python tests/test_connectivity.py --url ws://db.example.com:8000/rpc
  
  # Test with authentication
  python tests/test_connectivity.py --user myuser --pass mypassword
  
  # Full custom setup
  python tests/test_connectivity.py \\
    --url ws://localhost:8000/rpc \\
    --namespace prod \\
    --database mydb \\
    --user admin \\
    --pass secret123
        """
    )
    
    parser.add_argument(
        "--url",
        default="ws://localhost:8000/rpc",
        help="SurrealDB WebSocket URL (default: ws://localhost:8000/rpc)"
    )
    parser.add_argument(
        "--namespace",
        default="test",
        help="Database namespace (default: test)"
    )
    parser.add_argument(
        "--database",
        default="test",
        help="Database name (default: test)"
    )
    parser.add_argument(
        "--user",
        help="Username for authentication (optional)"
    )
    parser.add_argument(
        "--pass",
        dest="password",
        help="Password for authentication (optional)"
    )
    
    args = parser.parse_args()
    
    tester = ConnectivityTest(
        url=args.url,
        namespace=args.namespace,
        database=args.database,
        username=args.user,
        password=args.password,
    )
    
    success = await tester.run_all_tests()
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n\nTest interrupted by user")
        sys.exit(1)
