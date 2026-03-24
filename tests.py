"""
tests.py — Unit and integration tests for the crawler system.

Run:  python -m pytest tests.py -v
  or: python tests.py
"""

import os
import time
import unittest
import threading
import tempfile

from storage import CrawlStorage
from parser import (
    parse_page, normalize_url, tokenize,
    compute_term_frequencies, _is_crawlable_url,
)
from searcher import search
from indexer import TokenBucketRateLimiter, BackPressureController, CrawlMetrics


class TestNormalizeUrl(unittest.TestCase):
    """Test URL normalization and resolution."""

    def test_relative_path(self):
        result = normalize_url("/about", "https://example.com/page")
        self.assertEqual(result, "https://example.com/about")

    def test_relative_path_no_leading_slash(self):
        result = normalize_url("about", "https://example.com/dir/page")
        self.assertEqual(result, "https://example.com/dir/about")

    def test_absolute_url(self):
        result = normalize_url("https://other.com/path", "https://example.com")
        self.assertEqual(result, "https://other.com/path")

    def test_strips_fragment(self):
        result = normalize_url("/page#section", "https://example.com")
        self.assertEqual(result, "https://example.com/page")

    def test_preserves_query(self):
        result = normalize_url("/search?q=hello", "https://example.com")
        self.assertEqual(result, "https://example.com/search?q=hello")

    def test_rejects_javascript_urls(self):
        result = normalize_url("javascript:void(0)", "https://example.com")
        self.assertEqual(result, "")

    def test_rejects_mailto(self):
        result = normalize_url("mailto:test@example.com", "https://example.com")
        self.assertEqual(result, "")

    def test_lowercases_netloc(self):
        result = normalize_url("https://EXAMPLE.COM/Path", "https://example.com")
        self.assertEqual(result, "https://example.com/Path")

    def test_adds_slash_for_empty_path(self):
        result = normalize_url("https://example.com", "https://other.com")
        self.assertEqual(result, "https://example.com/")


class TestIsCrawlableUrl(unittest.TestCase):
    """Test the file extension filter."""

    def test_html_pages(self):
        self.assertTrue(_is_crawlable_url("https://example.com/page"))
        self.assertTrue(_is_crawlable_url("https://example.com/path/to/page"))
        self.assertTrue(_is_crawlable_url("https://example.com/"))

    def test_skips_images(self):
        self.assertFalse(_is_crawlable_url("https://example.com/image.jpg"))
        self.assertFalse(_is_crawlable_url("https://example.com/image.PNG"))

    def test_skips_documents(self):
        self.assertFalse(_is_crawlable_url("https://example.com/doc.pdf"))
        self.assertFalse(_is_crawlable_url("https://example.com/sheet.xlsx"))

    def test_skips_media(self):
        self.assertFalse(_is_crawlable_url("https://example.com/video.mp4"))

    def test_skips_assets(self):
        self.assertFalse(_is_crawlable_url("https://example.com/style.css"))
        self.assertFalse(_is_crawlable_url("https://example.com/script.js"))


class TestParser(unittest.TestCase):
    """Test HTML parsing and link extraction."""

    def test_basic_parse(self):
        html = """
        <html>
        <head><title>Test Page</title></head>
        <body>
            <p>Hello world</p>
            <a href="/about">About</a>
            <a href="https://other.com">Other</a>
        </body>
        </html>
        """
        title, text, links = parse_page(html, "https://example.com")
        self.assertEqual(title, "Test Page")
        self.assertIn("Hello world", text)
        self.assertIn("https://example.com/about", links)
        self.assertIn("https://other.com/", links)

    def test_deduplicates_links(self):
        html = '<a href="/a">A</a><a href="/a">A again</a>'
        _, _, links = parse_page(html, "https://example.com")
        count_a = sum(1 for l in links if l == "https://example.com/a")
        self.assertEqual(count_a, 1)

    def test_excludes_script_text(self):
        html = '<script>var x = "secret";</script><p>Visible</p>'
        _, text, _ = parse_page(html, "https://example.com")
        self.assertIn("Visible", text)
        # Script content should not be in the indexed text as meaningful content
        # (html.parser may include it, but it won't match useful queries)

    def test_handles_malformed_html(self):
        html = '<p>Unclosed paragraph<a href="/link">link'
        title, text, links = parse_page(html, "https://example.com")
        self.assertIn("https://example.com/link", links)

    def test_empty_html(self):
        title, text, links = parse_page("", "https://example.com")
        self.assertEqual(title, "")
        self.assertEqual(links, [])


