"""
storage.py — Thread-safe SQLite persistence layer.

All crawl state (visited URLs, page content, index terms, job metadata)
lives here so the system can resume after interruption.
"""

import sqlite3
import threading
import time
import os
from typing import Optional, List, Tuple, Dict


class CrawlStorage:
    """
    Thread-safe SQLite storage using a connection-per-thread model.
    SQLite supports concurrent reads; writes are serialized via WAL mode.
    """

    def __init__(self, db_path: str = "crawler.db"):
        self._db_path = db_path
        self._local = threading.local()
        self._init_lock = threading.Lock()
        self._initialize_schema()

    def _get_conn(self) -> sqlite3.Connection:
        """Return a thread-local SQLite connection."""
        if not hasattr(self._local, "conn") or self._local.conn is None:
            conn = sqlite3.connect(self._db_path, timeout=30)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA busy_timeout=5000")
            conn.row_factory = sqlite3.Row
            self._local.conn = conn
        return self._local.conn

    def _initialize_schema(self):
        """Create tables if they don't exist."""
        with self._init_lock:
            conn = self._get_conn()
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS crawl_jobs (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    origin      TEXT    NOT NULL,
                    max_depth   INTEGER NOT NULL,
                    status      TEXT    NOT NULL DEFAULT 'active',
                    created_at  REAL    NOT NULL,
                    updated_at  REAL    NOT NULL
                );

                CREATE TABLE IF NOT EXISTS pages (
                    url         TEXT    NOT NULL,
                    job_id      INTEGER NOT NULL,
                    depth       INTEGER NOT NULL,
                    title       TEXT    DEFAULT '',
                    body_text   TEXT    DEFAULT '',
                    status      TEXT    NOT NULL DEFAULT 'pending',
                    discovered_at REAL  NOT NULL,
                    crawled_at  REAL,
                    PRIMARY KEY (url, job_id),
                    FOREIGN KEY (job_id) REFERENCES crawl_jobs(id)
                );

                CREATE TABLE IF NOT EXISTS term_index (
                    term        TEXT    NOT NULL,
                    url         TEXT    NOT NULL,
                    job_id      INTEGER NOT NULL,
                    frequency   INTEGER NOT NULL DEFAULT 0,
                    in_title    INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (term, url, job_id)
                );

                CREATE INDEX IF NOT EXISTS idx_pages_status
                    ON pages(job_id, status);
                CREATE INDEX IF NOT EXISTS idx_pages_job_depth
                    ON pages(job_id, depth);
                CREATE INDEX IF NOT EXISTS idx_term_index_term
                    ON term_index(term);
            """)
            conn.commit()

    # ── Job Management ───────────────────────────────────────────────

    def create_job(self, origin: str, max_depth: int) -> int:
        conn = self._get_conn()
        now = time.time()
        cur = conn.execute(
            "INSERT INTO crawl_jobs (origin, max_depth, status, created_at, updated_at) "
            "VALUES (?, ?, 'active', ?, ?)",
            (origin, max_depth, now, now),
        )
        conn.commit()
        return cur.lastrowid

    def update_job_status(self, job_id: int, status: str):
        conn = self._get_conn()
        conn.execute(
            "UPDATE crawl_jobs SET status=?, updated_at=? WHERE id=?",
            (status, time.time(), job_id),
        )
        conn.commit()

    def get_job(self, job_id: int) -> Optional[dict]:
        conn = self._get_conn()
        row = conn.execute("SELECT * FROM crawl_jobs WHERE id=?", (job_id,)).fetchone()
        return dict(row) if row else None

    def get_all_jobs(self) -> List[dict]:
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM crawl_jobs ORDER BY created_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]

    def find_resumable_job(self, origin: str, max_depth: int) -> Optional[int]:
        """Find an existing active/paused job for the same origin and depth."""
        conn = self._get_conn()
        row = conn.execute(
            "SELECT id FROM crawl_jobs WHERE origin=? AND max_depth=? "
            "AND status IN ('active','paused') ORDER BY created_at DESC LIMIT 1",
            (origin, max_depth),
        ).fetchone()
        return row["id"] if row else None

    # ── Page / URL Management ────────────────────────────────────────

    def is_visited(self, url: str, job_id: int) -> bool:
        conn = self._get_conn()
        row = conn.execute(
            "SELECT 1 FROM pages WHERE url=? AND job_id=?", (url, job_id)
        ).fetchone()
        return row is not None

    def enqueue_url(self, url: str, job_id: int, depth: int) -> bool:
        conn = self._get_conn()
        try:
            conn.execute(
                "INSERT INTO pages (url, job_id, depth, status, discovered_at) "
                "VALUES (?, ?, ?, 'pending', ?)",
                (url, job_id, depth, time.time()),
            )
            conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False

    def dequeue_urls(self, job_id: int, batch_size: int = 10) -> List[dict]:
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT url, depth FROM pages "
            "WHERE job_id=? AND status='pending' "
            "ORDER BY depth ASC, discovered_at ASC LIMIT ?",
            (job_id, batch_size),
        ).fetchall()

        urls = [(r["url"], r["depth"]) for r in rows]
        if urls:
            placeholders = ",".join("?" for _ in urls)
            url_list = [u for u, _ in urls]
            conn.execute(
                f"UPDATE pages SET status='in_progress' "
                f"WHERE job_id=? AND url IN ({placeholders})",
                [job_id] + url_list,
            )
            conn.commit()
        return [{"url": u, "depth": d} for u, d in urls]

    def mark_crawled(self, url: str, job_id: int, title: str, body_text: str):
        conn = self._get_conn()
        conn.execute(
            "UPDATE pages SET status='crawled', title=?, body_text=?, crawled_at=? "
            "WHERE url=? AND job_id=?",
            (title, body_text, time.time(), url, job_id),
        )
        conn.commit()

    def mark_failed(self, url: str, job_id: int):
        conn = self._get_conn()
        conn.execute(
            "UPDATE pages SET status='failed', crawled_at=? WHERE url=? AND job_id=?",
            (time.time(), url, job_id),
        )
        conn.commit()

    def pending_count(self, job_id: int) -> int:
        conn = self._get_conn()
        row = conn.execute(
            "SELECT COUNT(*) as c FROM pages WHERE job_id=? AND status='pending'",
            (job_id,),
        ).fetchone()
        return row["c"]

    def in_progress_count(self, job_id: int) -> int:
        conn = self._get_conn()
        row = conn.execute(
            "SELECT COUNT(*) as c FROM pages WHERE job_id=? AND status='in_progress'",
            (job_id,),
        ).fetchone()
        return row["c"]

    def crawled_count(self, job_id: int) -> int:
        conn = self._get_conn()
        row = conn.execute(
            "SELECT COUNT(*) as c FROM pages WHERE job_id=? AND status='crawled'",
            (job_id,),
        ).fetchone()
        return row["c"]

    def failed_count(self, job_id: int) -> int:
        conn = self._get_conn()
        row = conn.execute(
            "SELECT COUNT(*) as c FROM pages WHERE job_id=? AND status='failed'",
            (job_id,),
        ).fetchone()
        return row["c"]

    def total_count(self, job_id: int) -> int:
        conn = self._get_conn()
        row = conn.execute(
            "SELECT COUNT(*) as c FROM pages WHERE job_id=?", (job_id,)
        ).fetchone()
        return row["c"]

    # ── Term Index ───────────────────────────────────────────────────

    def index_terms(self, url: str, job_id: int, terms: Dict[str, int], title_terms: set):
        """
        Bulk-insert term frequencies for a page.
        terms: {term: frequency}
        title_terms: set of terms that appeared in the page title
        """
        conn = self._get_conn()
        rows = [
            (term, url, job_id, freq, 1 if term in title_terms else 0)
            for term, freq in terms.items()
        ]
        conn.executemany(
            "INSERT OR REPLACE INTO term_index (term, url, job_id, frequency, in_title) "
            "VALUES (?, ?, ?, ?, ?)",
            rows,
        )
        conn.commit()

    def search(self, query_terms: List[str], sort_by: str = "relevance") -> List[dict]:
        """
        Search the index for pages matching query terms.

        Scoring formula (per the assignment):
            score = (frequency × 10) + 1000 (exact match bonus) - (depth × 5)

        Each matching term contributes its own (freq*10 + 1000) and the depth
        penalty is applied once per page.

        Returns: [{"url", "origin", "depth", "frequency", "relevance_score"}]
        """
        if not query_terms:
            return []

        conn = self._get_conn()
        placeholders = ",".join("?" for _ in query_terms)

        # Score breakdown:
        #   For each matching term on a page: (frequency * 10) + 1000
        #   Summed across all matching terms, then subtract (depth * 5) once.
        query = f"""
            SELECT
                ti.url,
                cj.origin,
                p.depth,
                SUM(ti.frequency) as total_frequency,
                (SUM(ti.frequency * 10 + 1000) - p.depth * 5) as relevance_score
            FROM term_index ti
            JOIN pages p ON p.url = ti.url AND p.job_id = ti.job_id
            JOIN crawl_jobs cj ON cj.id = ti.job_id
            WHERE ti.term IN ({placeholders})
              AND p.status = 'crawled'
            GROUP BY ti.url, cj.origin, p.depth
            ORDER BY relevance_score DESC
            LIMIT 50
        """
        rows = conn.execute(query, query_terms).fetchall()
        return [
            {
                "url": r["url"],
                "origin": r["origin"],
                "depth": r["depth"],
                "frequency": r["total_frequency"],
                "relevance_score": r["relevance_score"],
            }
            for r in rows
        ]

    # ── Data Export ──────────────────────────────────────────────────

    def export_term_data(self, output_path: str = "data/storage/p.data"):
        """
        Export the term index to a flat file for inspection.
        Each line: word url origin depth frequency

        This is the raw storage file the assignment asks you to inspect.
        """
        conn = self._get_conn()
        rows = conn.execute("""
            SELECT
                ti.term,
                ti.url,
                cj.origin,
                p.depth,
                ti.frequency
            FROM term_index ti
            JOIN pages p ON p.url = ti.url AND p.job_id = ti.job_id
            JOIN crawl_jobs cj ON cj.id = ti.job_id
            WHERE p.status = 'crawled'
            ORDER BY ti.term, ti.url
        """).fetchall()

        os.makedirs(os.path.dirname(output_path), exist_ok=True)

        with open(output_path, "w", encoding="utf-8") as f:
            for r in rows:
                f.write(f"{r['term']} {r['url']} {r['origin']} {r['depth']} {r['frequency']}\n")

        return len(rows)

    # ── Cleanup ──────────────────────────────────────────────────────

    def reset_in_progress(self, job_id: int):
        conn = self._get_conn()
        conn.execute(
            "UPDATE pages SET status='pending' WHERE job_id=? AND status='in_progress'",
            (job_id,),
        )
        conn.commit()

    def close(self):
        if hasattr(self._local, "conn") and self._local.conn:
            self._local.conn.close()
            self._local.conn = None
