"""Receipt text normalization.

Italian receipts print product names uppercased, abbreviated and padded, e.g.
    "MOZZAREL.  BUFALA 125G"  ->  "MOZZAREL BUFALA 125G"

The normalized form is the unique key of ProductAlias, so it has to be stable: the same
physical product must normalize identically across receipts and stores, or the alias table
never accumulates hits and tier-1 matching stays cold forever.

Phase 0 only needs this for seeding. Phase 1 uses it for the real matching pipeline.
"""

import re
import unicodedata

_PUNCT = re.compile(r"[^\w\s]", flags=re.UNICODE)
_SPACES = re.compile(r"\s+")
# Multipack prefix: the "2X" in "YOGURT 2X125G". Stripped first so the quantity+unit that
# follows is left at a word boundary for _PACK_SIZE to match.
_MULTIPLIER = re.compile(r"\b\d+\s*[xX]\s*(?=\d)")
# Pack sizes carry no identity: "LATTE 1L" and "LATTE 500ML" are the same product, and
# treating them as different aliases would fragment the alias table.
_PACK_SIZE = re.compile(r"\b\d+[.,]?\d*\s*(kg|gr|g|lt|l|ml|cl|pz)\b", flags=re.IGNORECASE)


def normalize_text(raw: str) -> str:
    """Uppercase, strip accents/punctuation/pack sizes, collapse whitespace."""
    if not raw:
        return ""
    # Decompose accents then drop the combining marks: "Caffè" -> "CAFFE"
    s = unicodedata.normalize("NFKD", raw)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = s.upper()
    s = _MULTIPLIER.sub(" ", s)
    s = _PACK_SIZE.sub(" ", s)
    s = _PUNCT.sub(" ", s)
    s = _SPACES.sub(" ", s).strip()
    return s
