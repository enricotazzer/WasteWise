"""Italian phrasing: the guardrails, not the prose.

Two properties are worth testing here and the quality of the writing is not one of them.

1. **It degrades.** Both free tiers rate-limit, and when they do the user must still get the
   deterministic ranked list rather than an error screen. That path runs on any machine with
   no API keys at all — which is how a reviewer first clones this repo.
2. **It cannot name food you do not have.** A suggestion that tells you to add pancetta you
   never bought is worse than no suggestion, because it silently converts a grounded
   recommender into a plausible-sounding one.
"""

from datetime import date, timedelta

import pytest
from factories import item as make_item

from app.models.catalog import Product
from app.models.enums import ItemStatus
from app.models.recipe import Recipe, RecipeSuggestion
from app.services.llm.base import LLMRateLimited, LLMUnavailable
from app.services.normalize import normalize_text
from app.services.recipes import generate


class StubLLM:
    """Stands in for the provider layer. `name` mirrors FailoverLLM's surface."""

    def __init__(self, payload=None, error: Exception | None = None) -> None:
        self.payload = payload
        self.error = error
        self.calls: list[dict] = []
        self.last_provider = "stub"

    def is_configured(self) -> bool:
        return True

    def complete_json(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.payload


@pytest.fixture
def kitchen(db, seeded):
    """One expiring item plus a recipe that uses it."""
    mozza, pasta = seeded["mozzarella"], seeded["pasta"]
    make_item(
        db,
        seeded["user"],
        mozza,
        expiry_date=date.today() + timedelta(days=1),
        status=ItemStatus.ACTIVE,
    )
    recipe = Recipe(
        source_url="test://pasta-mozzarella",
        source_site="test",
        title_it="Pasta e mozzarella",
        ingredients_raw=["320 g di pasta", "200 g di mozzarella"],
        ingredient_product_ids=[pasta.id, mozza.id],
        n_ingredients=2,
        servings=4,
        total_minutes=20,
        instructions="Cuoci la pasta. Aggiungi la mozzarella.",
    )
    db.add(recipe)
    db.commit()
    return {"recipe": recipe, "mozzarella": mozza, "pasta": pasta}


class TestDegradation:
    def test_no_provider_configured_still_returns_a_suggestion(self, db, seeded, kitchen, no_llm):
        result = generate.phrase_suggestion(db, seeded["user"].id, persist=False)

        assert result.text_it
        assert result.generated is False
        assert result.fallback_reason  # says *why*, rather than pretending nothing happened
        assert result.matches  # the deterministic ranking is always there

    @pytest.mark.parametrize(
        "error", [LLMRateLimited("429"), LLMUnavailable("all providers failed"), ValueError("?")]
    )
    def test_any_provider_failure_falls_back_rather_than_raising(self, db, seeded, kitchen, error):
        # Including a bare ValueError: an unexpected exception inside the LLM path must not
        # take down a screen whose deterministic answer was ready before the call was made.
        result = generate.phrase_suggestion(
            db, seeded["user"].id, llm=StubLLM(error=error), persist=False
        )

        assert result.generated is False
        assert result.text_it
        assert result.matches

    def test_the_fallback_text_names_the_actual_recipe(self, db, seeded, kitchen, no_llm):
        result = generate.phrase_suggestion(db, seeded["user"].id, persist=False)
        assert "Pasta e mozzarella" in result.text_it or result.title == "Pasta e mozzarella"


class TestGrounding:
    def test_a_valid_model_response_is_used(self, db, seeded, kitchen):
        llm = StubLLM(
            payload={
                "recipe_id": kitchen["recipe"].id,
                "titolo": "Pasta e mozzarella",
                "testo": "Usa la mozzarella in scadenza: falla sciogliere appena servi.",
                # Only what is genuinely in the kitchen — see the test below for what
                # happens when the model reaches past it.
                "ingredienti_usati": ["Mozzarella"],
            }
        )
        result = generate.phrase_suggestion(db, seeded["user"].id, llm=llm, persist=False)

        assert result.generated is True
        assert "mozzarella" in result.text_it.lower()

    def test_the_schema_constrains_recipe_id_to_the_retrieved_set(self, db, seeded, kitchen):
        # The model physically cannot invent a recipe: the enum is built from the ids that
        # were actually retrieved, the same trick `classify_with_llm` uses for categories.
        #
        # The enum members are STRINGS carrying an integer. Verified against both live APIs:
        # Gemini rejects a numeric enum outright and Groq's strict decoder fails the same
        # schema, so a string enum is the only form both accept. A stubbed provider happily
        # accepts either, which is exactly why this went unnoticed until a real call was made.
        llm = StubLLM(
            payload={
                "recipe_id": str(kitchen["recipe"].id),
                "titolo": "x",
                "testo": "x",
                "ingredienti_usati": [],
            }
        )
        generate.phrase_suggestion(db, seeded["user"].id, llm=llm, persist=False)

        schema = llm.calls[0]["schema"]
        assert schema["properties"]["recipe_id"]["enum"] == [str(kitchen["recipe"].id)]
        assert schema["properties"]["recipe_id"]["type"] == "string"
        assert schema["additionalProperties"] is False

    def test_prose_naming_food_you_do_not_own_is_rejected(self, db, seeded, kitchen):
        # Ricotta is in the catalog, is not in the kitchen, and is not in the recipe. A
        # suggestion that reaches for it has stopped being grounded.
        llm = StubLLM(
            payload={
                "recipe_id": kitchen["recipe"].id,
                "titolo": "Pasta e mozzarella",
                "testo": "Aggiungi la ricotta per un risultato piu cremoso.",
                "ingredienti_usati": ["Mozzarella"],
            }
        )
        result = generate.phrase_suggestion(db, seeded["user"].id, llm=llm, persist=False)

        assert result.generated is False
        assert result.fallback_reason
        assert "ricotta" not in result.text_it.lower()


class TestOffendingIngredients:
    def _catalog(self, db) -> dict[str, str]:
        return {
            normalize_text(p.name_it): p.name_it for p in db.scalars(Product.__table__.select())
        }

    def test_matches_ignore_case_accents_and_trailing_punctuation(self):
        catalog = {normalize_text("Pomodori"): "Pomodori"}
        assert generate.offending_ingredients("Aggiungi i pomodori.", set(), catalog) == [
            "Pomodori"
        ]

    def test_an_allowed_ingredient_does_not_offend(self):
        catalog = {normalize_text("Pomodori"): "Pomodori"}
        allowed = {normalize_text("Pomodori")}
        assert generate.offending_ingredients("Aggiungi i pomodori.", allowed, catalog) == []

    def test_word_boundaries_prevent_substring_false_positives(self):
        # "Te" (tea) must not fire inside "tempo". Without boundaries the guardrail would
        # reject almost every sentence and the LLM path would never be used at all.
        catalog = {normalize_text("Te"): "Te"}
        assert (
            generate.offending_ingredients("Lascia riposare per un tempo breve", set(), catalog)
            == []
        )

    def test_empty_text_offends_nothing(self):
        assert generate.offending_ingredients("", set(), {"pomodori": "Pomodori"}) == []


class TestPersistence:
    def test_a_generated_suggestion_is_recorded(self, db, seeded, kitchen):
        # `recipe_suggestions` is the feedback signal for whether recommendations change
        # behaviour. Before this phase the table had zero writers.
        llm = StubLLM(
            payload={
                "recipe_id": kitchen["recipe"].id,
                "titolo": "Pasta e mozzarella",
                "testo": "Falla sciogliere sulla pasta calda.",
                "ingredienti_usati": ["Mozzarella"],
            }
        )
        result = generate.phrase_suggestion(db, seeded["user"].id, llm=llm, persist=True)

        rows = list(db.scalars(RecipeSuggestion.__table__.select()))
        assert len(rows) == 1
        assert result.suggestion_id is not None

    def test_persist_false_writes_nothing(self, db, seeded, kitchen, no_llm):
        generate.phrase_suggestion(db, seeded["user"].id, persist=False)
        assert list(db.scalars(RecipeSuggestion.__table__.select())) == []
