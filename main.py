#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Proxy Server - Main Entry Point
Runs the API server and background worker together.
"""

import sys
import asyncio
import logging
import signal
import platform
from pathlib import Path

from server import ProxyServer
from worker import ProxyWorker
from config import LOG_LEVEL, LOG_FORMAT, API_HOST, API_PORT

# Configure logging
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL),
    format=LOG_FORMAT,
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('data/proxy_server.log')
    ]
)

logger = logging.getLogger(__name__)

# Suppress asyncio connection reset errors (expected when testing proxies)
asyncio_logger = logging.getLogger('asyncio')
asyncio_logger.setLevel(logging.WARNING)


class ProxyService:
    """Main service that runs server and worker together."""

    def __init__(self):
        self.server = ProxyServer()
        self.worker = ProxyWorker()
        self.worker_task = None
        self.shutdown_event = asyncio.Event()

    async def init(self):
        """Initialize server and worker."""
        logger.info("="*60)
        logger.info("PROXY SERVER STARTING")
        logger.info("="*60)

        await self.server.init()
        await self.worker.init()

        logger.info("All components initialized")

    async def close(self):
        """Close server and worker."""
        logger.info("Shutting down...")

        # Stop worker
        if self.worker_task:
            await self.worker.stop()
            self.worker_task.cancel()
            try:
                await self.worker_task
            except asyncio.CancelledError:
                pass

        # Close components
        await self.worker.close()
        await self.server.close()

        logger.info("Shutdown complete")

    async def run_worker_background(self):
        """Run worker in background."""
        try:
            await self.worker.run_forever()
        except asyncio.CancelledError:
            logger.info("Worker task cancelled")
        except Exception as e:
            logger.error(f"Worker crashed: {e}", exc_info=True)

    async def run(self):
        """Run the complete service."""
        try:
            # Initialize
            await self.init()

            # Start worker in background
            self.worker_task = asyncio.create_task(self.run_worker_background())

            logger.info("="*60)
            logger.info(f"API SERVER: http://{API_HOST}:{API_PORT}")
            logger.info("="*60)
            logger.info("Endpoints:")
            logger.info(f"  GET /list         - Get all working proxies (plain text)")
            logger.info(f"  GET /list?limit=N - Get N proxies")
            logger.info(f"  GET /random       - Get random proxy (plain text)")
            logger.info(f"  GET /stats        - Get statistics (JSON)")
            logger.info(f"  GET /health       - Health check (JSON)")
            logger.info("="*60)
            logger.info("Press Ctrl+C to stop")
            logger.info("="*60)

            # Run server (this blocks until server stops)
            await self._run_server()

        except KeyboardInterrupt:
            logger.info("\nReceived interrupt signal")
            self.shutdown_event.set()

        except Exception as e:
            logger.error(f"Fatal error: {e}", exc_info=True)

        finally:
            await self.close()

    async def _run_server(self):
        """Run the server with graceful shutdown."""
        from aiohttp import web

        # Create runner
        runner = web.AppRunner(self.server.app)
        await runner.setup()

        # Create site
        site = web.TCPSite(runner, API_HOST, API_PORT)
        await site.start()

        try:
            # Wait for shutdown signal
            await self.shutdown_event.wait()
        except KeyboardInterrupt:
            # On Windows, KeyboardInterrupt may interrupt the wait
            logger.info("Shutdown event interrupted")
            self.shutdown_event.set()
        finally:
            # Always cleanup
            await runner.cleanup()

    def handle_signal(self, sig):
        """Handle shutdown signals."""
        logger.info(f"\nReceived signal {sig}")
        self.shutdown_event.set()


def _handle_exception(loop, context):
    """Handle exceptions in event loop callbacks."""
    exception = context.get('exception')
    
    # Suppress expected connection reset errors on Windows
    if exception and isinstance(exception, ConnectionResetError):
        # These are expected when testing proxies - remote hosts close connections
        return
    
    # Log other exceptions
    if 'exception' in context:
        logger.debug(f"Event loop exception: {context.get('exception')}")
    else:
        logger.debug(f"Event loop message: {context.get('message', 'Unknown')}")


def main():
    """Main entry point."""
    service = ProxyService()

    # Setup signal handlers (Unix only - Windows doesn't support add_signal_handler)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    # Set exception handler to suppress expected connection reset errors
    loop.set_exception_handler(_handle_exception)

    if platform.system() != 'Windows':
        # Unix/Linux: Use signal handlers
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, lambda s=sig: service.handle_signal(s))
            except NotImplementedError:
                # Fallback if signal handler not available
                pass
    else:
        # Windows: KeyboardInterrupt will be handled in run() method
        logger.info("Running on Windows - using KeyboardInterrupt for shutdown")

    try:
        loop.run_until_complete(service.run())
    except KeyboardInterrupt:
        pass
    finally:
        loop.close()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nShutdown complete")
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)
