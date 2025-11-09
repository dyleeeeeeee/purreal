#!/bin/bash
# Quick test runner for purreal
# Usage: ./test.sh [connectivity|stress|monitor|benchmark|all]

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${GREEN}=================================${NC}"
echo -e "${GREEN}   Purreal Test Runner${NC}"
echo -e "${GREEN}=================================${NC}\n"

# Check SurrealDB is running
check_surrealdb() {
    echo "Checking SurrealDB connection..."
    if ! curl -s http://localhost:8000/status > /dev/null 2>&1; then
        echo -e "${RED}✗ SurrealDB is not running${NC}"
        echo ""
        echo "Start SurrealDB with:"
        echo "  surreal start --bind 0.0.0.0:8000 --user root --pass root"
        echo ""
        echo "Or use Docker:"
        echo "  docker run -p 8000:8000 surrealdb/surrealdb:latest start"
        exit 1
    fi
    echo -e "${GREEN}✓ SurrealDB is running${NC}\n"
}

# Test connectivity
test_connectivity() {
    echo -e "${YELLOW}Running connectivity test...${NC}\n"
    python tests/test_connectivity.py
}

# Stress test
test_stress() {
    echo -e "${YELLOW}Running stress test (500 connections)...${NC}\n"
    python examples/stress_test.py 500
}

# Monitor pool
test_monitor() {
    echo -e "${YELLOW}Running monitored load test...${NC}\n"
    python examples/monitor_pool.py
}

# Benchmark configs
test_benchmark() {
    echo -e "${YELLOW}Running configuration benchmark...${NC}\n"
    python benchmarks/benchmark_configs.py
}

# Throughput test
test_throughput() {
    echo -e "${YELLOW}Running high-throughput stress test...${NC}\n"
    python benchmarks/high_throughput.py
}

# Full load test
test_load() {
    echo -e "${YELLOW}Running comprehensive load test...${NC}\n"
    echo -e "${RED}Warning: This will take several minutes${NC}\n"
    read -p "Continue? (y/n) " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        python examples/load_test.py
    fi
}

# Parse arguments
TEST_TYPE=${1:-connectivity}

check_surrealdb

case $TEST_TYPE in
    connectivity|conn)
        test_connectivity
        ;;
    stress)
        test_stress
        ;;
    monitor|mon)
        test_monitor
        ;;
    benchmark|bench)
        test_benchmark
        ;;
    throughput|high)
        test_throughput
        ;;
    load)
        test_load
        ;;
    all)
        test_connectivity
        echo ""
        test_stress
        echo ""
        test_monitor
        ;;
    *)
        echo "Usage: $0 [connectivity|stress|monitor|benchmark|throughput|load|all]"
        echo ""
        echo "Tests:"
        echo "  connectivity  - Test basic connectivity (default)"
        echo "  stress        - Test 500 concurrent connections"
        echo "  monitor       - Monitor pool behavior in real-time"
        echo "  benchmark     - Benchmark different configurations"
        echo "  throughput    - High-throughput stress test (sustained load, bursts, churn)"
        echo "  load          - Comprehensive load test (slow)"
        echo "  all           - Run connectivity, stress, and monitor tests"
        exit 1
        ;;
esac

echo -e "\n${GREEN}✓ Test complete!${NC}"
