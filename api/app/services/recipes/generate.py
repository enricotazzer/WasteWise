"""Italian phrasing for a recipe suggestion — the first user-facing LLM call in the project.

**The model phrases; it never invents.** Everything factual in a suggestion is decided before
the model is asked anything: which recipes are candidates (`retrieval.py`), which one is best
(`rank.py`), what it uses, what is missing. The model receives that and writes two or three
sentences of Italian around it. Nothing it returns is allowed to introduce a fact.

Three layers enforce that, in decreasing order of strength:

  1. **The schema.** `recipe_id` is an `enum` of the retrieved ids and `ingredienti_usati` is
     an array whose items are an `enum` of the products actually in the kitchen. Groq's
     `gpt-oss` models support *strict* constrained decoding, so the model physically cannot
     emit an id we didn't retrieve or name an ingredient the user doesn't own. This is the
     same trick `barcode/category_map.classify_with_llm` uses to pin a category to the
     seeded set.
  2. **A prose guard.** `testo` is free text, so the schema can't police it. After the call,
     the text is scanned for canonical product names that are neither in the kitchen, nor a
     staple, nor part of the chosen recipe. A hit rejects the whole suggestion. "Aggiungi un
     po' di panna" when there is no panna is worse than no suggestion at all: it is a
     recommendation the user cannot act on, dressed as one they can.
  3. **The fallback.** Any failure — no key configured, both free tiers rate-limited, a
     malformed response, a guard rejection — degrades to a deterministic sentence built from
     the ranked list. Never an error screen. This is not the unhappy path; on a free tier it
     is a *routine* path, which is why it is tested explicitly.

The deterministic `/suggestions` endpoint is untouched by all of this. With no API keys
configured at all, ranking, the graph and the unlock suggestion work exactly as before, and
this module returns its fallback text.
"""

import logging
import re
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import InventoryItem, Product, RecipeSuggestion
from app.models.enums import ItemStatus
from app.services.normalize import normalize_text
from app.services.recipes.graph import active_products
from app.services.recipes.rank import RecipeMatch, rank_recipes

log = logging.getLogger(__name__)

#: How many ranked recipes the model gets to choose between. Small on purpose: the ranking
#: already decided, and a longer list mostly invites the model to disagree with it.
GROUNDING_RECIPES = 4

#: Ingredient lines shown per grounded recipe. Enough to write about, short enough that the
#: prompt stays small on a free tier.
GROUNDING_INGREDIENTS = 12

_SYSTEM = """Sei l'assistente di un'app italiana contro lo spreco alimentare.

Ti vengono dati: gli alimenti in casa che stanno per scadere, e alcune ricette gia' \
selezionate dal sistema. Il tuo compito e' SOLO scrivere il consiglio in italiano.

Regole non negoziabili:
- Scegli UNA delle ricette proposte, tramite il suo recipe_id. Non inventarne altre.
- In "testo" puoi nominare SOLO ingredienti che l'utente ha in casa, dispensa di base \
(olio, sale, pepe, aglio, cipolla, farina, zucchero, aceto) o ingredienti della ricetta \
scelta. Non aggiungere nulla di tuo.
- Non inventare quantita', tempi o passaggi che non ti sono stati dati.
- "testo": 2-3 frasi, tono pratico e diretto, seconda persona singolare. Di' cosa si salva \
e perche' quella ricetta. Niente elenchi puntati, niente emoji.
- "titolo": massimo 60 caratteri.
- "ingredienti_usati": gli alimenti in casa che la ricetta consuma davvero."""


@dataclass(frozen=True, slots=True)
class PhrasedSuggestion:
    """A suggestion, with its provenance attached rather than summarised away.

    `generated` is the field that matters: False means the text below is the deterministic
    fallback and no model was involved, and the UI should not present it as generated prose.
    """

    text_it: str
    generated: bool
    title: str | None = None
    recipe_id: int | None = None
    source_url: str | None = None
    uses: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    rescues: list[str] = field(default_factory=list)
    model_id: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    #: Why the LLM path wasn't used. None when it was.
    fallback_reason: str | None = None
    suggestion_id: int | None = None
    #: The deterministic ranked list, always present. The prose is a layer on top of this,
    #: never a replacement for it.
    matches: list[RecipeMatch] = field(default_factory=list)


