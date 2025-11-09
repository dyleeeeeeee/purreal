@echo off
REM Quick test runner for purreal (Windows)
REM Usage: test.bat [connectivity|stress|monitor|benchmark|all]

echo ================================
echo    Purreal Test Runner
echo ================================
echo.

REM Check if Python is available
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python is not installed or not in PATH
    exit /b 1
)

REM Check SurrealDB is running
echo Checking SurrealDB connection...
curl -s http://localhost:8000/status >nul 2>&1
if errorlevel 1 (
    echo [X] SurrealDB is not running
    echo.
    echo Start SurrealDB with:
    echo   surreal start --bind 0.0.0.0:8000 --user root --pass root
    echo.
    echo Or use Docker:
    echo   docker run -p 8000:8000 surrealdb/surrealdb:latest start
    exit /b 1
)
echo [✓] SurrealDB is running
echo.

REM Get test type (default: connectivity)
set TEST_TYPE=%1
if "%TEST_TYPE%"=="" set TEST_TYPE=connectivity

REM Run tests
if "%TEST_TYPE%"=="connectivity" goto :connectivity
if "%TEST_TYPE%"=="conn" goto :connectivity
if "%TEST_TYPE%"=="stress" goto :stress
if "%TEST_TYPE%"=="monitor" goto :monitor
if "%TEST_TYPE%"=="mon" goto :monitor
if "%TEST_TYPE%"=="benchmark" goto :benchmark
if "%TEST_TYPE%"=="bench" goto :benchmark
if "%TEST_TYPE%"=="load" goto :load
if "%TEST_TYPE%"=="throughput" goto :throughput
if "%TEST_TYPE%"=="high" goto :throughput
if "%TEST_TYPE%"=="all" goto :all
goto :usage

:connectivity
echo Running connectivity test...
echo.
python tests\test_connectivity.py
goto :end

:stress
echo Running stress test (500 connections)...
echo.
python examples\stress_test.py 500
goto :end

:monitor
echo Running monitored load test...
echo.
python examples\monitor_pool.py
goto :end

:benchmark
echo Running configuration benchmark...
echo.
python benchmarks\benchmark_configs.py
goto :end

:load
echo Running comprehensive load test...
echo WARNING: This will take several minutes
echo.
set /p CONFIRM="Continue? (y/n): "
if /i "%CONFIRM%"=="y" (
    python examples\load_test.py
)
goto :end

:throughput
echo Running high-throughput stress test...
echo This will test sustained load, bursts, and connection churn
echo.
python benchmarks\high_throughput.py
goto :end

:all
echo Running all quick tests...
echo.
call :connectivity
echo.
call :stress
echo.
call :monitor
goto :end

:usage
echo Usage: test.bat [connectivity^|stress^|monitor^|benchmark^|load^|throughput^|all]
echo.
echo Tests:
echo   connectivity  - Test basic connectivity (default)
echo   stress        - Test 500 concurrent connections
echo   monitor       - Monitor pool behavior in real-time
echo   benchmark     - Benchmark different configurations
echo   load          - Comprehensive load test (slow)
echo   throughput    - High-throughput stress test (sustained load, bursts, churn)
echo   all           - Run connectivity, stress, and monitor tests
exit /b 1

:end
echo.
echo [✓] Test complete!
