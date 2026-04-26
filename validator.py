#!/usr/bin/env python3
"""
Proxy Pool v2 — Validator

Lightweight proxy tester using single HEAD requests through each proxy.
One request per proxy — no retries, no brute force. Pass or fail.

A proxy is "working" if it can complete a HEAD request to the test URL
within the timeout. Response time is recorded for scoring.
"""
import asyncio
import aiohttp
import logging
import time
from typing import List, Tuple, Optional

from config import TEST_URL, TEST_TIMEOUT, MAX_CONCURRENT

logger = logging.getLogger(__name__)

# Result type: (proxy_url, success, response_ms_or_None)
TestResult = Tuple[str, bool, Optional[float]]


class Validator:
    """Tests proxies with minimal overhead — one HEAD request each."""

    def __init__(self) -> None:
        self.session: Optional[aiohttp.ClientSession] = None
        self.semaphore: asyncio.Semaphore = asyncio.Semaphore(MAX_CONCURRENT)

    async def init(self) -> None:
        """Initialize the shared HTTP session."""
        connector = aiohttp.TCPConnector(
            limit=MAX_CONCURRENT,
            ttl_dns_cache=300,
            force_close=True,  # avoid stale proxy connections
        )
        self.session = aiohttp.ClientSession(
            connector=connector,
            timeout=aiohttp.ClientTimeout(total=TEST_TIMEOUT),
            headers={"User-Agent": "proxy-pool-v2/1.0"},
        )
        logger.info("Validator initialized")

    async def close(self) -> None:
        """Close the HTTP session."""
        if self.session:
            await self.session.close()
            self.session = None

    # ── Batch Testing ────────────────────────────────────────────────────

    async def test_many(self, proxies: List[Tuple[str, str]]) -> List[TestResult]:
        """
        Test a batch of proxies concurrently.

        Args:
            proxies: List of (proxy_url, protocol) tuples.

        Returns:
            List of (proxy_url, success, response_ms) results.
        """
        if not proxies:
            return []

        logger.info(f"Testing {len(proxies)} proxies...")
        tasks = [self._test_one(url, proto) for url, proto in proxies]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        working = 0
        final: List[TestResult] = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                final.append((proxies[i][0], False, None))
            else:
                final.append(result)
                if result[1]:
                    working += 1

        rate = (working / len(proxies)) * 100 if proxies else 0
        logger.info(f"Tested {len(proxies)} proxies: {working} working ({rate:.0f}%)")
        return final

    # ── Single Test ──────────────────────────────────────────────────────

    async def _test_one(self, proxy_url: str, protocol: str) -> TestResult:
        """
        Test a single proxy by making a HEAD request through it.

        Returns (proxy_url, success, response_time_ms).
        """
        async with self.semaphore:
            try:
                start = time.monotonic()
                async with self.session.head(
                    TEST_URL,
                    proxy=proxy_url,
                    timeout=aiohttp.ClientTimeout(total=TEST_TIMEOUT),
                    allow_redirects=True,
                ) as resp:
                    elapsed = (time.monotonic() - start) * 1000
                    # Any response (even 4xx) means the proxy is working
                    return (proxy_url, True, elapsed)

            except Exception:
                return (proxy_url, False, None)