class TestTokenizer(unittest.TestCase):
    """Test text tokenization."""

    def test_basic_tokenization(self):
        tokens = tokenize("Hello World of Web Crawling")
        self.assertIn("hello", tokens)
        self.assertIn("world", tokens)
        self.assertIn("web", tokens)
        self.assertIn("crawling", tokens)

    def test_removes_stop_words(self):
        tokens = tokenize("the quick brown fox and the lazy dog")
        self.assertNotIn("the", tokens)
        self.assertNotIn("and", tokens)

    def test_removes_short_words(self):
        tokens = tokenize("I am a web developer")
        self.assertNotIn("i", tokens)
        self.assertNotIn("a", tokens)

    def test_handles_punctuation(self):
        tokens = tokenize("Hello, world! How's it going?")
        self.assertIn("hello", tokens)
        self.assertIn("world", tokens)

    def test_empty_input(self):
        self.assertEqual(tokenize(""), [])
        self.assertEqual(tokenize("   "), [])

    def test_term_frequencies(self):
        tokens = ["web", "crawl", "web", "search", "web"]
        freq = compute_term_frequencies(tokens)
        self.assertEqual(freq["web"], 3)
        self.assertEqual(freq["crawl"], 1)
        self.assertEqual(freq["search"], 1)


class TestStorage(unittest.TestCase):
    """Test the SQLite storage layer."""

    def setUp(self):
        self.db_fd, self.db_path = tempfile.mkstemp(suffix=".db")
        self.storage = CrawlStorage(self.db_path)

    def tearDown(self):
        self.storage.close()
        os.close(self.db_fd)
        # Clean up all WAL files
        for suffix in ["", "-wal", "-shm"]:
            path = self.db_path + suffix
            if os.path.exists(path):
                os.unlink(path)

    def test_create_and_get_job(self):
        jid = self.storage.create_job("https://example.com", 3)
        job = self.storage.get_job(jid)
        self.assertEqual(job["origin"], "https://example.com")
        self.assertEqual(job["max_depth"], 3)
        self.assertEqual(job["status"], "active")

    def test_enqueue_dequeue(self):
        jid = self.storage.create_job("https://example.com", 2)
        self.assertTrue(self.storage.enqueue_url("https://example.com", jid, 0))
        self.assertTrue(self.storage.enqueue_url("https://example.com/a", jid, 1))

        batch = self.storage.dequeue_urls(jid, 10)
        self.assertEqual(len(batch), 2)
        # BFS: depth 0 should come first
        self.assertEqual(batch[0]["depth"], 0)

    def test_duplicate_detection(self):
        jid = self.storage.create_job("https://example.com", 2)
        self.assertTrue(self.storage.enqueue_url("https://example.com", jid, 0))
        self.assertFalse(self.storage.enqueue_url("https://example.com", jid, 0))

    def test_is_visited(self):
        jid = self.storage.create_job("https://example.com", 2)
        self.assertFalse(self.storage.is_visited("https://example.com", jid))
        self.storage.enqueue_url("https://example.com", jid, 0)
        self.assertTrue(self.storage.is_visited("https://example.com", jid))

    def test_mark_crawled(self):
        jid = self.storage.create_job("https://example.com", 2)
        self.storage.enqueue_url("https://example.com", jid, 0)
        self.storage.dequeue_urls(jid, 1)
        self.storage.mark_crawled("https://example.com", jid, "Title", "Body text")
        self.assertEqual(self.storage.crawled_count(jid), 1)

    def test_mark_failed(self):
        jid = self.storage.create_job("https://example.com", 2)
        self.storage.enqueue_url("https://example.com", jid, 0)
        self.storage.dequeue_urls(jid, 1)
        self.storage.mark_failed("https://example.com", jid)
        self.assertEqual(self.storage.failed_count(jid), 1)

    def test_counts(self):
        jid = self.storage.create_job("https://example.com", 2)
        self.storage.enqueue_url("https://example.com/a", jid, 0)
        self.storage.enqueue_url("https://example.com/b", jid, 0)
        self.storage.enqueue_url("https://example.com/c", jid, 1)
        self.assertEqual(self.storage.pending_count(jid), 3)
        self.assertEqual(self.storage.total_count(jid), 3)

    def test_reset_in_progress(self):
        jid = self.storage.create_job("https://example.com", 2)
        self.storage.enqueue_url("https://example.com", jid, 0)
        self.storage.dequeue_urls(jid, 1)  # moves to in_progress
        self.assertEqual(self.storage.in_progress_count(jid), 1)
        self.storage.reset_in_progress(jid)
        self.assertEqual(self.storage.in_progress_count(jid), 0)
        self.assertEqual(self.storage.pending_count(jid), 1)

    def test_resumable_job(self):
        jid = self.storage.create_job("https://example.com", 2)
        found = self.storage.find_resumable_job("https://example.com", 2)
        self.assertEqual(found, jid)
        # Different origin → not found
        found2 = self.storage.find_resumable_job("https://other.com", 2)
        self.assertIsNone(found2)

    def test_index_and_search(self):
        jid = self.storage.create_job("https://example.com", 2)
        self.storage.enqueue_url("https://example.com", jid, 0)
        self.storage.dequeue_urls(jid, 1)
        self.storage.mark_crawled(
            "https://example.com", jid,
            "Python Tutorial", "Learn Python programming language basics"
        )
        freq = compute_term_frequencies(
            tokenize("Python Tutorial Learn Python programming language basics")
        )
        title_terms = set(tokenize("Python Tutorial"))
        self.storage.index_terms("https://example.com", jid, freq, title_terms)

        # Search should find it
        results = self.storage.search(["python"])
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0][0], "https://example.com")

        # Search for non-indexed term
        results2 = self.storage.search(["javascript"])
        self.assertEqual(len(results2), 0)

    def test_thread_safety(self):
        """Verify multiple threads can write concurrently without corruption."""
        jid = self.storage.create_job("https://example.com", 5)
        errors = []

        def writer(thread_id):
            try:
                for i in range(20):
                    url = f"https://example.com/thread{thread_id}/page{i}"
                    self.storage.enqueue_url(url, jid, 1)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=writer, args=(t,)) for t in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(len(errors), 0)
        self.assertEqual(self.storage.total_count(jid), 100)


