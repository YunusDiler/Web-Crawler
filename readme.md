# Web Crawler & Search Engine

A concurrent web crawler and real-time search engine built with Python. Crawls web pages breadth-first from a given origin URL to depth `k`, indexes their content, and serves keyword search results — all while respecting back pressure, rate limits, and thread safety.

**Core constraint:** All crawl and parse logic uses Python's standard library only (`urllib`, `html.parser`, `sqlite3`, `threading`). Flask is the sole external dependency (for the web dashboard).

---

## Quick Start

```bash
# 1. Clone and enter the project
git clone https://github.com/YunusDiler/Web-Crawler.git
cd crawler

# 2. Create a virtual environment (recommended)
python3 -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Run the application
python app.py
```

Open **http://localhost:3600** in your browser to access the dashboard.

---

## How It Works

### Indexing (`POST /api/index`)

1. You provide an **origin URL** and a **depth `k`** (number of hops).
2. The indexer seeds a BFS queue with the origin at depth 0.
3. A **thread pool** (default 8 workers) concurrently fetches pages using `urllib.request`.
4. Each page is parsed with `html.parser` to extract the title, body text, and outgoing links.
5. Extracted text is tokenized and stored in an **inverted term index** in SQLite.
6. Discovered links at `depth < k` are enqueued for crawling if not already visited.
7. **Back pressure** monitors queue depth — if it exceeds the high watermark, new link discovery pauses until the queue drains.
8. A **token-bucket rate limiter** caps requests per second to avoid overwhelming target servers.

### Searching (`GET /api/search?q=...`)

Search runs against the SQLite term index and can be invoked **at any time**, including while a crawl is active (SQLite WAL mode supports concurrent reads + writes). The relevancy model scores pages by term frequency with a 10× boost for title matches. Results are returned as `(relevant_url, origin_url, depth)` triples.

### Resumability

All state (jobs, discovered URLs, crawled content, term index) is persisted to SQLite. If the process is stopped mid-crawl, restarting with the same origin and depth will automatically resume from where it left off — URLs that were in-progress at the time of interruption are reset to pending.

---

## Dashboard

The web UI at `http://localhost:3600` provides:

- **Start crawl**: Input URL and depth, click "Start Indexing"
- **Job management**: View all jobs, switch between them, stop running jobs
- **Live metrics**: Pages crawled, pending, in-progress, failed, pages/sec, average fetch time
- **Back-pressure indicator**: Animated GREEN / YELLOW / RED status bar
- **Search**: Real-time keyword search with results table
- **Activity log**: Timestamped event stream

The dashboard auto-refreshes every 1.5 seconds while a job is active.

---

## Configuration

All settings are configurable via environment variables:

| Variable | Default | Description |
|---|---|---|
| `CRAWLER_DB` | `crawler.db` | SQLite database file path |
| `CRAWLER_WORKERS` | `8` | Number of concurrent fetch threads |
| `CRAWLER_RATE` | `5.0` | Maximum HTTP requests per second |
| `CRAWLER_MAX_QUEUE` | `2000` | Back-pressure high watermark (queue depth) |
| `PORT` | `3600` | Web server port |

Example:

```bash
CRAWLER_WORKERS=16 CRAWLER_RATE=10 PORT=8080 python app.py
```

---

## API Reference

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/api/index` | Start a crawl. Body: `{"origin": "https://...", "k": 2}` |
| `POST` | `/api/stop/<job_id>` | Stop a running crawl |
| `GET` | `/api/status/<job_id>` | Get job status, queue depth, metrics, back-pressure |
| `GET` | `/api/jobs` | List all crawl jobs |
| `GET` | `/api/search?q=<query>` | Search indexed pages |

---

## Architecture

```
┌──────────────────────────────────────────────────────┐
│                   Flask Web Server                    │
│  Dashboard UI │ REST API (/index, /search, /status)  │
└──────────┬───────────────────────────────┬───────────┘
           │                               │
    ┌──────▼──────┐                 ┌──────▼──────┐
    │   Indexer    │                 │  Searcher   │
    │ (BFS crawl) │                 │ (query TF)  │
    │             │                 │             │
    │ ThreadPool  │                 │   Reads     │
    │ RateLimiter │                 │  committed  │
    │ BackPressure│                 │   data      │
    └──────┬──────┘                 └──────┬──────┘
           │                               │
           │     ┌────────────────┐        │
           └────►│  SQLite (WAL)  │◄───────┘
                 │                │
                 │ • crawl_jobs   │
                 │ • pages        │
                 │ • term_index   │
                 └────────────────┘
