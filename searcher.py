"""
searcher.py — Query engine over the crawl index.

Relevancy model (per assignment):
    score = (frequency × 10) + 1000 (exact match bonus) - (depth × 5)

This module is safe to call while the indexer is running because:
  1. SQLite WAL mode allows concurrent readers + one writer.
  2. We query committed data only, so partial writes are invisible.
  3. Each search call opens a fresh read transaction, so it reflects
     whatever the indexer has committed up to that moment.
"""

from typing import List
from storage import CrawlStorage
from parser import tokenize


def search(
    storage: CrawlStorage,
    query: str,
    sort_by: str = "relevance",
    limit: int = 50,
) -> List[dict]:
    """
    Search indexed pages for relevance to a query string.

    Returns a list of dicts with keys:
        url, origin, depth, frequency, relevance_score
    ordered by descending relevance_score.
    """
    query_terms = tokenize(query)
    if not query_terms:
        return []

    results = storage.search(query_terms, sort_by=sort_by)
    return results[:limit]