# --- the deterministic floor ----------------------------------------------------------


def deterministic_text(match: RecipeMatch) -> str:
    """The sentence that runs when there is no model. Plain, correct, and never wrong.

    Written in the same register as the generated version so the screen doesn't visibly
    change character when the free tier runs out — the difference is reported in
    `generated`, not performed by the prose.
    """
    rescued = match.rescues or match.uses
    subject = _join_it(rescued[:3])
    parts = [f"Hai {subject} da consumare." if subject else "Ecco cosa puoi cucinare."]

    detail = f"«{match.title}»"
    if match.total_minutes:
        detail += f" in circa {match.total_minutes} minuti"
    if match.servings:
        detail += f", per {match.servings}"
    parts.append(f"{detail}.")

    if match.missing:
        parts.append(f"Ti manca solo: {_join_it(match.missing[:4])}.")
    elif match.cookable:
        parts.append("Hai gia' tutto in casa.")
    return " ".join(parts)


def _join_it(names: list[str]) -> str:
    """'a, b e c' — the Italian list separator, which is not a comma before the last item."""
    lowered = [n.lower() for n in names if n]
    if not lowered:
        return ""
    if len(lowered) == 1:
        return lowered[0]
    return ", ".join(lowered[:-1]) + " e " + lowered[-1]


# --- the prose guard ------------------------------------------------------------------


def offending_ingredients(text: str, allowed: set[str], catalog: dict[str, str]) -> list[str]:
    """Canonical products named in `text` that the user cannot act on.

    Matching happens in normalized space (uppercased, accents and punctuation stripped) so
    that "Pomodori" in the catalog and "pomodori." in a sentence are the same token, with
    word boundaries so "Te" doesn't fire inside "tempo".

    `allowed` holds the normalized names of everything in the kitchen, every staple, and
    every ingredient of the chosen recipe. Anything else in the catalog is, by construction,
    something the user was never told they'd need.
    """
    haystack = normalize_text(text)
    if not haystack:
        return []
    found: list[str] = []
    for norm, display in catalog.items():
        if norm in allowed or not norm:
            continue
        if re.search(rf"(?<![\w]){re.escape(norm)}(?![\w])", haystack):
            found.append(display)
    return sorted(found)


# --- the model call -------------------------------------------------------------------


def _schema(recipe_ids: list[int], inventory_names: list[str]) -> dict[str, Any]:
    """The strict schema. Both enums are the constraint, not decoration.

    `additionalProperties: false` plus every property in `required` is what Groq's strict
    mode demands; `gemini.py` strips the keys its dialect rejects, so callers write one
    schema and neither provider has to be special-cased here.

    **`recipe_id` is a string enum carrying an integer.** Verified against both live APIs:
    Gemini rejects a numeric `enum` outright (`Invalid value at ...enum[0] (TYPE_STRING)`)
    because its dialect types every enum member as a string, and Groq's strict decoder fails
    the same schema with `json_validate_failed`. A string enum is the only form both accept,
    so the id is stringified here and parsed back in `_call`. Without this the LLM path is
    dead on arrival with real keys while passing every test with a stubbed provider — which
    is exactly what happened.
    """
    return {
        "type": "object",
        "properties": {
            "recipe_id": {"type": "string", "enum": [str(i) for i in recipe_ids]},
            "titolo": {"type": "string"},
            "testo": {"type": "string"},
            "ingredienti_usati": {
                "type": "array",
                "items": {"type": "string", "enum": inventory_names},
            },
        },
        "required": ["recipe_id", "titolo", "testo", "ingredienti_usati"],
        "additionalProperties": False,
    }


