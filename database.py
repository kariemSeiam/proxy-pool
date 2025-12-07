#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Database Module - Clean SQLite interface with async support
Handles all database operations with proper connection pooling.
"""

import aiosqlite
import logging
from typing import List, Dict, Optional
from datetime import datetime, timezone
from pathlib import Path

from config import DB_FILE

logger = logging.getLogger(__name__)


class Database:
    """Async SQLite database manager with connection pooling."""

    def __init__(self):
        self.db_file = DB_FILE
        self.conn: Optional[aiosqlite.Connection] = None

    async def init(self):
        """Initialize database connection and create tables if needed."""
        if not self.db_file.exists():
            logger.info(f"Creating new database at {self.db_file}")
            await self._create_database()

        self.conn = await aiosqlite.connect(str(self.db_file))
        self.conn.row_factory = aiosqlite.Row

        # Optimize SQLite settings
        await self.conn.execute("PRAGMA journal_mode=WAL")
        await self.conn.execute("PRAGMA synchronous=NORMAL")
        await self.conn.execute("PRAGMA cache_size=10000")
        await self.conn.execute("PRAGMA temp_store=MEMORY")

        logger.info("Database initialized successfully")

    async def close(self):
        """Close database connection."""
        if self.conn:
            await self.conn.close()
            self.conn = None
            logger.info("Database connection closed")

    async def _create_database(self):
        """Create database tables and indexes."""
        conn = await aiosqlite.connect(str(self.db_file))

        # Enable optimizations
        await conn.execute("PRAGMA journal_mode=WAL")
        await conn.execute("PRAGMA synchronous=NORMAL")

        # Proxies table
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS proxies (
                proxy_url TEXT PRIMARY KEY,
                working INTEGER NOT NULL DEFAULT 0,
                timeout REAL,
                protocol TEXT,
                failed_count INTEGER NOT NULL DEFAULT 0,
                last_tested TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Indexes for fast queries
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_working ON proxies(working)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_timeout ON proxies(timeout) WHERE working = 1")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_failed ON proxies(failed_count)")

        # Meta table for tracking updates
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS meta (
                id INTEGER PRIMARY KEY,
                timestamp TEXT UNIQUE NOT NULL,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)

        await conn.commit()
        await conn.close()

    async def execute(self, query: str, params: tuple = ()):
        """Execute query and return results."""
        if not self.conn:
            await self.init()

        try:
            cursor = await self.conn.execute(query, params)
            await self.conn.commit()
            return await cursor.fetchall()
        except Exception as e:
            logger.error(f"Database error: {e}")
            raise

    # ========================================================================
    # PROXY OPERATIONS
    # ========================================================================

    async def get_working_proxies(self, limit: Optional[int] = None) -> List[str]:
        """Get all working proxy URLs sorted by timeout."""
        query = """
            SELECT proxy_url
            FROM proxies
            WHERE working = 1
            ORDER BY
                CASE WHEN timeout IS NOT NULL THEN 0 ELSE 1 END,
                COALESCE(timeout, 999999) ASC
        """
        if limit:
            query += " LIMIT ?"
            results = await self.execute(query, (limit,))
        else:
            results = await self.execute(query)

        return [row['proxy_url'] for row in results]

    async def get_random_proxy(self) -> Optional[str]:
        """Get a random working proxy - 70% from fastest proxies, 30% from all."""
        import random

        all_working = await self.get_working_proxies()
        if not all_working:
            return None

        # 70% chance from fastest 70% of proxies, 30% from all
        if random.random() < 0.7 and len(all_working) > 1:
            # Get fastest 70% of proxies
            fastest_count = max(1, int(len(all_working) * 0.7))
            fastest_proxies = all_working[:fastest_count]
            return random.choice(fastest_proxies)
        else:
            return random.choice(all_working)

    async def get_proxies_to_validate(self, limit: Optional[int] = None) -> List[Dict[str, any]]:
        """Get proxies that need validation (non-working or old)."""
        query = """
            SELECT proxy_url, working, failed_count, last_tested
            FROM proxies
            WHERE working = 0 OR last_tested IS NULL
               OR datetime(last_tested) < datetime('now', '-20 minutes')
            ORDER BY
                working ASC,
                failed_count ASC,
                last_tested ASC NULLS FIRST
        """
        if limit:
            query += " LIMIT ?"
            results = await self.execute(query, (limit,))
        else:
            results = await self.execute(query)
        return [dict(row) for row in results]

    async def upsert_proxy(self, proxy_url: str, working: bool,
                          timeout: Optional[float] = None,
                          protocol: Optional[str] = None):
        """Insert or update proxy status."""
        now = datetime.now(timezone.utc).isoformat()

        # Get existing data
        existing = await self.execute(
            "SELECT failed_count, timeout FROM proxies WHERE proxy_url = ?",
            (proxy_url,)
        )

        failed_count = 0
        best_timeout = timeout

        if existing:
            old_failed = existing[0]['failed_count'] or 0
            old_timeout = existing[0]['timeout']

            # Increment failures or reset
            failed_count = 0 if working else (old_failed + 1)

            # Keep best timeout
            if working and old_timeout and timeout:
                best_timeout = min(old_timeout, timeout)

        await self.execute("""
            INSERT INTO proxies (proxy_url, working, timeout, protocol, failed_count, last_tested)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(proxy_url) DO UPDATE SET
                working = excluded.working,
                timeout = excluded.timeout,
                protocol = COALESCE(excluded.protocol, protocol),
                failed_count = excluded.failed_count,
                last_tested = excluded.last_tested
        """, (proxy_url, working, best_timeout, protocol, failed_count, now))

    async def cleanup_failed_proxies(self, max_failures: int = 5):
        """Remove proxies that failed too many times."""
        await self.execute(
            "DELETE FROM proxies WHERE failed_count >= ?",
            (max_failures,)
        )

    async def remove_missing_proxies(self, valid_proxies: List[str]):
        """Remove proxies not in the valid list, but keep working proxies."""
        if not valid_proxies:
            # If no valid proxies, only remove non-working ones
            await self.execute(
                "DELETE FROM proxies WHERE working = 0 AND proxy_url NOT IN (SELECT proxy_url FROM proxies WHERE working = 1)"
            )
            return
        
        placeholders = ','.join('?' * len(valid_proxies))
        # Only remove non-working proxies that are not in the new list
        # Keep all working proxies regardless of whether they're in the new list
        await self.execute(
            f"DELETE FROM proxies WHERE working = 0 AND proxy_url NOT IN ({placeholders})",
            tuple(valid_proxies)
        )

    async def get_stats(self) -> Dict[str, int]:
        """Get proxy statistics."""
        result = await self.execute("""
            SELECT
                COUNT(*) as total,
                SUM(CASE WHEN working = 1 THEN 1 ELSE 0 END) as working,
                SUM(CASE WHEN working = 0 THEN 1 ELSE 0 END) as failed
            FROM proxies
        """)

        if result:
            row = result[0]
            return {
                'total': row['total'] or 0,
                'working': row['working'] or 0,
                'failed': row['failed'] or 0
            }
        return {'total': 0, 'working': 0, 'failed': 0}

    # ========================================================================
    # META OPERATIONS
    # ========================================================================

    async def get_last_meta(self) -> Optional[str]:
        """Get last meta timestamp."""
        result = await self.execute(
            "SELECT timestamp FROM meta ORDER BY id DESC LIMIT 1"
        )
        return result[0]['timestamp'] if result else None

    async def save_meta(self, timestamp: str):
        """Save meta timestamp."""
        await self.execute(
            "INSERT OR IGNORE INTO meta (timestamp) VALUES (?)",
            (timestamp,)
        )
