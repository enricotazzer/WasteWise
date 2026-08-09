"""Which Italian recipe sites the crawler is allowed to read, and why.

Every entry here was chosen by reading its `robots.txt` first and its recipe markup second.
The audit is written down rather than summarised, because "we checked" is not a claim
anyone can verify six months later, and because the *rejections* are the interesting part.

Checked on 2026-08-06, with `User-Agent: WasteWiseBot/0.1 (+contact)`.

INCLUDED
--------------------------------------------------------------------------------
misya.info
    `User-agent: *` disallows only `/post2pdf.php`, `/*/feed$` and `/search/`; recipe pages
    are explicitly crawlable. No AI-crawler directives at all. Publishes a single, clean
    `@type: Recipe` document per page with `prepTime` + `cookTime` and `HowToStep`
    instructions. Recipe URLs are `/ricetta/<slug>.htm`, so the sitemap alone tells us which
    pages are recipes and we never fetch a page to find out.

ricettedellanonna.net
    `User-agent: *` disallows the old vBulletin forum and some legacy demo pages. Nothing
    that touches recipes, and no AI-crawler directives. Yoast-style `@graph` wrapper with
    the Recipe alongside an Article node — the shape the `@graph` handling in `jsonld.py`
    exists for. Instructions arrive as a list of strings carrying HTML entities.

fattoincasadabenedetta.it
    `User-agent: *` blocks admin paths and, notably, every URL with a query string
    (`Disallow: /*?*`). We only ever request clean sitemap URLs, so nothing we fetch is
    affected. No AI-crawler directives. Has a dedicated `recipe-sitemap*.xml`, which is the
    politest possible arrangement: 100% of what we request is a recipe page. Its ingredient
    lines put the measure *after* the name ("spinaci 350 g lessati"), which is the shape
    `ingredients.parse_line` handles and the seed parser deliberately does not.

SKIPPED, and why
--------------------------------------------------------------------------------
giallozafferano.it / ricette.giallozafferano.it
    The obvious first choice — the largest Italian recipe site, clean JSON-LD, a published
    recipe sitemap — and skipped anyway. Its `robots.txt` carries an explicit block list for
    AI-adjacent crawlers: `GPTBot`, `ClaudeBot`, `Claude-Web`, `anthropic-ai`, `CCBot`,
    `cohere-ai`, `Google-Extended`, `PerplexityBot`, `Bytespider`, `meta-externalagent`.
    A literal reading lets us in, because `WasteWiseBot` is none of those and the
    `User-agent: *` group says `Allow: /`. We are not taking the literal reading. This
    corpus is retrieved over and then handed to an LLM to phrase, which is squarely the
    class of use that list is drawn around, and "our bot has a different name" is not an
    argument anyone would make in good faith. Skipped.

cookaround.com
    Same block list (`GPTBot`, `ClaudeBot`, `anthropic-ai`, `CCBot`, ...), same reasoning.
    Skipped.

blog.giallozafferano.it
    `Allow: /` with no exclusions whatsoever, so on its own terms it is open. It is the
    same publisher as the site above, and reading the umbrella brand's intent off one host
    and ignoring it on another is exactly the kind of technicality this file exists to avoid.
    Skipped.

cucchiaio.it
    `robots.txt` itself returns 403 behind Akamai. A site whose crawl policy we cannot read
    is a site we do not crawl — the fail-closed rule in `crawler.py` would refuse it anyway.

buonissimo.org
    `robots.txt` returns 410 Gone. Note that this one is a *judgement*, not the fail-closed
    rule: RFC 9309 treats any 4xx other than 401/403 as "no policy published, crawl freely",
    and `RobotsCache` implements exactly that, so the code would have allowed this host.
    Excluded here by hand instead, because a site actively serving 410 for robots.txt is one
    whose intent we would be guessing at, and there are three willing sites already.

ricette.donnamoderna.com
    `robots.txt` allows crawling, but recipe pages answer 403 to a non-browser User-Agent.
    Working around bot protection is not "obeying robots.txt in spirit", so it is out.
"""

import re
from dataclasses import dataclass
from urllib.parse import urlsplit


@dataclass(frozen=True, slots=True)
class Site:
    """One crawlable site. `name` is written to `Recipe.source_site` and is the provenance
    that lets a crawled recipe supersede a `seed` one without touching anything else."""

    name: str
    sitemap_index: str
    #: Which sub-sitemaps of the index are worth opening. Every site's index also lists tag,
    #: category and author sitemaps, which contain no recipes and thousands of URLs.
    sitemap_pattern: re.Pattern[str]
    #: Which `<loc>` entries are recipe pages. This is the filter that keeps the crawl
    #: courteous: a URL that can't be a recipe is never requested at all.
    recipe_pattern: re.Pattern[str]

    @property
    def host(self) -> str:
        return urlsplit(self.sitemap_index).netloc


SITES: tuple[Site, ...] = (
    Site(
        name="misya.info",
        sitemap_index="https://www.misya.info/sitemap.xml",
        sitemap_pattern=re.compile(r"/post-sitemap\d*\.xml$"),
        recipe_pattern=re.compile(r"^https://www\.misya\.info/ricetta/[^/?#]+\.htm$"),
    ),
    Site(
        name="ricettedellanonna.net",
        sitemap_index="https://www.ricettedellanonna.net/sitemap_index.xml",
        sitemap_pattern=re.compile(r"/post-sitemap\d*\.xml$"),
        recipe_pattern=re.compile(r"^https://www\.ricettedellanonna\.net/[a-z0-9%._-]+/$"),
    ),
    Site(
        name="fattoincasadabenedetta.it",
        sitemap_index="https://www.fattoincasadabenedetta.it/sitemap_index.xml",
        sitemap_pattern=re.compile(r"/recipe-sitemap\d*\.xml$"),
        recipe_pattern=re.compile(r"^https://www\.fattoincasadabenedetta\.it/ricetta/[^/?#]+/$"),
    ),
)


def site_by_name(name: str) -> Site | None:
    return next((s for s in SITES if s.name == name), None)