def _prompt(matches: list[RecipeMatch], kitchen: list[str], at_risk: list[str]) -> str:
    lines = ["In casa: " + (", ".join(kitchen) or "niente di registrato")]
    if at_risk:
        lines.append("In scadenza: " + ", ".join(at_risk))
    lines.append("\nRicette proposte dal sistema:")
    for match in matches:
        ingredients = ", ".join((match.ingredients_raw or [])[:GROUNDING_INGREDIENTS])
        lines.append(
            f"\n[recipe_id={match.recipe_id}] {match.title}"
            f" — {match.total_minutes or '?'} min, {match.servings or '?'} porzioni"
        )
        lines.append(f"  usa di tuo: {', '.join(match.uses) or '-'}")
        lines.append(f"  ti manca: {', '.join(match.missing) or 'niente'}")
        if ingredients:
            lines.append(f"  ingredienti: {ingredients}")
    return "\n".join(lines)


def phrase_suggestion(
    db: Session,
    user_id: int,
    *,
    limit: int = GROUNDING_RECIPES,
    llm: Any = None,
    persist: bool = True,
) -> PhrasedSuggestion:
    """Rank, then phrase. Always returns something usable.

    `persist=False` is for callers that want the text without a `recipe_suggestions` row —
    a preview, or a test. The row is the feedback signal for whether recommendations change
    behaviour, so on the real path it is written.
    """
    matches = rank_recipes(db, user_id, limit=limit)
    if not matches:
        return PhrasedSuggestion(
            text_it="Non c'e' niente in scadenza da salvare con le ricette del corpus.",
            generated=False,
            fallback_reason="no ranked recipe matched the kitchen",
        )

    best = matches[0]
    nodes = active_products(db, user_id)
    products = list(db.scalars(select(Product)))

    kitchen_names = [node.name for node in nodes.values()]
    at_risk_names = [node.name for node in nodes.values() if node.at_risk]

    fallback = PhrasedSuggestion(
        text_it=deterministic_text(best),
        generated=False,
        title=best.title,
        recipe_id=best.recipe_id,
        source_url=best.source_url,
        uses=best.uses,
        missing=best.missing,
        rescues=best.rescues,
        matches=matches,
    )

    provider = llm
    if provider is None:
        from app.services.llm import get_llm

        provider = get_llm()
    if not provider.is_configured():
        return _finish(db, user_id, fallback, matches, persist, "no LLM provider configured")

    result = _call(provider, matches, kitchen_names, at_risk_names)
    if isinstance(result, str):
        return _finish(db, user_id, fallback, matches, persist, result)

    chosen, payload = result

    # The guard's allow-list: the kitchen, the staples, and this recipe's own ingredients
    # (which the user is being shown as "missing", so naming them is honest).
    catalog = {normalize_text(p.name_it): p.name_it for p in products}
    allowed = {normalize_text(name) for name in kitchen_names}
    allowed |= {normalize_text(p.name_it) for p in products if p.is_staple}
    allowed |= {normalize_text(name) for name in chosen.uses + chosen.missing}

    text = str(payload.get("testo") or "").strip()
    if not text:
        return _finish(db, user_id, fallback, matches, persist, "model returned empty text")

    offending = offending_ingredients(text, allowed, catalog)
    if offending:
        # Deliberately loud. A suggestion naming food the user doesn't have is the specific
        # failure this whole module is built to prevent, so it is logged rather than only
        # counted, and the deterministic sentence goes out instead.
        log.warning("rejecting generated suggestion — names %s, not in inventory", offending)
        return _finish(
            db, user_id, fallback, matches, persist, f"named unavailable: {', '.join(offending)}"
        )

    usage = getattr(provider, "last_usage", None) or {}
    phrased = PhrasedSuggestion(
        text_it=text,
        generated=True,
        title=(str(payload.get("titolo") or chosen.title)).strip()[:120] or chosen.title,
        recipe_id=chosen.recipe_id,
        source_url=chosen.source_url,
        # From the ranker, not the model: these are facts we computed, and echoing the
        # model's version of them back would mean the screen showed its opinion of what it
        # was given rather than what it was given.
        uses=chosen.uses,
        missing=chosen.missing,
        rescues=chosen.rescues,
        model_id=getattr(provider, "last_model", None) or getattr(provider, "name", None),
        input_tokens=usage.get("input_tokens"),
        output_tokens=usage.get("output_tokens"),
        matches=matches,
    )
    return _finish(db, user_id, phrased, matches, persist, None)


