"""Which recipes are worth scoring at all.

With 82 seeded recipes, `rank.py` could load the whole table on every request and nobody
would notice. With a few thousand crawled ones that stops being sensible, so this module
produces the candidate pool that ranking then scores. It changes *how many* recipes are
considered, never *which one wins* — that is still coverage scoring, and it must stay that
way or the ranking becomes unexplainable.

The inverted index does the real work
--------------------------------------------------------------------------------
`recipes.ingredient_product_ids` is an indexed integer array, and "which recipes use any of
these products" is an array-overlap query against it. That is exact, it is the literal
question being asked, and Postgres answers it from an index. Everything else here is
secondary.

pgvector's job is narrow on purpose: **tie-breaking and near-misses**. It runs only when the
exact index returned too few candidates to rank over — a kitchen holding things the catalog
matched poorly, or a corpus whose ingredient strings didn't resolve. Letting the vector
become the primary retriever would be slower (a distance computation per row versus an index
probe) *and* worse (semantic similarity is a proxy for "shares an ingredient"; the array is
the fact itself). A recipe reached only by vector similarity is explicitly marked, because
it has not been shown to use anything you own.
"""

import logging
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Recipe

log = logging.getLogger(__name__)

#: How many exact-match candidates are enough that reaching for the vector adds nothing.
#: Below this the pool is too thin for ranking to have anything to choose between.
MIN_EXACT_POOL = 8
#: Ceiling on the pool handed to the ranker. Coverage scoring is linear in pool size and
#: cheap, so this is generous — it exists to bound the query, not to filter.
DEFAULT_POOL = 400


@dataclass(frozen=True, slots=True)
class Candidate:
    recipe: Recipe
    #: How many of the queried products this recipe uses. The exact signal.
    overlap: int
    #: True when the exact index never returned this row and only the vector did — i.e. it
    #: has *not* been shown to use anything in the kitchen.
    from_vector: bool = False


def overlapping(
    db: Session, product_ids: list[int], *, limit: int | None = DEFAULT_POOL
) -> list[Recipe]:
    """Every recipe sharing at least one product with `product_ids`.

    `&&` (array overlap) against the indexed `ingredient_product_ids` column. The `.overlap`
    operator is the whole retrieval story for a corpus this size, and it is exact: a recipe
    that comes back here demonstrably uses something you own.

    `limit=None` means no truncation, which is what the graph and the unlock suggestion
    want: both are exhaustive counts over the corpus ("how many recipes mention this", "what
    single purchase completes the most"), and a silently truncated pool would make them
    quietly wrong rather than merely slower.
    """
    if not product_ids:
        return []
    stmt = (
        select(Recipe).where(Recipe.ingredient_product_ids.overlap(product_ids)).order_by(Recipe.id)
    )
    if limit is not None:
        stmt = stmt.limit(limit)
    return list(db.scalars(stmt))


def _nearest(db: Session, embedding: list[float], *, exclude: set[int], limit: int) -> list[Recipe]:
    """Nearest neighbours by cosine distance, skipping rows the exact pass already found.

    Only ever called with a small `limit`, and only when the exact pass came up short — see
    the module docstring for why that restriction is the point rather than an optimisation.
    """
    stmt = select(Recipe).where(Recipe.embedding.isnot(None))
    if exclude:
        stmt = stmt.where(Recipe.id.notin_(exclude))
    stmt = stmt.order_by(Recipe.embedding.cosine_distance(embedding)).limit(limit)
    return list(db.scalars(stmt))


def retrieve(
    db: Session,
    product_ids: list[int],
    *,
    pool: int = DEFAULT_POOL,
    query_embedding: list[float] | None = None,
    min_exact: int = MIN_EXACT_POOL,
) -> list[Candidate]:
    """The candidate pool for ranking, most overlap first.

    Ordering by overlap is a *pre*-ranking convenience so that truncation at `pool` keeps the
    most relevant rows. It is not the ranking: `rank.py` re-scores everything returned here
    by urgency-weighted coverage, which is a different and better question than "how many of
    my things does this use".
    """
    exact = overlapping(db, product_ids, limit=pool)
    wanted = set(product_ids)
    candidates = [
        Candidate(recipe=r, overlap=len(wanted.intersection(r.ingredient_product_ids or [])))
        for r in exact
    ]
    candidates.sort(key=lambda c: (-c.overlap, c.recipe.id))

    if query_embedding is not None and len(candidates) < min_exact:
        # The near-miss path. Reached when the kitchen's products barely appear in the
        # corpus, which on a real corpus usually means missing aliases rather than an
        # uncookable kitchen — so returning nothing would be the wrong answer twice over.
        found = {c.recipe.id for c in candidates}
        topup = _nearest(db, query_embedding, exclude=found, limit=min_exact - len(candidates))
        candidates.extend(Candidate(recipe=r, overlap=0, from_vector=True) for r in topup)

    return candidates[:pool]


def candidate_recipes(
    db: Session,
    product_ids: list[int],
    *,
    pool: int = DEFAULT_POOL,
    query_embedding: list[float] | None = None,
) -> list[Recipe]:
    """`retrieve`, flattened — for callers that only want the rows.

    Vector-only candidates are excluded here. This is the pool the graph and the ranker read,
    and both of them are built on "this recipe uses something in your kitchen"; a row that is
    merely *similar* to one that does would be a claim neither of them can back up.
    """
    return [
        c.recipe
        for c in retrieve(db, product_ids, pool=pool, query_embedding=query_embedding)
        if not c.from_vector
    ]
