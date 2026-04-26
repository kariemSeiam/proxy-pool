# proxy-pool

> A scored, tiered proxy pool that doesn't brute-force.  
> Async Python · SQLite · REST API · ~900 LOC

Most proxy pools are brute-force engines — they hammer every proxy with 5+ retries,
test against heavy targets, and re-validate the entire pool every 30 seconds. This one
doesn't. Each proxy gets **one lightweight HEAD request**. If it works, great. If not,
next cycle. Over time, the scoring system separates the reliable proxies from the garbage
without wasting bandwidth.

---

## How it works

```
  ┌─────────────┐     ┌───────────┐     ┌────────────┐     ┌──────────┐
  │  ProxiFly   │────▶│  Sources   │────▶│  Validator  │────▶│ Database │
  │  CDN lists  │     │  fetcher   │     │  HEAD test  │     │ (SQLite) │
  └─────────────┘     └───────────┘     └────────────┘     └────┬─────┘
                                                                │
                                                                ▼
                                                          ┌──────────┐
                       Consumer ──GET /best──▶            │ REST API  │
                       Consumer ──GET /random──▶          │ :8000     │
                       Consumer ──GET /list──▶            └──────────┘
```

**Sources** — Pulls free proxy lists from [ProxiFly](https://github.com/proxifly/free-proxy-list)
via jsDelivr CDN. HTTP, HTTPS, SOCKS4, SOCKS5.

**Validation** — One HEAD request per proxy to `icanhazip.com`. No retries. No brute force.
Pass/fail in under 5 seconds.

**Scoring** — Every proxy earns a reliability score (0–100%) based on its success rate:

```
score = (successes / total_tests) × 100
```

**Tiering** — Proxies are classified into tiers that control how often they're re-tested:

| Tier | Criteria | Re-test interval |
|------|----------|-----------------|
| 🥇 Gold | ≥80% success, tested 5+ times | 4 hours |
| 🥈 Silver | ≥50% success, tested 3+ times | 1 hour |
| 🔵 New | Fewer than 3 tests | 10 minutes |
| ⚫ Dead | <30% success or 10 consecutive fails | 12 hours |

Proven proxies get tested *less*, not more. New ones get tested frequently so they
prove themselves quickly. Dead ones get occasional second chances.

**Cleanup** — Proxies scoring below 10% after 10+ tests are automatically removed.

---

## Quick start

```bash
# Clone
git clone https://github.com/kariemSeiam/proxy-pool.git
cd proxy-pool

# Install
pip install -r requirements.txt

# Run
python main.py
```

The pool starts fetching and testing proxies immediately. The API is live at
`http://localhost:8000`.

---

## API

| Endpoint | Description | Params |
|----------|-------------|--------|
| `GET /` | API overview | — |
| `GET /random` | Weighted random proxy | `min_score` (default: 30) |
| `GET /best` | Highest-scored proxy | `min_score` (default: 50) |
| `GET /list` | Scored proxy list | `limit` (default: 10), `min_score` (default: 50) |
| `GET /stats` | Pool statistics | — |
| `GET /health` | Liveness check | — |

### Examples

```bash
# Get the best proxy
curl http://localhost:8000/best
# {"proxy": "http://1.2.3.4:8080"}

# Get a random proxy (good for rotation)
curl http://localhost:8000/random
# {"proxy": "http://5.6.7.8:3128"}

# Top 20 proxies with at least 70% reliability
curl "http://localhost:8000/list?limit=20&min_score=70"
# {"count": 20, "proxies": [...]}

# Pool health
curl http://localhost:8000/stats
# {
#   "total": 2973,
#   "gold": 45,
#   "silver": 112,
#   "new_untested": 1503,
#   "dead": 1313,
#   "usable": 189,
#   "avg_score": 62.3,
#   "untested": 1503
# }
```

### Using with other tools

```bash
# yt-dlp through a random proxy
curl -s http://localhost:8000/random | jq -r .proxy | xargs -I{} yt-dlp --proxy {} <url>

# curl through the best proxy
BEST=$(curl -s http://localhost:8000/best | jq -r .proxy)
curl --proxy "$BEST" https://example.com
```

---

## Architecture

```
proxy-pool/
├── config.py        # All settings — sources, tiers, intervals, API
├── database.py      # Async SQLite with scoring & tier classification
├── sources.py       # Fetches from ProxiFly CDN (HTTP/HTTPS/SOCKS4/SOCKS5)
├── validator.py     # Lightweight HEAD-based proxy testing
├── scheduler.py     # Tiered revalidation loop (background worker)
├── server.py        # REST API (aiohttp)
├── main.py          # Entry point — runs server + scheduler
├── requirements.txt # aiohttp, aiosqlite
└── data/            # Runtime (gitignored) — proxies.db, logs
```

Every module has a single responsibility. No god classes, no circular imports,
no over-engineering. ~900 lines total.

---

## Configuration

All settings live in `config.py`. Key knobs you might want to tweak:

| Setting | Default | What it does |
|---------|---------|-------------|
| `BATCH_SIZE` | 200 | Proxies tested per scheduler cycle |
| `TEST_TIMEOUT` | 5s | Max wait per proxy test |
| `MAX_CONCURRENT` | 200 | Parallel test limit (semaphore) |
| `GOLD_MIN_SCORE` | 80% | Score threshold for gold tier |
| `WORKER_INTERVAL` | 60s | Time between scheduler cycles |
| `API_PORT` | 8000 | REST API listen port |
| `DATA_DIR` | `./data` | DB + logs location (env-overridable) |

---

## Running as a service

```bash
# systemd unit (example)
sudo cp proxy-pool.service /etc/systemd/system/
sudo systemctl enable --now proxy-pool

# Check logs
journalctl -u proxy-pool -f
```

---

## Why this exists

The original version of this project was a brute-force proxy tester that:

- Sent **5 requests per proxy** per validation cycle
- Tested against `maps.googleapis.com` (heavy, slow)
- Re-validated the **entire pool every 30 seconds**
- Only supported HTTP/HTTPS (no SOCKS)
- Had no scoring — proxies were just "alive" or "dead"

v2 fixes all of that with a fundamentally different approach: **test once, score over time,
prioritize by reliability**. The result is ~74x fewer requests while producing *better*
proxy data because the scoring actually reflects long-term reliability.

---

## License

MIT