def _call(
    provider: Any, matches: list[RecipeMatch], kitchen: list[str], at_risk: list[str]
) -> tuple[RecipeMatch, dict[str, Any]] | str:
    """One constrained call. Returns (chosen match, payload) or a failure reason string."""
    from app.services.llm import LLMError

    by_id = {m.recipe_id: m for m in matches}
    # Sorted for determinism: the same kitchen must produce the same prompt, or two runs
    # can't be compared.
    inventory_names = sorted(set(kitchen))
    if not inventory_names:
        return "kitchen is empty"

    try:
        payload = provider.complete_json(
            system=_SYSTEM,
            user=_prompt(matches, kitchen, at_risk),
            schema=_schema(sorted(by_id), inventory_names),
            schema_name="recipe_suggestion",
            max_tokens=700,
        )
    except LLMError as exc:
        log.info("recipe phrasing unavailable, falling back to the ranked list: %s", exc)
        return f"llm unavailable: {exc}"
    except Exception as exc:  # noqa: BLE001 - see below
        # A bare `except Exception` earns its place exactly here. The providers wrap their
        # known failures in LLMError, so anything landing here is a bug rather than a
        # rate limit — and the deterministic answer was computed before this call was made.
        # Letting an unexpected exception escape would turn a working screen into a 500 to
        # protect prose that is decoration over a ranked list the user can already read.
        log.exception("unexpected error while phrasing a suggestion")
        return f"llm error: {exc.__class__.__name__}"

    if not isinstance(payload, dict):
        return f"model returned {type(payload).__name__}, not an object"

    # Stringified in the schema so both providers accept the enum — see `_schema`. Accept an
    # int too, so a provider that returns the native type still works.
    raw_id = payload.get("recipe_id")
    try:
        chosen = by_id.get(int(raw_id))
    except (TypeError, ValueError):
        chosen = None
    if chosen is None:
        # Strict decoding should make this unreachable; a provider without it would land
        # here, and a suggestion pointing at a recipe we never retrieved is not usable.
        return f"model chose an unretrieved recipe_id {payload.get('recipe_id')!r}"

    named = [n for n in (payload.get("ingredienti_usati") or []) if isinstance(n, str)]
    unknown = sorted(set(named) - set(kitchen))
    if unknown:
        return f"model listed ingredients not in the kitchen: {', '.join(unknown)}"
    return chosen, payload


def _rescued_item_ids(db: Session, user_id: int, product_ids: list[int]) -> list[int]:
    """Inventory items the suggestion was built to rescue.

    Item ids, not product ids — `RecipeSuggestion.matched_item_ids` is how a later
    evaluation asks "did the thing this suggestion was about actually get eaten", and that
    question is only answerable per item.
    """
    if not product_ids:
        return []
    rows = db.scalars(
        select(InventoryItem.id).where(
            InventoryItem.user_id == user_id,
            InventoryItem.product_id.in_(product_ids),
            InventoryItem.status.in_([ItemStatus.ACTIVE, ItemStatus.PARTIAL]),
        )
    )
    return sorted(rows)


def _finish(
    db: Session,
    user_id: int,
    suggestion: PhrasedSuggestion,
    matches: list[RecipeMatch],
    persist: bool,
    reason: str | None,
) -> PhrasedSuggestion:
    """Attach the fallback reason, write the row, and hand back the final object."""
    suggestion = replace(suggestion, fallback_reason=reason, matches=matches)
    if not persist:
        return suggestion

    chosen = next((m for m in matches if m.recipe_id == suggestion.recipe_id), None)
    row = RecipeSuggestion(
        user_id=user_id,
        generated_at=datetime.now(UTC),
        matched_item_ids=_rescued_item_ids(db, user_id, chosen.use_ids if chosen else []),
        retrieved_recipe_ids=[m.recipe_id for m in matches],
        missing_ingredients=suggestion.missing,
        text_it=suggestion.text_it,
        model_id=suggestion.model_id,
        input_tokens=suggestion.input_tokens,
        output_tokens=suggestion.output_tokens,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return replace(suggestion, suggestion_id=row.id)
