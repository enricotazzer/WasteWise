"""The starter corpus and its ingredient matcher.

The match rate is the quality bar for this phase: an ingredient that fails to resolve is an
edge the graph will never have, and a recipe that quietly loses half its ingredients would
rank as though it were nearly cookable.
"""

from app.seeds.load_recipes import SYNONYMS, parse_ingredient
from app.seeds.recipes import RECIPES


def test_the_corpus_is_a_usable_size() -> None:
    assert len(RECIPES) >= 60
    # Fewer than ~3 ingredients and a recipe produces almost no edges.
    assert all(len(ings) >= 3 for _, _, _, ings, _ in RECIPES)


def test_every_recipe_is_complete() -> None:
    for title, servings, minutes, ingredients, instructions in RECIPES:
        assert title.strip(), "recipe with no title"
        assert servings and servings > 0, f"{title}: bad servings"
        assert minutes and minutes > 0, f"{title}: bad time"
        assert instructions.strip(), f"{title}: no method"
        assert len(set(ingredients)) == len(ingredients), f"{title}: duplicate ingredient"


def test_titles_are_unique() -> None:
    """Titles become the seed:// URL, which is the upsert key — a duplicate would silently
    overwrite the other recipe on every load."""
    titles = [r[0] for r in RECIPES]
    assert len(set(titles)) == len(titles)


class TestIngredientParsing:
    """Ingredients are written the way a recipe site writes them, so the parser has to strip
    the measure before the matcher ever sees the text."""

    def test_strips_quantity_unit_and_preposition(self) -> None:
        assert parse_ingredient("400 g di passata di pomodoro") == "passata di pomodoro"
        assert parse_ingredient("320 g di pasta") == "pasta"
        assert parse_ingredient("300 ml di latte intero") == "latte intero"

    def test_strips_a_bare_count(self) -> None:
        assert parse_ingredient("2 uova") == "uova"
        assert parse_ingredient("1 cipolla") == "cipolla"

    def test_strips_countable_measures(self) -> None:
        assert parse_ingredient("1 spicchio di aglio") == "aglio"
        assert parse_ingredient("4 fette di pane in cassetta") == "pane in cassetta"

    def test_leaves_an_unquantified_ingredient_alone(self) -> None:
        assert parse_ingredient("olio extravergine") == "olio extravergine"
        assert parse_ingredient("basilico") == "basilico"

    def test_does_not_eat_a_leading_number_that_is_part_of_the_name(self) -> None:
        # Nothing in the corpus looks like this today, but a "00" flour would.
        assert parse_ingredient("farina 00") == "farina 00"


def test_synonyms_point_at_plausible_catalog_names() -> None:
    """A typo here fails silently — the alias just never matches anything."""
    for phrase, target in SYNONYMS.items():
        assert phrase == phrase.lower(), f"{phrase} should be lowercase"
        assert target[0].isupper(), f"{target} should be a canonical Name_it"
        assert phrase != target.lower(), f"{phrase} -> {target} is a no-op"
