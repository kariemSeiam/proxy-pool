#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Proxy Server Configuration
All settings in one place for easy management.
"""

import os
from pathlib import Path

# ============================================================================
# PATHS
# ============================================================================
BASE_DIR = Path(__file__).parent
DEFAULT_DATA_DIR = BASE_DIR / "data"
DATA_DIR = Path(os.getenv("DATA_DIR", DEFAULT_DATA_DIR))
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_FILE = DATA_DIR / "proxies.db"
LOG_FILE = DATA_DIR / "proxy_server.log"

# ============================================================================
# API ENDPOINTS
# ============================================================================
META_API_URL = "https://cdn.jsdelivr.net/gh/proxifly/free-proxy-list@main/proxies/meta/data.json"
HTTP_PROXIES_URL = "https://cdn.jsdelivr.net/gh/proxifly/free-proxy-list@main/proxies/protocols/http/data.txt"
HTTPS_PROXIES_URL = "https://cdn.jsdelivr.net/gh/proxifly/free-proxy-list@main/proxies/protocols/https/data.txt"

# ============================================================================
# VALIDATION SETTINGS
# ============================================================================
# Test URL (Google Maps reveal endpoint - reliable and fast)
TEST_URL = "https://www.google.com/maps/preview/reveal?authuser=0&hl=ar&gl=eg&pb=!2m12!1m3!1d352!2d31.2357!3d30.0444!2m3!1f0!2f0!3f0!3m2!1i1536!2i740!4f13.1!3m2!2d31.2357!3d30.0444!7e81!5m5!2m4!1i96!2i64!3i1!4i8"

# Timeout for proxy tests (seconds)
TEST_TIMEOUT = 10

# Number of validation requests per proxy
VALIDATION_REQUESTS = 20

# Extended validation duration (minutes)
VALIDATION_DURATION = 30

# Max consecutive failures before marking as dead
MAX_FAILURES = 20

# ============================================================================
# TIMING SETTINGS
# ============================================================================
# Check meta for updates every N seconds
META_CHECK_INTERVAL = 260

# Revalidate all working proxies every N minutes
REVALIDATION_INTERVAL = 60

# ============================================================================
# CONCURRENCY SETTINGS
# ============================================================================
# Maximum concurrent proxy tests
MAX_CONCURRENT_TESTS = 1000

# Database connection pool size
DB_POOL_SIZE = 10

# HTTP session pool settings
HTTP_POOL_CONNECTIONS = 2000
HTTP_POOL_MAXSIZE = 500

# ============================================================================
# SERVER SETTINGS
# ============================================================================
# API server host and port
API_HOST = os.getenv("HOST", "0.0.0.0")
API_PORT = int(os.getenv("PORT", "8000"))

# ============================================================================
# LOGGING
# ============================================================================
LOG_LEVEL = "INFO"
LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
