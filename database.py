#!/usr/bin/env python3
"""
Proxy Pool v2 — Database

Async SQLite with reliability scoring and tiered proxy classification.

Each proxy earns a score (0–100%) based on its success rate across tests.
Proxies are classified into tiers (gold / silver / new / dead) that determine
how often they get re-validated. Good proxies are tested less often; new ones
are tested frequently so they prove themselves quickly.
"""
import aiosqlite
import logging
import random
from typing import Any, Dict, List, Optional, Tuple

from config import (
    DB_FILE,
    GOLD_MIN_SCORE, GOLD_MIN_TESTS,
    SILVER_MIN_SCORE, SILVER_MIN_TESTS,
    DEAD_MAX_SCORE, DEAD_CONSECUTIVE,
    GOLD_INTERVAL, SILVER_INTERVAL, NEW_INTERVAL, DEAD_INTERVAL,
    CLEANUP_SCORE, CLEANUP_MIN_TESTS,
)

logger = logging.getLogger(__name__)

# ── Helpers ──────────────────────────────────────────────────────────────────

def _now() -> str:
    """UTC timestamp as ISO-8601 string."""
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def _calc_tier(score: float, total_tests: int, consecutive_fails: int) -> str:
    """
    Classify a proxy into a tier based on score, test count, and failure streak.

    Returns one of: 'gold', 'silver', 'new', 'dead'.
    """
    if consecutive_fails >= DEAD_CONSECUTIVE:
        return "dead"
    if total_tests >= GOLD_MIN_TESTS and score >= GOLD_MIN_SCORE:
        return "gold"
    if total_tests >= SILVER_MIN_TESTS and score >= SILVER_MIN_SCORE:
        return "silver"
    if total_tests >= 3 and score < DEAD_MAX_SCORE:
        return "dead"
    return "new"


# ── Database ─────────────────────────────────────────────────────────────────

