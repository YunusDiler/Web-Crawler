# Agent: Fetcher Agent

## Role
HTTP networking specialist. Owns all outbound web requests, robots.txt compliance, and error normalization. Never raises exceptions — all outcomes are captured in a typed result object.

## File Owned
`fetcher.py`

## Responsibilities
1. **Page fetching** — `fetch_page(url, timeout=15) -> FetchResult` using `urllib.request` only
2. **robots.txt compliance** — `is_allowed_by_robots(url, user_agent) -> bool` with module-level cache
3. **Error normalization** — all exceptions (HTTP errors, timeouts, SSL, encoding) produce a `FetchResult(ok=False, error=...)`
4. **Charset detection** — parse `Content-Type` header for charset; fallback to UTF-8 with `errors='replace'`
5. **Body size cap** — read at most 2 MB per page to prevent memory exhaustion on large pages

## Constraints
- `urllib.request`, `urllib.error`, `urllib.robotparser`, `ssl`, `socket`, `http.client` only
- No `requests`, no `httpx`, no third-party HTTP libraries
- Module-level `_robots_cache: Dict[str, urllib.robotparser.RobotFileParser]` with `threading.Lock`
- Permissive SSL context: `ssl.create_default_context()` with certificate verification (never disabled in production)
- User-Agent: `"WebCrawler/1.0"`

## FetchResult Contract
```python
@dataclass
class FetchResult:
    ok: bool
    url: str              # final URL after redirects
    status_code: int      # 0 if connection failed
    content_type: str
    html: str             # empty string on failure
    error: str            # empty string on success
    fetch_ms: float       # wall-clock time in milliseconds
```

## Inputs
- URL string from indexer-agent
- Optional timeout parameter

## Outputs
- `FetchResult` dataclass consumed by indexer-agent's `_process_url`

## Error Taxonomy
| Exception | FetchResult.error prefix |
|---|---|
| `urllib.error.HTTPError` | `HTTP {code}` |
| `urllib.error.URLError` | `URL error: {reason}` |
| `socket.timeout` | `Timeout after {timeout}s` |
| `ssl.SSLError` | `SSL error: {msg}` |
| `UnicodeDecodeError` | handled by charset fallback, not an error |

## robots.txt Caching
robots.txt is fetched once per origin host and cached in-process. Cache is never invalidated during a crawl — consistent with a single-run assumption. The cache lock prevents duplicate fetches under concurrent workers.
