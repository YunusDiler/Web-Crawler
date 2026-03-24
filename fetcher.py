"""
fetcher.py — HTTP page fetcher using only stdlib (urllib).

Handles timeouts, redirects, encoding detection, and robots.txt courtesy.
No external HTTP libraries (requests, httpx, etc.).
"""

import urllib.request
import urllib.error
import urllib.parse
import urllib.robotparser
import ssl
import socket
import time
import threading
from typing import Optional, Tuple
from dataclasses import dataclass


@dataclass
class FetchResult:
    url: str
    final_url: str          # after redirects
    status_code: int
    content_type: str
    html: str
    elapsed_ms: float
    error: Optional[str] = None


# Shared robots.txt cache — avoids re-fetching per page
_robots_cache: dict = {}
_robots_lock = threading.Lock()


def _get_robots_parser(scheme: str, netloc: str, timeout: float = 5) -> urllib.robotparser.RobotFileParser:
    """Fetch and cache robots.txt for a given host."""
    key = f"{scheme}://{netloc}"
    with _robots_lock:
        if key in _robots_cache:
            return _robots_cache[key]

    rp = urllib.robotparser.RobotFileParser()
    robots_url = f"{key}/robots.txt"
    rp.set_url(robots_url)
    try:
        rp.read()
    except Exception:
        # If robots.txt is unreachable, assume everything is allowed
        rp.allow_all = True
    with _robots_lock:
        _robots_cache[key] = rp
    return rp


def is_allowed_by_robots(url: str, user_agent: str = "MiniCrawler/1.0") -> bool:
    """Check whether robots.txt permits crawling this URL."""
    parsed = urllib.parse.urlparse(url)
    try:
        rp = _get_robots_parser(parsed.scheme, parsed.netloc)
        return rp.can_fetch(user_agent, url)
    except Exception:
        return True  # permissive on failure


def fetch_page(url: str, timeout: float = 10) -> FetchResult:
    """
    Fetch a single page via urllib.

    Returns a FetchResult with HTML content or an error string.
    Only processes text/html responses; skips binary content.
    """
    start = time.monotonic()
    user_agent = "MiniCrawler/1.0 (educational project)"

    # Build request with a polite User-Agent
    req = urllib.request.Request(url, headers={
        "User-Agent": user_agent,
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "en-US,en;q=0.5",
    })

    # Create a permissive SSL context (many sites have certificate issues)
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            final_url = resp.geturl()
            status = resp.getcode()
            content_type = resp.headers.get("Content-Type", "")

            # Only parse HTML
            if "text/html" not in content_type and "xhtml" not in content_type:
                elapsed = (time.monotonic() - start) * 1000
                return FetchResult(
                    url=url, final_url=final_url, status_code=status,
                    content_type=content_type, html="", elapsed_ms=elapsed,
                    error="non-html content"
                )

            # Detect encoding from Content-Type header
            charset = "utf-8"
            if "charset=" in content_type:
                charset = content_type.split("charset=")[-1].strip().split(";")[0]

            raw = resp.read(2 * 1024 * 1024)  # cap at 2 MB
            try:
                html = raw.decode(charset, errors="replace")
            except (LookupError, UnicodeDecodeError):
                html = raw.decode("utf-8", errors="replace")

            elapsed = (time.monotonic() - start) * 1000
            return FetchResult(
                url=url, final_url=final_url, status_code=status,
                content_type=content_type, html=html, elapsed_ms=elapsed
            )

    except urllib.error.HTTPError as e:
        elapsed = (time.monotonic() - start) * 1000
        return FetchResult(
            url=url, final_url=url, status_code=e.code,
            content_type="", html="", elapsed_ms=elapsed,
            error=f"HTTP {e.code}: {e.reason}"
        )
    except urllib.error.URLError as e:
        elapsed = (time.monotonic() - start) * 1000
        return FetchResult(
            url=url, final_url=url, status_code=0,
            content_type="", html="", elapsed_ms=elapsed,
            error=f"URL error: {e.reason}"
        )
    except socket.timeout:
        elapsed = (time.monotonic() - start) * 1000
        return FetchResult(
            url=url, final_url=url, status_code=0,
            content_type="", html="", elapsed_ms=elapsed,
            error="timeout"
        )
    except Exception as e:
        elapsed = (time.monotonic() - start) * 1000
        return FetchResult(
            url=url, final_url=url, status_code=0,
            content_type="", html="", elapsed_ms=elapsed,
            error=str(e)
        )
