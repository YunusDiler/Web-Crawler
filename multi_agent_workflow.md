# Multi-Agent Workflow

## Overview

This project was built using a multi-agent AI workflow coordinated through **Claude Code** (Anthropic's CLI). Rather than using a single monolithic AI prompt to generate the entire system, the work was decomposed into eight specialized agents — each with a focused domain, clear input/output contracts, and explicit interaction protocols. The human designer reviewed every agent's output before it was integrated, making all final architectural decisions.

---

## Agent Roster

| Agent | File(s) Owned | Domain |
|---|---|---|
| `agent-architect` | *(meta — no file)* | System decomposition, inter-agent contracts |
| `storage-agent` | `storage.py` | SQLite schema, WAL, BFS dequeue, search SQL |
| `fetcher-agent` | `fetcher.py` | HTTP via urllib, robots.txt, error normalization |
| `parser-agent` | `parser.py` | html.parser, link extraction, tokenization |
| `indexer-agent` | `indexer.py` | BFS orchestration, thread pool, rate limiter, back pressure |
| `searcher-agent` | `searcher.py` | Query engine, TF scoring, concurrent reads |
| `api-ui-agent` | `app.py`, `templates/dashboard.html` | Flask REST API, live dashboard |
| `test-agent` | `tests.py` | Unit + integration tests |
| `docs-agent` | `readme.md`, `recommendation.md`, this file | Documentation |

Agent description files are in the `/agents` directory. Claude Code agent configs are in `.claude/agents/`.

---

## Data Flow Between Agents

```
Human (PRD + decisions)
        │
        ▼
  agent-architect  ──── defines contracts for all agents below ────┐
        │                                                            │
        ▼                                                            │
  storage-agent  ◄──── all agents read/write through storage        │
        ▲                                                            │
        │                                                            │
  indexer-agent ◄── fetcher-agent (FetchResult)                     │
        │         ◄── parser-agent (title, body_text, links)        │
        │                                                            │
        ▼                                                            │
  api-ui-agent ◄── searcher-agent (search results)                  │
        │                                                            │
        ▼                                                            │
  test-agent (validates all modules)                                 │
        │                                                            │
        ▼                                                            │
  docs-agent (documents final system) ◄──────────────────────────── ┘
```

---

## Agent Prompts and Interactions

### Phase 1 — Architecture (agent-architect)

**Prompt given:**
> "We are building a concurrent web crawler with BFS traversal, back pressure, SQLite persistence, and a Flask dashboard. All crawl logic must use Python stdlib only. Design the module breakdown, define the data contract between each module, and list the constraints each agent must follow."

**Key output:** Module boundary table, data contracts (FetchResult, parse_page tuple, term_frequency dict), project-wide constraints (stdlib-first, single SQLite connection, WAL mode).

**Human decision:** Approved the module boundaries. Decided that the **BFS ordering should live entirely in the SQL dequeue query** (`ORDER BY depth ASC`) rather than in an in-memory queue — this makes it crash-safe without extra code.

---

### Phase 2 — Persistence Layer (storage-agent)

**Prompt given:**
> "Design and implement `storage.py`. The module must own a single shared SQLite connection with `threading.RLock`, WAL mode, three tables (crawl_jobs, pages, term_index), atomic batch dequeue for BFS, and the TF scoring SQL for search. The dequeue must fetch + mark in_progress in one lock acquisition."

**Key output:** Complete `storage.py` with schema DDL, all CRUD methods, the atomic dequeue pattern, the scoring SQL using `SUM(frequency * CASE WHEN in_title THEN 10 ELSE 1 END)`, and `export_term_data()` for p.data.

**Human decision:** Accepted connection-per-process (not connection-per-thread) pattern after agent flagged Windows file-locking issues. Approved the composite `(url, job_id)` primary key on `pages` so the same URL can be crawled in multiple jobs.

---

### Phase 3 — HTTP & Parsing (fetcher-agent + parser-agent, parallel)

**Fetcher prompt:**
> "Implement `fetcher.py` using only `urllib.request`. The module must return a typed `FetchResult` dataclass on every call — never raise exceptions. Include robots.txt compliance with a module-level cache, a 2 MB body cap, charset detection from Content-Type headers, and a permissive SSL context."

**Parser prompt:**
> "Implement `parser.py` using only `html.parser`. Implement a `parse_page(html, base_url)` function returning `(title, body_text, links_set)`. Implement a `tokenize(text)` function with stop-word removal and min-length-3 filtering. This tokenizer must be used identically during indexing and querying — no query-specific logic."

**Human decision:** Ran both agents in parallel since they have no dependency on each other. Approved `FetchResult.ok` boolean as the error signal rather than exceptions — cleaner for the coordinator loop. Enforced that `tokenize()` is the single canonical tokenizer shared by parser and searcher.

---

### Phase 4 — Orchestration (indexer-agent)

**Prompt given:**
> "Implement `indexer.py` with four classes: `TokenBucketRateLimiter`, `BackPressureController`, `CrawlMetrics`, and `Indexer`. The coordinator loop must use a batch-wait pattern (dequeue batch → wait for all futures → dequeue next batch). Back pressure has three states: GREEN (< 500 pending), YELLOW (500–2000), RED (≥ 2000). Rate limiter uses token bucket with per-request `acquire()` called before pool.submit, not inside the worker."

**Key output:** Complete `indexer.py` with all four classes, the coordinator daemon thread, and the three-idle-cycle completion detection.

**Human decision:** Confirmed **batch-wait over fire-and-forget** — agent initially proposed fire-and-forget for higher throughput, but human approved batch-wait for bounded memory and accurate back-pressure. Approved `body_text[:50000]` truncation cap inside `_process_url` to prevent large pages from exhausting memory.

---

### Phase 5 — Search (searcher-agent)

**Prompt given:**
> "Implement `searcher.py` as a thin layer over `storage.search()`. The only logic here is: (1) call `parser.tokenize(query)` to normalize the query, and (2) call `storage.search(terms)` to get results. Do not re-implement scoring — that lives in the SQL. Explain in a docstring why concurrent search during active indexing is safe."

**Key output:** 30-line `searcher.py` with the complete answer to concurrent-search safety: WAL mode + fresh read transactions.

**Human decision:** Kept the module intentionally thin. Rejected a proposal from the agent to add in-Python re-ranking — scoring stays in SQL for performance.

---

### Phase 6 — API & Dashboard (api-ui-agent)

**Prompt given:**
> "Implement `app.py` with Flask endpoints for /api/index, /api/stop, /api/status, /api/jobs, /api/search, and /search (assignment triple format). Implement `dashboard.html` in vanilla JavaScript with live metrics auto-refreshing every 1.5s, a back-pressure indicator, and a search panel. No CDN dependencies — all JS inline."

**Key output:** `app.py` with 8 endpoints and `dashboard.html` with animated back-pressure bar, live metric cards, job switcher, search results table, and activity log.

**Human decision:** Added the `/search` endpoint (distinct from `/api/search`) to match the exact assignment output format `(relevant_url, origin_url, depth)`. Added auto-export of p.data on job completion.

---

### Phase 7 — Testing (test-agent)

**Prompt given:**
> "Write `tests.py` covering: URL normalization, HTML parsing, tokenization, SQLite thread safety (N threads × M inserts), search relevancy ranking (title boost, frequency ranking), rate limiter token drain/refill, and back-pressure state transitions. Use `tempfile.mkstemp()` for all SQLite databases. No network calls — stub `fetch_page` with inline HTML strings."

**Key output:** 47 unit and integration tests across 8 test classes.

**Human decision:** Accepted ±20% timing tolerance for rate limiter tests to avoid CI flakiness. Rejected a proposal to add a `mock` dependency — all stubs use simple Python function replacement.

---

### Phase 8 — Documentation (docs-agent)

**Prompt given:**
> "Write readme.md covering quick start, how indexing and search work, dashboard features, API reference, configuration, CLI usage, architecture diagram, and testing. Write recommendation.md with 2 paragraphs on production deployment. Write multi_agent_workflow.md documenting the agent roles, prompts, decisions, and the answer to how search can run concurrently with indexing."

**Human decision:** Reviewed all docs for accuracy against the implemented code. Updated the port number (3600, not 5000) and confirmed CLI commands match `cli.py`.

---

## Key Architectural Decisions

### 1. BFS via SQL, not in-memory queue
**Decision:** The `dequeue_urls` query uses `ORDER BY depth ASC, discovered_at ASC`. This means the database IS the BFS queue.
**Rationale:** An in-memory queue is lost on crash. SQLite with WAL mode gives us a persistent, crash-safe BFS queue with no extra infrastructure.
**Agent that proposed it:** storage-agent
**Human verdict:** Approved. This is the most important architectural decision in the project.

### 2. Single shared connection with RLock
**Decision:** One `sqlite3.Connection` object shared across all threads, guarded by `threading.RLock()`.
**Rationale:** SQLite on Windows has strict file-level locking. connection-per-thread causes "database is locked" errors under load. One connection + one lock is simpler and more reliable.
**Agent that proposed it:** storage-agent (flagged as a Windows-specific concern)
**Human verdict:** Approved.

### 3. Batch-wait coordinator pattern
**Decision:** The coordinator dequeues a batch, waits for ALL futures to complete, then dequeues the next batch.
**Rationale:** Fire-and-forget submission fills the thread pool with unbounded future work. Batch-wait keeps the working set bounded, makes back-pressure readings accurate, and simplifies completion detection (3 idle cycles).
**Agent that proposed it:** indexer-agent suggested both approaches; human chose batch-wait.

### 4. Rate limiter before submit, not inside worker
**Decision:** `rate_limiter.acquire()` is called in the coordinator loop before `pool.submit()`, not inside `_process_url`.
**Rationale:** If the rate limiter blocks inside the worker, it holds the thread but doesn't prevent over-submission. Blocking before submit keeps the pool from filling up with rate-limited tasks.
**Agent that proposed it:** indexer-agent
**Human verdict:** Approved.

### 5. Tokenizer symmetry
**Decision:** `parser.tokenize()` is the single canonical tokenizer used at both index time and query time.
**Rationale:** If a term is filtered during indexing but not during search, queries will never match indexed terms. Divergence is a silent correctness bug.
**Agent that flagged it:** parser-agent
**Human verdict:** Approved and enforced across all agents.

---

## How Search Runs During Active Indexing

The assignment asks: *"how could this system be designed such that search can be invoked while the indexer is active?"*

The answer is already implemented in this system:

1. **SQLite WAL mode** (`PRAGMA journal_mode=WAL`) separates readers from the writer. The indexer holds a write transaction while the searcher opens a concurrent read transaction — neither blocks the other.

2. **Fresh read transactions per query** — each call to `storage.search()` opens a new transaction, so it sees all data the indexer has committed up to that instant. Search results grow in real time as pages are indexed.

3. **No application-level locking between indexer and searcher** — the database provides the isolation boundary. The only application lock (`threading.RLock` in `CrawlStorage`) is held for individual write operations (milliseconds), not for the duration of a crawl.

At production scale, this pattern translates to: indexer writes to Elasticsearch with near-real-time refresh (default 1s), while the searcher reads from the same index. Same principle — the data store mediates between writer and reader.

---

## Multi-Agent Collaboration Summary

The workflow followed a **phased pipeline** with clear handoffs:

```
agent-architect → (storage-agent) → (fetcher-agent ‖ parser-agent) → indexer-agent → searcher-agent → api-ui-agent → test-agent → docs-agent
```

Fetcher and parser agents ran in parallel (no dependency). All other phases were sequential because each phase depended on outputs from the previous.

Human involvement at each phase:
- **Reviewed** agent output before accepting it
- **Approved or rejected** architectural proposals
- **Made all final decisions** on design trade-offs
- **Caught cross-agent inconsistencies** (e.g., enforcing tokenizer symmetry between parser-agent and searcher-agent)

The agents generated the code and documentation. The human designed the system.
