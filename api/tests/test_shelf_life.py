"""Expiry inference.

The fallback chain (label -> product -> category -> none) is the cold-start prior for the
whole risk model, and `expiry_source` is what Phase 3 uses to decide how much to trust a
given item's date. Both are asserted here.
"""

from datetime import date, timedelta

from app.models import Category, Product
from app.models.enums import ExpirySource, Storage, Unit
from app.services.shelf_life import estimate_expiry

ACQUIRED = date(2026, 8, 3)


def _category(days: int | None) -> Category:
    return Category(
        code="TEST",
        name_it="Test",
        name_en="Test",
        storage=Storage.FRIDGE,
        default_shelf_life_days=days,
    )


def _product(
    *, shelf_life: int | None, opened: int | None = None, cat_days: int | None = 7
) -> Product:
    product = Product(
        name_it="Test", name_en="test", default_unit=Unit.PZ, shelf_life_days=shelf_life
    )
    product.shelf_life_days_opened = opened
    product.category = _category(cat_days)
    return product


def test_label_date_wins_over_everything() -> None:
    label = date(2026, 8, 20)
    expiry, source = estimate_expiry(_product(shelf_life=5), ACQUIRED, label_expiry=label)
    assert (expiry, source) == (label, ExpirySource.LABEL)


def test_product_shelf_life_used_when_no_label() -> None:
    expiry, source = estimate_expiry(_product(shelf_life=5), ACQUIRED)
    assert (expiry, source) == (ACQUIRED + timedelta(days=5), ExpirySource.PRODUCT)


def test_falls_back_to_category_default() -> None:
    expiry, source = estimate_expiry(_product(shelf_life=None, cat_days=7), ACQUIRED)
    assert (expiry, source) == (ACQUIRED + timedelta(days=7), ExpirySource.CATEGORY)


def test_no_expiry_when_nothing_is_known() -> None:
    """Non-perishables (detergent, dried pasta) must not get a fabricated expiry — a fake
    date would show up as a false 'at risk' flag forever."""
    expiry, source = estimate_expiry(_product(shelf_life=None, cat_days=None), ACQUIRED)
    assert expiry is None and source is None


def test_opening_shortens_shelf_life() -> None:
    opened_on = date(2026, 8, 10)
    product = _product(shelf_life=60, opened=3)
    expiry, source = estimate_expiry(product, ACQUIRED, opened_at=opened_on)
    assert (expiry, source) == (opened_on + timedelta(days=3), ExpirySource.PRODUCT)


def test_opening_can_beat_a_printed_label_date() -> None:
    """An opened passata is done in days regardless of the best-before printed on the jar."""
    opened_on = date(2026, 8, 10)
    far_label = date(2027, 1, 1)
    product = _product(shelf_life=540, opened=4)
    expiry, source = estimate_expiry(product, ACQUIRED, opened_at=opened_on, label_expiry=far_label)
    assert expiry == opened_on + timedelta(days=4)
    assert source == ExpirySource.PRODUCT


def test_label_kept_when_it_is_sooner_than_the_opened_estimate() -> None:
    opened_on = date(2026, 8, 10)
    near_label = date(2026, 8, 11)
    product = _product(shelf_life=540, opened=30)
    expiry, source = estimate_expiry(
        product, ACQUIRED, opened_at=opened_on, label_expiry=near_label
    )
    assert (expiry, source) == (near_label, ExpirySource.LABEL)
