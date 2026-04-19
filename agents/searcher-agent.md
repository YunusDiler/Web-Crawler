# Agent: Searcher Agent

## Role
Search query specialist. Owns the thin query layer between the REST API and the SQLite term index. Intentionally kept minimal — the scoring logic lives in storage-agent's SQL, and the tokenization logic lives in parser-agent.

## File Owned
`searcher.py`

## Responsibilities
1. **`search(storage, query, sort_by="relevance") -> List[dict]`** — tokenize query, delegate to storage
2. **Query normalization** — calls `parser.tokenize(query)` to apply the same filters used at index time
3. **Result shaping** — returns list of dicts: `{relevant_url, origin_url, depth, score, title}`
4. **Concurrent-read safety** — reads from SQLite WAL mode; no explicit locking needed

## Constraints
- No full-text search libraries (no Whoosh, no Elasticsearch client)
- `tokenize()` from `parser.py` must be used identically as during indexing — no query-specific preprocessing
- The scoring formula lives in `storage.search()` SQL, not here
- Results capped at 50 by storage-agent; searcher does not re-cap

## Search Flow
```
query string
  → tokenize(query)  [parser-agent's tokenize, identical to indexing]
  → storage.search(terms, sort_by)  [SQL: SUM(freq * title_boost) with depth penalty]
  → List[dict] with (relevant_url, origin_url, depth, score, title)
```

## Concurrent Search Design
Search can run while the indexer is active because:
1. SQLite WAL mode allows concurrent readers alongside one writer
2. Each search opens a fresh read transaction — sees all data committed up to that instant
3. No additional locking needed — the database provides the isolation boundary

This is the answer to the assignment question: "how can search be invoked while the indexer is active?" — WAL mode + read transactions give near-real-time search over in-progress crawl data.

## Inputs
- Query string from api-ui-agent or cli
- `CrawlStorage` instance

## Outputs
- List of result dicts for api-ui-agent to serialize as JSON
- Format: `[{relevant_url, origin_url, depth, score, title}, ...]`

## Relevancy Model
Scoring is OR-based (any matching query term contributes score). The formula:
```
score = SUM(frequency * (10 if in_title else 1)) - depth * 5
```
- Title match: 10× multiplier (strong signal that the page is about this topic)
- Depth penalty: slight penalty for pages discovered far from the origin
- Stop words and short terms filtered before lookup — queries like "the web" search only meaningful terms
