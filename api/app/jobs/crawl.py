"""Crawl the configured Italian recipe sites into the local corpus.

Idempotent and resumable, in the strong sense: the frontier under `/srv/data` remembers what
was fetched, recipes are upserted by `source_url`, and re-running after a Ctrl-C picks up
where it stopped rather than starting over. That is a courtesy to the sites before it is a
convenience for us — at one request per two seconds, a restart from zero would mean asking
for thousands of pages we already had.

One thread per host. The rate limit is *per host*, so serialising three unrelated sites
behind one another would take three times as long for no benefit to any of them; each thread
has its own database session and shares the throttle, whose per-host bookkeeping is what
makes that safe.

The number this job exists to print is the **ingredient match rate**, and it will be far
below the seed corpus's 420/420. That is the finding, not the failure: real ingredient
strings are messy in ways a hand-written corpus isn't, and every unmatched string listed at
the end is a missing `ProductAlias` — a concrete, one-line fix, not a mystery.
"""

import argparse
import logging
import sys
from concurrent.futures import ThreadPoolExecutor

import httpx

from app.config import settings
from app.db import SessionLocal
from app.services.recipes.crawler import (
    CrawlReport,
    HostThrottle,
    RobotsCache,
    crawl_site,
    discover_urls,
)
from app.services.recipes.frontier import DEFAULT_FRONTIER_PATH, Frontier
from app.services.recipes.sites import SITES, Site

log = logging.getLogger(__name__)


def run_site(
    site: Site,
    frontier: Frontier,
    *,
    client: httpx.Client,
    throttle: HostThrottle,
    robots: RobotsCache,
    max_pages: int | None,
    discover: bool,
) -> CrawlReport:
    """Seed this site's frontier from its sitemap, then crawl it."""
    if discover:
        urls = discover_urls(site, client=client, throttle=throttle, robots=robots)
        added = frontier.enqueue(site.name, site.host, urls)
        print(f"  {site.name}: {len(urls)} recipe URLs in sitemap, {added} new to the frontier")

    # A session per thread: SQLAlchemy Sessions are not thread-safe, and sharing one across
    # three crawlers would interleave three unrelated transactions.
    with SessionLocal() as db:
        return crawl_site(
            site,
            db,
            frontier,
            client=client,
            throttle=throttle,
            robots=robots,
            max_pages=max_pages,
        )


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--max-pages",
        type=int,
        default=None,
        help=f"pages per host this run (default: {settings.crawler_max_pages_per_host})",
    )
    parser.add_argument("--site", action="append", help="crawl only this site (repeatable)")
    parser.add_argument(
        "--no-discover",
        action="store_true",
        help="skip sitemap discovery and work the existing frontier only",
    )
    parser.add_argument("--frontier", default=str(DEFAULT_FRONTIER_PATH))
    args = parser.parse_args()

    chosen = [s for s in SITES if not args.site or s.name in args.site]
    if not chosen:
        print(f"no such site; known: {', '.join(s.name for s in SITES)}", file=sys.stderr)
        return 1

    print(f"crawling {len(chosen)} site(s) as {settings.crawler_user_agent}")
    print(f"  {settings.crawler_delay_s:.1f}s between requests per host, robots.txt obeyed")
    print(f"  frontier: {args.frontier}\n")

    throttle = HostThrottle()
    reports: list[CrawlReport] = []

    with (
        Frontier(args.frontier) as frontier,
        httpx.Client(timeout=settings.crawler_timeout_s) as client,
    ):
        robots = RobotsCache(client, throttle)
        with ThreadPoolExecutor(max_workers=len(chosen)) as pool:
            futures = [
                pool.submit(
                    run_site,
                    site,
                    frontier,
                    client=client,
                    throttle=throttle,
                    robots=robots,
                    max_pages=args.max_pages,
                    discover=not args.no_discover,
                )
                for site in chosen
            ]
            for future in futures:
                reports.append(future.result())

        _print_summary(reports, frontier)
    return 0


def _print_summary(reports: list[CrawlReport], frontier: Frontier) -> None:
    total = CrawlReport(site="all")
    print(f"\n  {'site':<28} {'fetched':>8} {'recipes':>8} {'new':>6} {'skipped':>8} {'failed':>7}")
    for report in sorted(reports, key=lambda r: r.site):
        print(
            f"  {report.site:<28} {report.fetched:>8} {report.recipes:>8} "
            f"{report.created:>6} {report.skipped:>8} {report.failed:>7}"
        )
        if report.stopped_reason:
            print(f"      stopped: {report.stopped_reason}")
        total.merge(report)

    print(f"\n  {total.recipes} recipes written ({total.created} new, {total.updated} updated)")

    rate = total.match_rate
    if rate is None:
        print("  no ingredients seen — nothing was fetched")
    else:
        print(
            f"  ingredient match rate: {total.ingredients_matched}/{total.ingredients_total} "
            f"= {rate:.0%}"
        )
        # Said out loud, because the number invites the wrong conclusion. The seed corpus
        # matches 100% because it was written against the catalog; this one is measuring the
        # catalog against the language real recipe sites use.
        print("  (the seed corpus's 100% is not the comparison — see the unmatched list below)")

    if total.unmatched:
        print("\n  top unmatched ingredient strings — each one is a missing ProductAlias:")
        for text, count in total.unmatched.most_common(25):
            print(f"    {count:>5}x  {text}")
        print(f"\n  {len(total.unmatched)} distinct unmatched strings in total")

    counts = frontier.counts()
    if counts:
        state = ", ".join(f"{status}={n}" for status, n in sorted(counts.items()))
        print(f"\n  frontier: {state}")
        print("  re-run `make crawl` to continue where this left off.")


if __name__ == "__main__":
    raise SystemExit(main())
