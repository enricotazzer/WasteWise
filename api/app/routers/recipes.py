from fastapi import APIRouter, Query

from app.deps import CurrentUser, DbSession
from app.schemas.recipes import FridgeGraphOut, RecipeMatchOut, UnlockOut
from app.services.recipes import best_unlocks, build_graph, rank_recipes

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
