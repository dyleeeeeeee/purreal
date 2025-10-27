# Purreal Test Suite

Comprehensive test suite for the purreal SurrealDB connection pooler.

## Running Tests

### Quick Start

```bash
# Install test dependencies
pip install -e ".[test]"

# Run all tests
pytest

# Run with coverage
pytest --cov=purreal --cov-report=html

# Run specific test categories
pytest -m unit          # Unit tests only
pytest -m integration   # Integration tests only
pytest -m performance   # Performance tests only
pytest -m "not slow"    # Skip slow tests
```

### Advanced Usage

```bash
# Run with verbose output
pytest -v

# Run specific test file
pytest tests/test_pooler.py

# Run specific test class
pytest tests/test_pooler.py::TestConnectionPoolInitialization

# Run specific test
pytest tests/test_pooler.py::TestConnectionPoolInitialization::test_pool_initialization

# Run with parallel execution
pytest -n auto

# Stop on first failure
pytest -x

# Run last failed tests
pytest --lf
```

## Test Structure

```
tests/
├── conftest.py           # Shared fixtures and configuration
├── test_pooler.py        # Connection pool tests
└── README.md            # This file
```

## Test Categories

### Unit Tests (`@pytest.mark.unit`)
- Test individual components in isolation
- Fast execution
- Mock external dependencies

### Integration Tests (`@pytest.mark.integration`)
- Test component interactions
- May require external services
- Slower execution

### Performance Tests (`@pytest.mark.performance`)
- Benchmark and load testing
- Verify O(1) operations
- Concurrent load handling

## Coverage Requirements

- **Target**: 90%+ code coverage
- **Branch Coverage**: Enabled
- **Excluded**: Test files, abstract methods, type checking blocks

## Writing Tests

### Best Practices

1. **Use descriptive names**: `test_connection_reset_on_release`
2. **One assertion per test**: Focus on single behavior
3. **Use fixtures**: Reuse common setup code
4. **Parametrize**: Test multiple scenarios efficiently
5. **Mock external deps**: Keep tests fast and isolated
6. **Clean up**: Use fixtures for automatic teardown

### Example Test

```python
@pytest.mark.asyncio
async def test_acquire_context_manager(mock_pool, mock_surreal_connection):
    """Test acquiring connection via async context manager."""
    async with mock_pool.acquire() as conn:
        assert conn is mock_surreal_connection
        assert any(c.in_use for c in mock_pool._pool)
    
    # After release, no connections should be in use
    assert not any(c.in_use for c in mock_pool._pool)
```

## Continuous Integration

Tests run automatically on:
- Push to `main` or `develop`
- Pull requests
- Multiple OS: Ubuntu, macOS, Windows
- Python versions: 3.11, 3.12

## Debugging Failed Tests

```bash
# Show local variables on failure
pytest -l

# Drop into debugger on failure
pytest --pdb

# Show print statements
pytest -s

# More verbose output
pytest -vv
```

## Performance Benchmarking

```bash
# Run performance tests
pytest -m performance -v

# With detailed output
pytest -m performance -v --tb=short
```

## Test Markers

- `unit`: Fast isolated tests
- `integration`: Tests with external dependencies
- `performance`: Benchmark and load tests
- `slow`: Tests that take >1 second

## Contributing

When adding new features:
1. Write tests first (TDD)
2. Ensure >90% coverage
3. Add docstrings to tests
4. Use appropriate markers
5. Update this README if needed
