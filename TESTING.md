# Testing Guide for Purreal

This document provides comprehensive guidance on testing the purreal connection pooler library.

## Quick Start

```bash
# Install with test dependencies
pip install -e ".[test]"

# Run all tests
pytest

# Run with coverage report
pytest --cov=purreal --cov-report=html
open htmlcov/index.html  # View coverage report
```

## Test Architecture

### Design Principles

1. **Comprehensive Coverage**: Target 90%+ code coverage
2. **Fast Execution**: Unit tests complete in <1s
3. **Isolation**: Mock external dependencies (SurrealDB)
4. **Realistic Scenarios**: Test actual usage patterns
5. **Performance Validation**: Verify O(1) operations

### Test Structure

```
tests/
├── conftest.py              # Shared fixtures and configuration
├── test_pooler.py           # Main test suite (600+ lines)
│   ├── TestPooledConnection         # Connection wrapper tests
│   ├── TestConnectionPoolInitialization  # Pool setup tests
│   ├── TestConnectionAcquisition    # Acquire/release tests
│   ├── TestConnectionRelease        # Cleanup tests
│   ├── TestQueryExecution           # Query execution tests
│   ├── TestPoolStatistics           # Stats tracking tests
│   ├── TestPoolClosure              # Shutdown tests
│   ├── TestPoolManager              # Singleton manager tests
│   └── TestPoolPerformance          # Performance tests
└── README.md                # Testing documentation
```

## Test Categories

### Unit Tests
**Purpose**: Test individual components in isolation  
**Speed**: <10ms per test  
**Coverage**: 80% of test suite

```python
@pytest.mark.unit
async def test_mark_as_used(pooled_connection):
    """Test marking connection as used increments counters."""
    pooled_connection.mark_as_used()
    assert pooled_connection.in_use
    assert pooled_connection.usage_count == 1
```

### Integration Tests
**Purpose**: Test component interactions  
**Speed**: 100-500ms per test  
**Coverage**: 15% of test suite

```python
@pytest.mark.integration
async def test_pool_with_real_schema(pool_config):
    """Test pool with actual schema file execution."""
    pool_config["schema_file"] = "schema.surql"
    pool = await SurrealDBConnectionPool(**pool_config)
    # ...
```

### Performance Tests
**Purpose**: Validate performance characteristics  
**Speed**: 1-5s per test  
**Coverage**: 5% of test suite

```python
@pytest.mark.performance
@pytest.mark.parametrize("concurrent_tasks", [10, 50, 100])
async def test_concurrent_load(pool_config, concurrent_tasks):
    """Test pool handles concurrent load efficiently."""
    # Verify O(1) waiter operations, throughput, etc.
```

## Key Test Scenarios

### 1. Connection Lifecycle
- ✅ Pooled connection creation
- ✅ Mark as used/free
- ✅ Health status tracking
- ✅ Usage count increment

### 2. Pool Initialization
- ✅ Min/max connection validation
- ✅ Concurrent connection creation
- ✅ Configuration validation
- ✅ Context manager support

### 3. Connection Acquisition
- ✅ Context manager pattern
- ✅ Concurrent acquisitions
- ✅ Timeout handling
- ✅ Waiter queue (deque O(1))
- ✅ Pool exhaustion

### 4. Connection Release
- ✅ Connection reset
- ✅ Unhealthy connection removal
- ✅ Max usage count enforcement
- ✅ Waiter notification

### 5. Query Execution
- ✅ Successful execution
- ✅ Parameter passing
- ✅ Error propagation
- ✅ Connection exclusivity

### 6. Statistics
- ✅ Counter tracking
- ✅ Peak value tracking
- ✅ Real-time metrics
- ✅ Thread-safe updates

### 7. Pool Closure
- ✅ Clean shutdown
- ✅ Connection cleanup
- ✅ Waiter cancellation
- ✅ Idempotent close

### 8. Pool Manager
- ✅ Singleton pattern
- ✅ Named pool management
- ✅ Duplicate prevention
- ✅ Bulk closure

### 9. Performance
- ✅ Concurrent load (10/50/100 tasks)
- ✅ Waiter queue efficiency
- ✅ Sub-linear scaling

## Fixtures

### Core Fixtures