class Database:
    """Async SQLite wrapper tailored for proxy scoring and tiering."""

    def __init__(self) -> None:
        self.db_file: Path = DB_FILE
        self.conn: Optional[aiosqlite.Connection] = None

    async def init(self) -> None:
        """Open (or create) the database and apply performance PRAGMAs."""
        if not self.db_file.parent.exists():
            self.db_file.parent.mkdir(parents=True)

        if not self.db_file.exists():
            await self._create_schema()

        self.conn = await aiosqlite.connect(str(self.db_file))
        self.conn.row_factory = aiosqlite.Row
        await self.conn.execute("PRAGMA journal_mode=WAL")
        await self.conn.execute("PRAGMA synchronous=NORMAL")
        await self.conn.execute("PRAGMA cache_size=10000")
        await self.conn.execute("PRAGMA temp_store=MEMORY")
        logger.info("Database initialized")

    async def close(self) -> None:
        """Flush and close the connection."""
        if self.conn:
            await self.conn.close()
            self.conn = None

    # ── Schema ───────────────────────────────────────────────────────────

    async def _create_schema(self) -> None:
        """Create tables and indexes from scratch."""
        conn = await aiosqlite.connect(str(self.db_file))
        await conn.execute("PRAGMA journal_mode=WAL")
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS proxies (
                proxy_url         TEXT PRIMARY KEY,
                protocol          TEXT NOT NULL,
                source            TEXT,
                score             REAL NOT NULL DEFAULT 0,
                success_count     INTEGER NOT NULL DEFAULT 0,
                fail_count        INTEGER NOT NULL DEFAULT 0,
                total_tests       INTEGER NOT NULL DEFAULT 0,
                consecutive_fails INTEGER NOT NULL DEFAULT 0,
                avg_response_ms   REAL,
                last_tested       TEXT,
                last_success      TEXT,
                created_at        TEXT DEFAULT CURRENT_TIMESTAMP,
                tier              TEXT NOT NULL DEFAULT 'new'
            )
        """)
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_tier ON proxies(tier)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_score ON proxies(score)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_last_tested ON proxies(last_tested)")
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS meta (
                key        TEXT PRIMARY KEY,
                value      TEXT,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await conn.commit()
        await conn.close()
        logger.info(f"Created database at {self.db_file}")

    # ── Core Operations ──────────────────────────────────────────────────

    async def _execute(self, query: str, params: tuple = ()) -> List[aiosqlite.Row]:
        """Execute a query, commit, and return fetched rows."""
        cursor = await self.conn.execute(query, params)
        await self.conn.commit()
        return await cursor.fetchall()

    async def add_proxies(self, proxies: List[dict]) -> int:
        """
        Insert new proxies, skipping duplicates.

        Args:
            proxies: List of dicts with keys 'url', 'protocol', and optional 'source'.

        Returns:
            Number of actually-new proxies inserted.
        """
        if not proxies:
            return 0

        added = 0
        for p in proxies:
            url = p["url"]
            try:
                cursor = await self.conn.execute(
                    "INSERT OR IGNORE INTO proxies (proxy_url, protocol, source, tier) "
                    "VALUES (?, ?, ?, 'new')",
                    (url, p["protocol"], p.get("source")),
                )
                await self.conn.commit()
                if cursor.rowcount > 0:
                    added += 1
            except Exception as e:
                logger.debug(f"Skip proxy {url}: {e}")
        return added

    async def record_test(
        self,
        proxy_url: str,
        success: bool,
        response_ms: Optional[float] = None,
    ) -> None:
        """
        Record a single test result and recalculate score + tier.

        Uses a running average for response time so no history table is needed.
        """
        now = _now()

        rows = await self._execute(
            "SELECT success_count, fail_count, total_tests, "
            "       consecutive_fails, avg_response_ms, last_success "
            "FROM proxies WHERE proxy_url = ?",
            (proxy_url,),
        )
        if not rows:
            return

        r = rows[0]
        success_count: int = r["success_count"] + (1 if success else 0)
        fail_count: int = r["fail_count"] + (0 if success else 1)
        total_tests: int = r["total_tests"] + 1
        consecutive_fails: int = 0 if success else r["consecutive_fails"] + 1

        # Running average for response time
        old_avg: float = r["avg_response_ms"] or 0.0
        if response_ms is not None:
            avg_ms: float = ((old_avg * (total_tests - 1)) + response_ms) / total_tests
        else:
            avg_ms = old_avg

        score: float = (success_count / total_tests) * 100 if total_tests > 0 else 0.0
        tier: str = _calc_tier(score, total_tests, consecutive_fails)
        last_success: Optional[str] = now if success else r["last_success"]

        await self._execute(
            "UPDATE proxies SET score=?, success_count=?, fail_count=?, "
            "total_tests=?, consecutive_fails=?, avg_response_ms=?, "
            "last_tested=?, last_success=?, tier=? "
            "WHERE proxy_url = ?",
            (score, success_count, fail_count, total_tests, consecutive_fails,
             avg_ms, now, last_success, tier, proxy_url),
        )

    # ── Queries ──────────────────────────────────────────────────────────

    async def get_due_proxies(self, limit: int = 200) -> List[Tuple[str, str]]:
        """
        Return proxies due for re-testing based on their tier's interval.

        Priority: new > silver > gold > dead, then oldest-tested first.
        """
        query = f"""
            SELECT proxy_url, protocol FROM proxies
            WHERE
                (tier = 'gold'   AND (last_tested IS NULL OR datetime(last_tested) < datetime('now', '-{GOLD_INTERVAL // 60} minutes')))
             OR (tier = 'silver' AND (last_tested IS NULL OR datetime(last_tested) < datetime('now', '-{SILVER_INTERVAL // 60} minutes')))
             OR (tier = 'new'    AND (last_tested IS NULL OR datetime(last_tested) < datetime('now', '-{NEW_INTERVAL // 60} minutes')))
             OR (tier = 'dead'   AND (last_tested IS NULL OR datetime(last_tested) < datetime('now', '-{DEAD_INTERVAL // 60} minutes')))
            ORDER BY
                CASE tier
                    WHEN 'new'    THEN 0
                    WHEN 'silver' THEN 1
                    WHEN 'gold'   THEN 2
                    WHEN 'dead'   THEN 3
                END,
                last_tested ASC NULLS FIRST
            LIMIT ?
        """
        rows = await self._execute(query, (limit,))
        return [(r["proxy_url"], r["protocol"]) for r in rows]

    async def get_best_proxy(self, min_score: float = 50) -> Optional[str]:
        """Return the single highest-scored proxy meeting the threshold."""
        rows = await self._execute(
            "SELECT proxy_url FROM proxies "
            "WHERE score >= ? AND total_tests >= 2 "
            "ORDER BY score DESC, avg_response_ms ASC "
            "LIMIT 1",
            (min_score,),
        )
        return rows[0]["proxy_url"] if rows else None

    async def get_random_proxy(self, min_score: float = 30) -> Optional[str]:
        """
        Return a weighted-random proxy.

        70% chance: picked from the top 30% by score.
        30% chance: picked from all usable proxies.
        """
        rows = await self._execute(
            "SELECT proxy_url, score FROM proxies "
            "WHERE score >= ? AND total_tests >= 1",
            (min_score,),
        )
        if not rows:
            return None

        if random.random() < 0.7 and len(rows) > 5:
            top_n = max(1, len(rows) * 3 // 10)
            rows_sorted = sorted(rows, key=lambda r: r["score"], reverse=True)
            return random.choice(rows_sorted[:top_n])["proxy_url"]
        return random.choice(rows)["proxy_url"]

    async def get_working_proxies(
        self,
        limit: Optional[int] = None,
        min_score: float = 50,
    ) -> List[str]:
        """Return proxy URLs ordered by score (desc) then response time (asc)."""
        query = (
            "SELECT proxy_url FROM proxies "
            "WHERE score >= ? AND total_tests >= 1 "
            "ORDER BY score DESC, avg_response_ms ASC"
        )
        params: tuple = (min_score,)
        if limit:
            query += " LIMIT ?"
            params = (min_score, limit)
        rows = await self._execute(query, params)
        return [r["proxy_url"] for r in rows]

    async def get_stats(self) -> Dict[str, Any]:
        """Return a snapshot of pool health and tier distribution."""
        rows = await self._execute("""
            SELECT
                COUNT(*) as total,
                SUM(CASE WHEN tier='gold'   THEN 1 ELSE 0 END) as gold,
                SUM(CASE WHEN tier='silver' THEN 1 ELSE 0 END) as silver,
                SUM(CASE WHEN tier='new'    THEN 1 ELSE 0 END) as new_untested,
                SUM(CASE WHEN tier='dead'   THEN 1 ELSE 0 END) as dead,
                SUM(CASE WHEN score >= 50   THEN 1 ELSE 0 END) as usable,
                ROUND(AVG(CASE WHEN total_tests > 0 THEN score END), 1) as avg_score,
                SUM(CASE WHEN total_tests = 0 THEN 1 ELSE 0 END) as untested
            FROM proxies
        """)
        result = dict(rows[0]) if rows else {}
        from datetime import datetime, timezone
        result["timestamp"] = datetime.now(timezone.utc).isoformat()
        return result

    async def cleanup(self) -> int:
        """
        Remove hopelessly dead proxies (low score after many tests).

        Returns the remaining total proxy count.
        """
        await self._execute(
            "DELETE FROM proxies WHERE score < ? AND total_tests >= ?",
            (CLEANUP_SCORE, CLEANUP_MIN_TESTS),
        )
        stats = await self.get_stats()
        return stats.get("total", 0)

    # ── Source Meta Tracking ─────────────────────────────────────────────

    async def get_meta(self, key: str) -> Optional[str]:
        """Retrieve a stored meta value (e.g. last source update timestamp)."""
        rows = await self._execute(
            "SELECT value FROM meta WHERE key = ?", (key,)
        )
        return rows[0]["value"] if rows else None

    async def set_meta(self, key: str, value: str) -> None:
        """Store or update a meta key-value pair."""
        await self._execute(
            "INSERT OR REPLACE INTO meta (key, value, updated_at) VALUES (?, ?, ?)",
            (key, value, _now()),
        )
