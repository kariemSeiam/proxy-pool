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

from config import (  # noqa: E402
    API_HOST,
    API_PORT,
    DATA_DIR,
    DB_FILE,
    HTTP_PROXIES_URL,
    HTTPS_PROXIES_URL,
    META_API_URL,
    MAX_CONCURRENT_TESTS,
    MAX_FAILURES,
    META_CHECK_INTERVAL,
    REVALIDATION_INTERVAL,
    TEST_TIMEOUT,
    TEST_URL,
    VALIDATION_DURATION,
    VALIDATION_REQUESTS,
)
from database import Database  # noqa: E402
from fetcher import ProxyFetcher  # noqa: E402
from validator import ProxyValidator  # noqa: E402

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


def _run_async(coro: Awaitable):
    """Run an async coroutine in a fresh event loop (serverless-safe)."""
    return asyncio.run(coro)


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


def _is_writable(path: Path) -> bool:
    """Check whether a directory is writable."""
    try:
        path.mkdir(parents=True, exist_ok=True)
        test_file = path / ".write_test"
        test_file.write_text("ok", encoding="utf-8")
        test_file.unlink(missing_ok=True)
        return True
    except Exception:
        logger.exception("Data directory is not writable", extra={"path": str(path)})
        return False


async def _gather_stats(db: Database):
    stats = await db.get_stats()
    last_meta = await db.get_last_meta()
    return {
        "total_proxies": stats["total"],
        "working_proxies": stats["working"],
        "failed_proxies": stats["failed"],
        "last_meta_update": last_meta,
    }


async def _run_on_demand_cycle(limit: int = 20):
    """
    Run a serverless-friendly mini-cycle:
    - fetch meta
    - fetch proxy lists
    - test a limited batch of proxies once
    - upsert results into the DB
    """
    db = Database()
    fetcher = ProxyFetcher()
    validator = ProxyValidator()

    await db.init()
    await fetcher.init()
    await validator.init()

    summary = {
        "meta_saved": None,
        "fetched_total_unique": 0,
        "tested": 0,
        "working": 0,
        "errors": [],
    }

    try:
        # Meta
        try:
            meta_ts = await fetcher.fetch_meta()
            summary["meta_saved"] = meta_ts
            if meta_ts:
                await db.save_meta(meta_ts)
        except Exception as e:  # pragma: no cover
            summary["errors"].append(f"meta: {e}")

        # Fetch lists
        http_list, https_list = [], []
        try:
            http_list, https_list = await fetcher.fetch_proxy_lists()
        except Exception as e:  # pragma: no cover
            summary["errors"].append(f"lists: {e}")

        combined = list({*http_list, *https_list})
        summary["fetched_total_unique"] = len(combined)

        if combined:
            candidates = combined[:limit]
            summary["tested"] = len(candidates)

            # Single-attempt tests to fit serverless timing
            tasks = [validator.test_proxy(p) for p in candidates]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            for proxy_url, result in zip(candidates, results):
                if isinstance(result, Exception):
                    summary["errors"].append(f"{proxy_url}: {result}")
                    await db.upsert_proxy(proxy_url, False, None, None)
                    continue

                working, timeout, protocol = result
                if working:
                    summary["working"] += 1
                await db.upsert_proxy(proxy_url, working, timeout, protocol)

    finally:
        await validator.close()
        await fetcher.close()
        await db.close()

    return summary


async def _gather_fetcher_diagnostics():
    """Fetch meta and proxy lists once to verify external sources."""
    fetcher = ProxyFetcher()
    await fetcher.init()

    meta_timestamp = None
    meta_status = "unavailable"
    meta_error = None

    try:
        meta_timestamp = await fetcher.fetch_meta()
        meta_status = "ok" if meta_timestamp else "missing"
    except Exception as e:
        meta_error = str(e)

    lists = {
        "http_count": 0,
        "https_count": 0,
        "total_unique": 0,
        "sample": [],
        "error": None,
    }

    try:
        http_proxies, https_proxies = await fetcher.fetch_proxy_lists()
        combined = list({*http_proxies, *https_proxies})
        lists.update(
            {
                "http_count": len(http_proxies),
                "https_count": len(https_proxies),
                "total_unique": len(combined),
                "sample": combined[:5],
            }
        )
    except Exception as e:
        lists["error"] = str(e)

    await fetcher.close()

    return {
        "meta": {
            "timestamp": meta_timestamp,
            "status": meta_status,
            "error": meta_error,
        },
        "lists": lists,
    }


