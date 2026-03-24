"""
indexer.py — Core crawl engine with back pressure and concurrency control.

Architecture:
  - A thread pool of N workers fetches pages concurrently.
  - A bounded work queue enforces back pressure (max queue depth).
  - A token-bucket rate limiter caps requests per second.
  - BFS ordering is maintained via depth-ordered dequeue from SQLite.
  - The crawl can be paused/resumed; state is persisted in SQLite.

Design note on concurrent search:
  Because all crawl results are committed to SQLite as they are discovered,
  the search module can query the DB at any time and will see partial results.
  SQLite's WAL mode allows concurrent readers alongside a single writer,
  making search-while-indexing safe without additional coordination.
"""

import threading
import time
import logging
from concurrent.futures import ThreadPoolExecutor, Future
from typing import Optional, Callable
from collections import deque

from storage import CrawlStorage
from fetcher import fetch_page, is_allowed_by_robots
from parser import parse_page, tokenize, compute_term_frequencies

logger = logging.getLogger(__name__)


class TokenBucketRateLimiter:
    """
    Token-bucket rate limiter.
    Allows `rate` requests per second with burst capacity up to `rate`.
    Thread-safe via a lock.
    """

    def __init__(self, rate: float):
        self.rate = rate              # tokens per second
        self.capacity = rate          # max burst
        self.tokens = rate            # current tokens
        self.last_refill = time.monotonic()
        self._lock = threading.Lock()

    def acquire(self, timeout: float = 30) -> bool:
        """Block until a token is available or timeout expires."""
        deadline = time.monotonic() + timeout
        while True:
            with self._lock:
                self._refill()
                if self.tokens >= 1:
                    self.tokens -= 1
                    return True
            # Wait a bit before retrying
            if time.monotonic() > deadline:
                return False
            time.sleep(0.05)

    def _refill(self):
        now = time.monotonic()
        elapsed = now - self.last_refill
        self.tokens = min(self.capacity, self.tokens + elapsed * self.rate)
        self.last_refill = now

    @property
    def available_tokens(self) -> float:
        with self._lock:
            self._refill()
            return self.tokens


class BackPressureController:
    """
    Monitors queue depth and applies back pressure signals.

    Thresholds:
      - queue_depth < low_watermark:  GREEN  (full speed)
      - low_watermark <= depth < high_watermark: YELLOW (reduced concurrency)
      - depth >= high_watermark:      RED    (pause enqueueing new links)
    """

    def __init__(self, low_watermark: int = 500, high_watermark: int = 2000):
        self.low_watermark = low_watermark
        self.high_watermark = high_watermark
        self._current_depth = 0
        self._lock = threading.Lock()

    def update(self, queue_depth: int):
        with self._lock:
            self._current_depth = queue_depth

    @property
    def status(self) -> str:
        with self._lock:
            if self._current_depth >= self.high_watermark:
                return "RED"
            elif self._current_depth >= self.low_watermark:
                return "YELLOW"
            return "GREEN"

    @property
    def depth(self) -> int:
        with self._lock:
            return self._current_depth

    def should_enqueue(self) -> bool:
        """Whether it's safe to add new URLs to the queue."""
        return self.status != "RED"


class CrawlMetrics:
    """Thread-safe crawl statistics for the UI."""

    def __init__(self):
        self._lock = threading.Lock()
        self.pages_fetched = 0
        self.pages_failed = 0
        self.links_discovered = 0
        self.links_skipped_duplicate = 0
        self.links_skipped_robots = 0
        self.links_skipped_backpressure = 0
        self.start_time: Optional[float] = None
        self.last_fetch_time: Optional[float] = None
        self.avg_fetch_ms: float = 0

    def record_fetch(self, elapsed_ms: float, success: bool):
        with self._lock:
            if success:
                self.pages_fetched += 1
            else:
                self.pages_failed += 1
            self.last_fetch_time = time.time()
            # Running average
            total = self.pages_fetched + self.pages_failed
            self.avg_fetch_ms = (
                (self.avg_fetch_ms * (total - 1) + elapsed_ms) / total
            )

    def record_links(self, discovered: int, dup: int, robots: int, bp: int):
        with self._lock:
            self.links_discovered += discovered
            self.links_skipped_duplicate += dup
            self.links_skipped_robots += robots
            self.links_skipped_backpressure += bp

    def snapshot(self) -> dict:
        with self._lock:
            elapsed = 0
            if self.start_time:
                elapsed = time.time() - self.start_time
            return {
                "pages_fetched": self.pages_fetched,
                "pages_failed": self.pages_failed,
                "links_discovered": self.links_discovered,
                "links_skipped_duplicate": self.links_skipped_duplicate,
                "links_skipped_robots": self.links_skipped_robots,
                "links_skipped_backpressure": self.links_skipped_backpressure,
                "avg_fetch_ms": round(self.avg_fetch_ms, 1),
                "elapsed_seconds": round(elapsed, 1),
                "pages_per_second": round(
                    (self.pages_fetched + self.pages_failed) / max(elapsed, 0.1), 2
                ),
            }


