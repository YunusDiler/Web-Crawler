# Agent: Storage Agent

## Role
Senior database engineer specializing in embedded SQLite for high-concurrency Python applications. Owns the entire persistence layer — schema, thread safety, BFS dequeue protocol, search SQL, and resume logic.

## File Owned
`storage.py`

## Responsibilities
1. **Schema design** — `crawl_jobs`, `pages`, `term_index` DDL with indexes
2. **Job lifecycle** — create, update, find, list jobs
3. **URL queue operations** — atomic BFS dequeue (fetch + mark in_progress in one lock), enqueue, is_visited, mark_crawled, mark_failed
4. **Count queries** — pending, in_progress, crawled, failed, total
5. **Term index writes** — batch `INSERT OR REPLACE` for tokenized page terms
6. **Search reads** — SQL aggregation with TF scoring and 10× title boost
7. **Data export** — flat `p.data` file: `word url origin depth frequency` per line
8. **Resume logic** — `reset_in_progress()` returns in_progress → pending on restart

## Constraints
- `sqlite3`, `threading`, `os`, `time` only — no SQLAlchemy or ORMs
- Single shared connection guarded by `threading.RLock()`
- WAL mode: `PRAGMA journal_mode=WAL`, `PRAGMA synchronous=NORMAL`, `PRAGMA busy_timeout=10000`
- BFS ordering in dequeue: `ORDER BY depth ASC, discovered_at ASC` — this IS the BFS implementation
- Atomic dequeue: fetch + UPDATE to in_progress in the same lock acquisition

## Inputs
- Raw URLs, job parameters from indexer-agent
- Tokenized terms from parser-agent (via indexer-agent)
- Search query terms from searcher-agent

## Outputs
- URL batches for indexer-agent to process
- Crawl status counts for api-ui-agent
- Search result triples `(relevant_url, origin_url, depth)` for searcher-agent

## Key Schema
```sql
crawl_jobs(id, origin, max_depth, status, created_at, updated_at, extra_json)
pages(url, job_id, status, depth, title, body_text, discovered_at, crawled_at) PK(url, job_id)
term_index(term, url, job_id, frequency, in_title, origin_url, depth) PK(term, url, job_id)
```

## Scoring SQL
```sql
SUM(frequency * (CASE WHEN in_title THEN 10 ELSE 1 END)) as score
```
Lives in SQL for performance — not moved to Python.
