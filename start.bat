@echo off
REM Start script for Proxy Server (Windows)

echo ==================================
echo Starting Proxy Server
echo ==================================

REM Check if Python is installed
python --version >nul 2>&1
if errorlevel 1 (
    echo Error: Python is not installed
    exit /b 1
)

REM Check if dependencies are installed
python -c "import aiohttp" >nul 2>&1
if errorlevel 1 (
    echo Installing dependencies...
    pip install -r requirements.txt
)

REM Create data directory
if not exist data mkdir data

echo Starting server on http://0.0.0.0:8000
echo Press Ctrl+C to stop
echo.

REM Run the server
python main.py
