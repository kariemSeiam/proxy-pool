#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Proxy Server API - Simple REST API with 2 endpoints
Provides access to validated proxies via HTTP.
"""

import asyncio
import logging
from datetime import datetime, timezone
from aiohttp import web

from database import Database
from config import API_HOST, API_PORT

logger = logging.getLogger(__name__)


class ProxyServer:
    """Simple HTTP server for proxy access."""

    def __init__(self):
        self.db = Database()
        self.app = None

    async def init(self):
        """Initialize database and create app."""
        await self.db.init()
        self.app = self._create_app()
        logger.info("Server initialized")

    async def close(self):
        """Close database connection."""
        await self.db.close()
        logger.info("Server closed")

    def _create_app(self) -> web.Application:
        """Create aiohttp application with routes."""
        app = web.Application()

        # Add routes
        app.router.add_get('/list', self.handle_list)
        app.router.add_get('/random', self.handle_random)
        app.router.add_get('/stats', self.handle_stats)
        app.router.add_get('/health', self.handle_health)

        # Lifecycle handlers
        app.on_startup.append(self._on_startup)
        app.on_cleanup.append(self._on_cleanup)

        return app

    async def _on_startup(self, app):
        """Called when server starts."""
        logger.info(f"Server starting on http://{API_HOST}:{API_PORT}")

    async def _on_cleanup(self, app):
        """Called when server stops."""
        logger.info("Server shutting down")

    # ========================================================================
    # ROUTE HANDLERS
    # ========================================================================

    async def handle_list(self, request: web.Request) -> web.Response:
        """
        GET /list - Get all working proxies as plain text (one per line)

        Query params:
            limit (optional): Maximum number of proxies to return

        Returns:
            Plain text with one proxy per line
        """
        try:
            # Get limit parameter
            limit = request.query.get('limit')
            if limit:
                try:
                    limit = int(limit)
                    if limit <= 0:
                        return web.Response(
                            text="Error: limit must be positive",
                            status=400,
                            content_type='text/plain'
                        )
                except ValueError:
                    return web.Response(
                        text="Error: limit must be an integer",
                        status=400,
                        content_type='text/plain'
                    )

            # Get proxies
            proxies = await self.db.get_working_proxies(limit=limit)

            if not proxies:
                return web.Response(
                    text="No working proxies available",
                    status=404,
                    content_type='text/plain'
                )

            # Return as plain text (one per line)
            proxy_text = '\n'.join(proxies)
            return web.Response(
                text=proxy_text,
                content_type='text/plain',
                charset='utf-8'
            )

        except Exception as e:
            logger.error(f"Error in handle_list: {e}", exc_info=True)
            return web.Response(
                text=f"Internal server error: {str(e)}",
                status=500,
                content_type='text/plain'
            )

    async def handle_random(self, request: web.Request) -> web.Response:
        """
        GET /random - Get a random working proxy (weighted toward fast ones)

        Returns:
            Plain text with single proxy URL
        """
        try:
            proxy = await self.db.get_random_proxy()

            if not proxy:
                return web.Response(
                    text="No working proxies available",
                    status=404,
                    content_type='text/plain'
                )

            return web.Response(
                text=proxy,
                content_type='text/plain',
                charset='utf-8'
            )

        except Exception as e:
            logger.error(f"Error in handle_random: {e}", exc_info=True)
            return web.Response(
                text=f"Internal server error: {str(e)}",
                status=500,
                content_type='text/plain'
            )

    async def handle_stats(self, request: web.Request) -> web.Response:
        """
        GET /stats - Get proxy statistics (JSON)

        Returns:
            JSON with proxy counts and last update time
        """
        try:
            stats = await self.db.get_stats()
            last_meta = await self.db.get_last_meta()

            response_data = {
                'total_proxies': stats['total'],
                'working_proxies': stats['working'],
                'failed_proxies': stats['failed'],
                'last_meta_update': last_meta,
                'timestamp': datetime.now(timezone.utc).isoformat()
            }

            return web.json_response(response_data)

        except Exception as e:
            logger.error(f"Error in handle_stats: {e}", exc_info=True)
            return web.json_response(
                {'error': str(e)},
                status=500
            )

    async def handle_health(self, request: web.Request) -> web.Response:
        """
        GET /health - Health check endpoint

        Returns:
            JSON with status
        """
        return web.json_response({
            'status': 'ok',
            'timestamp': datetime.now(timezone.utc).isoformat()
        })

    def run(self):
        """Run the server (blocking)."""
        web.run_app(
            self.app,
            host=API_HOST,
            port=API_PORT,
            print=lambda x: None  # Suppress default aiohttp logs
        )
