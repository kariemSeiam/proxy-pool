# Proxy Server

**Production-ready proxy validation and API server** that provides continuously validated free proxies from ProxiFly.

## Features

- **Automatic Updates**: Checks ProxiFly meta every 60 seconds for new proxies
- **Smart Validation**: Tests proxies against Google Maps API (fast and reliable)
- **Continuous Monitoring**: Revalidates working proxies every 20 minutes
- **Auto-Cleanup**: Removes dead proxies after 5 failures
- **Fast & Efficient**: Async I/O with connection pooling (1000+ concurrent tests)
- **Simple API**: 2 endpoints - `/list` and `/random`
- **Zero Maintenance**: Runs 24/7 with automatic recovery

## Quick Start

### 1. Install Dependencies

```bash
pip install -r requirements.txt
```

### 2. Run the Server

```bash
python main.py
```

The server will:
- Start API on `http://0.0.0.0:8000`
- Create database at `data/proxies.db`
- Start background validation worker
- Begin fetching and validating proxies

## API Endpoints

### GET /list

Get all working proxies as plain text (one per line).

**Query Parameters:**
- `limit` (optional): Maximum number of proxies to return

**Examples:**
```bash
# Get all proxies
curl http://localhost:8000/list

# Get top 10 fastest proxies
curl http://localhost:8000/list?limit=10
```

**Response:**
```
http://1.2.3.4:8080
http://5.6.7.8:3128
http://9.10.11.12:80
```

### GET /random

Get a single random proxy (weighted 70% toward fastest proxies).

**Example:**
```bash
curl http://localhost:8000/random
```

**Response:**
```
http://1.2.3.4:8080
```

### GET /stats

Get proxy statistics (JSON).

**Example:**
```bash
curl http://localhost:8000/stats
```

**Response:**
```json
{
  "total_proxies": 1523,
  "working_proxies": 342,
  "failed_proxies": 1181,
  "last_meta_update": "2024-12-03T14:30:00",
  "timestamp": "2024-12-03T14:35:22.123456Z"
}
```

### GET /health

Health check endpoint.

**Example:**
```bash
curl http://localhost:8000/health
```

**Response:**
```json
{
  "status": "ok",
  "timestamp": "2024-12-03T14:35:22.123456Z"
}
```

## Configuration

Edit `config.py` to customize settings:

```python
# API Server
API_HOST = "0.0.0.0"
API_PORT = 8000

# Validation
TEST_TIMEOUT = 5
MAX_FAILURES = 5

# Timing
META_CHECK_INTERVAL = 60       # Check for updates every 60 seconds
REVALIDATION_INTERVAL = 20     # Revalidate all proxies every 20 minutes

# Concurrency
MAX_CONCURRENT_TESTS = 1000
```

## Architecture

### Clean Modular Design

```
├── config.py       # All configuration in one place
├── database.py     # SQLite async database layer
├── validator.py    # Proxy testing with connection pooling
├── fetcher.py      # Fetch proxies from ProxiFly
├── worker.py       # Background validation worker
├── server.py       # HTTP API server
└── main.py         # Main entry point
```

### How It Works

1. **Worker Loop** (runs every 30 seconds):
   - Check meta for updates (every 60 seconds)
   - Fetch new proxies if meta changed
   - Validate failed proxies
   - Revalidate working proxies (every 20 minutes)
   - Clean up dead proxies (5+ failures)

2. **Validation**:
   - Tests proxy against Google Maps reveal API
   - 5 second timeout
   - Verifies response is valid JSON
   - Tracks timeout for speed ranking

3. **Database**:
   - SQLite with WAL mode (concurrent reads)
   - Tracks: working status, timeout, failures, last_tested
   - Auto-indexes for fast queries
   - Stores in `data/proxies.db`

## Production Deployment

### Run as systemd service (Linux)

Create `/etc/systemd/system/proxy-server.service`:

```ini
[Unit]
Description=Proxy Server
After=network.target

[Service]
Type=simple
User=www-data
WorkingDirectory=/opt/proxy-server
ExecStart=/usr/bin/python3 main.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

Enable and start:
```bash
sudo systemctl enable proxy-server
sudo systemctl start proxy-server
sudo systemctl status proxy-server
```

### Run with Docker

Create `Dockerfile`:

```dockerfile
FROM python:3.11-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY *.py .
VOLUME /app/data

EXPOSE 8000
CMD ["python", "main.py"]
```

Build and run:
```bash
docker build -t proxy-server .
docker run -d -p 8000:8000 -v $(pwd)/data:/app/data --name proxy-server proxy-server
```

### Deploy to Render

1. Commit and push this repo to GitHub.
2. In Render, create a new Web Service from the repo or this `render.yaml`.
3. Use these settings:
   - Environment: Python
   - Build command: `pip install -r requirements.txt`
   - Start command: `python main.py`
   - Health check path: `/health`
   - Env vars: `PYTHON_VERSION=3.11`, `DATA_DIR=/var/data`
   - Disk: mount 1GB at `/var/data` (keeps SQLite DB and logs)
4. Render supplies `PORT`; the server reads it automatically.

## Troubleshooting

### No proxies available

Wait 1-2 minutes after startup. The server needs time to:
1. Fetch proxy lists from ProxiFly
2. Validate each proxy
3. Build working proxy database

### Proxies timing out

Free proxies are unreliable. The server:
- Continuously validates proxies
- Removes dead ones automatically
- Fetches new ones every 20 minutes

### Database locked

SQLite uses WAL mode for concurrent access. If you see locks:
- Check for orphaned processes
- Delete `data/proxies.db-wal` and `data/proxies.db-shm`

## Monitoring

Logs are written to:
- **Console**: Real-time logs
- **File**: `data/proxy_server.log`

Watch logs:
```bash
tail -f data/proxy_server.log
```

Check stats:
```bash
watch -n 5 'curl -s http://localhost:8000/stats | python -m json.tool'
```

## Performance

- **Validation Speed**: 1000+ proxies/minute
- **Memory Usage**: ~50-100MB
- **CPU Usage**: Low (mostly I/O bound)
- **Database Size**: ~1-2MB for 1000 proxies

## License

MIT License - Free for any use

## Support

For issues or questions, check the logs and stats endpoint first.