class TestSearcher(unittest.TestCase):
    """Test the search module."""

    def setUp(self):
        self.db_fd, self.db_path = tempfile.mkstemp(suffix=".db")
        self.storage = CrawlStorage(self.db_path)

    def tearDown(self):
        self.storage.close()
        os.close(self.db_fd)
        for suffix in ["", "-wal", "-shm"]:
            path = self.db_path + suffix
            if os.path.exists(path):
                os.unlink(path)

    def _index_page(self, url, jid, depth, title, body):
        self.storage.enqueue_url(url, jid, depth)
        self.storage.dequeue_urls(jid, 1)
        self.storage.mark_crawled(url, jid, title, body)
        freq = compute_term_frequencies(tokenize(f"{title} {body}"))
        title_terms = set(tokenize(title))
        self.storage.index_terms(url, jid, freq, title_terms)

    def test_search_returns_triples(self):
        jid = self.storage.create_job("https://example.com", 2)
        self._index_page(
            "https://example.com/python", jid, 1,
            "Learn Python", "Python is a great programming language"
        )
        results = search(self.storage, "python")
        self.assertEqual(len(results), 1)
        url, origin, depth = results[0]
        self.assertEqual(url, "https://example.com/python")
        self.assertEqual(origin, "https://example.com")
        self.assertEqual(depth, 1)

    def test_title_boost(self):
        """Pages with query terms in the title should rank higher."""
        jid = self.storage.create_job("https://example.com", 2)
        # Page 1: "python" only in body (many times)
        self._index_page(
            "https://example.com/body", jid, 1,
            "General Programming", "python python python python python"
        )
        # Page 2: "python" in title + body
        self._index_page(
            "https://example.com/title", jid, 1,
            "Python Guide", "Learn python basics"
        )
        results = search(self.storage, "python")
        self.assertGreaterEqual(len(results), 2)
        # Title-match page should rank first due to 10x title boost
        self.assertEqual(results[0][0], "https://example.com/title")

    def test_empty_query(self):
        results = search(self.storage, "")
        self.assertEqual(results, [])

    def test_no_results(self):
        jid = self.storage.create_job("https://example.com", 2)
        self._index_page(
            "https://example.com", jid, 0,
            "Cooking Recipes", "Delicious pasta and pizza recipes"
        )
        results = search(self.storage, "quantum physics")
        self.assertEqual(results, [])


