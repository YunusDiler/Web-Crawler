# Agent: Indexer Agent

## Role
Crawl orchestration systems engineer. Owns the BFS coordinator, thread pool, rate limiter, and back-pressure controller. Coordinates all other agents' outputs into a controlled, resumable crawl pipeline.

## File Owned
`indexer.py`

## Responsibilities
1. **`TokenBucketRateLimiter`** — global request rate cap, thread-safe `acquire()` with 50ms sleep loop
2. **`BackPressureController`** — GREEN/YELLOW/RED states based on pending queue depth vs watermarks
3. **`CrawlMetrics`** — thread-safe running stats: pages/sec, avg fetch ms, counts
4. **`Indexer.start(origin, k, resume=True) -> int`** — non-blocking; spawns coordinator daemon thread
5. **`Indexer.stop(job_id)`** — sets stop event; coordinator finishes current batch and exits
6. **`Indexer._run_crawl(job_id, max_depth, stop_event)`** — coordinator loop
7. **`Indexer._process_url(url, depth, job_id, max_depth)`** — per-URL worker function

## Constraints
- `threading`, `concurrent.futures.ThreadPoolExecutor`, `time`, `logging`, `collections.deque` only
- No asyncio, no Celery, no external task queues
- Coordinator loop in a single daemon thread; workers in ThreadPoolExecutor
- Default config: `max_workers=8`, `requests_per_second=5.0`, `max_queue_depth=2000`, `batch_size=10`

## Back-Pressure States
| State | Condition | Behavior |
|---|---|---|
| GREEN | pending < 500 | Enqueue all child links |
| YELLOW | 500 ≤ pending < 2000 | Enqueue but log warning |
| RED | pending ≥ 2000 | Skip all new link discovery |

## Coordinator Loop Pattern
```
loop:
  batch = storage.dequeue_urls(batch_size)
  if empty: idle_count += 1; if idle_count >= 3 and in_progress == 0: done
  else: idle_count = 0
  futures = [pool.submit(_process_url, url, depth, ...) for url, depth in batch]
  for f in futures: f.result(timeout=60)  # wait for batch before next dequeue
```
This **batch-wait** pattern is intentional — keeps memory bounded and makes back-pressure accurate.

## Per-URL Worker Sequence
1. `is_allowed_by_robots(url)` → skip if disallowed
2. `rate_limiter.acquire()` → wait for token
3. `fetch_page(url)` → get FetchResult
4. `storage.mark_crawled(url, ...)` or `storage.mark_failed(url, ...)`
5. `tokenize(body_text)` + `compute_term_frequencies` → `storage.index_terms(...)`
6. For each link in parsed links: if depth < max_depth and bp.should_enqueue(): `storage.enqueue_url(link, depth+1, ...)`

## Inputs
- `start(origin, k)` call from api-ui-agent or cli
- `FetchResult` from fetcher-agent
- `(title, body_text, links)` from parser-agent
- URL batches from storage-agent

## Outputs
- Status dict for api-ui-agent: `{queued, in_progress, crawled, failed, backpressure_state, metrics}`
- Write calls to storage-agent

## Resume Logic
`start()` with `resume=True` finds any `paused` job with matching origin+depth, calls `reset_in_progress()`, and continues from where it left off. New job created only if no resumable job exists.
