#!/usr/bin/env python3
"""
Proxy Pool v2 — Validator

Real-world proxy tester using HTTPS GET requests against multiple targets.

A proxy is "working" only if it can complete a real HTTPS GET request
and return valid content. No more false positives from easy HTTP HEAD pings.

Multi-target strategy:
  - Tries up to 3 HTTPS targets per proxy
  - A proxy passes if it succeeds on ≥ TEST_MIN_PASSES targets
  - This eliminates proxies that only work with certain sites

Scoring is accurate because the test reflects real proxy usage.
"""
import asyncio
import aiohttp
import logging
import time
from typing import List, Tuple, Optional

from config import TEST_URLS, TEST_MIN_PASSES, TEST_TIMEOUT, MAX_CONCURRENT

logger = logging.getLogger(__name__)

# Result type: (proxy_url, success, response_ms_or_None)
TestResult = Tuple[str, bool, Optional[float]]


class Validator:
    """Tests proxies with real HTTPS GET requests against multiple targets."""

    def __init__(self) -> None:
        self.session: Optional[aiohttp.ClientSession] = None
        self.semaphore: asyncio.Semaphore = asyncio.Semaphore(MAX_CONCURRENT)

    async def init(self) -> None:
        """Initialize the shared HTTP session with browser-like headers."""
        connector = aiohttp.TCPConnector(
            limit=MAX_CONCURRENT,
            ttl_dns_cache=300,
            force_close=True,  # avoid stale proxy connections
        )
        self.session = aiohttp.ClientSession(
            connector=connector,
            timeout=aiohttp.ClientTimeout(total=TEST_TIMEOUT),
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/125.0.0.0 Safari/537.36"
                ),
                "Accept": "text/html,application/xhtml+xml,*/*",
                "Accept-Language": "en-US,en;q=0.9",
            },
        )
        logger.info(
            f"Validator initialized — {len(TEST_URLS)} HTTPS targets, "
            f"min passes: {TEST_MIN_PASSES}, concurrency: {MAX_CONCURRENT}"
        )

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

        logger.info(f"Testing {len(proxies)} proxies against {len(TEST_URLS)} HTTPS targets...")
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
        logger.info(f"Tested {len(proxies)} proxies: {working} working ({rate:.1f}%)")
        return final

    # ── Single Proxy Test (multi-target) ────────────────────────────────

    async def _test_one(self, proxy_url: str, protocol: str) -> TestResult:
        """
        Test a single proxy against multiple HTTPS targets.

        A proxy passes if it succeeds on ≥ TEST_MIN_PASSES targets.
        Returns the fastest successful response time.
        """
        async with self.semaphore:
            passes = 0
            best_ms: Optional[float] = None

            for target in TEST_URLS:
                try:
                    start = time.monotonic()
                    async with self.session.get(
                        target["url"],
                        proxy=proxy_url,
                        timeout=aiohttp.ClientTimeout(total=TEST_TIMEOUT),
                        allow_redirects=True,
                        ssl=False,  # free proxies often have bad certs
                    ) as resp:
                        body = await resp.text()
                        elapsed = (time.monotonic() - start) * 1000

                        # Validate response content
                        expect = target.get("expect", "")
                        if expect and expect not in body:
                            continue  # got response but wrong content = fail

                        passes += 1
                        if best_ms is None or elapsed < best_ms:
                            best_ms = elapsed

                        # Early exit if we already have enough passes
                        if passes >= TEST_MIN_PASSES:
                            break

                except Exception:
                    continue

            success = passes >= TEST_MIN_PASSES
            return (proxy_url, success, best_ms if success else None)
