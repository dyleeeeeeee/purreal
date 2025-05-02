# Purreal: Robust SurrealDB Connection Pooling with SSL Support

[![License](https://www.gnu.org/graphics/gplv3-with-text-136x68.png)](https://opensource.org/licenses/GNU)
[![Python Version](https://img.shields.io/badge/python-3.8+-blue.svg)](https://www.python.org/downloads/)
[![Code Style: Black](https://img.shields.io/badge/code%20style-black-000000.svg)](https://github.com/psf/black)
<!-- Add CI/CD status badges here, e.g., for GitHub Actions -->

## Overview

Purreal is a custom connection pooler designed to enhance the performance and reliability of SurrealDB interactions within Python applications. It provides robust connection pooling with built-in SSL/TLS support, ensuring secure and efficient communication with your SurrealDB database. This library addresses the need for persistent and secure connections, reducing latency and improving overall application responsiveness.

## Key Features

*   **Connection Pooling:**  Maintains a pool of active connections to SurrealDB, minimizing the overhead of establishing new connections for each database interaction.
*   **SSL/TLS Encryption:**  Secures communication between your application and SurrealDB using SSL/TLS encryption, protecting sensitive data in transit.
*   **Customizable Pool Size:**  Allows you to configure the maximum and minimum number of connections in the pool, optimizing resource utilization based on your application's needs.
*   **Connection Health Checks:**  Periodically validates connections in the pool to ensure they are active and healthy, automatically replacing any broken connections.
*   **Asynchronous Support:** Designed to work seamlessly with asynchronous Python code.
*   **Easy Integration:** Simple and straightforward API for integrating Purreal into your existing SurrealDB projects.

## Installation

pip install purreal


## Usage

### Basic Example

```python
from purreal import SurrealDBPoolManager, SurrealDBConnectionPool
import asyncio
import random
# Initialize the pool manager
pool_manager = SurrealDBPoolManager()
import logging

# --- Example Usage (for testing/demonstration) ---

async def example_usage():
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(name)s - %(message)s')
    logger = logging.getLogger(__name__)

    pool_config = {
        "uri": "wss://test-prod-insta-06amo5vohhuqn9u9qa1lbl9rcg.aws-use1.surreal.cloud", # Adjust URI if needed
        "credentials": {"username": "root", "password": "rootrm"},
        "namespace": "test",
        "database": "test",
        "min_connections": 50,
        "max_connections": 70,
        "acquisition_timeout": 15,
        "log_queries": True,
        # "schema_file": "your_schema.surql" # Optional: Create a schema file
    }

    # Create a dummy schema file if it doesn't exist
    schema_path = pool_config.get("schema_file")
    if schema_path:
        try:
            with open(schema_path, "w", encoding="utf-8") as f:
                f.write("DEFINE TABLE user SCHEMAFULL;\n")
                f.write("DEFINE FIELD name ON user TYPE string;\n")
                f.write("DEFINE FIELD email ON user TYPE string ASSERT is::email($value);\n")
                f.write("DEFINE INDEX userEmail ON user COLUMNS email UNIQUE;\n")
            logger.info(f"Created dummy schema file: {schema_path}")
        except Exception as e:
            logger.warning(f"Could not create dummy schema file {schema_path}: {e}")


    # Method 1: Direct Pool Instance
    pool = SurrealDBConnectionPool(**pool_config)
    try:
        async with pool: # Handles initialize() and close()
            # Test execute_query helper
            try:
                result = await pool.execute_query("INFO FOR DB;")
                logger.info(f"DB Info: {result}")
            except Exception as e:
                 logger.error(f"Initial query failed: {e}")
                 return # Exit if initial query fails

            # Simulate concurrent usage
            async def worker(worker_id):
                for i in range(3):
                    try:
                        async with pool.acquire() as conn:
                            user_id = f"user_{worker_id}_{i}"
                            # Example write query
                            await conn.query(f"CREATE {user_id} SET name = 'Worker {worker_id}', email = '{worker_id}_{i}@example.com'")
                            logger.info(f"Worker {worker_id} created {user_id}")
                            # Example read query
                            data = await conn.query(f"SELECT * FROM {user_id}")
                            logger.info(f"Worker {worker_id} read data: {data}")
                        # Add random delay to simulate work
                        await asyncio.sleep(random.uniform(0.1, 0.5))
                    except asyncio.TimeoutError:
                        logger.warning(f"Worker {worker_id} timed out acquiring connection.")
                    except Exception as e:
                        logger.error(f"Worker {worker_id} encountered error: {e}", exc_info=True)

            tasks = [worker(i) for i in range(15)] # More workers than max_connections
            await asyncio.gather(*tasks)

            stats = await pool.get_stats()
            logger.info(f"Final Pool Stats: {stats}")

    except Exception as e:
        logger.error(f"Error during pool example usage: {e}", exc_info=True)
    finally:
        # Pool is closed by async with, but explicit close is safe too
        await pool.close()


if __name__ == "__main__":
     # Make sure you have SurrealDB running locally for this example
     try:
        asyncio.create_task(example_usage())
     except KeyboardInterrupt:
          print("\nExiting...")
```


### Explanation

1.  **Import `SurrealDBPoolManager`:** Imports the necessary class from the `purreal` library.
2.  **Create a `SurrealDBPoolManager` Instance:**  Creates a `SurrealDBPoolManager` object, configuring it with your SurrealDB connection details, including:
    *   `uri`: The connection URI for SurrealDB.
    *   `credentials`: The username and password for authentication.
    *   `namespace`: The namespace to use.
    *   `database`: The database to connect to.
    *   `min_size`: The minimum number of connections that the pool should maintain.
    *   `max_size`: The maximum number of connections allowed in the pool.
3.  **Acquire a Connection:** Uses an `async with` statement to acquire a connection from the pool. This ensures that the connection is automatically returned to the pool when the block exits, even if errors occur.
4.  **Perform Database Operations:**  Executes SurrealDB queries or other operations using the acquired connection (e.g., `await conn.query(...)`).
5.  **Connection Returned to Pool:** The `async with` statement automatically returns the connection to the pool, making it available for reuse.
6.  **Close the Pool (Important):**  Calls `await pool.close()` to gracefully close all connections in the pool when your application is finished using them.  This is crucial to avoid resource leaks.

### Advanced Configuration

*   **SSL Context:**  You can provide a custom `ssl.SSLContext` object for more fine-grained control over SSL/TLS settings.
*   **Connection Timeout:** Configure the timeout for establishing new connections.
*   **Health Check Interval:**  Adjust the frequency of connection health checks.



## API Reference

### `SurrealDBPoolManager`

*   `__init__(uri, credentials, namespace, database, min_size=2, max_size=10, connection_timeout=None, health_check_interval=None)`:  Initializes a new connection pool.
    *   `uri` (str): The SurrealDB connection URI.
    *   `credentials` (dict): The username and password for authentication.
    *   `namespace` (str): The namespace to use.
    *   `database` (str): The database to use.
    *   `min_size` (int, optional): The minimum number of connections in the pool. Defaults to `2`.
    *   `max_size` (int, optional): The maximum number of connections in the pool. Defaults to `10`.
    *   `connection_timeout` (int, optional): Timeout in seconds for establishing a connection. Defaults to None.
    *   `health_check_interval` (int, optional): Interval in seconds between connection health checks. Defaults to None.
*   `connection()`:  Asynchronously acquires a connection from the pool.  Returns an asynchronous context manager.
*   `close()`:  Asynchronously closes all connections in the pool.

## Contributing

Contributions are welcome! Please follow these steps:

1.  Fork the repository.
2.  Create a new branch for your feature or bug fix.
3.  Implement your changes, ensuring that you adhere to the project's coding style (e.g., using Black).
4.  Write tests to cover your changes.
5.  Submit a pull request.

## License

This project is licensed under the GNU General Public License v3 (GPLv3) - see the [LICENSE](LICENSE) file for details.

## Acknowledgments

*   This project is inspired by the need for robust and secure connection pooling for SurrealDB in Python.
*   Thanks to the SurrealDB team for building a fantastic database.