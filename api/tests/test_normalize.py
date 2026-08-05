"""Alias normalization.

These assertions matter more than they look: `raw_text_norm` is the unique key of
ProductAlias, so if normalization is unstable the alias table never accumulates hits and
tier-1 matching (Phase 1) stays permanently cold.
"""

import pytest

from app.services.normalize import normalize_text


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Mozzarella", "MOZZARELLA"),
        ("  mozzarella  ", "MOZZARELLA"),
        ("MOZZAREL.  BUFALA", "MOZZAREL BUFALA"),
        ("Caffè", "CAFFE"),  # accents folded
        ("Prosciutto  crudo", "PROSCIUTTO CRUDO"),  # whitespace collapsed
        ("", ""),
    ],
)
def test_basic_normalization(raw: str, expected: str) -> None:
    assert normalize_text(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "LATTE 1L",
        "LATTE 500ML",
        "LATTE 1,5 L",
        "LATTE  2x500g",
    ],
)
def test_pack_sizes_are_stripped(raw: str) -> None:
    """Same product, different pack size, must collapse to one alias key — otherwise every
    pack variant becomes a separate unmatched alias."""
    assert normalize_text(raw) == "LATTE"


def test_accent_and_case_variants_converge() -> None:
    variants = ["Caffè", "CAFFE", "caffe", "Caffé"]
    assert len({normalize_text(v) for v in variants}) == 1


def test_distinct_products_do_not_collide() -> None:
    assert normalize_text("Latte intero") != normalize_text("Latte scremato")
