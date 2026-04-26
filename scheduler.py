#!/usr/bin/env python3
"""
Proxy Pool v2 — Scheduler

Background worker that fetches proxy sources and validates them on a tiered
schedule. Proxies that have proven reliable (gold) are re-checked less often
than new, untested ones. This keeps the pool fresh without wasting bandwidth.

Cycle flow:
    1. Check if source lists have changed → fetch new proxies
    2. Pick the next batch of due-for-testing proxies (by tier priority)
    3. Test the batch and record results
    4. Log pool stats
    5. Sleep until next cycle
"""
import asyncio
import logging
import time as _time
from typing import Optional

from database import Database
from sources import ProxySource
from validator import Validator
from config import WORKER_INTERVAL, SOURCE_CHECK_INTERVAL, BATCH_SIZE

logger = logging.getLogger(__name__)


class Scheduler:
    """Background worker — fetch, validate, score, repeat."""

    def __init__(self) -> None:
        self.db = Database()
        self.source = ProxySource()
        self.validator = Validator()
        self.running: bool = False
        self._last_source_check: float = 0

    async def init(self) -> None:
        """Initialize all sub-components."""
        await self.db.init()
        await self.source.init()
        await self.validator.init()
        logger.info("Scheduler initialized")

    async def close(self) -> None:
        """Gracefully shut down all sub-components."""
        await self.validator.close()
        await self.source.close()
        await self.db.close()
        logger.info("Scheduler closed")

    def stop(self) -> None:
        """Signal the main loop to exit."""
        self.running = False

    # ── Main Loop ────────────────────────────────────────────────────────

    async def run_forever(self) -> None:
        """Run scheduler cycles until stopped."""
        self.running = True
        logger.info("Scheduler starting main loop")

        try:
            while self.running:
                try:
                    await self._cycle()
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    logger.error(f"Cycle error: {e}", exc_info=True)

                await asyncio.sleep(WORKER_INTERVAL)

        except asyncio.CancelledError:
            logger.info("Scheduler cancelled")
        finally:
            self.running = False

    # ── Single Cycle ─────────────────────────────────────────────────────

    async def _cycle(self) -> None:
        """One iteration: fetch sources if due, then validate a batch."""
        now = _time.monotonic()

        # 1. Check sources periodically
        if now - self._last_source_check >= SOURCE_CHECK_INTERVAL:
            await self._fetch_sources()
            self._last_source_check = now

        # 2. Get proxies that are due for (re-)testing
        due = await self.db.get_due_proxies(limit=BATCH_SIZE)
        if not due:
            logger.debug("No proxies due for testing")
            return

        # 3. Test the batch
        logger.info(f"Testing {len(due)} due proxies")
        results = await self.validator.test_many(due)

        # 4. Record results
        for proxy_url, success, response_ms in results:
            await self.db.record_test(proxy_url, success, response_ms)

        # 5. Log stats
        stats = await self.db.get_stats()
        logger.info(
            f"Stats: {stats.get('usable', 0)} usable, "
            f"{stats.get('gold', 0)} gold, "
            f"{stats.get('silver', 0)} silver, "
            f"{stats.get('new_untested', 0)} new, "
            f"{stats.get('dead', 0)} dead "
            f"(total: {stats.get('total', 0)})"
        )

    # ── Source Fetching ──────────────────────────────────────────────────

    async def _fetch_sources(self) -> None:
        """Fetch proxy lists if the source has been updated."""
        logger.info("Checking proxy sources...")

        # Check meta for changes
        current_meta = await self.source.fetch_meta()
        last_meta = await self.db.get_meta("source_updated")

        if current_meta and current_meta != last_meta:
            logger.info(f"Source meta updated: {last_meta} → {current_meta}")
        elif current_meta == last_meta:
            logger.debug("Source meta unchanged, skipping full fetch")
            return

        # Fetch all sources
        proxies = await self.source.fetch_all()
        if not proxies:
            logger.warning("No proxies fetched from sources")
            return

        added = await self.db.add_proxies(proxies)
        logger.info(f"Added {added} new proxies (total fetched: {len(proxies)})")

        # Save meta
        if current_meta:
            await self.db.set_meta("source_updated", current_meta)
