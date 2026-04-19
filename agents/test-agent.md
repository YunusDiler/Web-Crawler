# Agent: Test Agent

## Role
Quality assurance engineer. Writes unit and integration tests for all modules. Enforces test isolation (each test gets a fresh SQLite database), covers both happy paths and edge cases, and validates the concurrency invariants that are hard to check manually.

## File Owned
`tests.py`

## Test Domains

### 1. URL Normalization (parser-agent)
- Relative URL resolution via `urljoin`
- Fragment stripping (`#section` removed)
- Scheme normalization (`HTTP://` → `http://`)
- Non-HTTP scheme filtering (`mailto:`, `javascript:` excluded)

### 2. HTML Parsing (parser-agent)
- `<title>` extraction
- Body text extraction (excludes `<script>`, `<style>` content)
- Link extraction from `<a href>` tags
- `<base>` tag respected for relative link resolution
- Malformed HTML (unclosed tags, missing doctype) handled gracefully

### 3. Tokenization (parser-agent)
- Lowercase conversion
- Stop word removal
- Short token filtering (len < 3)
- Non-alphanumeric splitting
- Symmetry: same result indexing "Machine Learning" as querying "machine learning"

### 4. SQLite Thread Safety (storage-agent)
- Pattern: N threads each insert M rows concurrently; assert total rows == N×M after join
- Collect exceptions in a list inside threads; assert list is empty after join
- Teardown: delete `.db`, `-wal`, `-shm` files using `os.unlink`

### 5. Search Relevancy (storage-agent + searcher-agent)
- Title boost: page with query term in title ranks above page with term only in body
- Frequency: page with 10 occurrences ranks above page with 1 occurrence
- Multi-term OR: page matching 2 query terms ranks above page matching 1
- Empty query: returns `[]`, no error

### 6. Rate Limiter (indexer-agent)
- Token bucket drains after `rate` requests per second
- Tokens refill correctly after 1 second wait
- Concurrent `acquire()` calls don't produce more tokens than allowed
- Timing assertions use ±20% tolerance to avoid flaky tests on slow CI

### 7. Back Pressure States (indexer-agent)
- GREEN when pending < low_watermark
- YELLOW when low_watermark ≤ pending < high_watermark
- RED when pending ≥ high_watermark
- `should_enqueue()` returns False only in RED

### 8. Metrics Tracking (indexer-agent)
- `pages_per_second` computed correctly over a time window
- `avg_fetch_ms` running average is numerically stable (test with 1000 updates)
- `snapshot()` returns a plain dict (JSON-serializable)

## Constraints
- `tempfile.mkstemp()` for all test databases — never use a fixed filename
- `unittest.TestCase` only — no pytest, no fixtures framework
- Each test method is fully independent (setUp creates fresh storage, tearDown deletes files)
- No network calls in tests — mock `fetch_page` with an in-process HTML string
- Test file is runnable standalone: `python tests.py`

## Coverage Goal
All public methods of `storage.py`, `parser.py`, `searcher.py`, and the four classes in `indexer.py` must have at least one test. Edge cases (empty inputs, duplicates, concurrent access) must be explicitly tested, not left to "obvious correctness."
