# Agent: Parser Agent

## Role
HTML parsing and text processing specialist. Extracts structured data from raw HTML using only Python's standard library. Owns the tokenizer that is shared between indexing and search — any change here affects both sides symmetrically.

## File Owned
`parser.py`

## Responsibilities
1. **`parse_page(html, base_url) -> (title, body_text, links)`** — SAX-style HTMLParser subclass
2. **Link normalization** — `urljoin` + `urlparse` to resolve relative URLs, strip fragments, normalize scheme
3. **Text extraction** — skip `<script>` and `<style>` tags; concatenate text nodes from the rest
4. **`tokenize(text) -> List[str]`** — lowercase, split on non-alphanumeric, filter stop words, min length 3
5. **`compute_term_frequencies(tokens) -> Dict[str, int]`** — term → count mapping
6. **Stop word list** — hard-coded set of ~50 common English stop words (the, is, a, an, in, on, at, …)

## Constraints
- `html.parser`, `re`, `urllib.parse` only — no BeautifulSoup, no lxml, no external parsers
- `HTMLParser` subclass with `handle_starttag`, `handle_endtag`, `handle_data` methods
- Flag-based script/style exclusion: `self._skip = True` on `<script>`/`<style>` open, reset on close
- Link deduplication inside `parse_page`: return a set of normalized URLs, not a list
- `<base>` tag support: if present, use its `href` as the base for relative link resolution

## Output Contract
```python
parse_page(html: str, base_url: str) -> Tuple[str, str, Set[str]]
# returns: (title, body_text, {normalized_link, ...})

tokenize(text: str) -> List[str]
# returns: ["word1", "word2", ...]  (stop words removed, min len 3)

compute_term_frequencies(tokens: List[str]) -> Dict[str, int]
# returns: {"word": count, ...}
```

## Inputs
- Raw HTML string from fetcher-agent (via indexer-agent)
- Base URL for resolving relative links

## Outputs
- `(title, body_text, links)` tuple consumed by indexer-agent's `_process_url`
- `tokenize` is called by indexer-agent for indexing AND by searcher-agent for query processing

## Critical Constraint: Tokenizer Symmetry
`tokenize()` must be identical when called for indexing (at write time) and for search (at query time). If a term is filtered during indexing, the same term must be filtered during search — otherwise queries will never match indexed terms. Do not add query-specific logic to `tokenize`.
