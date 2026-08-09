"""schema.org/Recipe extraction from a page's embedded JSON-LD.

Recipe sites publish a machine-readable copy of the recipe in a
`<script type="application/ld+json">` tag, because Google's rich results depend on it. That
copy is the thing worth reading: it is the same data the site's own template renders, it is
versioned by the publisher rather than by us, and it survives a redesign that would break
any CSS selector. Nothing here looks at the surrounding HTML beyond finding those tags —
`selectolax` locates them, and everything after that is JSON.

The awkward part is that "JSON-LD" describes a family of shapes rather than one, and every
site in the corpus picked a different member of it. All of these are real, observed on the
three sites this crawler uses:

    - a bare `{"@type": "Recipe", ...}` document                    (misya.info)
    - a `{"@graph": [...]}` wrapper with the Recipe buried inside    (Yoast/WordPress)
    - a top-level array of unrelated nodes
    - `"@type": ["Recipe", "NewsArticle"]` — a list, not a string
    - `recipeInstructions` as a paragraph, a list of strings, a list of `HowToStep`,
      or `HowToSection`s that contain the steps one level down
    - `totalTime` absent, with `prepTime` + `cookTime` present instead
    - `recipeYield` as `4`, `"4"`, `"4 persone"` or `["4", "4 servings"]`

A page with one malformed `<script>` block among several is also normal — ad tooling and
consent managers inject their own, and they are not always valid JSON. One bad block must
not cost us the page, so parsing is per-block and failures are skipped rather than raised.

Pure functions, no network, no database: this module turns a string of HTML into a
`ParsedRecipe` or None, which is what makes it testable against saved fixtures.
"""

import json
import logging
import re
from dataclasses import dataclass, field
from html import unescape
from typing import Any

from selectolax.parser import HTMLParser

log = logging.getLogger(__name__)

_TAG = re.compile(r"<[^>]+>")
_HORIZONTAL_SPACE = re.compile(r"[ \t   ]+")
_BLANK_LINES = re.compile(r"\n{2,}")

# ISO-8601 durations, in the two dialects the corpus actually emits: the compact "PT1H30M"
# and the fully-qualified "P0DT0H30M0S" that WP Recipe Maker writes. Weeks are accepted
# because the grammar allows them, not because a recipe has ever needed one.
_DURATION = re.compile(
    r"^P(?!$)"
    r"(?:(?P<weeks>\d+(?:[.,]\d+)?)W)?"
    r"(?:(?P<days>\d+(?:[.,]\d+)?)D)?"
    r"(?:T(?!$)"
    r"(?:(?P<hours>\d+(?:[.,]\d+)?)H)?"
    r"(?:(?P<minutes>\d+(?:[.,]\d+)?)M)?"
    r"(?:(?P<seconds>\d+(?:[.,]\d+)?)S)?"
    r")?$",
    re.IGNORECASE,
)
_FIRST_NUMBER = re.compile(r"\d+")

# A yield outside this range is not a serving count. Sites put "500" (grams) and "1"
# (one cake) in the same field, and a 500-serving recipe would silently divide every
# per-portion figure downstream by five hundred.
_MIN_SERVINGS = 1
_MAX_SERVINGS = 50

# Nothing legitimate is this long, and a runaway instructions blob is usually a template
# that dumped the whole page into the field.
_MAX_INSTRUCTIONS_CHARS = 12_000


@dataclass(frozen=True, slots=True)
class ParsedRecipe:
    """One recipe as the page published it. Nothing here is resolved or normalized yet —
    that happens in `ingredients.py`, against the catalog."""

    name: str
    ingredients: list[str] = field(default_factory=list)
    instructions: str | None = None
    servings: int | None = None
    total_minutes: int | None = None


def script_blocks(html: str) -> list[str]:
    """Every `application/ld+json` payload on the page, verbatim.

    `selectolax` rather than a regex over `<script>`: a JSON-LD block routinely contains a
    `</` inside a string (escaped URLs, HTML in a description), and a regex that stops at
    the first one truncates the document into something unparseable.
    """
    tree = HTMLParser(html)
    blocks: list[str] = []
    for node in tree.css('script[type="application/ld+json"]'):
        text = node.text(deep=True, strip=False)
        if text and text.strip():
            blocks.append(text)
    return blocks


def _load(block: str) -> Any | None:
    """Parse one block, or None. A CDATA wrapper is stripped first — older CMSs emit it and
    it is not valid JSON."""
    text = block.strip()
    if text.startswith("<![CDATA["):
        text = text[len("<![CDATA[") :]
        if text.rstrip().endswith("]]>"):
            text = text.rstrip()[: -len("]]>")]
    try:
        return json.loads(text)
    except ValueError as exc:
        # Expected, not exceptional: consent managers and ad tooling inject their own
        # blocks and some of them are malformed. Losing the page over one would be absurd.
        log.debug("skipping unparseable ld+json block: %s", exc)
        return None


def _walk(node: Any) -> list[dict[str, Any]]:
    """Every dict reachable from a JSON-LD document, `@graph` and arrays included.

    Deliberately broad. The Recipe is sometimes the document, sometimes an element of a
    top-level array, sometimes a member of `@graph`, and occasionally nested one level
    deeper inside a `mainEntity`. Walking is cheaper than enumerating the shapes.
    """
    found: list[dict[str, Any]] = []
    stack: list[Any] = [node]
    seen: set[int] = set()
    while stack:
        current = stack.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        if isinstance(current, list):
            stack.extend(current)
        elif isinstance(current, dict):
            found.append(current)
            stack.extend(current.values())
    return found


