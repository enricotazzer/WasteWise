"""Open Food Facts lookup.

Free and keyless, but with teeth: OFF documents ~100 req/min for product GETs and bans IPs
that ignore it, and it rejects requests without a descriptive User-Agent. Both are handled
here rather than left to callers.

Rate limiting barely matters in practice because every result is cached permanently in
product_barcodes — the throttle only ever bites during a first big shop.

An unknown barcode is a normal outcome, not an error: OFF answers 200 with `status: 0`.
This returns None for that case so callers can route to manual naming without exception
handling.

API version: **v2, deliberately, not the newer v3.** Verified against the live API on
2026-08-05 with 8076809513692 (Barilla Napoletana): v2 returns `product_name_it` plus a
parsed `product_quantity` / `product_quantity_unit` pair, which is exactly the Italian name
and machine-readable pack size this app needs. v3 wraps the same data in a different
envelope (`status: "success"`, `result.id`) without adding anything used here. v2 is
documented as deprecated-but-supported; if it is ever withdrawn, the change is confined to
`fetch_product` below and the `off_response` builder in tests/factories.py.
"""

import logging
import re
import threading
import time

import httpx
from pydantic import BaseModel

from app.config import settings

log = logging.getLogger(__name__)

# Only what's needed. Requesting everything pulls ~40 KB of nutrition data per product.
FIELDS = (
    "code,product_name,product_name_it,generic_name,generic_name_it,brands,"
    "quantity,product_quantity,product_quantity_unit,categories_tags"
)

_DIGITS = re.compile(r"\D")


class OffProduct(BaseModel):
    """The slice of an OFF record this app uses. `payload` keeps the response verbatim —
    OFF is community-edited, so the remote record can change under us and the snapshot at
    bind time is the only stable record of what we matched on."""

    barcode: str
    name: str | None = None
    brand: str | None = None
    quantity: str | None = None
    quantity_value: float | None = None
    quantity_unit: str | None = None
    categories: list[str] = []
    payload: dict = {}


class _RateLimiter:
    """Fixed-window throttle, shared across threads. Blocks rather than dropping: a scan
    that waits 300 ms is fine, a scan that silently fails is not."""

    def __init__(self, per_minute: int) -> None:
        self._per_minute = per_minute
        self._lock = threading.Lock()
        self._window_start = 0.0
        self._count = 0

    def acquire(self) -> None:
        with self._lock:
            now = time.monotonic()
            if now - self._window_start >= 60.0:
                self._window_start = now
                self._count = 0
            if self._count >= self._per_minute:
                sleep_for = 60.0 - (now - self._window_start)
                if sleep_for > 0:
                    log.info("OFF rate limit reached, sleeping %.1fs", sleep_for)
                    time.sleep(sleep_for)
                self._window_start = time.monotonic()
                self._count = 0
            self._count += 1


_limiter = _RateLimiter(settings.off_rate_limit_per_min)


def normalize_barcode(raw: str) -> str:
    """Strip anything that isn't a digit. Scanners occasionally emit whitespace or a
    trailing newline, and a barcode with a stray space would miss the cache forever."""
    return _DIGITS.sub("", raw or "")


def is_store_internal(barcode: str) -> bool:
    """EAN-13 codes beginning with 2 are the in-store range: supermarkets print them on
    weighed produce and they encode a price, not a product. They are meaningless outside
    that shop and never in OFF, so there is no point spending a network call on them.

    Returning True routes straight to manual naming — and once named, that store's label
    resolves instantly on every future shop.
    """
    return len(barcode) == 13 and barcode.startswith("2")


def _first(*values: str | None) -> str | None:
    for v in values:
        if v and v.strip():
            return v.strip()
    return None


def fetch_product(barcode: str, *, client: httpx.Client | None = None) -> OffProduct | None:
    """Look a barcode up on OFF. None means "no record" — an ordinary outcome.

    Network errors also return None: this is a convenience layer over an add form that
    already works, so it degrades to manual naming rather than failing the request.
    """
    code = normalize_barcode(barcode)
    if not code:
        return None

    url = f"{settings.off_base_url}/api/v2/product/{code}.json"
    params = {"fields": FIELDS, "lc": "it", "cc": "it"}
    headers = {"User-Agent": settings.off_user_agent}

    _limiter.acquire()
    try:
        if client is not None:
            resp = client.get(url, params=params, headers=headers)
        else:
            resp = httpx.get(url, params=params, headers=headers, timeout=settings.off_timeout_s)
    except httpx.HTTPError as exc:
        log.warning("OFF lookup failed for %s: %s", code, exc)
        return None

    if resp.status_code == 404:
        return None
    if resp.status_code != 200:
        log.warning("OFF returned %s for %s", resp.status_code, code)
        return None

    try:
        body = resp.json()
    except ValueError:
        log.warning("OFF returned non-JSON for %s", code)
        return None

    # OFF signals "not found" in the body with status 0, not with an HTTP error.
    if body.get("status") != 1:
        return None

    product = body.get("product") or {}
    quantity_value = product.get("product_quantity")
    try:
        quantity_value = float(quantity_value) if quantity_value not in (None, "") else None
    except (TypeError, ValueError):
        quantity_value = None

    return OffProduct(
        barcode=code,
        # Italian name first: it's what goes in the UI and what normalizes into the alias
        # table. generic_name is the fallback for own-brand products with no product_name.
        name=_first(
            product.get("product_name_it"),
            product.get("product_name"),
            product.get("generic_name_it"),
            product.get("generic_name"),
        ),
        brand=_first((product.get("brands") or "").split(",")[0]),
        quantity=_first(product.get("quantity")),
        quantity_value=quantity_value,
        quantity_unit=_first(product.get("product_quantity_unit")),
        categories=list(product.get("categories_tags") or []),
        payload=product,
    )
