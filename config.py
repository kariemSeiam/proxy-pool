#!/usr/bin/env python3
"""
Proxy Pool v2 — Configuration

Central config for sources, tiering, validation, and the API server.
Everything is overridable via environment variables where it makes sense.
"""
import os
from pathlib import Path

# ── Paths ────────────────────────────────────────────────────────────────────
BASE_DIR: Path = Path(__file__).parent
DATA_DIR: Path = Path(os.getenv("DATA_DIR", str(BASE_DIR / "data")))
DATA_DIR.mkdir(exist_ok=True, parents=True)
DB_FILE: Path = DATA_DIR / "proxies.db"

# ── Proxy Sources (ProxiFly via jsDelivr CDN) ────────────────────────────────
SOURCES: dict[str, dict[str, str]] = {
    "proxifly_http": {
        "url": "https://cdn.jsdelivr.net/gh/proxifly/free-proxy-list@main/proxies/protocols/http/data.txt",
        "protocol": "http",
    },
    "proxifly_https": {
        "url": "https://cdn.jsdelivr.net/gh/proxifly/free-proxy-list@main/proxies/protocols/https/data.txt",
        "protocol": "http",  # listed as "https" but used as HTTP proxy
    },
    "proxifly_socks4": {
        "url": "https://cdn.jsdelivr.net/gh/proxifly/free-proxy-list@main/proxies/protocols/socks4/data.txt",
        "protocol": "socks4",
    },
    "proxifly_socks5": {
        "url": "https://cdn.jsdelivr.net/gh/proxifly/free-proxy-list@main/proxies/protocols/socks5/data.txt",
        "protocol": "socks5",
    },
}

META_URL: str = (
    "https://cdn.jsdelivr.net/gh/proxifly/free-proxy-list@main/proxies/meta/data.json"
)

# ── Validation (FIXED — real HTTPS testing) ──────────────────────────────────
# Multiple test targets: a proxy must succeed on AT LEAST ONE to count as working.
# Uses GET requests (not HEAD) over HTTPS (not HTTP) for realistic validation.
TEST_URLS: list[dict[str, str]] = [
    {"url": "https://httpbin.org/ip",           "name": "httpbin",    "expect": "origin"},
    {"url": "https://icanhazip.com",             "name": "icanhazip",  "expect": ""},
    {"url": "https://www.google.com/robots.txt", "name": "google",     "expect": "User-agent"},
]
# How many test targets a proxy must pass to be considered "working".
# 1 = lenient (passes if ANY target works), 2 = strict, 3 = very strict
TEST_MIN_PASSES: int = 1

TEST_TIMEOUT: int = 8       # seconds per proxy test (increased for HTTPS)
MAX_CONCURRENT: int = 50    # semaphore cap — lowered for HTTPS (heavier)
BATCH_SIZE: int = 100       # proxies tested per scheduler cycle

# Keep old TEST_URL for backward compat (scheduler/validator import it)
TEST_URL: str = "https://httpbin.org/ip"

# ── Tier Thresholds ──────────────────────────────────────────────────────────
#   gold   = battle-tested, high reliability
#   silver = decent, still proving itself
#   new    = untested or fewer than SILVER_MIN_TESTS
#   dead   = consistently failing
GOLD_MIN_SCORE: int = 80    # ≥ 80% success rate
GOLD_MIN_TESTS: int = 5     # tested at least 5 times
SILVER_MIN_SCORE: int = 50
SILVER_MIN_TESTS: int = 3
DEAD_MAX_SCORE: int = 30    # below 30% = dead
DEAD_CONSECUTIVE: int = 10  # 10 straight fails → dead regardless of score

# ── Revalidation Intervals (seconds) ────────────────────────────────────────
GOLD_INTERVAL: int = 4 * 3600    # 4 hours — proven proxies rarely change
SILVER_INTERVAL: int = 1 * 3600  # 1 hour
NEW_INTERVAL: int = 10 * 60      # 10 minutes — new proxies need quick testing
DEAD_INTERVAL: int = 12 * 3600   # 12 hours — give dead ones a second chance

# ── Cleanup ──────────────────────────────────────────────────────────────────
CLEANUP_SCORE: int = 10     # remove proxies scoring below 10%
CLEANUP_MIN_TESTS: int = 10 # ...after at least 10 tests

# ── Worker / Scheduler ───────────────────────────────────────────────────────
WORKER_INTERVAL: int = 60        # seconds between scheduler cycles
SOURCE_CHECK_INTERVAL: int = 300 # re-fetch source lists every 5 minutes

# ── API Server ───────────────────────────────────────────────────────────────
API_HOST: str = "0.0.0.0"
API_PORT: int = 8000

# ── Logging ──────────────────────────────────────────────────────────────────
LOG_LEVEL: str = "INFO"
LOG_FORMAT: str = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