def node_types(node: dict[str, Any]) -> set[str]:
    """`@type` normalized to a set. It is a string on most sites and a list on the ones that
    publish a recipe and an article from the same page."""
    raw = node.get("@type")
    if isinstance(raw, str):
        return {raw}
    if isinstance(raw, list):
        return {t for t in raw if isinstance(t, str)}
    return set()


def find_recipe(document: Any) -> dict[str, Any] | None:
    """The first Recipe node in a JSON-LD document, or None.

    Requires `recipeIngredient` as well as the type. A `@type: Recipe` node with no
    ingredients is a stub — a category page's summary card, or a "related recipes" widget —
    and importing it would put a title with an empty ingredient list into the corpus, where
    it would rank as trivially cookable.
    """
    for node in _walk(document):
        if "Recipe" in node_types(node) and node.get("recipeIngredient"):
            return node
    return None


def parse_duration_minutes(value: Any) -> int | None:
    """ISO-8601 duration -> whole minutes. Anything unrecognised is None, never 0."""
    if isinstance(value, int | float) and not isinstance(value, bool):
        return int(value) if value > 0 else None
    if not isinstance(value, str):
        return None
    match = _DURATION.match(value.strip())
    if match is None:
        return None

    def part(name: str) -> float:
        raw = match.group(name)
        return float(raw.replace(",", ".")) if raw else 0.0

    minutes = (
        part("weeks") * 7 * 24 * 60
        + part("days") * 24 * 60
        + part("hours") * 60
        + part("minutes")
        + part("seconds") / 60
    )
    return int(round(minutes)) or None


def parse_yield(value: Any) -> int | None:
    """`recipeYield` -> a serving count.

    "4 persone", "per 4-6 persone" and `["4", "4 servings"]` all mean four. The first
    number wins for a range, which is the conservative reading: a recipe that serves 4–6 is
    a recipe you can definitely feed four people with.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        candidates: list[Any] = [value]
    elif isinstance(value, str):
        candidates = [value]
    elif isinstance(value, list):
        candidates = list(value)
    else:
        return None

    for candidate in candidates:
        if isinstance(candidate, bool):
            continue
        if isinstance(candidate, int | float):
            number = int(candidate)
        elif isinstance(candidate, str):
            match = _FIRST_NUMBER.search(candidate)
            if match is None:
                continue
            number = int(match.group())
        else:
            continue
        if _MIN_SERVINGS <= number <= _MAX_SERVINGS:
            return number
    return None


def _clean_text(raw: Any) -> str:
    """One field of published text -> plain text.

    Entities and stray markup both appear inside JSON-LD string values — `&#8217;` in an
    instruction and a `<br>` between two steps are both routine — because the site is
    serialising the same fragment it renders.
    """
    if not isinstance(raw, str):
        return ""
    text = _TAG.sub(" ", raw)
    text = unescape(text)
    text = _HORIZONTAL_SPACE.sub(" ", text)
    return text.strip()


def _instruction_steps(value: Any) -> list[str]:
    """Flatten `recipeInstructions` into ordered step texts.

    Handles the four published shapes plus `HowToSection`, which nests its steps in
    `itemListElement` and is the reason this recurses instead of looping.
    """
    if isinstance(value, str):
        text = _clean_text(value)
        return [text] if text else []

    if isinstance(value, list):
        steps: list[str] = []
        for element in value:
            steps.extend(_instruction_steps(element))
        return steps

    if isinstance(value, dict):
        types = node_types(value)
        if "HowToSection" in types:
            inner = _instruction_steps(value.get("itemListElement"))
            heading = _clean_text(value.get("name"))
            # The section name is kept as its own line: "Per la base" / "Per la crema" is
            # load-bearing in Italian baking recipes, not decoration.
            return ([heading] if heading and inner else []) + inner
        # HowToStep, HowToTip, or an untyped {"text": ...} — all carry their prose the same
        # way, so the type is not worth branching on.
        text = _clean_text(value.get("text")) or _clean_text(value.get("name"))
        return [text] if text else []

    return []


def _ingredient_lines(value: Any) -> list[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    lines: list[str] = []
    for element in value:
        if isinstance(element, dict):
            # A few sites wrap each ingredient as {"@type": "HowToSupply", "name": ...}.
            element = element.get("name") or element.get("text")
        text = _clean_text(element)
        if text:
            lines.append(text)
    return lines


def parse_html(html: str) -> ParsedRecipe | None:
    """A page of HTML -> its Recipe, or None if it doesn't publish one.

    None is the ordinary outcome for most URLs a crawl touches (category listings, articles,
    the site's own index pages), so callers treat it as "not a recipe" rather than an error.
    """
    for block in script_blocks(html):
        document = _load(block)
        if document is None:
            continue
        node = find_recipe(document)
        if node is None:
            continue
        return _to_parsed(node)
    return None


def _to_parsed(node: dict[str, Any]) -> ParsedRecipe | None:
    name = _clean_text(node.get("name")) or _clean_text(node.get("headline"))
    ingredients = _ingredient_lines(node.get("recipeIngredient"))
    if not name or not ingredients:
        return None

    steps = _instruction_steps(node.get("recipeInstructions"))
    instructions = "\n".join(steps)[:_MAX_INSTRUCTIONS_CHARS] or None
    if instructions:
        instructions = _BLANK_LINES.sub("\n", instructions)

    minutes = parse_duration_minutes(node.get("totalTime"))
    if minutes is None:
        # `totalTime` is optional and often missing; prep + cook is the same quantity the
        # site simply chose not to precompute.
        prep = parse_duration_minutes(node.get("prepTime")) or 0
        cook = parse_duration_minutes(node.get("cookTime")) or 0
        minutes = (prep + cook) or None

    return ParsedRecipe(
        name=name,
        ingredients=ingredients,
        instructions=instructions,
        servings=parse_yield(node.get("recipeYield")),
        total_minutes=minutes,
    )
