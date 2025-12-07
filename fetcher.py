#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Proxy Fetcher - Fetches proxy lists and meta from ProxiFly
Handles meta checking and proxy list updates.
"""

import aiohttp
import logging
from typing import List, Tuple, Optional

from config import META_API_URL, HTTP_PROXIES_URL, HTTPS_PROXIES_URL

logger = logging.getLogger(__name__)


class ProxyFetcher:
    """Fetches proxy lists and metadata from ProxiFly CDN."""

    def __init__(self):
        self.session: Optional[aiohttp.ClientSession] = None

    async def init(self):
        """Initialize HTTP session."""
        self.session = aiohttp.ClientSession()
        logger.info("Fetcher session initialized")

    async def close(self):
        """Close HTTP session."""
        if self.session:
            await self.session.close()
            self.session = None
            logger.info("Fetcher session closed")

    async def fetch_meta(self) -> Optional[str]:
        """
        Fetch meta timestamp from API.

        Returns:
            Timestamp string or None if failed
        """
        try:
            timeout = aiohttp.ClientTimeout(total=10)
            async with self.session.get(META_API_URL, timeout=timeout) as response:
                if response.status == 200:
                    data = await response.json()
                    timestamp = data.get('timestamp')
                    if timestamp:
                        logger.info(f"Meta timestamp: {timestamp}")
                        return timestamp
                else:
                    logger.warning(f"Meta fetch failed with status {response.status}")
            return None

        except aiohttp.ClientError as e:
            logger.error(f"Network error fetching meta: {e}")
            return None

        except Exception as e:
            logger.error(f"Unexpected error fetching meta: {e}")
            return None

    async def fetch_proxy_lists(self) -> Tuple[List[str], List[str]]:
        """
        Fetch HTTP and HTTPS proxy lists from API.

        Returns:
            Tuple of (http_proxies, https_proxies)
        """
        http_proxies = []
        https_proxies = []

        try:
            timeout = aiohttp.ClientTimeout(total=30)

            # Fetch HTTP proxies
            try:
                async with self.session.get(HTTP_PROXIES_URL, timeout=timeout) as response:
                    if response.status == 200:
                        text = await response.text()
                        http_proxies = self._parse_proxy_list(text)
                        logger.info(f"Fetched {len(http_proxies)} HTTP proxies")
                    else:
                        logger.warning(f"HTTP proxy list fetch failed with status {response.status}")
            except Exception as e:
                logger.error(f"Error fetching HTTP proxies: {e}")

            # Fetch HTTPS proxies
            try:
                async with self.session.get(HTTPS_PROXIES_URL, timeout=timeout) as response:
                    if response.status == 200:
                        text = await response.text()
                        https_proxies = self._parse_proxy_list(text)
                        logger.info(f"Fetched {len(https_proxies)} HTTPS proxies")
                    else:
                        logger.warning(f"HTTPS proxy list fetch failed with status {response.status}")
            except Exception as e:
                logger.error(f"Error fetching HTTPS proxies: {e}")

        except Exception as e:
            logger.error(f"Unexpected error fetching proxy lists: {e}")

        return http_proxies, https_proxies

    def _parse_proxy_list(self, text: str) -> List[str]:
        """
        Parse proxy list text file.

        Args:
            text: Plain text with one proxy per line

        Returns:
            List of normalized proxy URLs
        """
        proxies = []
        for line in text.strip().split('\n'):
            line = line.strip()
            if line and not line.startswith('#'):
                # Normalize: ensure http:// prefix
                if not line.startswith('http://') and not line.startswith('https://'):
                    line = f'http://{line}'
                proxies.append(line)

        return proxies

    async def get_all_proxies(self) -> List[str]:
        """
        Fetch and combine all proxies (deduplicated).

        Returns:
            List of unique proxy URLs
        """
        http_proxies, https_proxies = await self.fetch_proxy_lists()

        # Combine and deduplicate
        all_proxies = list(set(http_proxies + https_proxies))

        logger.info(f"Total unique proxies: {len(all_proxies)}")
        return all_proxies
