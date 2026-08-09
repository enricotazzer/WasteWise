"""The crawl frontier: which URLs are queued, done, or not worth trying again.

Persisted and resumable, because a polite crawl is a *slow* crawl. At one request every two
seconds per host, a few thousand pages is a run measured in tens of minutes, and a run that
restarted from zero after a Ctrl-C would either never finish or — worse — re-request pages
it had already fetched, which is exactly the behaviour the rate limit exists to prevent.
Resuming is a courtesy to the sites, not just a convenience for us.

SQLite rather than JSONL, for three reasons:

  - Resumption is a *set membership* question ("have I already fetched this URL?"), asked
    once per candidate. SQLite answers it from a primary-key index; a log file has to be
    replayed into a Python set first, which is fine at ten thousand rows and not fine later.
  - A URL's status changes (queued -> done, or -> failed after a retry). Those are updates.
    An append-only log expresses them as later records that shadow earlier ones, which then
    needs compaction and a "last record wins" rule nobody would remember.
  - A process killed mid-write leaves SQLite consistent. It leaves a JSONL file with half a
    line at the end, which is a parse error on the next run — precisely when resilience was
    the point.

It lives under `/srv/data`, the volume mounted in docker-compose.yml, so it survives a
container rebuild.
"""

import sqlite3
import threading
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

DEFAULT_FRONTIER_PATH = Path("/srv/data/crawl/frontier.sqlite")


class UrlStatus(StrEnum):
    """Why a URL is or isn't going to be fetched again.

    SKIPPED and BLOCKED are kept apart on purpose. SKIPPED is "we fetched it and it had no
    recipe on it" — our judgement about the content. BLOCKED is "robots.txt says don't", and
    that one must never be retried on a later run, so conflating the two would eventually
    turn a robots decision into a repeated request.
    """

    QUEUED = "queued"
    DONE = "done"
    SKIPPED = "skipped"
    FAILED = "failed"
    BLOCKED = "blocked"


#: Statuses that mean "settled" — a resumed crawl leaves these alone.
TERMINAL = (UrlStatus.DONE, UrlStatus.SKIPPED, UrlStatus.BLOCKED)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS frontier (
    url        TEXT PRIMARY KEY,
    host       TEXT NOT NULL,
    site       TEXT NOT NULL,
    status     TEXT NOT NULL,
    attempts   INTEGER NOT NULL DEFAULT 0,
    note       TEXT,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_frontier_host_status ON frontier (host, status);
"""


@dataclass(frozen=True, slots=True)
class FrontierEntry:
    url: str
    host: str
    site: str
    attempts: int


class Frontier:
    """A URL queue that survives the process.

    Thread-safe by a single lock around a single connection. The crawler runs one worker
    thread per host so that the per-host delay doesn't serialise three unrelated sites, and
    frontier writes are microseconds against a two-second wait, so there is nothing to gain
    from a finer-grained scheme and a lot to lose in correctness.
    """

    def __init__(self, path: Path | str = DEFAULT_FRONTIER_PATH) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        # WAL so a reader (a second terminal running `sqlite3` to watch progress) never
        # blocks the crawl, and so an interrupted write rolls back cleanly.
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    # --- writing -----------------------------------------------------------------

    def enqueue(self, site: str, host: str, urls: Iterable[str]) -> int:
        """Add URLs, ignoring any already known. Returns how many were genuinely new.

        `INSERT OR IGNORE` is what makes re-seeding from a sitemap idempotent: the sitemap
        is re-read on every run and is mostly the same URLs, and a row that already says
        `done` must keep saying so.
        """
        now = datetime.now(UTC).isoformat()
        rows = [(url, host, site, UrlStatus.QUEUED.value, now) for url in urls]
        if not rows:
            return 0
        with self._lock:
            before = self._conn.total_changes
            self._conn.executemany(
                "INSERT OR IGNORE INTO frontier (url, host, site, status, updated_at) "
                "VALUES (?, ?, ?, ?, ?)",
                rows,
            )
            self._conn.commit()
            return self._conn.total_changes - before

    def mark(self, url: str, status: UrlStatus, *, note: str | None = None) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE frontier SET status = ?, note = ?, updated_at = ? WHERE url = ?",
                (status.value, note, datetime.now(UTC).isoformat(), url),
            )
            self._conn.commit()

    def requeue(self, url: str, *, note: str | None = None) -> int:
        """Put a URL back and count the attempt. Returns the new attempt count, which is what
        the caller uses to decide whether to give up on it."""
        with self._lock:
            self._conn.execute(
                "UPDATE frontier SET status = ?, attempts = attempts + 1, note = ?, "
                "updated_at = ? WHERE url = ?",
                (UrlStatus.QUEUED.value, note, datetime.now(UTC).isoformat(), url),
            )
            self._conn.commit()
            row = self._conn.execute(
                "SELECT attempts FROM frontier WHERE url = ?", (url,)
            ).fetchone()
        return int(row["attempts"]) if row else 0

    # --- reading -----------------------------------------------------------------

    def next_queued(self, host: str, *, limit: int = 1) -> list[FrontierEntry]:
        """The next queued URLs for a host, fewest attempts first.

        Ordering by `attempts` puts anything that has already failed once at the back, so a
        run isn't spent retrying the same broken URL while fresh ones wait.
        """
        with self._lock:
            rows = self._conn.execute(
                "SELECT url, host, site, attempts FROM frontier "
                "WHERE host = ? AND status = ? ORDER BY attempts, rowid LIMIT ?",
                (host, UrlStatus.QUEUED.value, limit),
            ).fetchall()
        return [
            FrontierEntry(url=r["url"], host=r["host"], site=r["site"], attempts=r["attempts"])
            for r in rows
        ]

    def counts(self, host: str | None = None) -> Counter[str]:
        sql = "SELECT status, count(*) AS n FROM frontier"
        params: tuple = ()
        if host is not None:
            sql += " WHERE host = ?"
            params = (host,)
        sql += " GROUP BY status"
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return Counter({r["status"]: r["n"] for r in rows})

    def completed(self, host: str) -> int:
        """Pages already accounted for on this host — the budget `crawler_max_pages_per_host`
        is spent against, so a resumed crawl doesn't get a fresh allowance."""
        counts = self.counts(host)
        return sum(counts[status.value] for status in TERMINAL)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def __enter__(self) -> "Frontier":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
