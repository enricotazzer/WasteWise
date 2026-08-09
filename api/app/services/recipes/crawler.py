"""A deliberately slow, deliberately polite recipe crawler.

The rules below are what make running this acceptable at all, so they are enforced here
rather than left to the caller, and none of them has an override:

  1. **robots.txt is obeyed**, per host, fetched once with our own User-Agent and cached for
     the run. A disallowed path is never requested. If robots.txt cannot be *read* — a 403,
     a 5xx, a timeout — the host is treated as fully disallowed. That is the opposite of the
     usual default and it is the right way round: a crawl policy we failed to read is not a
     crawl policy we may ignore.
  2. **One request every `crawler_delay_s` seconds per host**, enforced by a throttle the
     fetch path cannot bypass. Per host, not global, so three sites are not punished for
     each other's slowness — and never the reverse.
  3. **An identifying User-Agent with a contact address**, so a site owner who objects has
     somewhere to write.
  4. **A page budget per host** (`crawler_max_pages_per_host`), spent against the *frontier*
     rather than the process, so resuming a crawl does not hand it a fresh allowance.
  5. **429 and 503 back off exponentially and then stop.** Repeated failures end the host's
     crawl with a reason; they never turn into a retry loop against a struggling server.

The corpus is personal and is never redistributed. `source_url` and `source_site` are stored
on every row so attribution can be shown in the UI, and so a crawled recipe can supersede a
seeded one by provenance.

What this module is *not* is an HTML scraper. It fetches pages and hands them to
`jsonld.py`, which reads the schema.org/Recipe the publisher already put there for search
engines. A page that publishes no recipe is marked SKIPPED and never fetched again.
"""

import logging
import re
import threading
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Recipe
from app.services.recipes.frontier import Frontier, UrlStatus
from app.services.recipes.ingredients import resolve_line
from app.services.recipes.jsonld import ParsedRecipe, parse_html
from app.services.recipes.sites import Site

log = logging.getLogger(__name__)

#: Consecutive transport-level failures before a host's crawl is abandoned. Five is enough
#: to ride out a blip and few enough that a site having a bad day is left alone.
MAX_CONSECUTIVE_FAILURES = 5
#: Per-URL retries. A URL that failed twice is almost always gone, not busy.
MAX_ATTEMPTS = 2
#: Backoff ceiling for 429/503. Past this the host is abandoned rather than waited on.
MAX_BACKOFF_S = 60.0

_LOC = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>", re.IGNORECASE)


# --- rate limiting -------------------------------------------------------------------