```python
@pytest.fixture
def mock_surreal_connection():
    """Mock SurrealDB connection with common methods."""
    # Returns AsyncMock with query, use, signin, close

@pytest.fixture
def pool_config():
    """Standard pool configuration for tests."""
    # Returns dict with uri, credentials, namespace, etc.

@pytest.fixture
async def mock_pool(pool_config, mock_surreal_connection):
    """Fully initialized pool with mocked connections."""
    # Returns initialized pool, auto-cleanup on teardown

@pytest.fixture
def pooled_connection(mock_surreal_connection):
    """Single pooled connection instance."""
    # Returns PooledConnection wrapper
```

## Running Specific Tests

```bash
# By marker
pytest -m unit
pytest -m integration
pytest -m performance
pytest -m "not slow"

# By file
pytest tests/test_pooler.py

# By class
pytest tests/test_pooler.py::TestConnectionAcquisition

# By name pattern
pytest -k "acquisition"
pytest -k "test_pool and not slow"

# First N failures
pytest --maxfail=3

# Last failed
pytest --lf

# Failed first, then rest
pytest --ff
```

## Coverage Analysis

```bash
# Generate HTML report
pytest --cov=purreal --cov-report=html

# Terminal report with missing lines
pytest --cov=purreal --cov-report=term-missing

# Branch coverage
pytest --cov=purreal --cov-branch

# Fail if coverage below threshold
pytest --cov=purreal --cov-fail-under=90
```

## Continuous Integration

### GitHub Actions Workflow

- **Trigger**: Push to main/develop, PRs
- **Matrix**: 
  - OS: Ubuntu, macOS, Windows
  - Python: 3.11, 3.12
- **Jobs**:
  - Tests with coverage
  - Linting (black, ruff, mypy)
  - Coverage upload to Codecov

### Local CI Simulation

```bash
# Run full CI suite locally
pytest --cov=purreal --cov-report=html
black --check purreal tests
ruff check purreal tests
mypy purreal --ignore-missing-imports
```

## Debugging Tests

```bash
# Show local variables on failure
pytest -l

# Drop into PDB on failure
pytest --pdb

# Show print statements
pytest -s

# Very verbose
pytest -vv

# Show full tracebacks
pytest --tb=long

# Show test durations
pytest --durations=10
```

## Best Practices

### Writing Tests

1. **Descriptive Names**: `test_connection_reset_on_release`
2. **Single Responsibility**: One behavior per test
3. **AAA Pattern**: Arrange, Act, Assert
4. **Use Fixtures**: Avoid code duplication
5. **Parametrize**: Test multiple scenarios
6. **Async Properly**: Use `@pytest.mark.asyncio`
7. **Clean Up**: Let fixtures handle teardown

### Mocking

```python
# Good: Mock external dependencies
pool._create_connection = AsyncMock(return_value=mock_conn)

# Good: Verify interactions
mock_conn.query.assert_called_once()

# Avoid: Over-mocking internal methods
```

### Assertions

```python
# Good: Specific assertions
assert pooled_conn.usage_count == 1
assert pooled_conn.in_use is True

# Good: Exception matching
with pytest.raises(RuntimeError, match="pool is closed"):
    await pool.acquire()

# Avoid: Generic assertions
assert pooled_conn  # Too vague
```

## Performance Benchmarks

### Expected Performance

- **Acquisition**: <1ms per connection
- **Release**: <1ms per connection
- **100 concurrent tasks**: <1s total
- **Waiter popleft**: <1μs (O(1))

### Running Benchmarks

```bash
pytest -m performance -v
pytest -m performance --durations=0
```

## Troubleshooting

### Common Issues

**Tests hang**
```bash
# Use timeout
pytest --timeout=30
```

**Asyncio warnings**
```bash
# Check asyncio mode
pytest --asyncio-mode=auto
```

**Coverage missing**
```bash
# Install pytest-cov
pip install pytest-cov
```

**Import errors**
```bash
# Install in editable mode
pip install -e .
```

## Contributing Tests

When adding features:

1. **Write tests first** (TDD approach)
2. **Ensure coverage** (>90% for new code)
3. **Add docstrings** to test functions
4. **Use markers** appropriately
5. **Update README** if adding new patterns
6. **Run full suite** before committing

## Resources

- [pytest documentation](https://docs.pytest.org/)
- [pytest-asyncio](https://pytest-asyncio.readthedocs.io/)
- [pytest-cov](https://pytest-cov.readthedocs.io/)
- [Testing Best Practices](https://docs.pytest.org/en/stable/goodpractices.html)
