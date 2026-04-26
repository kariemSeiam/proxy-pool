#!/usr/bin/env python3
"""
Proxy Pool v2 — API Server

Lightweight REST API built on aiohttp. Serves proxy data from the SQLite
database with JSON responses.

Endpoints:
    GET /        — API overview
    GET /random  — Weighted random usable proxy
    GET /best    — Highest-scored proxy (query: min_score)
    GET /list    — Scored proxy list (query: limit, min_score)
    GET /stats   — Pool statistics (tiers, counts, averages)
    GET /health  — Liveness check
"""
import logging
from typing import Optional

from aiohttp import web

from database import Database

logger = logging.getLogger(__name__)


class Server:
    """REST API serving proxy data from the pool."""

    def __init__(self) -> None:
        self.db = Database()
        self.app: Optional[web.Application] = None

    async def init(self) -> None:
        """Initialize the database connection and register routes."""
        await self.db.init()
        self.app = self._create_app()
        logger.info("Server initialized")

    async def close(self) -> None:
        """Close the database connection."""
        await self.db.close()

    def _create_app(self) -> web.Application:
        """Build the aiohttp application with all routes."""
        app = web.Application()
        r = app.router.add_get
        r("/", self.handle_index)
        r("/random", self.handle_random)
        r("/best", self.handle_best)
        r("/list", self.handle_list)
        r("/stats", self.handle_stats)
        r("/health", self.handle_health)
        return app

    # ── Helpers ──────────────────────────────────────────────────────────

    @staticmethod
    def _json_response(data: dict, status: int = 200) -> web.Response:
        """Return a JSON response with proper headers."""
        import json
        return web.Response(
            text=json.dumps(data, indent=2, default=str),
            status=status,
            content_type="application/json",
        )

    @staticmethod
    def _query_int(request: web.Request, name: str, default: int) -> int:
        """Parse an integer query parameter with a fallback default."""
        try:
            return int(request.query.get(name, default))
        except (ValueError, TypeError):
            return default

    @staticmethod
    def _query_float(request: web.Request, name: str, default: float) -> float:
        """Parse a float query parameter with a fallback default."""
        try:
            return float(request.query.get(name, default))
        except (ValueError, TypeError):
            return default

    # ── Handlers ─────────────────────────────────────────────────────────

    async def handle_index(self, request: web.Request) -> web.Response:
        """API overview with endpoint documentation."""
        return self._json_response({
            "name": "proxy-pool-v2",
            "version": "2.0.0",
            "endpoints": {
                "GET /random": "Weighted random usable proxy",
                "GET /best?min_score=50": "Highest-scored proxy",
                "GET /list?limit=10&min_score=50": "Scored proxy list",
                "GET /stats": "Pool statistics (tiers, counts, avg score)",
                "GET /health": "Liveness check",
            },
        })

    async def handle_random(self, request: web.Request) -> web.Response:
        """Return a weighted-random proxy from the usable pool."""
        min_score = self._query_float(request, "min_score", 30)
        proxy = await self.db.get_random_proxy(min_score=min_score)

        if proxy:
            return self._json_response({"proxy": proxy})
        return self._json_response({"error": "No usable proxies"}, status=404)

    async def handle_best(self, request: web.Request) -> web.Response:
        """Return the single best proxy meeting the score threshold."""
        min_score = self._query_float(request, "min_score", 50)
        proxy = await self.db.get_best_proxy(min_score=min_score)

        if proxy:
            return self._json_response({"proxy": proxy})
        return self._json_response({"error": "No proxies meeting criteria"}, status=404)

    async def handle_list(self, request: web.Request) -> web.Response:
        """Return a list of scored proxies."""
        limit = self._query_int(request, "limit", 10)
        min_score = self._query_float(request, "min_score", 50)
        proxies = await self.db.get_working_proxies(limit=limit, min_score=min_score)
        return self._json_response({"count": len(proxies), "proxies": proxies})

    async def handle_stats(self, request: web.Request) -> web.Response:
        """Return pool health statistics."""
        stats = await self.db.get_stats()
        return self._json_response(stats)

    async def handle_health(self, request: web.Request) -> web.Response:
        """Simple liveness check."""
        return self._json_response({"status": "ok"})
