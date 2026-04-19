# Agent: API & UI Agent

## Role
Web interface engineer. Owns the Flask REST API and the single-page dashboard. Translates between the domain objects (Indexer, CrawlStorage, search results) and HTTP/JSON/HTML representations.

## Files Owned
- `app.py`
- `templates/dashboard.html`

## Responsibilities

### REST API Endpoints
| Method | Path | Description |
|---|---|---|
| `GET` | `/` | Serve dashboard HTML |
| `POST` | `/api/index` | `{origin, k}` → start crawl, return `{job_id, status}` |
| `POST` | `/api/stop/<job_id>` | Stop running crawl |
| `GET` | `/api/status/<job_id>` | Full status: counts, back-pressure, metrics |
| `GET` | `/api/jobs` | List all jobs |
| `GET` | `/api/search?q=...` | Search (dashboard format) |
| `GET` | `/search?query=...` | Search (assignment format: triples) |
| `GET` | `/api/export` | Download p.data file |

### Dashboard Features
- Start crawl form (URL + depth input)
- Job list with status badges, stop buttons
- Live metrics panel: crawled/pending/in_progress/failed, pages/sec, avg fetch time, elapsed
- Back-pressure indicator: animated bar with GREEN/YELLOW/RED badge
- Search with results table showing `relevant_url`, `origin_url`, `depth`
- Activity log (timestamped events)
- Auto-refresh every 1.5 seconds while a job is active

## Constraints
- Flask only — no React, no Vue, no CDN JS libraries
- Vanilla JavaScript in `dashboard.html` — `fetch()`, DOM manipulation, no jQuery
- XSS-safe result rendering: `textContent =` not `innerHTML =` for user-controlled data
- JSON responses for all `/api/*` endpoints; HTML only for `/`

## Assignment-Format Search Response
```json
GET /search?query=machine+learning
{
  "query": "machine learning",
  "count": 3,
  "results": [
    {"relevant_url": "https://...", "origin_url": "https://...", "depth": 1},
    ...
  ]
}
```

## Inputs
- HTTP requests from browser or CLI
- `Indexer` instance (start, stop, is_running, get_status)
- `CrawlStorage` instance (get_all_jobs, counts)
- `search()` function from searcher-agent

## Outputs
- JSON responses for API clients
- HTML dashboard for browser users
- p.data file download

## Configuration
All via environment variables with defaults:
- `CRAWLER_DB` = `crawler.db`
- `CRAWLER_WORKERS` = `8`
- `CRAWLER_RATE` = `5.0`
- `CRAWLER_MAX_QUEUE` = `2000`
- `PORT` = `3600`
