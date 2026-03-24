"""
cli.py — Command-line interface for the crawler.

Usage:
  python cli.py index https://example.com --depth 2
  python cli.py search "machine learning"
  python cli.py status 1
  python cli.py jobs
  python cli.py resume 1

Provides a terminal-based alternative to the web dashboard.
"""

import argparse
import sys
import time
import threading
import signal

from storage import CrawlStorage
from indexer import Indexer
from searcher import search


# ── ANSI Colors ──────────────────────────────────────────────────────

class C:
    RESET   = "\033[0m"
    BOLD    = "\033[1m"
    DIM     = "\033[2m"
    GREEN   = "\033[32m"
    YELLOW  = "\033[33m"
    RED     = "\033[31m"
    CYAN    = "\033[36m"
    BLUE    = "\033[34m"
    MAGENTA = "\033[35m"


def colored(text, color):
    return f"{color}{text}{C.RESET}"


def bp_color(status):
    return {"GREEN": C.GREEN, "YELLOW": C.YELLOW, "RED": C.RED}.get(status, C.DIM)


# ── Commands ─────────────────────────────────────────────────────────

def cmd_index(args):
    """Start or resume a crawl with live progress display."""
    storage = CrawlStorage(args.db)
    indexer = Indexer(
        storage,
        max_workers=args.workers,
        requests_per_second=args.rate,
        max_queue_depth=args.max_queue,
    )

    origin = args.origin
    if not origin.startswith(("http://", "https://")):
        origin = "https://" + origin

    print(f"\n{colored('▸ Starting crawl', C.BOLD)}")
    print(f"  Origin: {colored(origin, C.CYAN)}")
    print(f"  Depth:  {colored(str(args.depth), C.CYAN)}")
    print(f"  Workers: {args.workers}  Rate: {args.rate}/s  Max queue: {args.max_queue}")
    print()

    job_id = indexer.start(origin, args.depth)
    print(f"  Job ID: {colored(f'#{job_id}', C.GREEN)}")
    print(f"  {colored('Press Ctrl+C to stop gracefully', C.DIM)}\n")

    # Handle Ctrl+C
    stop_requested = threading.Event()

    def signal_handler(sig, frame):
        if not stop_requested.is_set():
            stop_requested.set()
            print(f"\n{colored('  ⏹ Stopping...', C.YELLOW)} (will finish in-progress pages)")
            indexer.stop(job_id)
        else:
            sys.exit(1)

    signal.signal(signal.SIGINT, signal_handler)

    # Live progress display
    while indexer.is_running(job_id) and not stop_requested.is_set():
        status = indexer.get_status(job_id)
        q = status["queue"]
        m = status["metrics"]
        bp = status["backpressure"]
        bp_status = bp["status"]

        line = (
            f"\r  {colored('●', bp_color(bp_status))} "
            f"Crawled: {colored(str(q['crawled']), C.GREEN)}  "
            f"Pending: {colored(str(q['pending']), C.YELLOW)}  "
            f"Failed: {colored(str(q['failed']), C.RED)}  "
            f"Total: {q['total_discovered']}  "
            f"Speed: {m['pages_per_second']}/s  "
            f"BP: {colored(bp_status, bp_color(bp_status))}  "
            f"Elapsed: {m['elapsed_seconds']}s"
            f"   "  # trailing spaces to clear previous output
        )
        sys.stdout.write(line)
        sys.stdout.flush()
        time.sleep(1)

    # Final status
    status = indexer.get_status(job_id)
    q = status["queue"]
    m = status["metrics"]
    print(f"\n\n{colored('▸ Crawl finished', C.BOLD)}")
    print(f"  Status:     {status['job_status']}")
    print(f"  Crawled:    {colored(str(q['crawled']), C.GREEN)}")
    print(f"  Failed:     {colored(str(q['failed']), C.RED)}")
    print(f"  Discovered: {q['total_discovered']}")
    print(f"  Duration:   {m['elapsed_seconds']}s")
    print(f"  Avg fetch:  {m['avg_fetch_ms']}ms")
    print()


def cmd_search(args):
    """Search the index and display results."""
    storage = CrawlStorage(args.db)
    query = args.query

    print(f"\n{colored('▸ Searching:', C.BOLD)} {colored(query, C.CYAN)}\n")

    results = search(storage, query)

    if not results:
        print(f"  {colored('No results found.', C.DIM)}")
        print(f"  Make sure you have run an index first.\n")
        return

    # Table header
    print(f"  {'#':<4} {'Depth':<6} {'Origin':<35} {'Relevant URL'}")
    print(f"  {'─'*4} {'─'*6} {'─'*35} {'─'*50}")

    for i, (url, origin, depth) in enumerate(results, 1):
        origin_short = origin[:33] + ".." if len(origin) > 35 else origin
        print(f"  {colored(str(i), C.DIM):<4} {depth:<6} {origin_short:<35} {colored(url, C.CYAN)}")

    print(f"\n  {colored(f'{len(results)} results', C.GREEN)}\n")