class TestRateLimiter(unittest.TestCase):
    """Test the token bucket rate limiter."""

    def test_initial_burst(self):
        rl = TokenBucketRateLimiter(rate=10)
        # Should be able to acquire several tokens immediately
        for _ in range(10):
            self.assertTrue(rl.acquire(timeout=0.1))

    def test_rate_limiting(self):
        rl = TokenBucketRateLimiter(rate=2)
        # Drain all tokens
        while rl.available_tokens >= 1:
            rl.acquire(timeout=0.01)
        # Next acquire should take ~0.5s (1/rate)
        start = time.monotonic()
        self.assertTrue(rl.acquire(timeout=2))
        elapsed = time.monotonic() - start
        self.assertGreater(elapsed, 0.1)  # had to wait


class TestBackPressure(unittest.TestCase):
    """Test the back pressure controller."""

    def test_green_status(self):
        bp = BackPressureController(low_watermark=100, high_watermark=500)
        bp.update(50)
        self.assertEqual(bp.status, "GREEN")
        self.assertTrue(bp.should_enqueue())

    def test_yellow_status(self):
        bp = BackPressureController(low_watermark=100, high_watermark=500)
        bp.update(250)
        self.assertEqual(bp.status, "YELLOW")
        self.assertTrue(bp.should_enqueue())

    def test_red_status(self):
        bp = BackPressureController(low_watermark=100, high_watermark=500)
        bp.update(600)
        self.assertEqual(bp.status, "RED")
        self.assertFalse(bp.should_enqueue())


class TestCrawlMetrics(unittest.TestCase):
    """Test metrics tracking."""

    def test_record_fetch(self):
        m = CrawlMetrics()
        m.start_time = time.time()
        m.record_fetch(100.0, True)
        m.record_fetch(200.0, True)
        m.record_fetch(50.0, False)
        snap = m.snapshot()
        self.assertEqual(snap["pages_fetched"], 2)
        self.assertEqual(snap["pages_failed"], 1)

    def test_thread_safe_metrics(self):
        m = CrawlMetrics()
        m.start_time = time.time()
        errors = []

        def record(n):
            try:
                for _ in range(100):
                    m.record_fetch(10.0, True)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=record, args=(i,)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(len(errors), 0)
        self.assertEqual(m.snapshot()["pages_fetched"], 500)


if __name__ == "__main__":
    unittest.main(verbosity=2)
