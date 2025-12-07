#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Proxy Validator - Smart proxy testing with HTTP session pooling
Tests proxies efficiently and tracks their performance.
"""

import asyncio
import aiohttp
import logging
from typing import Tuple, Optional

from config import TEST_URL, TEST_TIMEOUT, MAX_CONCURRENT_TESTS

logger = logging.getLogger(__name__)


class ProxyValidator:
    """Validates proxies with smart testing and connection pooling."""

    def __init__(self):
        self.session: Optional[aiohttp.ClientSession] = None
        self.semaphore = asyncio.Semaphore(MAX_CONCURRENT_TESTS)

    async def init(self):
        """Initialize HTTP session with connection pooling."""
        connector = aiohttp.TCPConnector(
            limit=2000,
            limit_per_host=500,
            ttl_dns_cache=300,
            force_close=False
        )
        self.session = aiohttp.ClientSession(connector=connector)
        logger.info("Validator session initialized")

    async def close(self):
        """Close HTTP session."""
        if self.session:
            await self.session.close()
            self.session = None
            logger.info("Validator session closed")

    async def test_proxy(self, proxy_url: str) -> Tuple[bool, Optional[float], Optional[str]]:
        """
        Test a single proxy.

        Args:
            proxy_url: Proxy URL (with or without http:// prefix)

        Returns:
            Tuple of (working, timeout, protocol)
        """
        async with self.semaphore:
            # Normalize proxy URL
            if not proxy_url.startswith('http://') and not proxy_url.startswith('https://'):
                proxy_url = f'http://{proxy_url}'

            try:
                start_time = asyncio.get_event_loop().time()

                timeout = aiohttp.ClientTimeout(total=TEST_TIMEOUT)
                headers = {
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                    'Accept-Language': 'en-US,en;q=0.9',
                    'Accept-Encoding': 'gzip, deflate, br',
                    'Connection': 'keep-alive'
                }

                async with self.session.get(
                    TEST_URL,
                    proxy=proxy_url,
                    headers=headers,
                    timeout=timeout,
                    ssl=False
                ) as response:
                    response_time = asyncio.get_event_loop().time() - start_time

                    if response.status == 200:
                        # Verify response is valid (Google Maps specific check)
                        text = await response.text()
                        if text.startswith(")]}'") or '"' in text[:100]:
                            protocol = 'http' if proxy_url.startswith('http://') else 'https'
                            return (True, response_time, protocol)

                    return (False, None, None)

            except asyncio.TimeoutError:
                logger.debug(f"Timeout for proxy {proxy_url}")
                return (False, None, None)

            except aiohttp.ClientProxyConnectionError:
                logger.debug(f"Proxy connection error for {proxy_url}")
                return (False, None, None)

            except aiohttp.ClientError as e:
                logger.debug(f"Client error for {proxy_url}: {type(e).__name__}")
                return (False, None, None)

            except Exception as e:
                logger.debug(f"Unexpected error for {proxy_url}: {e}")
                return (False, None, None)

    async def test_proxy_multiple(self, proxy_url: str, attempts: int = 5, is_working_proxy: bool = False) -> Tuple[bool, Optional[float], Optional[str]]:
        """
        Test a proxy multiple times. If at least one succeeds, mark as working.
        Uses staggered attempts with delay only for working proxies to avoid rate limiting.
        
        Args:
            proxy_url: Proxy URL to test
            attempts: Number of attempts (default: 5)
            is_working_proxy: If True, adds delay between batches (for revalidation)
            
        Returns:
            Tuple of (working, best_timeout, protocol)
        """
        # Normalize proxy URL
        if not proxy_url.startswith('http://') and not proxy_url.startswith('https://'):
            proxy_url = f'http://{proxy_url}'
        
        # Run attempts - split into batches only for working proxies
        successful_results = []
        
        # First batch: 5 attempts concurrently
        batch1_tasks = [self.test_proxy(proxy_url) for _ in range(min(5, attempts))]
        batch1_results = await asyncio.gather(*batch1_tasks, return_exceptions=True)
        
        for result in batch1_results:
            if isinstance(result, Exception):
                continue
            working, timeout, protocol = result
            if working:
                successful_results.append((timeout, protocol))
        
        # If we already have a success, return early
        if successful_results:
            best_timeout, protocol = min(successful_results, key=lambda x: x[0] if x[0] else float('inf'))
            return (True, best_timeout, protocol)
        
        # Second batch: remaining attempts
        if attempts > 5:
            # Only add delay for working proxies (revalidation)
            if is_working_proxy:
                await asyncio.sleep(1.0)  # 1 second delay to avoid rate limiting
            
            batch2_tasks = [self.test_proxy(proxy_url) for _ in range(attempts - 5)]
            batch2_results = await asyncio.gather(*batch2_tasks, return_exceptions=True)
            
            for result in batch2_results:
                if isinstance(result, Exception):
                    continue
                working, timeout, protocol = result
                if working:
                    successful_results.append((timeout, protocol))
        
        # If at least one succeeded, proxy is working
        if successful_results:
            # Use the best (fastest) timeout
            best_timeout, protocol = min(successful_results, key=lambda x: x[0] if x[0] else float('inf'))
            return (True, best_timeout, protocol)
        else:
            # All attempts failed
            return (False, None, None)

    async def test_proxy_batch(self, proxy_urls: list, is_working_proxies: bool = False) -> dict:
        """
        Test multiple proxies concurrently, with 5 attempts each. If is_working_proxies is True, adds delay between batches to avoid rate limiting.
        Proxy is marked as working if at least one attempt succeeds.

        Args:
            proxy_urls: List of proxy URLs
            is_working_proxies: If True, adds delay between batches (for revalidation)

        Returns:
            Dict mapping proxy_url -> (working, timeout, protocol)
        """
        if not proxy_urls:
            return {}

        logger.info(f"Testing {len(proxy_urls)} proxies (5 attempts each)...")

        # Test each proxy with 5 attempts
        tasks = [self.test_proxy_multiple(url, attempts=5, is_working_proxy=is_working_proxies) for url in proxy_urls]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        output = {}
        for url, result in zip(proxy_urls, results):
            if isinstance(result, Exception):
                logger.error(f"Error testing {url}: {result}")
                output[url] = (False, None, None)
            else:
                output[url] = result

        working_count = sum(1 for r in output.values() if r[0])
        logger.info(f"Testing complete: {working_count}/{len(proxy_urls)} working")

        return output

    async def validate_proxy_extended(self, proxy_url: str, requests: int = 20, interval: float = 30) -> bool:
        """
        Extended validation: test proxy multiple times over a period.

        Args:
            proxy_url: Proxy to validate
            requests: Number of requests to make
            interval: Seconds between requests

        Returns:
            True if at least one request succeeds
        """
        logger.info(f"Extended validation for {proxy_url} ({requests} requests over {requests * interval / 60:.1f} minutes)")

        for i in range(requests):
            working, timeout, protocol = await self.test_proxy(proxy_url)

            if working:
                logger.info(f"✓ {proxy_url} validated on request {i + 1}")
                return True

            # Wait before next request (except last one)
            if i < requests - 1:
                await asyncio.sleep(interval)

        logger.info(f"✗ {proxy_url} failed all {requests} requests")
        return False