def cmd_status(args):
    """Display status of a specific job."""
    storage = CrawlStorage(args.db)
    indexer = Indexer(storage)

    status = indexer.get_status(args.job_id)
    if "error" in status:
        print(f"\n  {colored('Job not found', C.RED)}\n")
        return

    q = status["queue"]
    bp = status["backpressure"]
    m = status["metrics"]

    print(f"\n{colored(f'▸ Job #{status[\"job_id\"]}', C.BOLD)}")
    print(f"  Origin:      {colored(status['origin'], C.CYAN)}")
    print(f"  Max depth:   {status['max_depth']}")
    print(f"  Status:      {status['job_status']}")
    print(f"  Running:     {status['is_running']}")
    print()
    print(f"  {colored('Queue', C.BOLD)}")
    print(f"    Crawled:     {colored(str(q['crawled']), C.GREEN)}")
    print(f"    Pending:     {colored(str(q['pending']), C.YELLOW)}")
    print(f"    In progress: {q['in_progress']}")
    print(f"    Failed:      {colored(str(q['failed']), C.RED)}")
    print(f"    Discovered:  {q['total_discovered']}")
    print()
    print(f"  {colored('Backpressure', C.BOLD)}")
    print(f"    Status:      {colored(bp['status'], bp_color(bp['status']))}")
    print(f"    Depth:       {bp['queue_depth']} / {bp['high_watermark']}")
    print()
    print(f"  {colored('Metrics', C.BOLD)}")
    print(f"    Pages/sec:   {m['pages_per_second']}")
    print(f"    Avg fetch:   {m['avg_fetch_ms']}ms")
    print(f"    Elapsed:     {m['elapsed_seconds']}s")
    print()


def cmd_jobs(args):
    """List all crawl jobs."""
    storage = CrawlStorage(args.db)
    jobs = storage.get_all_jobs()

    if not jobs:
        print(f"\n  {colored('No jobs found.', C.DIM)}\n")
        return

    print(f"\n{colored('▸ All Jobs', C.BOLD)}\n")
    print(f"  {'ID':<5} {'Status':<12} {'Depth':<6} {'Crawled':<10} {'Origin'}")
    print(f"  {'─'*5} {'─'*12} {'─'*6} {'─'*10} {'─'*40}")

    for job in jobs:
        jid = job["id"]
        status_color = {
            "active": C.GREEN, "completed": C.CYAN,
            "paused": C.YELLOW, "failed": C.RED
        }.get(job["status"], C.DIM)

        crawled = storage.crawled_count(jid)
        total = storage.total_count(jid)

        print(
            f"  {jid:<5} "
            f"{colored(job['status'], status_color):<21} "
            f"{job['max_depth']:<6} "
            f"{crawled}/{total:<8} "
            f"{job['origin']}"
        )
    print()


def cmd_resume(args):
    """Resume a paused/interrupted job."""
    storage = CrawlStorage(args.db)
    job = storage.get_job(args.job_id)
    if not job:
        print(f"\n  {colored('Job not found', C.RED)}\n")
        return

    # Simulate an index command with the same origin/depth
    args.origin = job["origin"]
    args.depth = job["max_depth"]
    cmd_index(args)


# ── Main ─────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Web Crawler & Search Engine CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python cli.py index https://example.com --depth 2
  python cli.py search "machine learning"
  python cli.py status 1
  python cli.py jobs
  python cli.py resume 1
        """,
    )
    parser.add_argument("--db", default="crawler.db", help="SQLite database path")

    sub = parser.add_subparsers(dest="command", help="Command to run")

    # index
    p_index = sub.add_parser("index", help="Start a crawl from an origin URL")
    p_index.add_argument("origin", help="URL to start crawling from")
    p_index.add_argument("-d", "--depth", type=int, default=2, help="Max crawl depth (default: 2)")
    p_index.add_argument("-w", "--workers", type=int, default=8, help="Thread pool size (default: 8)")
    p_index.add_argument("-r", "--rate", type=float, default=5.0, help="Requests per second (default: 5)")
    p_index.add_argument("--max-queue", type=int, default=2000, help="Back-pressure watermark (default: 2000)")

    # search
    p_search = sub.add_parser("search", help="Search indexed pages")
    p_search.add_argument("query", help="Search query string")

    # status
    p_status = sub.add_parser("status", help="Show status of a job")
    p_status.add_argument("job_id", type=int, help="Job ID")

    # jobs
    sub.add_parser("jobs", help="List all crawl jobs")

    # resume
    p_resume = sub.add_parser("resume", help="Resume a paused/interrupted job")
    p_resume.add_argument("job_id", type=int, help="Job ID to resume")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return

    commands = {
        "index": cmd_index,
        "search": cmd_search,
        "status": cmd_status,
        "jobs": cmd_jobs,
        "resume": cmd_resume,
    }
    commands[args.command](args)


if __name__ == "__main__":
    main()
