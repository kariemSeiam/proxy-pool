#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Background Worker - Continuous proxy validation and updates
Runs in background to keep proxy database fresh and validated.
"""

import asyncio
import logging
from datetime import datetime, timezone

from database import Database
from validator import ProxyValidator
from fetcher import ProxyFetcher
from config import (
    META_CHECK_INTERVAL,
    REVALIDATION_INTERVAL,
    MAX_FAILURES,
    VALIDATION_REQUESTS,
    VALIDATION_DURATION
)

logger = logging.getLogger(__name__)


class ProxyWorker:
    """Background worker for proxy validation and updates."""

    def __init__(self):
        self.db = Database()
        self.validator = ProxyValidator()
        self.fetcher = ProxyFetcher()
        self.running = False
        self.last_meta_check = None
        self.last_revalidation = None

    async def init(self):
        """Initialize all components."""
        await self.db.init()
        await self.validator.init()
        await self.fetcher.init()
        logger.info("Worker initialized")

    async def close(self):
        """Close all components."""
        await self.fetcher.close()
        await self.validator.close()
        await self.db.close()
        logger.info("Worker closed")

    async def check_meta_and_update(self):
        """Check meta for changes and update proxy list if needed."""
        try:
            # Fetch current meta
            current_meta = await self.fetcher.fetch_meta()
            if not current_meta:
                logger.warning("Failed to fetch meta, skipping update")
                return

            # Get last known meta
            last_meta = await self.db.get_last_meta()

            if last_meta == current_meta:
                logger.debug("Meta unchanged, skipping update")
                return

            logger.info(f"Meta changed: {last_meta} -> {current_meta}")

            # Save new meta
            await self.db.save_meta(current_meta)

            # Fetch new proxy lists
            all_proxies = await self.fetcher.get_all_proxies()
            if not all_proxies:
                logger.warning("No proxies fetched")
                return

            logger.info(f"Fetched {len(all_proxies)} proxies from API")

            # Get existing working proxies
            existing_working = set(await self.db.get_working_proxies())

            # Find new proxies
            new_proxies = [p for p in all_proxies if p not in existing_working]

            if new_proxies:
                logger.info(f"Testing {len(new_proxies)} new proxies...")
                await self._validate_proxies(new_proxies)

            # Clean up proxies not in the new list
            await self.db.remove_missing_proxies(all_proxies)

            # Clean up failed proxies
            await self.db.cleanup_failed_proxies(MAX_FAILURES)

            logger.info("Meta update complete")

        except Exception as e:
            logger.error(f"Error in check_meta_and_update: {e}", exc_info=True)

    async def revalidate_working_proxies(self):
        """Revalidate all working proxies periodically."""
        try:
            logger.info("Starting revalidation of working proxies...")

            working_proxies = await self.db.get_working_proxies()
            if not working_proxies:
                logger.info("No working proxies to revalidate")
                return

            logger.info(f"Revalidating {len(working_proxies)} working proxies...")

            # Test all working proxies (with delay to avoid rate limiting)
            await self._validate_proxies(working_proxies, is_working_proxies=True)

            # Clean up failed proxies
            await self.db.cleanup_failed_proxies(MAX_FAILURES)

            logger.info("Revalidation complete")

        except Exception as e:
            logger.error(f"Error in revalidate_working_proxies: {e}", exc_info=True)

    async def validate_failed_proxies(self):
        """Try to recover failed proxies - processes all proxies in batches."""
        try:
            # Get all proxies that need validation
            all_proxies_to_validate = await self.db.get_proxies_to_validate()
            if not all_proxies_to_validate:
                logger.debug("No failed proxies to validate")
                return

            total_count = len(all_proxies_to_validate)
            logger.info(f"Processing {total_count} proxies that need validation...")

            # Process in batches to avoid overwhelming the system
            BATCH_SIZE = 1000
            processed = 0

            for i in range(0, total_count, BATCH_SIZE):
                batch = all_proxies_to_validate[i:i + BATCH_SIZE]
                proxy_urls = [p['proxy_url'] for p in batch]
                
                logger.info(f"Processing batch {i // BATCH_SIZE + 1}: {len(proxy_urls)} proxies ({(processed + len(proxy_urls))}/{total_count})...")
                
                await self._validate_proxies(proxy_urls)
                processed += len(proxy_urls)

            logger.info(f"Completed validation of all {total_count} proxies")

            # Clean up proxies that failed too many times
            await self.db.cleanup_failed_proxies(MAX_FAILURES)

        except Exception as e:
            logger.error(f"Error in validate_failed_proxies: {e}", exc_info=True)

    async def _validate_proxies(self, proxy_urls: list, is_working_proxies: bool = False):
        """
        Validate a list of proxies and update database.

        Args:
            proxy_urls: List of proxy URLs to validate
            is_working_proxies: If True, indicates these are working proxies being revalidated
        """
        if not proxy_urls:
            return

        # Test proxies
        results = await self.validator.test_proxy_batch(proxy_urls, is_working_proxies=is_working_proxies)

        # Update database
        for proxy_url, (working, timeout, protocol) in results.items():
            await self.db.upsert_proxy(proxy_url, working, timeout, protocol)

        # Log stats
        working_count = sum(1 for r in results.values() if r[0])
        logger.info(f"Validation complete: {working_count}/{len(proxy_urls)} working")

    async def run_forever(self):
        """Main worker loop."""
        self.running = True
        loop_count = 0

        logger.info("Worker starting main loop...")

        try:
            while self.running:
                loop_count += 1
                now = datetime.now(timezone.utc).timestamp()

                logger.info(f"\n{'='*60}\nLoop #{loop_count}\n{'='*60}")

                # 1. Check meta every META_CHECK_INTERVAL seconds
                if (self.last_meta_check is None or
                    (now - self.last_meta_check) >= META_CHECK_INTERVAL):
                    logger.info("Checking meta for updates...")
                    await self.check_meta_and_update()
                    self.last_meta_check = now

                # 2. Validate failed proxies (every loop)
                await self.validate_failed_proxies()

                # 3. Revalidate working proxies every REVALIDATION_INTERVAL minutes
                if (self.last_revalidation is None or
                    (now - self.last_revalidation) >= (REVALIDATION_INTERVAL * 60)):
                    logger.info("Starting periodic revalidation...")
                    await self.revalidate_working_proxies()
                    self.last_revalidation = now

                # 4. Log stats
                stats = await self.db.get_stats()
                logger.info(f"Stats: {stats['working']} working, {stats['failed']} failed, {stats['total']} total")

                # Wait before next loop (30 seconds)
                logger.info("Sleeping 30 seconds before next loop...")
                await asyncio.sleep(30)

        except asyncio.CancelledError:
            logger.info("Worker cancelled")
            raise

        except Exception as e:
            logger.error(f"Fatal error in worker loop: {e}", exc_info=True)
            raise

        finally:
            self.running = False
            logger.info("Worker stopped")

    async def stop(self):
        """Stop the worker."""
        logger.info("Stopping worker...")
        self.running = False
