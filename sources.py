#!/usr/bin/env python3
"""
Proxy Pool v2 — Sources

Fetches free proxy lists from the ProxiFly CDN (via jsDelivr).
Supports HTTP, HTTPS, SOCKS4, and SOCKS5 protocols.

Source lists are plain text files with one `ip:port` entry per line,
updated regularly by the ProxiFly community project.
"""
import aiohttp
import logging
from typing import Dict, List, Optional

from config import SOURCES, META_URL

logger = logging.getLogger(__name__)


class ProxySource:
    """Fetches and parses proxy lists from CDN sources."""

    def __init__(self) -> None:
        self.session: Optional[aiohttp.ClientSession] = None

    async def init(self) -> None:
        """Initialize the HTTP session."""
        self.session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=15),
            headers={"User-Agent": "proxy-pool-v2/1.0"},
        )
        logger.info("Source fetcher initialized")

    async def close(self) -> None:
        """Close the HTTP session."""
        if self.session:
            await self.session.close()
            self.session = None

    # ── Fetching ─────────────────────────────────────────────────────────

    async def fetch_all(self) -> List[dict]:
        """
        Fetch proxy lists from all configured sources.

        Returns a deduplicated list of proxy dicts:
            {"url": "http://1.2.3.4:8080", "protocol": "http", "source": "proxifly_http"}
        """
        all_proxies: Dict[str, dict] = {}

        for source_name, source_cfg in SOURCES.items():
            try:
                raw = await self._fetch_text(source_cfg["url"])
                protocol = source_cfg["protocol"]
                count = 0

                for line in raw.splitlines():
                    line = line.strip()
                    if not line or ":" not in line:
                        continue

                    # Build the proxy URL with the correct scheme
                    proxy_url = f"{protocol}://{line}" if "://" not in line else line

                    if proxy_url not in all_proxies:
                        all_proxies[proxy_url] = {
                            "url": proxy_url,
                            "protocol": protocol,
                            "source": source_name,
                        }
                        count += 1

                logger.info(f"{source_name}: {count} raw entries")

            except Exception as e:
                logger.warning(f"Failed to fetch {source_name}: {e}")

        proxies = list(all_proxies.values())
        logger.info(f"Total unique proxies from all sources: {len(proxies)}")
        return proxies

    async def fetch_meta(self) -> Optional[str]:
        """
        Fetch the source meta JSON and return the `updated` timestamp.

        Used to detect when source lists have changed so we only re-fetch
        when there's actually new data.
        """
        try:
            import json
            raw = await self._fetch_text(META_URL)
            meta = json.loads(raw)
            updated = meta.get("updated", "")
            logger.info(f"Source meta: {updated}")
            return updated
        except Exception as e:
            logger.warning(f"Failed to fetch meta: {e}")
            return None

    # ── Internal ─────────────────────────────────────────────────────────

    async def _fetch_text(self, url: str) -> str:
        """Fetch a URL and return its response body as text."""
        async with self.session.get(url) as resp:
            resp.raise_for_status()
            return await resp.text()
