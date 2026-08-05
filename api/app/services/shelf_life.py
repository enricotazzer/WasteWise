"""Expiry estimation.

Receipts do not carry expiry dates, so every item's expiry is inferred unless the user
types the printed date. The fallback chain mirrors the survival model's cold-start chain
in PLAN.md: the most specific source available wins, and the source is recorded on the
item so Phase 3 can weight (or exclude) weakly-grounded dates.

    user-typed label  ->  product.shelf_life_days  ->  category.default_shelf_life_days

Opening an item usually matters more than the printed date (an opened passata lasts days,
not months), so `shelf_life_days_opened` overrides the rest once `opened_at` is set.
"""

from datetime import date, timedelta

from app.models import Product
from app.models.enums import ExpirySource


def estimate_expiry(
    product: Product,
    acquired_at: date,
    opened_at: date | None = None,
    label_expiry: date | None = None,
) -> tuple[date | None, ExpirySource | None]:
    """Return (expiry_date, source). Both are None for non-perishables."""

    # A date the user read off the package always wins.
    if label_expiry is not None:
        # Opening can still shorten it.
        if opened_at is not None and product.shelf_life_days_opened is not None:
            opened_expiry = opened_at + timedelta(days=product.shelf_life_days_opened)
            if opened_expiry < label_expiry:
                return opened_expiry, ExpirySource.PRODUCT
        return label_expiry, ExpirySource.LABEL

    if opened_at is not None and product.shelf_life_days_opened is not None:
        return opened_at + timedelta(days=product.shelf_life_days_opened), ExpirySource.PRODUCT

    if product.shelf_life_days is not None:
        return acquired_at + timedelta(days=product.shelf_life_days), ExpirySource.PRODUCT

    category = product.category
    if category is not None and category.default_shelf_life_days is not None:
        return (
            acquired_at + timedelta(days=category.default_shelf_life_days),
            ExpirySource.CATEGORY,
        )

    # Non-perishable (pasta, detergent): no meaningful expiry.
    return None, None
