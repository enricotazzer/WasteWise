from fastapi import APIRouter, Query

from app.deps import CurrentUser, DbSession
from app.schemas.recipes import (
    FridgeGraphOut,
    PhrasedSuggestionOut,
    RecipeMatchOut,
    UnlockOut,
)
from app.services.recipes import best_unlocks, build_graph, phrase_suggestion, rank_recipes

router = APIRouter(prefix="/api/recipes", tags=["recipes"])


@router.get("/graph", response_model=FridgeGraphOut)
def fridge_graph(
    db: DbSession,
    user: CurrentUser,
    include_staples: bool = Query(
        default=False,
        description="Staples connect to nearly everything and turn the graph into a "
        "hub-and-spoke hairball centred on olive oil. Off by default.",
    ),
) -> FridgeGraphOut:
    """Products in the house, connected where a recipe uses both.

    The explanation layer: it shows *why* a suggestion holds together. The ranked list below
    is the answer to "what should I cook".
    """
    graph = build_graph(db, user.id, include_staples=include_staples)
    return FridgeGraphOut(
        nodes=[
            {
                **node.__dict__,
                "at_risk": node.at_risk,  # a property, so not in __dict__
            }
            for node in graph.nodes
        ],
        edges=[edge.__dict__ for edge in graph.edges],
        isolated=graph.isolated,
    )


@router.get("/suggestions", response_model=list[RecipeMatchOut])
def suggestions(
    db: DbSession,
    user: CurrentUser,
    limit: int = Query(default=12, le=50),
) -> list[RecipeMatchOut]:
    """Recipes ranked by how much at-risk food they rescue, minus what you'd have to buy."""
    return [RecipeMatchOut(**m.__dict__) for m in rank_recipes(db, user.id, limit=limit)]


@router.get("/suggestions/phrased", response_model=PhrasedSuggestionOut)
def phrased_suggestion(
    db: DbSession,
    user: CurrentUser,
    limit: int = Query(default=4, le=8, description="how many ranked recipes ground the call"),
) -> PhrasedSuggestionOut:
    """The same ranking, written out in Italian by a model.

    Strictly additive: `/suggestions` above is unchanged and needs no API key, and this
    endpoint returns 200 with `generated: false` when no provider is configured or both free
    tiers are exhausted. There is no configuration in which this returns an error because a
    model was unavailable — that is a routine state, not a failure.

    The model chooses among the retrieved recipes and names only ingredients that are in the
    kitchen; both are enforced by the response schema and re-checked afterwards. See
    `services/recipes/generate.py`.
    """
    suggestion = phrase_suggestion(db, user.id, limit=limit)
    return PhrasedSuggestionOut(
        text_it=suggestion.text_it,
        generated=suggestion.generated,
        title=suggestion.title,
        recipe_id=suggestion.recipe_id,
        source_url=suggestion.source_url,
        uses=suggestion.uses,
        missing=suggestion.missing,
        rescues=suggestion.rescues,
        model_id=suggestion.model_id,
        fallback_reason=suggestion.fallback_reason,
        suggestion_id=suggestion.suggestion_id,
        matches=[RecipeMatchOut(**m.__dict__) for m in suggestion.matches],
    )


@router.get("/unlock", response_model=list[UnlockOut])
def unlock(
    db: DbSession,
    user: CurrentUser,
    limit: int = Query(default=3, le=10),
) -> list[UnlockOut]:
    """The single purchases that would each make several at-risk recipes fully cookable.

    Graph-derived, and the one thing coverage ranking can't produce.
    """
    return [UnlockOut(**s.__dict__) for s in best_unlocks(db, user.id, limit=limit)]
