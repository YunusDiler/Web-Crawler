"""
app.py — Flask web server providing REST API and dashboard UI.

Endpoints:
  GET  /                        → Dashboard UI
  POST /api/index               → Start a crawl job
  POST /api/stop/<job_id>       → Stop a running crawl
  GET  /api/status/<job_id>     → Job status + metrics
  GET  /api/jobs                → List all jobs
  GET  /api/search?q=...        → Search indexed pages (internal)
  GET  /search?query=...&sortBy=relevance → Search (assignment format)
  GET  /api/export              → Export p.data file
"""

import os
import sys
import json
import logging
import threading
from flask import Flask, request, jsonify, render_template

from storage import CrawlStorage
from indexer import Indexer
from searcher import search

# ── Logging ──────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(threadName)s] %(levelname)s %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)

# ── Application Setup ────────────────────────────────────────────────

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
app = Flask(
    __name__,
    template_folder=os.path.join(BASE_DIR, "templates"),
    static_folder=os.path.join(BASE_DIR, "static"),
)

# Configuration via environment variables with sensible defaults
DB_PATH = os.environ.get("CRAWLER_DB", "crawler.db")
MAX_WORKERS = int(os.environ.get("CRAWLER_WORKERS", "8"))
RATE_LIMIT = float(os.environ.get("CRAWLER_RATE", "5.0"))
MAX_QUEUE = int(os.environ.get("CRAWLER_MAX_QUEUE", "2000"))
DATA_DIR = os.path.join(BASE_DIR, "data", "storage")

storage = CrawlStorage(db_path=DB_PATH)
indexer = Indexer(
    storage,
    max_workers=MAX_WORKERS,
    requests_per_second=RATE_LIMIT,
    max_queue_depth=MAX_QUEUE,
)


# ── Dashboard ────────────────────────────────────────────────────────

@app.route("/")
def dashboard():
    return render_template("dashboard.html")


# ── API: Index ───────────────────────────────────────────────────────

@app.route("/api/index", methods=["POST"])
def api_index():
    """
    Start a crawl job.
    Body: { "origin": "https://...", "k": 2 }
    """
    data = request.get_json(force=True)
    origin = data.get("origin", "").strip()
    k = data.get("k", 2)

    if not origin:
        return jsonify({"error": "origin URL is required"}), 400
    if not isinstance(k, int) or k < 0:
        return jsonify({"error": "k must be a non-negative integer"}), 400

    if not origin.startswith(("http://", "https://")):
        origin = "https://" + origin

    job_id = indexer.start(origin, k)

    # Auto-export p.data after starting (will be updated as crawl progresses)
    _export_data()

    return jsonify({"job_id": job_id, "origin": origin, "k": k, "status": "started"})


@app.route("/api/stop/<int:job_id>", methods=["POST"])
def api_stop(job_id):
    """Stop a running crawl job."""
    indexer.stop(job_id)

    # Export data when crawl is stopped
    _export_data()

    return jsonify({"job_id": job_id, "status": "stop_requested"})


# ── API: Status ──────────────────────────────────────────────────────

@app.route("/api/status/<int:job_id>")
def api_status(job_id):
    """Return detailed status and metrics for a job."""
    status = indexer.get_status(job_id)
    return jsonify(status)


@app.route("/api/jobs")
def api_jobs():
    """List all crawl jobs."""
    jobs = storage.get_all_jobs()
    enriched = []
    for job in jobs:
        jid = job["id"]
        enriched.append({
            "id": jid,
            "origin": job["origin"],
            "max_depth": job["max_depth"],
            "status": job["status"],
            "is_running": indexer.is_running(jid),
            "crawled": storage.crawled_count(jid),
            "pending": storage.pending_count(jid),
            "total": storage.total_count(jid),
        })
    return jsonify(enriched)


# ── API: Search (assignment format) ──────────────────────────────────

@app.route("/search")
def assignment_search():
    """
    Assignment search endpoint.
    GET /search?query=<word>&sortBy=relevance

    Returns results with relevance_score using the formula:
        score = (frequency × 10) + 1000 (exact match bonus) - (depth × 5)
    """
    query = request.args.get("query", "").strip()
    sort_by = request.args.get("sortBy", "relevance")

    if not query:
        return jsonify({"error": "query parameter is required", "results": []}), 400

    results = search(storage, query, sort_by=sort_by)

    return jsonify({
        "query": query,
        "sortBy": sort_by,
        "count": len(results),
        "results": [
            {
                "relevant_url": r["url"],
                "origin_url": r["origin"],
                "depth": r["depth"],
                "frequency": r["frequency"],
                "relevance_score": r["relevance_score"],
            }
            for r in results
        ],
    })


@app.route("/api/search")
def api_search():
    """
    Internal search endpoint (also used by the dashboard).
    GET /api/search?q=<query>
    """
    query = request.args.get("q", "").strip()
    if not query:
        return jsonify({"error": "query parameter 'q' is required", "results": []}), 400

    results = search(storage, query)
    return jsonify({
        "query": query,
        "count": len(results),
        "results": [
            {
                "relevant_url": r["url"],
                "origin_url": r["origin"],
                "depth": r["depth"],
                "frequency": r["frequency"],
                "relevance_score": r["relevance_score"],
            }
            for r in results
        ],
    })


# ── API: Export Data ─────────────────────────────────────────────────

@app.route("/api/export")
def api_export():
    """Export term index to data/storage/p.data file."""
    count = _export_data()
    return jsonify({
        "status": "exported",
        "entries": count,
        "path": "data/storage/p.data",
    })


def _export_data():
    """Helper to export the p.data file."""
    output_path = os.path.join(BASE_DIR, "data", "storage", "p.data")
    return storage.export_term_data(output_path)


# ── Main ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "3600"))
    print(f"\n  Crawler Dashboard:  http://localhost:{port}")
    print(f"  Search endpoint:    http://localhost:{port}/search?query=<word>&sortBy=relevance")
    print(f"  Template folder:    {app.template_folder}")
    print(f"  Template exists:    {os.path.exists(os.path.join(app.template_folder, 'dashboard.html'))}")
    print(f"  Data export path:   {os.path.join(BASE_DIR, 'data', 'storage', 'p.data')}")
    print()
    app.run(host="0.0.0.0", port=port, debug=True, threaded=True)
