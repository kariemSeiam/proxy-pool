#!/bin/bash
# Start script for Proxy Server

echo "=================================="
echo "Starting Proxy Server"
echo "=================================="

# Check if Python 3 is installed
if ! command -v python3 &> /dev/null; then
    echo "Error: Python 3 is not installed"
    exit 1
fi

# Check if dependencies are installed
if ! python3 -c "import aiohttp" &> /dev/null; then
    echo "Installing dependencies..."
    pip3 install -r requirements.txt
fi

# Create data directory
mkdir -p data

echo "Starting server on http://0.0.0.0:8000"
echo "Press Ctrl+C to stop"
echo ""

# Run the server
python3 main.py
