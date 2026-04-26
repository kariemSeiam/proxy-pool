#!/usr/bin/env python3
"""
Proxy Pool v2 — Main Entry Point

Starts the REST API server and the background scheduler in a single process.
The scheduler continuously fetches and validates proxies while the API serves
them to consumers.

Signal handling: SIGINT / SIGTERM trigger a graceful shutdown.
"""
import sys
import asyncio
import logging
import signal
import platform
from pathlib import Path

from server import Server
from scheduler import Scheduler
from config import LOG_LEVEL, LOG_FORMAT, API_HOST, API_PORT

# ── Logging Setup ────────────────────────────────────────────────────────────
log_dir: Path = Path(__file__).parent / "data"
log_dir.mkdir(exist_ok=True)

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL),
    format=LOG_FORMAT,
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(log_dir / "proxy-pool.log"),
    ],
)
logger = logging.getLogger("main")

# Suppress noisy asyncio connection errors (expected when testing proxies)
logging.getLogger("asyncio").setLevel(logging.WARNING)


class ProxyPoolService:
    """Orchestrates the API server and background scheduler."""

    def __init__(self) -> None:
        self.server = Server()
        self.scheduler = Scheduler()
        self._scheduler_task: Optional[asyncio.Task] = None
        self._shutdown = asyncio.Event()

    async def start(self) -> None:
        """Start all components and block until shutdown."""
        logger.info("=" * 50)
        logger.info("PROXY POOL v2 STARTING")
        logger.info("=" * 50)

        await self.server.init()
        await self.scheduler.init()

        # Launch the scheduler as a background task
        self._scheduler_task = asyncio.create_task(self._run_scheduler())

        logger.info(f"API: http://{API_HOST}:{API_PORT}")
        logger.info("Endpoints: /random /best /list /stats /health")
        logger.info("=" * 50)

        # Start the aiohttp server (blocks until _shutdown is set)
        from aiohttp import web
        runner = web.AppRunner(self.server.app)
        await runner.setup()
        site = web.TCPSite(runner, API_HOST, API_PORT)
        await site.start()

        try:
            await self._shutdown.wait()
        except KeyboardInterrupt:
            pass
        finally:
            await self._stop(runner)

    async def _run_scheduler(self) -> None:
        """Run the scheduler, catching crashes so they don't kill the process."""
        try:
            await self.scheduler.run_forever()
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Scheduler crashed: {e}", exc_info=True)

    async def _stop(self, runner) -> None:
        """Gracefully shut down all components."""
        logger.info("Shutting down...")
        await self.scheduler.stop()
        if self._scheduler_task:
            self._scheduler_task.cancel()
            try:
                await self._scheduler_task
            except asyncio.CancelledError:
                pass
        await self.scheduler.close()
        await runner.cleanup()
        await self.server.close()
        logger.info("Shutdown complete")

    def request_shutdown(self) -> None:
        """Signal the main loop to exit."""
        self._shutdown.set()


def main() -> None:
    """Entry point — set up the event loop and start the service."""
    service = ProxyPoolService()
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    # Suppress connection reset errors that flood the log when testing proxies
    loop.set_exception_handler(lambda loop, ctx: None)

    # Register signal handlers on Unix
    if platform.system() != "Windows":
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, service.request_shutdown)
            except NotImplementedError:
                pass

    try:
        loop.run_until_complete(service.start())
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
        logging.getLogger("main").error(f"Fatal: {e}", exc_info=True)
        sys.exit(1)