def _diagnostics_payload():
    """Collect runtime diagnostics for troubleshooting the full cycle."""
    db_exists = DB_FILE.exists()
    db_size = DB_FILE.stat().st_size if db_exists else 0

    try:
        stats = _run_with_db(_gather_stats)
    except Exception:
        logger.exception("Failed to load stats for diagnostics")
        stats = {"error": "stats_unavailable"}

    vercel = bool(os.getenv("VERCEL"))

    fetcher_diag = _run_async(_gather_fetcher_diagnostics())

    return {
        "runtime": {
            "vercel": vercel,
            "data_dir": str(DATA_DIR),
            "data_dir_writable": _is_writable(DATA_DIR),
            "env_data_dir_override": os.getenv("DATA_DIR") is not None,
        },
        "database": {
            "db_file": str(DB_FILE),
            "db_exists": db_exists,
            "db_size_bytes": db_size,
        },
        "stats": stats,
        "fetcher": fetcher_diag,
        "config": {
            "api_host": API_HOST,
            "api_port": API_PORT,
            "meta_api_url": META_API_URL,
            "http_proxies_url": HTTP_PROXIES_URL,
            "https_proxies_url": HTTPS_PROXIES_URL,
            "test_url": TEST_URL,
            "test_timeout_seconds": TEST_TIMEOUT,
            "validation_requests": VALIDATION_REQUESTS,
            "validation_duration_minutes": VALIDATION_DURATION,
            "max_failures": MAX_FAILURES,
            "meta_check_interval_seconds": META_CHECK_INTERVAL,
            "revalidation_interval_minutes": REVALIDATION_INTERVAL,
            "max_concurrent_tests": MAX_CONCURRENT_TESTS,
        },
        "worker": {
            "runs_in_this_environment": not vercel,
            "note": "Vercel serverless does not execute the background worker",
        },
        "server": {
            "routes": ["/", "/list", "/random", "/stats", "/health", "/diagnostics"],
            "implementation": "Flask on Vercel (serverless) / aiohttp locally via main.py",
        },
        "on_demand_cycle": {
            "description": "POST /run-cycle triggers a single fetch+validate mini-cycle (serverless-friendly)",
            "default_limit": 20,
            "serverless_runtime": vercel,
        },
    }


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


@app.get("/stats")
def stats():
    """Return proxy statistics."""
    try:
        data = _run_with_db(_gather_stats)
    except Exception:
        logger.exception("Failed to load stats")
        return jsonify({"error": "Internal server error"}), 500

    return jsonify(data)


@app.get("/diagnostics")
def diagnostics():
    """
    Return runtime diagnostics to verify the fetch/validate cycle.

    Notes:
    - On Vercel, only the API handler runs; background worker is not active.
    - data_dir_writable=False indicates the runtime cannot persist the database.
    """
    payload = _diagnostics_payload()
    return jsonify(payload)


@app.post("/run-cycle")
def run_cycle():
    """
    Trigger a single serverless-friendly mini-cycle:
    - fetch meta
    - fetch proxy lists
    - test a limited batch once
    - upsert results into the DB

    Query params:
        limit (int, optional): number of proxies to test (default: 20, max: 100)
    """
    raw_limit = request.args.get("limit")
    limit = 20
    if raw_limit:
        try:
            limit = max(1, min(100, int(raw_limit)))
        except ValueError:
            return jsonify({"error": "limit must be an integer"}), 400

    try:
        result = _run_async(_run_on_demand_cycle(limit=limit))
        return jsonify({"limit": limit, "result": result})
    except Exception:
        logger.exception("run-cycle failed")
        return jsonify({"error": "internal_error"}), 500


@app.get("/health")
def health():
    """Simple health check."""
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    app.run(
        host=os.environ.get("HOST", "0.0.0.0"),
        port=int(os.environ.get("PORT", "3000")),
    )