class Indexer:
    """
    The main crawl orchestrator.

    Usage:
        indexer = Indexer(storage)
        job_id = indexer.start("https://example.com", k=2)
        # ... search can run concurrently ...
        indexer.wait(job_id)
    """

    def __init__(
        self,
        storage: CrawlStorage,
        max_workers: int = 8,
        requests_per_second: float = 5.0,
        max_queue_depth: int = 2000,
        batch_size: int = 10,
    ):
        self.storage = storage
        self.max_workers = max_workers
        self.batch_size = batch_size

        self.rate_limiter = TokenBucketRateLimiter(rate=requests_per_second)
        self.backpressure = BackPressureController(
            low_watermark=max_queue_depth // 4,
            high_watermark=max_queue_depth,
        )
        self.metrics = CrawlMetrics()

        # Active job tracking
        self._active_jobs: dict = {}  # job_id -> threading.Event (stop signal)
        self._lock = threading.Lock()

    def start(self, origin: str, k: int, resume: bool = True) -> int:
        """
        Start (or resume) a crawl job.
        Returns the job_id immediately; crawl runs in background threads.
        """
        # Check for resumable job
        job_id = None
        if resume:
            job_id = self.storage.find_resumable_job(origin, k)

        if job_id:
            logger.info(f"Resuming job {job_id} for {origin}")
            self.storage.reset_in_progress(job_id)
            self.storage.update_job_status(job_id, "active")
        else:
            job_id = self.storage.create_job(origin, k)
            # Seed the origin URL at depth 0
            self.storage.enqueue_url(origin, job_id, 0)
            logger.info(f"Created job {job_id} for {origin} (depth={k})")

        stop_event = threading.Event()
        with self._lock:
            self._active_jobs[job_id] = stop_event

        self.metrics.start_time = time.time()

        # Launch the coordinator thread
        coordinator = threading.Thread(
            target=self._run_crawl,
            args=(job_id, k, stop_event),
            daemon=True,
            name=f"crawl-coord-{job_id}",
        )
        coordinator.start()
        return job_id

    def stop(self, job_id: int):
        """Signal a running crawl to stop gracefully."""
        with self._lock:
            event = self._active_jobs.get(job_id)
        if event:
            event.set()
            logger.info(f"Stop signal sent to job {job_id}")

    def is_running(self, job_id: int) -> bool:
        with self._lock:
            event = self._active_jobs.get(job_id)
        if event is None:
            return False
        return not event.is_set()

    def get_status(self, job_id: int) -> dict:
        """Return a status snapshot for the UI."""
        job = self.storage.get_job(job_id)
        if not job:
            return {"error": "job not found"}

        pending = self.storage.pending_count(job_id)
        in_progress = self.storage.in_progress_count(job_id)
        crawled = self.storage.crawled_count(job_id)
        failed = self.storage.failed_count(job_id)
        total = self.storage.total_count(job_id)

        self.backpressure.update(pending)

        return {
            "job_id": job_id,
            "origin": job["origin"],
            "max_depth": job["max_depth"],
            "job_status": job["status"],
            "is_running": self.is_running(job_id),
            "queue": {
                "pending": pending,
                "in_progress": in_progress,
                "crawled": crawled,
                "failed": failed,
                "total_discovered": total,
            },
            "backpressure": {
                "status": self.backpressure.status,
                "queue_depth": pending,
                "high_watermark": self.backpressure.high_watermark,
                "low_watermark": self.backpressure.low_watermark,
            },
            "rate_limiter": {
                "available_tokens": round(self.rate_limiter.available_tokens, 1),
                "rate": self.rate_limiter.rate,
            },
            "metrics": self.metrics.snapshot(),
        }

    def _run_crawl(self, job_id: int, max_depth: int, stop_event: threading.Event):
        """
        Coordinator loop: dequeues batches of URLs, dispatches to thread pool.
        Runs until no more pending work or stop_event is set.
        """
        logger.info(f"Crawl coordinator started for job {job_id}")

        with ThreadPoolExecutor(
            max_workers=self.max_workers,
            thread_name_prefix=f"crawl-{job_id}",
        ) as pool:
            idle_cycles = 0

            while not stop_event.is_set():
                # Dequeue a batch
                batch = self.storage.dequeue_urls(job_id, self.batch_size)

                if not batch:
                    # Check if anything is still in_progress
                    if self.storage.in_progress_count(job_id) == 0:
                        idle_cycles += 1
                        if idle_cycles >= 3:
                            # No pending, no in_progress → done
                            break
                    time.sleep(0.5)
                    continue

                idle_cycles = 0

                # Dispatch each URL to the pool
                futures = []
                for item in batch:
                    if stop_event.is_set():
                        break
                    # Rate limit: block until a token is available
                    self.rate_limiter.acquire()
                    f = pool.submit(
                        self._process_url,
                        item["url"],
                        item["depth"],
                        job_id,
                        max_depth,
                    )
                    futures.append(f)

                # Wait for this batch to finish before dequeuing more
                # (this keeps memory bounded and backpressure accurate)
                for f in futures:
                    try:
                        f.result(timeout=60)
                    except Exception as e:
                        logger.error(f"Worker exception: {e}")

                # Update backpressure after batch
                self.backpressure.update(self.storage.pending_count(job_id))

        # Mark job complete or paused
        final_status = "paused" if stop_event.is_set() else "completed"
        self.storage.update_job_status(job_id, final_status)
        with self._lock:
            self._active_jobs.pop(job_id, None)
        logger.info(f"Job {job_id} finished with status: {final_status}")

    def _process_url(self, url: str, depth: int, job_id: int, max_depth: int):
        """
        Fetch a single URL, parse it, index terms, enqueue child links.
        Runs inside a worker thread.
        """
        # Check robots.txt
        if not is_allowed_by_robots(url):
            self.storage.mark_failed(url, job_id)
            self.metrics.record_fetch(0, False)
            return

        # Fetch
        result = fetch_page(url)
        if result.error:
            self.storage.mark_failed(url, job_id)
            self.metrics.record_fetch(result.elapsed_ms, False)
            logger.debug(f"Failed: {url} — {result.error}")
            return

        # Parse
        title, body_text, links = parse_page(result.html, result.final_url)

        # Store page content
        self.storage.mark_crawled(url, job_id, title, body_text[:50000])

        # Index terms for search
        title_tokens = tokenize(title)
        body_tokens = tokenize(body_text[:50000])
        all_tokens = title_tokens + body_tokens
        freq = {}
        for t in all_tokens:
            freq[t] = freq.get(t, 0) + 1
        title_set = set(title_tokens)
        if freq:
            self.storage.index_terms(url, job_id, freq, title_set)

        self.metrics.record_fetch(result.elapsed_ms, True)

        # Enqueue child links (if within depth)
        if depth < max_depth:
            new_depth = depth + 1
            dup = 0
            robots_blocked = 0
            bp_blocked = 0
            enqueued = 0

            for link in links:
                # Back pressure check
                if not self.backpressure.should_enqueue():
                    bp_blocked += len(links) - (dup + robots_blocked + enqueued + bp_blocked)
                    break

                # Deduplicate via DB (the visited set)
                if not self.storage.enqueue_url(link, job_id, new_depth):
                    dup += 1
                else:
                    enqueued += 1

            self.metrics.record_links(enqueued, dup, robots_blocked, bp_blocked)
