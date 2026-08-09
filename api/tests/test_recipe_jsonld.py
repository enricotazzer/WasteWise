"""The JSON-LD parser, against saved pages.

Fixtures, never the network. A parser tested against live sites passes or fails depending on
what someone else deployed this morning, which makes it useless as a regression signal — and
it would put three real hosts in the path of every test run.

Each fixture is one shape observed on the three sites the crawler actually uses; the docstring
at the top of each file says which site and which trap it carries.
"""

from pathlib import Path

import pytest

from app.services.recipes import jsonld

FIXTURES = Path(__file__).parent / "fixtures" / "recipes"


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


class TestParseHtml:
    def test_bare_recipe_document(self):
        r = jsonld.parse_html(fixture("bare_recipe.html"))

        assert r is not None
        assert r.name == "Pasta alla Norma"
        assert r.servings == 4  # from "4 persone"
        assert len(r.ingredients) == 5
        assert "320 g di pasta" in r.ingredients

    def test_prep_plus_cook_stands_in_for_a_missing_total(self):
        # The field the corpus wants is `totalTime`, and misya.info doesn't publish it.
        # Falling back to prepTime + cookTime is the difference between a usable minute
        # count and a null on a third of the corpus.
        r = jsonld.parse_html(fixture("bare_recipe.html"))
        assert r is not None
        assert r.total_minutes == 45

    def test_recipe_is_found_inside_a_graph_wrapper(self):
        r = jsonld.parse_html(fixture("graph_wrapper.html"))

        assert r is not None
        assert r.name == "Risotto ai funghi"

    def test_type_may_be_a_list_not_a_string(self):
        # `"@type": ["Recipe", "NewsArticle"]` is valid JSON-LD and common on sites that
        # syndicate recipes as articles. Matching only on a string would drop the page.
        r = jsonld.parse_html(fixture("graph_wrapper.html"))
        assert r is not None
        assert r.total_minutes == 45  # from the fully-qualified "P0DT0H45M0S"

    def test_one_malformed_block_does_not_cost_the_page(self):
        # graph_wrapper.html carries a second, deliberately broken <script> block. Consent
        # managers and ad tooling inject these routinely.
        html = fixture("graph_wrapper.html")
        assert len(jsonld.script_blocks(html)) == 2

        r = jsonld.parse_html(html)
        assert r is not None and r.name == "Risotto ai funghi"

    def test_html_entities_are_unescaped_in_instructions(self):
        r = jsonld.parse_html(fixture("graph_wrapper.html"))
        assert r is not None and r.instructions is not None
        assert "&amp;" not in r.instructions
        assert "&" in r.instructions

    def test_top_level_array_and_howtosection_nesting(self):
        r = jsonld.parse_html(fixture("sections_and_array.html"))

        assert r is not None
        assert r.name == "Torta di mele"
        # The steps live one level down inside two HowToSections; all three must surface.
        assert r.instructions is not None
        assert "Sbatti le uova" in r.instructions
        assert "Unisci farina" in r.instructions
        assert "Inforna a 180" in r.instructions

    def test_a_yield_that_is_really_a_weight_is_rejected(self):
        # "500" here is grams. Accepting it as a serving count would divide every
        # per-portion figure downstream by five hundred.
        r = jsonld.parse_html(fixture("sections_and_array.html"))
        assert r is not None
        assert r.servings is None

    def test_a_page_with_no_recipe_returns_none(self):
        # Real JSON-LD, but a CollectionPage and an Article. Inventing a recipe out of an
        # Article is worse than returning nothing.
        assert jsonld.parse_html(fixture("no_recipe.html")) is None

    def test_a_page_with_no_json_ld_at_all_returns_none(self):
        assert jsonld.parse_html("<html><body><h1>ciao</h1></body></html>") is None

    def test_garbage_input_raises_nothing(self):
        # The crawler must never die on a page. Whatever comes back over HTTP, the worst
        # outcome allowed here is None.
        for junk in ["", "<<<", "\x00\x01", "not html at all"]:
            assert jsonld.parse_html(junk) is None


class TestParseDuration:
    @pytest.mark.parametrize(
        "value,expected",
        [
            ("PT30M", 30),
            ("PT1H", 60),
            ("PT1H30M", 90),
            ("P0DT0H45M0S", 45),  # WP Recipe Maker's fully-qualified dialect
            ("PT2H15M30S", 136),  # 135.5 min rounds to nearest, not truncated
            ("P1D", 1440),
            (None, None),
            ("", None),
            ("half an hour", None),
            ("P", None),  # grammatically incomplete
        ],
    )
    def test_durations(self, value, expected):
        assert jsonld.parse_duration_minutes(value) == expected


class TestParseYield:
    @pytest.mark.parametrize(
        "value,expected",
        [
            (4, 4),
            ("4", 4),
            ("4 persone", 4),
            ("per 6 persone", 6),
            (["4", "4 servings"], 4),
            ("500", None),  # grams
            (0, None),
            (None, None),
            ("q.b.", None),
        ],
    )
    def test_yields(self, value, expected):
        assert jsonld.parse_yield(value) == expected
