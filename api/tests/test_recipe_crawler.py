"""The crawler's courtesy rules, and the frontier that makes a restart cheap.

No network and no real sleeping anywhere in this file. `respx` answers the HTTP, and
`HostThrottle` takes an injectable clock — the delay is proven by asserting what it *asked*
to sleep for, not by waiting.

These are the tests that matter most in the whole Phase 4 suite. Everything else here is
about corpus quality; this is about not being a bad citizen of three sites that never agreed
to any of this.
"""

import httpx
import pytest
import respx

from app.services.recipes.crawler import HostThrottle, RobotsCache
from app.services.recipes.frontier import Frontier, UrlStatus

ROBOTS_OPEN = "User-agent: *\nAllow: /\n"
ROBOTS_BLOCKS_SEARCH = "User-agent: *\nDisallow: /search/\nDisallow: /*?*\n"


class FakeClock:
    """A monotonic clock that only moves when something sleeps."""

    def __init__(self) -> None:
        self.now = 1000.0
        self.slept: list[float] = []

    def time(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


class TestHostThrottle:
    def test_first_request_to_a_host_does_not_wait(self):
        clock = FakeClock()
        t = HostThrottle(2.0, clock=clock.time, sleep=clock.sleep)

        assert t.wait("misya.info") == 0.0
        assert clock.slept == []

    def test_second_request_waits_the_full_delay(self):
        clock = FakeClock()
        t = HostThrottle(2.0, clock=clock.time, sleep=clock.sleep)

        t.wait("misya.info")
        waited = t.wait("misya.info")

        assert waited == pytest.approx(2.0)
        assert clock.slept == [pytest.approx(2.0)]

    def test_the_delay_is_per_host_not_global(self):
        # Three unrelated sites serialised behind one throttle would take three times as
        # long and benefit none of them. Each host gets its own two seconds.
        clock = FakeClock()
        t = HostThrottle(2.0, clock=clock.time, sleep=clock.sleep)

        t.wait("misya.info")
        assert t.wait("ricettedellanonna.net") == 0.0
        assert t.wait("fattoincasadabenedetta.it") == 0.0
        assert clock.slept == []

    def test_time_already_spent_elsewhere_counts_towards_the_delay(self):
        clock = FakeClock()
        t = HostThrottle(2.0, clock=clock.time, sleep=clock.sleep)

        t.wait("misya.info")
        clock.now += 1.5  # parsing, database work, whatever
        waited = t.wait("misya.info")

        assert waited == pytest.approx(0.5)

    def test_the_throttle_cannot_be_disabled_through_the_public_surface(self):
        # A `delay_s=0` escape hatch is the kind of thing that ends up in production.
        clock = FakeClock()
        t = HostThrottle(clock=clock.time, sleep=clock.sleep)  # settings default
        t.wait("misya.info")
        assert t.wait("misya.info") > 0


class TestRobotsCache:
    def _cache(self) -> tuple[RobotsCache, FakeClock]:
        clock = FakeClock()
        throttle = HostThrottle(0.0, clock=clock.time, sleep=clock.sleep)
        return RobotsCache(httpx.Client(), throttle, user_agent="WasteWiseBot/0.1"), clock

    @respx.mock
    def test_an_allowed_path_is_allowed(self):
        respx.get("https://misya.info/robots.txt").mock(
            return_value=httpx.Response(200, text=ROBOTS_OPEN)
        )
        cache, _ = self._cache()
        assert cache.allowed("https://misya.info/ricetta/pasta-alla-norma.htm") is True

    @respx.mock
    def test_a_disallowed_path_is_refused(self):
        respx.get("https://misya.info/robots.txt").mock(
            return_value=httpx.Response(200, text=ROBOTS_BLOCKS_SEARCH)
        )
        cache, _ = self._cache()

        assert cache.allowed("https://misya.info/search/pasta") is False
        assert cache.allowed("https://misya.info/ricetta/x.htm") is True

    @respx.mock
    def test_robots_is_fetched_once_per_host_and_cached(self):
        route = respx.get("https://misya.info/robots.txt").mock(
            return_value=httpx.Response(200, text=ROBOTS_OPEN)
        )
        cache, _ = self._cache()

        for i in range(5):
            cache.allowed(f"https://misya.info/ricetta/{i}.htm")

        assert route.call_count == 1

    @respx.mock
    @pytest.mark.parametrize("status", [401, 403, 500, 503])
    def test_a_refused_or_broken_robots_fails_closed(self, status):
        # A policy we were refused sight of (401/403) or that the server failed to give us
        # (5xx) is not the same as no policy. RFC 9309 says treat it as disallow-all, and
        # the opposite default would amount to assuming consent from silence.
        respx.get("https://blocked.example/robots.txt").mock(return_value=httpx.Response(status))
        cache, _ = self._cache()
        assert cache.allowed("https://blocked.example/ricetta/x.htm") is False

    @respx.mock
    def test_a_network_error_on_robots_also_fails_closed(self):
        respx.get("https://down.example/robots.txt").mock(
            side_effect=httpx.ConnectError("no route")
        )
        cache, _ = self._cache()
        assert cache.allowed("https://down.example/ricetta/x.htm") is False

    @respx.mock
    def test_a_missing_robots_means_no_policy_so_crawling_is_allowed(self):
        # 404 is different from 403: the file genuinely does not exist, which the standard
        # treats as "no restrictions", rather than a policy we were refused sight of.
        respx.get("https://norobots.example/robots.txt").mock(return_value=httpx.Response(404))
        cache, _ = self._cache()
        assert cache.allowed("https://norobots.example/ricetta/x.htm") is True

    @respx.mock
    def test_robots_is_fetched_with_our_own_user_agent(self):
        # `RobotFileParser.read()` would use urllib's agent instead of ours, so a site
        # serving a different policy per agent would be answering a question we didn't ask.
        route = respx.get("https://misya.info/robots.txt").mock(
            return_value=httpx.Response(200, text=ROBOTS_OPEN)
        )
        cache, _ = self._cache()
        cache.allowed("https://misya.info/ricetta/x.htm")

        assert "WasteWiseBot" in route.calls[0].request.headers["user-agent"]


class TestFrontier:
    def test_enqueue_then_resume_from_disk(self, tmp_path):
        # The whole point: at one request per two seconds, restarting from zero would mean
        # asking three sites for thousands of pages we already had.
        path = tmp_path / "frontier.sqlite3"
        f = Frontier(path)
        f.enqueue("misya.info", "misya.info", ["https://misya.info/a", "https://misya.info/b"])
        f.mark("https://misya.info/a", UrlStatus.DONE)
        f.close()

        reopened = Frontier(path)
        assert reopened.completed("misya.info") == 1
        counts = reopened.counts("misya.info")
        assert counts[UrlStatus.QUEUED] == 1
        reopened.close()

    def test_enqueueing_the_same_url_twice_does_not_duplicate_it(self, tmp_path):
        f = Frontier(tmp_path / "f.sqlite3")
        f.enqueue("misya.info", "misya.info", ["https://misya.info/a"])
        added = f.enqueue("misya.info", "misya.info", ["https://misya.info/a"])

        assert added == 0
        assert sum(f.counts("misya.info").values()) == 1
        f.close()

    def test_a_completed_url_is_not_re_enqueued(self, tmp_path):
        # Sitemap discovery re-runs on every crawl; without this, a second run would refetch
        # the entire corpus.
        f = Frontier(tmp_path / "f.sqlite3")
        f.enqueue("misya.info", "misya.info", ["https://misya.info/a"])
        f.mark("https://misya.info/a", UrlStatus.DONE)

        f.enqueue("misya.info", "misya.info", ["https://misya.info/a"])
        assert f.counts("misya.info")[UrlStatus.QUEUED] == 0
        f.close()

    def test_a_failed_url_can_be_requeued(self, tmp_path):
        f = Frontier(tmp_path / "f.sqlite3")
        f.enqueue("misya.info", "misya.info", ["https://misya.info/a"])
        f.mark("https://misya.info/a", UrlStatus.FAILED, note="503")

        assert f.requeue("https://misya.info/a") == 1
        assert f.counts("misya.info")[UrlStatus.QUEUED] == 1
        f.close()

    def test_counts_are_per_host(self, tmp_path):
        f = Frontier(tmp_path / "f.sqlite3")
        f.enqueue("misya.info", "misya.info", ["https://misya.info/a"])
        f.enqueue("nonna", "ricettedellanonna.net", ["https://ricettedellanonna.net/b"])

        assert sum(f.counts("misya.info").values()) == 1
        assert sum(f.counts().values()) == 2
        f.close()
