from pydantic import BaseModel


class GraphNodeOut(BaseModel):
    product_id: int
    name: str
    category_code: str
    quantity: float
    unit: str
    days_to_expiry: int | None
    is_staple: bool
    at_risk: bool
    #: Node radius comes from this: how many other in-house products it cooks with.
    degree: int
    recipe_count: int


class GraphEdgeOut(BaseModel):
    source: int
    target: int
    #: Shared-recipe count. Drives stroke width (1–3px), nothing else.
    weight: int
    recipe_ids: list[int]


class FridgeGraphOut(BaseModel):
    nodes: list[GraphNodeOut]
    edges: list[GraphEdgeOut]
    #: In-house products no corpus recipe mentions. Usually a missing alias rather than a
    #: genuinely uncookable ingredient, so it's surfaced instead of quietly dropped.
    isolated: list[str]


class RecipeMatchOut(BaseModel):
    recipe_id: int
    title: str
    servings: int | None
    total_minutes: int | None
    source_url: str
    instructions: str | None
    uses: list[str]
    use_ids: list[int]
    rescues: list[str]
    missing: list[str]
    score: float
    cookable: bool


class UnlockOut(BaseModel):
    product_id: int
    name: str
    category_code: str
    unlocks: int
    rescues: list[str]
    recipe_titles: list[str]


class PhrasedSuggestionOut(BaseModel):
    """An LLM-phrased suggestion, or the deterministic sentence that replaces it.

    `generated` is the field a caller must read before presenting this as written prose:
    False means both free tiers were unavailable (or the output was rejected) and `text_it`
    is the template. `fallback_reason` says which. The response is otherwise identical in
    shape, so the screen degrades without a branch — and `matches` carries the full ranked
    list either way, so nothing is lost when the model is.
    """

    text_it: str
    generated: bool
    title: str | None
    recipe_id: int | None
    source_url: str | None
    uses: list[str]
    missing: list[str]
    rescues: list[str]
    model_id: str | None
    fallback_reason: str | None
    #: Row id in `recipe_suggestions`. The handle for the accept/reject feedback signal.
    suggestion_id: int | None
    matches: list[RecipeMatchOut]
