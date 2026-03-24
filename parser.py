"""
parser.py — HTML parsing using only stdlib html.parser.

Extracts:
  - Page title
  - Body text (for indexing)
  - Outgoing links (normalized, deduplicated)

No BeautifulSoup, lxml, or other third-party parsers.
"""

import re
import urllib.parse
from html.parser import HTMLParser
from typing import List, Tuple, Set


class _LinkExtractor(HTMLParser):
    """Extract href values from <a> tags and page title."""

    def __init__(self):
        super().__init__()
        self.links: List[str] = []
        self.title_parts: List[str] = []
        self.text_parts: List[str] = []
        self._in_title = False
        self._in_script = False
        self._in_style = False

    def handle_starttag(self, tag: str, attrs: list):
        tag = tag.lower()
        if tag == "a":
            for name, value in attrs:
                if name.lower() == "href" and value:
                    self.links.append(value)
        elif tag == "title":
            self._in_title = True
        elif tag == "script":
            self._in_script = True
        elif tag == "style":
            self._in_style = True

    def handle_endtag(self, tag: str):
        tag = tag.lower()
        if tag == "title":
            self._in_title = False
        elif tag == "script":
            self._in_script = False
        elif tag == "style":
            self._in_style = False

    def handle_data(self, data: str):
        if self._in_title:
            self.title_parts.append(data)
        if not self._in_script and not self._in_style:
            self.text_parts.append(data)

    def error(self, message):
        pass  # Swallow malformed HTML errors


def normalize_url(href: str, base_url: str) -> str:
    """
    Resolve a potentially relative href against a base URL.
    Strips fragments, normalizes scheme, removes trailing slash inconsistencies.
    """
    # Resolve relative URLs
    resolved = urllib.parse.urljoin(base_url, href)

    # Parse and rebuild to normalize
    parsed = urllib.parse.urlparse(resolved)

    # Only follow http/https
    if parsed.scheme not in ("http", "https"):
        return ""

    # Strip fragment
    normalized = urllib.parse.urlunparse((
        parsed.scheme,
        parsed.netloc.lower(),
        parsed.path if parsed.path else "/",
        parsed.params,
        parsed.query,
        "",  # no fragment
    ))
    return normalized


def _is_crawlable_url(url: str) -> bool:
    """Filter out non-HTML resources by file extension heuristic."""
    skip_extensions = {
        ".jpg", ".jpeg", ".png", ".gif", ".svg", ".webp", ".ico",
        ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
        ".zip", ".tar", ".gz", ".rar",
        ".mp3", ".mp4", ".avi", ".mkv", ".mov",
        ".css", ".js", ".json", ".xml", ".rss",
        ".woff", ".woff2", ".ttf", ".eot",
    }
    parsed = urllib.parse.urlparse(url)
    path_lower = parsed.path.lower()
    return not any(path_lower.endswith(ext) for ext in skip_extensions)


def parse_page(html: str, base_url: str) -> Tuple[str, str, List[str]]:
    """
    Parse an HTML page and return (title, body_text, [outgoing_urls]).

    All URLs are normalized and deduplicated.
    Only crawlable (likely-HTML) URLs are returned.
    """
    extractor = _LinkExtractor()
    try:
        extractor.feed(html)
    except Exception:
        pass  # best-effort on malformed HTML

    title = " ".join(extractor.title_parts).strip()

    # Clean body text: collapse whitespace
    raw_text = " ".join(extractor.text_parts)
    body_text = re.sub(r"\s+", " ", raw_text).strip()

    # Normalize and deduplicate links
    seen: Set[str] = set()
    unique_links: List[str] = []
    for href in extractor.links:
        normalized = normalize_url(href, base_url)
        if normalized and normalized not in seen and _is_crawlable_url(normalized):
            seen.add(normalized)
            unique_links.append(normalized)

    return title, body_text, unique_links


def tokenize(text: str) -> List[str]:
    """
    Simple tokenizer for indexing/search.
    Lowercase, strip punctuation, remove short/stop words.
    """
    # Common English stop words
    stop_words = {
        "a", "an", "the", "and", "or", "but", "in", "on", "at", "to",
        "for", "of", "with", "by", "from", "is", "it", "as", "be",
        "was", "are", "were", "been", "has", "have", "had", "do",
        "does", "did", "will", "would", "could", "should", "may",
        "might", "can", "this", "that", "these", "those", "not",
        "no", "so", "if", "than", "then", "its", "my", "your",
        "his", "her", "our", "their", "all", "each", "any", "some",
        "he", "she", "we", "they", "i", "me", "him", "us", "them",
        "who", "what", "which", "how", "when", "where", "why",
        "up", "out", "about", "just", "also", "more", "very",
    }

    # Extract word tokens via regex
    words = re.findall(r"[a-zA-Z0-9]+", text.lower())

    # Filter: minimum length 2, not a stop word
    return [w for w in words if len(w) >= 2 and w not in stop_words]


def compute_term_frequencies(tokens: List[str]) -> dict:
    """Count occurrences of each token."""
    freq = {}
    for t in tokens:
        freq[t] = freq.get(t, 0) + 1
    return freq
