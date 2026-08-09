"""Ingredient-line parsing, and its resolution against the catalog.

`parse_line` is where a crawled corpus stops being text and starts being data, and it is the
single biggest lever on the ingredient match rate the crawl reports. Every case below is a
real shape from the three crawled sites, not an invented one.
"""

import pytest

from app.models.catalog import Product
from app.models.enums import AliasSource
from app.services.recipes import ingredients


class TestParseLine:
    @pytest.mark.parametrize(
        "raw,text,quantity,unit",
        [
            # The common shape: measure first.
            ("320 g di pasta", "pasta", 320, "g"),
            ("2 melanzane", "melanzane", 2, None),
            ("400 g di passata di pomodoro", "passata di pomodoro", 400, "g"),
            ("1 kg di patate", "patate", 1, "kg"),
            # fattoincasadabenedetta.it puts the measure AFTER the name. The seed parser
            # deliberately doesn't handle this; the crawler must.
            ("spinaci 350 g", "spinaci", 350, "g"),
            ("burro 50 g", "burro", 50, "g"),
            # No measure at all.
            ("basilico q.b.", "basilico", None, None),
            ("sale", "sale", None, None),
        ],
    )
    def test_shapes(self, raw, text, quantity, unit):
        p = ingredients.parse_line(raw)
        assert p.text == text
        assert p.quantity == quantity
        assert p.unit == unit

    def test_everything_after_the_first_comma_is_dropped(self):
        # On every crawled site a comma introduces a preparation note, never a second
        # ingredient — recipe markup puts one ingredient per array element. Keeping the tail
        # pushes the line past what prefix backoff can recover.
        assert ingredients.parse_line("spinaci, lessati e strizzati").text == "spinaci"
        assert ingredients.parse_line("1 uovo, il tuorlo per spennellare").text == "uovo"

    def test_parenthetical_notes_are_removed(self):
        assert ingredients.parse_line("200 g di ricotta (di pecora)").text == "ricotta"

    def test_html_entities_are_decoded(self):
        assert ingredients.parse_line("pepe nero q.b.&nbsp;").text == "pepe nero"

    def test_unicode_fractions_become_numbers(self):
        # "½ cipolla" is written with the glyph on two of the three sites.
        p = ingredients.parse_line("½ cipolla")
        assert p.quantity == 0.5
        assert p.text == "cipolla"

    def test_a_range_takes_the_low_end(self):
        # The amount the recipe is guaranteed to work with.
        assert ingredients.parse_line("4-6 pomodori").quantity == 4

    def test_decimal_comma_is_italian_not_a_separator(self):
        # Regression: the comma cut that strips preparation notes used to run first and
        # halve "1,5 kg di zucca" into "1", losing the ingredient entirely. Italian recipes
        # write decimals this way by default, so this was a silent drain on the match rate.
        p = ingredients.parse_line("1,5 kg di zucca")
        assert p.quantity == 1.5
        assert p.unit == "kg"
        assert p.text == "zucca"

    def test_decimal_comma_survives_alongside_a_real_preparation_note(self):
        p = ingredients.parse_line("0,5 l di latte, tiepido")
        assert p.quantity == 0.5
        assert p.text == "latte"

    def test_a_line_that_is_only_a_quantity_does_not_vanish(self):
        # Stripping a trailing measure off "500 g" would leave the empty string, and an
        # empty ingredient silently shrinks the denominator of the match rate.
        p = ingredients.parse_line("500 g")
        assert p.text != "" or p.is_empty

    def test_empty_and_junk_lines_raise_nothing(self):
        for junk in ["", "   ", "-", "•", " "]:
            assert ingredients.parse_line(junk).is_empty


class TestResolveAgainstCatalog:
    def test_resolves_through_the_existing_alias_pipeline(self, db, seeded):
        # The point of routing crawled ingredients through `normalize_text` + ProductAlias
        # is that there is ONE vocabulary: every barcode confirmation improves recipe
        # matching for free, and vice versa.
        product = db.query(Product).filter(Product.name_it == "Pomodori").one_or_none()
        if product is None:  # pragma: no cover - depends on the seed set
            pytest.skip("seed catalog has no Pomodori")

        pid, text = ingredients.resolve_line(db, "400 g di pomodori")
        assert pid == product.id
        assert text == "pomodori"

    def test_an_unmatched_line_returns_none_and_keeps_its_text(self, db, seeded):
        # An unmatched ingredient is a missing alias, reported by the crawl job — never a
        # silent drop, which would quietly inflate the match rate.
        pid, text = ingredients.resolve_line(db, "300 g di ingrediente inventato")
        assert pid is None
        assert text == "ingrediente inventato"

    def test_a_learned_alias_resolves_on_the_next_call(self, db, seeded):
        from app.models.catalog import ProductAlias
        from app.services.normalize import normalize_text

        product = db.query(Product).first()
        assert product is not None

        assert ingredients.resolve_product_id(db, "sfiziosita locale") is None

        db.add(
            ProductAlias(
                raw_text_norm=normalize_text("sfiziosita locale"),
                product_id=product.id,
                source=AliasSource.USER,
                confidence=1.0,
            )
        )
        db.commit()

        assert ingredients.resolve_product_id(db, "sfiziosita locale") == product.id
