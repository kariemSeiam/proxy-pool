import asyncio
import logging
import os
import sys
from pathlib import Path
from typing import Awaitable, Callable, Optional

from flask import Flask, Response, jsonify, request

# Ensure project root is on the import path when running from /api on Vercel
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.append(str(ROOT_DIR))

from database import Database

app = Flask(__name__)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def _run_with_db(fn: Callable[[Database], Awaitable]):
    """
    Run a coroutine that needs a Database instance inside a fresh event loop.

    Each invocation opens and closes the database to keep serverless
    executions stateless and avoid cross-request loop reuse.
    """

    async def _inner():
        db = Database()
        await db.init()
        try:
            return await fn(db)
        finally:
            await db.close()

    return asyncio.run(_inner())


def _parse_limit(raw_limit: Optional[str]):
    """Validate and parse the limit query parameter."""
    if raw_limit is None:
        return None, None

    try:
        limit = int(raw_limit)
    except ValueError:
        return None, Response(
            "Error: limit must be an integer",
            status=400,
            mimetype="text/plain",
        )

    if limit <= 0:
        return None, Response(
            "Error: limit must be positive",
            status=400,
            mimetype="text/plain",
        )

    return limit, None


@app.get("/")
def root():
    """Lightweight landing endpoint."""
    return jsonify(
        {
            "status": "ok",
            "message": "Proxy API on Vercel",
            "routes": ["/list", "/random", "/stats", "/health"],
        }
    )


@app.get("/list")
def list_proxies():
    """Return working proxies as plain text (one per line)."""
    limit, error_response = _parse_limit(request.args.get("limit"))
    if error_response:
        return error_response

    try:
        proxies = _run_with_db(lambda db: db.get_working_proxies(limit=limit))
    except Exception:
        logger.exception("Failed to load proxies")
        return Response("Internal server error", status=500, mimetype="text/plain")

    if not proxies:
        return Response(
            "No working proxies available", status=404, mimetype="text/plain"
        )

    return Response("\n".join(proxies), status=200, mimetype="text/plain")


@app.get("/random")
def random_proxy():
    """Return a single random working proxy."""
    try:
        proxy = _run_with_db(lambda db: db.get_random_proxy())
    except Exception:
        logger.exception("Failed to load random proxy")
        return Response("Internal server error", status=500, mimetype="text/plain")

    if not proxy:
        return Response(
            "No working proxies available", status=404, mimetype="text/plain"
        )

    return Response(proxy, status=200, mimetype="text/plain")


async def _gather_stats(db: Database):
    stats = await db.get_stats()
    last_meta = await db.get_last_meta()
    return {
        "total_proxies": stats["total"],
        "working_proxies": stats["working"],
        "failed_proxies": stats["failed"],
        "last_meta_update": last_meta,
    }


@app.get("/stats")
def stats():
    """Return proxy statistics."""
    try:
        data = _run_with_db(_gather_stats)
    except Exception:
        logger.exception("Failed to load stats")
        return jsonify({"error": "Internal server error"}), 500

    return jsonify(data)


@app.get("/health")
def health():
    """Simple health check."""
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    app.run(
        host=os.environ.get("HOST", "0.0.0.0"),
        port=int(os.environ.get("PORT", "3000")),
    )

