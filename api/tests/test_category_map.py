"""OFF category tags -> seeded categories.

The rules table is what keeps the LLM tail small, so these tests pin the behaviour that
makes it work: specific tags beat general ones, and broad tags never win on their own.
"""

from app.services.barcode.category_map import (
    LOW_SIGNAL_TAGS,
    OFF_TAG_TO_CATEGORY,
    classify_with_llm,
    map_off_categories,
)

ALL_CODES = set(OFF_TAG_TO_CATEGORY.values()) | {"ALTRO"}


def test_specific_tag_wins_over_general_one():
    """OFF orders tags broad -> specific. Matching forward would classify a jar of passata
    as fresh vegetables, because 'en:plant-based-foods' comes first."""
    tags = [
        "en:plant-based-foods-and-beverages",
        "en:plant-based-foods",
        "en:vegetables",
        "en:sauces",
        "en:tomato-sauces",
    ]
    assert map_off_categories(tags, ALL_CODES) == "PASSATA"


def test_low_signal_tags_are_skipped_entirely():
    tags = ["en:plant-based-foods", "en:groceries", "en:foods"]
    assert map_off_categories(tags, ALL_CODES) is None
    # And they really are the ones we think they are.
    assert "plant-based-foods" in LOW_SIGNAL_TAGS


def test_unknown_tags_return_none_so_the_llm_gets_a_turn():
    assert map_off_categories(["en:something-nobody-has-seen"], ALL_CODES) is None


def test_empty_tags_return_none():
    assert map_off_categories([], ALL_CODES) is None


def test_a_code_not_present_in_the_database_is_ignored():
    """The rules table is static but categories are seeded data. A rule pointing at a
    category the user deleted must not produce a dangling reference."""
    assert map_off_categories(["en:tomato-sauces"], {"ALTRO"}) is None


def test_tags_without_a_language_prefix_still_match():
    assert map_off_categories(["tomato-sauces"], ALL_CODES) == "PASSATA"


def test_every_rule_points_at_a_plausible_code():
    """Guards against a typo in the rules table silently disabling a mapping."""
    for tag, code in OFF_TAG_TO_CATEGORY.items():
        assert code.isupper(), f"{tag} -> {code} is not an upper-case category code"
        assert " " not in code


class _StubLLM:
    """Minimal provider double — the router is tested separately."""

    def __init__(self, result=None, configured=True, raises=None):
        self._result, self._configured, self._raises = result, configured, raises
        self.last_schema = None

    def is_configured(self):
        return self._configured

    def complete_json(self, *, system, user, schema, schema_name="response", max_tokens=1024):
        self.last_schema = schema
        if self._raises:
            raise self._raises
        return self._result


def test_llm_tail_returns_a_parsed_triple():
    llm = _StubLLM({"category_code": "PASSATA", "shelf_life_days": 540, "is_staple": False})
    assert classify_with_llm(
        name="Passata", brand="Mutti", tags=[], known_codes=ALL_CODES, llm=llm
    ) == ("PASSATA", 540, False)


def test_llm_is_constrained_to_the_seeded_codes():
    """The schema pins category_code to an enum of real codes, so strict decoding makes an
    invented category physically impossible rather than merely unlikely."""
    llm = _StubLLM({"category_code": "PASSATA", "shelf_life_days": 5, "is_staple": False})
    classify_with_llm(name="X", brand=None, tags=[], known_codes={"PASSATA", "ALTRO"}, llm=llm)
    assert llm.last_schema["properties"]["category_code"]["enum"] == ["ALTRO", "PASSATA"]


def test_a_category_outside_the_allowed_set_is_rejected():
    llm = _StubLLM({"category_code": "INVENTATA", "shelf_life_days": 3, "is_staple": False})
    assert classify_with_llm(name="X", brand=None, tags=[], known_codes=ALL_CODES, llm=llm) is None


def test_nonsense_shelf_life_becomes_none_rather_than_a_bad_expiry():
    """A negative or zero shelf life would put the item permanently in the 'expired' bucket
    and pollute the risk model's training data."""
    llm = _StubLLM({"category_code": "PASSATA", "shelf_life_days": -5, "is_staple": False})
    assert classify_with_llm(name="X", brand=None, tags=[], known_codes=ALL_CODES, llm=llm) == (
        "PASSATA",
        None,
        False,
    )


def test_unconfigured_provider_returns_none_quietly():
    llm = _StubLLM(configured=False)
    assert classify_with_llm(name="X", brand=None, tags=[], known_codes=ALL_CODES, llm=llm) is None


def test_provider_failure_returns_none_rather_than_propagating():
    from app.services.llm import LLMUnavailable

    llm = _StubLLM(raises=LLMUnavailable("no key"))
    assert classify_with_llm(name="X", brand=None, tags=[], known_codes=ALL_CODES, llm=llm) is None
