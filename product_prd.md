# Product Requirements Document — Web Crawler & Search Engine

## 1. Overview

A concurrent web crawler and real-time search engine built with Python, designed to index web pages breadth-first from a given origin URL and serve keyword-based search results while indexing is still in progress. The system emphasizes architectural sensibility: back pressure, thread safety, persistence for resume-after-interruption, and exclusive use of language-native libraries for core crawl and parse logic.

## 2. Goals

- **Functional correctness**: Crawl to depth `k` from an origin URL without visiting any page twice; return relevant search triples `(relevant_url, origin_url, depth)`.
- **Concurrency**: Index and search run simultaneously, with thread-safe shared state.
- **Scalability (single machine)**: Handle large crawl surfaces via bounded queues, rate limiting, and back pressure—ensuring memory and network resources remain controlled.
- **Resumability**: Persist all crawl state to SQLite so a job can be restarted after process termination.
- **Observability**: A web dashboard shows real-time crawl progress, queue depth, and back-pressure status.

## 3. Architecture

### 3.1 Module Breakdown

| Module       | Responsibility                                          | Key Libraries         |
|-------------|--------------------------------------------------------|-----------------------|
| `storage.py` | Thread-safe SQLite persistence (WAL mode). Crawl jobs, pages, inverted term index. | `sqlite3`, `threading` |
| `fetcher.py` | HTTP page retrieval, robots.txt compliance.             | `urllib.request`, `ssl`, `socket` |
| `parser.py`  | HTML link extraction, text extraction, tokenization.    | `html.parser`, `re`, `urllib.parse` |
| `indexer.py` | Crawl orchestrator: thread pool, BFS queue, rate limiter, back pressure controller. | `concurrent.futures`, `threading` |
| `searcher.py`| Query engine over the term index; safe during active crawl. | (uses `storage.py`) |
| `app.py`     | Flask web server: REST API + dashboard UI.              | `flask` |

### 3.2 Data Flow

```
Origin URL → Indexer (BFS queue + thread pool)
    → Fetcher (urllib, rate-limited)
    → Parser (html.parser → links + text)
    → Storage (SQLite: pages + term_index)
    → Searcher (reads term_index concurrently)
```

### 3.3 Concurrency Model

- **Thread pool** (`concurrent.futures.ThreadPoolExecutor`): `N` worker threads fetch and process pages concurrently.
- **Rate limiter** (token bucket): Caps outbound HTTP requests to `R` requests/second, preventing target hosts from being overwhelmed.
- **Back pressure controller**: Monitors queue depth against configurable watermarks. Three states:
  - **GREEN** (queue < 25% capacity): All systems nominal.
  - **YELLOW** (queue 25–100% capacity): Enqueuing continues but monitored.
  - **RED** (queue ≥ 100% capacity): New link discovery is paused until the queue drains.
- **SQLite WAL mode**: Allows the search module to read committed data while the indexer writes, without blocking either side.

### 3.4 Persistence & Resumability

All state lives in SQLite:
- `crawl_jobs`: Job metadata (origin, depth, status, timestamps).
- `pages`: Every discovered URL with status (`pending`, `in_progress`, `crawled`, `failed`), depth, title, and body text.
- `term_index`: Inverted index mapping terms to URLs with frequency and title-match flags.

On resume, URLs left in `in_progress` are reset to `pending`, and the crawl continues where it left off.

## 4. API Specification

### `POST /api/index`
```json
{ "origin": "https://example.com", "k": 2 }
→ { "job_id": 1, "origin": "...", "k": 2, "status": "started" }
```

### `POST /api/stop/<job_id>`
```json
→ { "job_id": 1, "status": "stop_requested" }
```

### `GET /api/status/<job_id>`
Returns queue counts, back-pressure state, rate limiter state, and performance metrics.

### `GET /api/jobs`
Returns list of all jobs with summary counts.

### `GET /api/search?q=<query>`
```json
→ {
    "query": "machine learning",
    "count": 12,
    "results": [
      { "relevant_url": "...", "origin_url": "...", "depth": 1 }
    ]
  }
```

## 5. Search Relevancy Model

Simple but effective term-frequency scoring with title boost:
- Tokenize query and page content identically (lowercase, stop-word removal, min-length filtering).
- For each matching term: `score += frequency + (10 × in_title)`.
- Title matches receive 10× weight because a term in the page title is a much stronger relevancy signal.
- Results sorted by descending aggregate score, capped at 50.

## 6. Configuration

All tunable via environment variables:

| Variable            | Default | Description                    |
|--------------------|---------|--------------------------------|
| `CRAWLER_DB`        | `crawler.db` | SQLite database file path  |
| `CRAWLER_WORKERS`   | `8`     | Thread pool size               |
| `CRAWLER_RATE`      | `5.0`   | Max requests per second        |
| `CRAWLER_MAX_QUEUE` | `2000`  | Back-pressure high watermark   |
| `PORT`              | `5000`  | Web server port                |

## 7. UI Dashboard

A single-page web dashboard (served by Flask) provides:
- **Crawl initiation**: Input origin URL and depth, start/stop jobs.
- **Job list**: All jobs with status indicators, crawled/total counts, stop button.
- **Live metrics**: Crawled, pending, in-progress, failed, discovered, pages/sec, avg fetch time, elapsed time.
- **Back-pressure indicator**: Animated bar and status badge (GREEN/YELLOW/RED).
- **Search**: Real-time search with results table showing (relevant_url, origin_url, depth).
- **Activity log**: Timestamped event stream.

Auto-refreshes every 1.5 seconds while a job is active.

## 8. Design Decisions

1. **stdlib-first**: `urllib.request`, `html.parser`, `sqlite3`, `threading`, `concurrent.futures` — no Scrapy, BeautifulSoup, Requests, or Elasticsearch. Flask is the only external dependency (for the web UI).
2. **SQLite as the visited set**: Rather than an in-memory set (lost on crash), the `pages` table with a `(url, job_id)` primary key serves as a persistent, thread-safe visited set.
3. **Batch dequeue**: Workers fetch URLs in small batches from SQLite (ordered by depth for BFS), keeping the coordinator loop simple and memory stable.
4. **Token bucket over semaphore**: A token bucket smooths out request rate over time rather than just capping concurrency, which is more respectful to target servers.

## 9. Non-Goals (Scoped Out)

- Distributed crawling across multiple machines.
- JavaScript rendering (SPA content).
- Full-text search ranking (TF-IDF, BM25, PageRank).
- Authentication / session handling for crawled sites.
- Comprehensive robots.txt `Crawl-delay` directive support.
