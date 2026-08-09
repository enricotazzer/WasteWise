"""Recipe ingredient strings -> canonical product ids.

**One matcher, not two.** `resolve_product_id` below is the function `seeds/load_recipes.py`
uses, imported from here rather than reimplemented, and it is the same `normalize_text` +
`ProductAlias` lookup the barcode path uses. That matters for a reason beyond tidiness: the
seed corpus's published match rate (420/420) and the crawler's much lower one are then
measurements of the *same* matcher on different inputs, so the gap between them says
something about the inputs. Two matchers would make the comparison meaningless.

What is genuinely different is the *parsing* in front of it, which is why that part is here
and not shared. A hand-written seed corpus writes every ingredient the same way:

    "400 g di passata di pomodoro"     quantity, unit, preposition, ingredient

A crawled corpus writes them four ways, all of these observed on the three sites crawled:

    "350 gr di pasta corta tipo rigatoni"       leading measure, then a varietal qualifier
    "spinaci 350 g lessati, peso da cotti"      measure *after* the name, plus a note
    "rotolo di pasta per pizza 1"               bare trailing count
    "uova 1 il tuorlo, per spennellare"         count, then which part of it to use

`seeds/load_recipes.parse_ingredient` stays as it is — it is tested, its behaviour is
published in the seed loader's output, and it must keep treating "farina 00" as flour of
grade 00 rather than as a quantity of flour. `parse_line` here does the messier job, and
`resolve_line` pairs it with a prefix backoff: if the whole phrase misses, the head of it is
tried, because "pasta corta tipo rigatoni" is pasta and "passata di pomodoro" is not passata
of something else. Longest first, so the more specific product always wins.
"""

import re
import unicodedata
from dataclasses import dataclass
from html import unescape

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Product, ProductAlias
from app.services.normalize import normalize_text

# Measure words an Italian recipe puts between the number and the ingredient. Longest forms
# first so "cucchiaino" is never matched as "cucchiaio" with a stray "no" left behind.
_UNITS = (
    "cucchiaini|cucchiaino|cucchiai|cucchiaio|bicchieri|bicchiere|confezioni|confezione|"
    "barattoli|barattolo|vasetti|vasetto|bustine|bustina|scatole|scatola|mazzetti|mazzetto|"
    "rametti|rametto|pizzichi|pizzico|spicchi|spicchio|foglie|foglia|fette|fetta|tazze|tazza|"
    "grammi|litri|litro|kg|gr|g|ml|cl|dl|lt|l|pz|pezzi|pezzo"
)

# Vulgar fractions appear in scraped text often enough to matter, and NFKD turns them into
# "1⁄2" rather than a number, so they are mapped explicitly.
_FRACTIONS = {"½": "0.5", "⅓": "0.33", "¼": "0.25", "¾": "0.75", "⅔": "0.66", "⅛": "0.125"}

_QTY = r"\d+(?:[.,]\d+)?(?:\s*[-–/]\s*\d+(?:[.,]\d+)?)?"

_LEADING = re.compile(
    rf"^\s*(?P<qty>{_QTY})?\s*(?P<unit>{_UNITS})?\b\s*(?:di\s+|d'\s*|dello\s+|della\s+|degli\s+"
    rf"|delle\s+|del\s+|dei\s+)?",
    flags=re.IGNORECASE,
)
_TRAILING = re.compile(
    rf"\s+(?P<qty>{_QTY})\s*(?P<unit>{_UNITS})?\b.*$",
    flags=re.IGNORECASE,
)

# "q.b." (quanto basta) is a quantity in the same sense that "to taste" is one.
_QB = re.compile(r"\bq\.?\s*b\.?\b", flags=re.IGNORECASE)
# A parenthetical is always an aside: "(io uso quella di bufala)".
_PARENS = re.compile(r"\([^)]*\)")
# A comma between two digits is an Italian decimal point, not the preparation-note separator
# that the comma cut in `parse_line` is looking for. Italian recipes write "1,5 kg" by
# default, so without this the cut turns that line into "1" and the ingredient disappears
# entirely — a silent drain on the match rate rather than a visible parse failure.
_DECIMAL_COMMA = re.compile(r"(\d)\s*,\s*(\d)")
# Articles and leftovers that survive the measure strip.
_LEADING_NOISE = re.compile(
    r"^(?:un|uno|una|un'|il|lo|la|i|gli|le|di|d'|circa|qualche)\s+", flags=re.IGNORECASE
)
_SPACES = re.compile(r"\s+")