class HostThrottle:
    """One request per `delay_s` per host, blocking.

    Blocks rather than dropping, for the same reason the Open Food Facts limiter does: a
    request that waits two seconds is the design, a request that silently disappears is a
    gap in the corpus nobody would notice.

    `clock` and `sleep` are injectable purely so the tests can prove the delay is applied
    without spending real seconds doing it. There is no production caller that passes them,
    and no way to disable the throttle through the public surface.
    """

    def __init__(
        self,
        delay_s: float | None = None,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._delay = settings.crawler_delay_s if delay_s is None else delay_s
        self._clock = clock
        self._sleep = sleep
        self._lock = threading.Lock()
        self._last: dict[str, float] = {}

    def wait(self, host: str) -> float:
        """Block until this host may be hit again. Returns how long it waited."""
        with self._lock:
            now = self._clock()
            previous = self._last.get(host)
            waited = 0.0
            if previous is not None:
                remaining = self._delay - (now - previous)
                if remaining > 0:
                    self._sleep(remaining)
                    waited = remaining
                    now = self._clock()
            self._last[host] = now
            return waited


# --- robots.txt ----------------------------------------------------------------------


class RobotsCache:
    """robots.txt per host, fetched once and cached for the run.

    `urllib.robotparser` does the parsing, but not the fetching: `RobotFileParser.read()`
    goes through `urllib.request`, which would send its own User-Agent instead of ours. A
    site that serves a different policy to different agents would then be answering a
    question we didn't ask. So the file is fetched with the same httpx client and the same
    User-Agent as every other request, and only the parsing is delegated.
    """

    def __init__(
        self,
        client: httpx.Client,
        throttle: HostThrottle,
        *,
        user_agent: str | None = None,
    ) -> None:
        self._client = client
        self._throttle = throttle
        self.user_agent = user_agent or settings.crawler_user_agent
        self._parsers: dict[str, RobotFileParser | None] = {}
        self._lock = threading.Lock()

    def _parser(self, host: str, scheme: str) -> RobotFileParser | None:
        with self._lock:
            if host in self._parsers:
                return self._parsers[host]

        parser: RobotFileParser | None = RobotFileParser()
        url = f"{scheme}://{host}/robots.txt"
        self._throttle.wait(host)
        try:
            resp = self._client.get(url, headers={"User-Agent": self.user_agent})
        except httpx.HTTPError as exc:
            # Unreachable, not absent. RFC 9309 says treat that as "disallow everything",
            # and so do we: we cannot claim to be obeying a policy we never read.
            log.warning(
                "robots.txt unreachable for %s (%s) — treating host as disallowed", host, exc
            )
            parser = None
        else:
            if resp.status_code in (401, 403):
                log.warning(
                    "robots.txt is %s for %s — treating host as disallowed", resp.status_code, host
                )
                parser = None
            elif resp.status_code >= 500:
                log.warning(
                    "robots.txt is %s for %s — treating host as disallowed", resp.status_code, host
                )
                parser = None
            elif resp.status_code >= 400:
                # 404/410: the site published no policy, which means no restrictions.
                assert parser is not None
                parser.parse([])
            else:
                assert parser is not None
                parser.parse(resp.text.splitlines())

        with self._lock:
            self._parsers[host] = parser
        return parser

    def allowed(self, url: str) -> bool:
        parts = urlsplit(url)
        parser = self._parser(parts.netloc, parts.scheme or "https")
        if parser is None:
            return False
        return parser.can_fetch(self.user_agent, url)


# --- results -------------------------------------------------------------------------


@dataclass
class CrawlReport:
    """What one host's crawl did. Every field is reported, including the unflattering ones —
    the unmatched-ingredient tally in particular is the actionable output of a run."""

    site: str
    fetched: int = 0
    recipes: int = 0
    created: int = 0
    updated: int = 0
    skipped: int = 0
    failed: int = 0
    blocked: int = 0
    ingredients_total: int = 0
    ingredients_matched: int = 0
    unmatched: Counter[str] = field(default_factory=Counter)
    #: None means the crawl ran out of queued URLs, which is the healthy ending.
    stopped_reason: str | None = None

    @property
    def match_rate(self) -> float | None:
        if not self.ingredients_total:
            return None
        return self.ingredients_matched / self.ingredients_total

    def merge(self, other: "CrawlReport") -> None:
        self.fetched += other.fetched
        self.recipes += other.recipes
        self.created += other.created
        self.updated += other.updated
        self.skipped += other.skipped
        self.failed += other.failed
        self.blocked += other.blocked
        self.ingredients_total += other.ingredients_total
        self.ingredients_matched += other.ingredients_matched
        self.unmatched.update(other.unmatched)


# --- sitemap discovery ---------------------------------------------------------------


def _get(
    client: httpx.Client, throttle: HostThrottle, robots: RobotsCache, url: str
) -> httpx.Response | None:
    """One courteous GET. None means robots said no — the caller must not retry."""
    if not robots.allowed(url):
        log.info("robots.txt disallows %s", url)
        return None
    throttle.wait(urlsplit(url).netloc)
    return client.get(
        url,
        headers={"User-Agent": robots.user_agent, "Accept-Language": "it-IT,it;q=0.9"},
        follow_redirects=True,
    )


def discover_urls(
    site: Site, *, client: httpx.Client, throttle: HostThrottle, robots: RobotsCache
) -> list[str]:
    """Recipe URLs from the site's own sitemap.

    Sitemaps rather than link-following, and that is a courtesy decision as much as a
    technical one: the sitemap is the list of pages the publisher *wants* crawled, it is
    already filtered to canonical URLs, and following links would mean fetching category and
    tag pages purely to discover what the site has already told us.

    `Site.recipe_pattern` filters before anything is requested, so a page that cannot be a
    recipe never costs the site a request.
    """
    urls: list[str] = []
    try:
        index = _get(client, throttle, robots, site.sitemap_index)
    except httpx.HTTPError as exc:
        log.warning("sitemap index unreachable for %s: %s", site.name, exc)
        return []
    if index is None or index.status_code != 200:
        return []

    children = [u for u in _LOC.findall(index.text) if site.sitemap_pattern.search(u)]
    if not children:
        # Some sites publish a flat sitemap with no index layer.
        children = [site.sitemap_index]

    for child in children:
        try:
            resp = _get(client, throttle, robots, child)
        except httpx.HTTPError as exc:
            log.warning("sitemap %s unreachable: %s", child, exc)
            continue
        if resp is None or resp.status_code != 200:
            continue
        urls.extend(u for u in _LOC.findall(resp.text) if site.recipe_pattern.match(u))

    # Preserve sitemap order (newest first on every site here) while de-duplicating.
    return list(dict.fromkeys(urls))


# --- persistence ---------------------------------------------------------------------


def upsert_recipe(
    db: Session, site: Site, url: str, parsed: ParsedRecipe, report: CrawlReport
) -> Recipe:
    """Write one parsed recipe, resolving its ingredients through the catalog matcher.

    Upserted by `source_url`, so re-crawling a page corrects it in place rather than
    duplicating it — which is also what makes an interrupted crawl safe to simply re-run.

    Unmatched ingredients are counted into the report, never silently dropped. An ingredient
    that fails to resolve is a missing alias: the recipe still enters the corpus, one edge
    short, and the string that failed is reported so the alias can be added.
    """
    product_ids: list[int] = []
    for line in parsed.ingredients:
        report.ingredients_total += 1
        product_id, matched_text = resolve_line(db, line)
        if product_id is None:
            if matched_text:
                report.unmatched[matched_text] += 1
            continue
        report.ingredients_matched += 1
        if product_id not in product_ids:
            product_ids.append(product_id)

    recipe = db.scalar(select(Recipe).where(Recipe.source_url == url))
    if recipe is None:
        recipe = Recipe(source_url=url)
        db.add(recipe)
        report.created += 1
    else:
        report.updated += 1

    recipe.source_site = site.name
    recipe.title_it = parsed.name[:300]
    recipe.ingredients_raw = parsed.ingredients
    recipe.ingredient_product_ids = product_ids
    recipe.n_ingredients = len(parsed.ingredients)
    recipe.servings = parsed.servings
    recipe.total_minutes = parsed.total_minutes
    recipe.instructions = parsed.instructions
    recipe.scraped_at = datetime.now(UTC)
    # The ingredient set changed, so any vector computed from the old one is stale. Nulling
    # it hands the row back to `jobs/embed`, which is the only thing that knows how to
    # re-embed it — rather than leaving a confidently wrong vector in place.
    recipe.embedding = None
    return recipe


# --- the crawl loop ------------------------------------------------------------------


def crawl_site(
    site: Site,
    db: Session,
    frontier: Frontier,
    *,
    client: httpx.Client,
    throttle: HostThrottle,
    robots: RobotsCache,
    max_pages: int | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> CrawlReport:
    """Fetch one host's queued URLs until the budget, the queue or our welcome runs out.

    Synchronous and single-host by design: the per-host delay is the constraint, so there is
    nothing to gain from concurrency *within* a host, and a lot to lose. `jobs/crawl.py`
    runs one of these per site in its own thread, which is where the parallelism belongs.
    """
    report = CrawlReport(site=site.name)
    budget = settings.crawler_max_pages_per_host if max_pages is None else max_pages
    host = site.host
    consecutive_failures = 0
    backoff = settings.crawler_delay_s

    while True:
        done = frontier.completed(host)
        if done >= budget:
            report.stopped_reason = f"page budget reached ({budget} for {host})"
            break

        batch = frontier.next_queued(host, limit=1)
        if not batch:
            break
        entry = batch[0]

        try:
            resp = _get(client, throttle, robots, entry.url)
        except httpx.HTTPError as exc:
            consecutive_failures += 1
            report.failed += 1
            if entry.attempts + 1 >= MAX_ATTEMPTS:
                frontier.mark(entry.url, UrlStatus.FAILED, note=str(exc)[:200])
            else:
                frontier.requeue(entry.url, note=str(exc)[:200])
            if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                report.stopped_reason = f"{consecutive_failures} consecutive transport failures"
                break
            continue

        if resp is None:
            # robots.txt said no. Terminal, and never retried on a later run.
            report.blocked += 1
            frontier.mark(entry.url, UrlStatus.BLOCKED, note="robots.txt")
            continue

        if resp.status_code in (429, 503):
            # The server is asking us to slow down. Honour Retry-After when it sends one —
            # it is a better number than anything we could guess.
            retry_after = _retry_after_seconds(resp) or backoff
            log.info("%s said %s — backing off %.0fs", host, resp.status_code, retry_after)
            if retry_after > MAX_BACKOFF_S:
                report.stopped_reason = f"{host} asked for {retry_after:.0f}s — stopping instead"
                frontier.requeue(entry.url, note=f"http {resp.status_code}")
                break
            sleep(retry_after)
            backoff = min(backoff * 2, MAX_BACKOFF_S)
            consecutive_failures += 1
            frontier.requeue(entry.url, note=f"http {resp.status_code}")
            if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                report.stopped_reason = f"{host} rate-limited {consecutive_failures} times in a row"
                break
            continue

        backoff = settings.crawler_delay_s
        consecutive_failures = 0
        report.fetched += 1

        if resp.status_code != 200:
            report.skipped += 1
            frontier.mark(entry.url, UrlStatus.SKIPPED, note=f"http {resp.status_code}")
            continue

        parsed = parse_html(resp.text)
        if parsed is None:
            # Not a recipe page. Ordinary — and terminal, so it is never fetched again.
            report.skipped += 1
            frontier.mark(entry.url, UrlStatus.SKIPPED, note="no schema.org/Recipe")
            continue

        upsert_recipe(db, site, entry.url, parsed, report)
        db.commit()
        report.recipes += 1
        frontier.mark(entry.url, UrlStatus.DONE)

    return report


def _retry_after_seconds(resp: httpx.Response) -> float | None:
    raw = resp.headers.get("Retry-After")
    if not raw:
        return None
    try:
        return max(float(raw), 0.0)
    except ValueError:
        # The HTTP-date form. Not worth parsing for our purposes — falling through to the
        # exponential backoff is both simpler and never *less* polite than the header asked.
        return None
