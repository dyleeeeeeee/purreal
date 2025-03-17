# Purreal: Robust SurrealDB Connection Pooling with SSL Support

[![License](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python Version](https://img.shields.io/badge/python-3.8+-blue.svg)](https://www.python.org/downloads/)
[![Code Style: Black](https://img.shields.io/badge/code%20style-black-000000.svg)](https://github.com/psf/black)
<!-- Add CI/CD status badges here, e.g., for GitHub Actions -->

## Overview

Purreal is a custom connection pooler designed to enhance the performance and reliability of SurrealDB interactions within Python applications. It provides robust connection management with built-in SSL/TLS support, ensuring secure and efficient communication with your SurrealDB database. This library addresses the need for persistent and secure connections, reducing latency and improving overall application responsiveness.

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
from purreal import SurrealDBPoolManager

# Initialize the pool manager
pool_manager = SurrealDBPoolManager()

def main():
    # Create a connection pool
    pool = await pool_manager.create_pool(
        name="my_pool",
        uri=SURREAL_URI,
        credentials={"username": SURREAL_USER, "password": SURREAL_PASS},
        namespace=NAMESPACE,
        database=DATABASE,
        min_connections=2,
        max_connections=10,
        max_idle_time=300,
        connection_timeout=5.0,
        acquisition_timeout=10.0,
        health_check_interval=30,
        max_usage_count=1000,
        connection_retry_attempts=3,
        connection_retry_delay=1.0,
        schema_file=SCHEMA_FILE,
        reset_on_return=True,
        log_queries=True,
    )

    async with pool.connection() as conn:
        # Perform SurrealDB operations using the connection
        result = await conn.query("SELECT * FROM your_table;")
        print(result)

    await pool.close() # Close all connections in the pool when done

if name == "__main__":
    asyncio.run(main())
```


### Explanation

1.  **Import `ConnectionPool`:** Imports the necessary class from the `purreal` library.
2.  **Create a `ConnectionPool` Instance:**  Creates a `ConnectionPool` object, configuring it with your SurrealDB connection details, including:
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

### `ConnectionPool`

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