# Prefix backoff stops here. Past four words a "prefix" is no longer a shorter name for the
# same thing, it is a different phrase that happens to start the same way.
_MAX_BACKOFF_WORDS = 4


@dataclass(frozen=True, slots=True)
class ParsedIngredient:
    """One `recipeIngredient` line, split into what it says and how much of it."""

    raw: str
    text: str
    quantity: float | None = None
    unit: str | None = None

    @property
    def is_empty(self) -> bool:
        return not self.text


def _to_float(raw: str | None) -> float | None:
    """ "1,5" -> 1.5; "4-6" -> 4. The low end of a range, for the same reason `parse_yield`
    takes it: it is the amount you can be sure the recipe works with."""
    if not raw:
        return None
    head = re.split(r"[-–/]", raw)[0].strip().replace(",", ".")
    try:
        return float(head)
    except ValueError:
        return None


def parse_line(raw: str) -> ParsedIngredient:
    """Split a crawled ingredient line into quantity, unit and ingredient text.

    The comma cut is the load-bearing decision and worth stating plainly: everything after
    the first comma is dropped. On every site crawled a comma introduces a preparation note
    ("lessati, peso da cotti" / "1 il tuorlo, per spennellare"), never a second ingredient —
    recipe markup puts one ingredient per array element. Keeping the tail would push every
    line past the point where prefix backoff can recover it.
    """
    text = unescape(raw or "")
    text = unicodedata.normalize("NFC", text)
    for glyph, value in _FRACTIONS.items():
        text = text.replace(glyph, value)
    text = _PARENS.sub(" ", text)
    text = _QB.sub(" ", text)
    text = _DECIMAL_COMMA.sub(r"\1.\2", text)  # before the cut below, which would halve it
    text = text.split(",")[0]
    text = _SPACES.sub(" ", text).strip(" -–—•*·:;.")

    if not text:
        return ParsedIngredient(raw=raw, text="")

    quantity: float | None = None
    unit: str | None = None

    leading = _LEADING.match(text)
    if leading and (leading.group("qty") or leading.group("unit")):
        quantity = _to_float(leading.group("qty"))
        unit = (leading.group("unit") or "").lower() or None
        text = text[leading.end() :].strip()
    else:
        # No leading measure: the site may put it after the name instead. Only strip a
        # trailing one when something is left in front of it, so a line that is *only* a
        # quantity doesn't reduce to the empty string.
        trailing = _TRAILING.search(text)
        if trailing and trailing.start() > 0:
            quantity = _to_float(trailing.group("qty"))
            unit = (trailing.group("unit") or "").lower() or None
            text = text[: trailing.start()].strip()

    previous = None
    while previous != text:
        previous = text
        text = _LEADING_NOISE.sub("", text).strip()

    return ParsedIngredient(raw=raw, text=text, quantity=quantity, unit=unit)


def resolve_product_id(db: Session, text: str) -> int | None:
    """Ingredient text -> product id, or None.

    The same two tiers as the barcode resolver, in the same order: the learned alias table
    first, then an exact canonical name. Aliases strip pack sizes and accents, canonical
    names don't, so the second tier catches what the first normalizes away.

    This is *the* matcher. `seeds/load_recipes` imports it rather than keeping its own copy,
    so an alias confirmed anywhere in the app improves recipe matching everywhere.
    """
    norm = normalize_text(text)
    if not norm:
        return None

    alias = db.scalar(select(ProductAlias).where(ProductAlias.raw_text_norm == norm))
    if alias is not None:
        return alias.product_id

    product = db.scalar(select(Product).where(Product.name_it.ilike(text.strip())))
    return product.id if product else None


def resolve_line(db: Session, raw: str) -> tuple[int | None, str]:
    """A crawled ingredient line -> (product id or None, the text that was matched on).

    Returns the text as well as the id because an unmatched string is the actionable half of
    the result: it is a missing alias, and the crawl reports the commonest ones so they can
    be added in one pass rather than hunted one at a time.

    Prefix backoff, longest first. "pasta corta tipo rigatoni" misses as a whole phrase and
    hits on "pasta"; "passata di pomodoro" hits as a whole phrase and never gets shortened,
    which is the point of trying the longest form first.
    """
    parsed = parse_line(raw)
    if parsed.is_empty:
        return None, ""

    words = parsed.text.split()
    for length in range(min(len(words), _MAX_BACKOFF_WORDS), 0, -1):
        candidate = " ".join(words[:length])
        product_id = resolve_product_id(db, candidate)
        if product_id is not None:
            return product_id, candidate
    return None, parsed.text