```

**Key design decisions:**
- **SQLite as the visited set**: The `pages` table with `(url, job_id)` primary key acts as a persistent, thread-safe deduplication mechanism — no in-memory set to lose on crash.
- **Token bucket rate limiter**: Smooths request rate over time (vs. a simple semaphore that only caps concurrency).
- **Batch dequeue by depth**: Ensures BFS ordering and keeps the coordinator loop simple.
- **WAL mode**: Enables concurrent search reads while the indexer writes without blocking.

---

## CLI Usage

A full command-line interface is available as an alternative to the web dashboard:

```bash
# Start a crawl (with live progress bar)
python cli.py index https://example.com --depth 2

# Search the index
python cli.py search "machine learning"

# View job status
python cli.py status 1

# List all jobs
python cli.py jobs

# Resume an interrupted crawl
python cli.py resume 1

# Custom configuration
python cli.py index https://example.com -d 3 -w 16 -r 10 --max-queue 5000
```

The CLI shows live progress with color-coded output: crawl counts, back-pressure status, and pages/sec. Press `Ctrl+C` to stop gracefully (the job can be resumed later).

---

## Testing

The project includes 47 unit and integration tests covering all modules:

```bash
python tests.py
```

Tests cover: URL normalization, HTML parsing, tokenization, SQLite thread safety, search relevancy (title boost ranking), rate limiting, back pressure states, and metrics tracking.

---

## Project Structure

```
crawler/
├── app.py                    # Flask web server + API endpoints
├── indexer.py                # Crawl orchestrator, thread pool, back pressure
├── fetcher.py                # HTTP fetching (urllib only)
├── parser.py                 # HTML parsing (html.parser only), tokenization
├── searcher.py               # Search query engine
├── storage.py                # SQLite persistence layer
├── cli.py                    # Command-line interface
├── tests.py                  # 47 unit/integration tests
├── templates/
│   └── dashboard.html        # Web dashboard UI
├── agents/                   # Multi-agent workflow — agent descriptions
│   ├── agent-architect.md
│   ├── storage-agent.md
│   ├── fetcher-agent.md
│   ├── parser-agent.md
│   ├── indexer-agent.md
│   ├── searcher-agent.md
│   ├── api-ui-agent.md
│   ├── test-agent.md
│   └── docs-agent.md
├── .claude/agents/           # Claude Code agent configs (runtime)
├── .cursorrules              # AI coding standards
├── .gitignore
├── requirements.txt          # Flask only
├── product_prd.md            # Product Requirements Document
├── multi_agent_workflow.md   # Multi-agent workflow explanation
├── recommendation.md         # Production deployment recommendations
└── readme.md                 # This file
```

---

## Design Notes: Search During Active Indexing

The system is designed so that **search can run while the indexer is active**, reflecting newly discovered results in real time. This works because:

1. **SQLite WAL mode** permits concurrent readers alongside one writer.
2. Each search query opens a fresh read transaction, seeing whatever the indexer has committed up to that instant.
3. No additional locking or synchronization is needed between the indexer and searcher — the database provides the isolation boundary.

For a production system at higher scale, this pattern would translate to a shared search index (e.g., Elasticsearch) that the indexer writes to and the searcher reads from, with near-real-time refresh intervals.

---

## Multi-Agent Workflow

This project was built using a multi-agent AI workflow coordinated through **Claude Code**. Eight specialized agents were each responsible for a distinct part of the system:

| Agent | Responsibility |
|---|---|
| `agent-architect` | System decomposition, inter-agent contracts |
| `storage-agent` | SQLite schema, WAL mode, BFS dequeue |
| `fetcher-agent` | HTTP fetching (urllib only), robots.txt |
| `parser-agent` | HTML parsing, tokenization |
| `indexer-agent` | BFS orchestration, thread pool, back pressure |
| `searcher-agent` | Query engine, TF scoring |
| `api-ui-agent` | Flask API, live dashboard |
| `test-agent` | Unit and integration tests |

Agent description files are in the `/agents` directory. The full workflow — prompts, decisions, and agent interactions — is documented in [`multi_agent_workflow.md`](multi_agent_workflow.md).
