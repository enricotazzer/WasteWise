"""Recipe embeddings — 384-dim vectors over the *ingredient set*, not the prose.

What gets embedded is the whole design decision here. A recipe page is mostly method
("scolare la pasta al dente", "infornare a 180°"), and a vector built from that text answers
"which recipes are written similarly", which nobody asked. The question this system asks is
**"what is this made of"**, so the document is the ingredient list — canonical product names
where the matcher resolved them, the parsed ingredient text where it didn't, and the title
in front as the one piece of prose that genuinely names the dish.

Keeping the unresolved ingredients in the document is deliberate. They are precisely the
information the integer array `ingredient_product_ids` has *lost*, so they are where a vector
can add something the inverted index cannot. If the document were only the resolved ids, the
vector would be a lossy re-encoding of a column we already have indexed.

`fastembed` rather than `sentence-transformers`: identical model (all-MiniLM-L6-v2) and
identical 384-dim output — which is why no migration is needed, the column was already sized
for it — but run on onnxruntime at ~50 MB instead of pulling ~2 GB of torch into the image
to do the same arithmetic. The first call downloads the model; every later one is offline.
"""

import logging
import threading
from collections.abc import Iterable, Sequence
from typing import Any

from app.config import settings
from app.models import Product, Recipe
from app.services.recipes.ingredients import parse_line

log = logging.getLogger(__name__)

_model: Any | None = None
_model_lock = threading.Lock()

#: Long enough for a normal ingredient list, short enough that a pathological recipe cannot
#: push the encoder's 512-token window into truncating the *start* of the document, which is
#: where the title and the first ingredients are.
MAX_DOCUMENT_CHARS = 1200


def get_model() -> Any:
    """The shared embedding model, loaded on first use.

    Module-global and lock-guarded because loading it costs a few seconds and ~50 MB of
    resident memory, and the embedding job would otherwise pay that per batch. Imported
    lazily too: `fastembed` pulls onnxruntime at import time, and nothing on the request path
    should carry that just because a router happened to import this package.
    """
    global _model
    with _model_lock:
        if _model is None:
            from fastembed import TextEmbedding

            log.info("loading embedding model %s", settings.embedding_model)
            _model = TextEmbedding(model_name=settings.embedding_model)
        return _model


def reset_model_cache() -> None:
    """Test hook. The model is expensive and process-global; tests inject a fake instead."""
    global _model
    with _model_lock:
        _model = None


def ingredient_document(recipe: Recipe, product_names: dict[int, str]) -> str:
    """The text that represents this recipe in vector space.

    Canonical names first because they are the vocabulary the rest of the system speaks, then
    whatever the matcher could not resolve, in the site's own words.
    """
    resolved = [
        product_names[pid] for pid in (recipe.ingredient_product_ids or []) if pid in product_names
    ]

    unresolved: list[str] = []
    seen = {name.lower() for name in resolved}
    for line in recipe.ingredients_raw or []:
        if not isinstance(line, str):
            continue
        text = parse_line(line).text
        if text and text.lower() not in seen:
            unresolved.append(text)
            seen.add(text.lower())

    parts = [recipe.title_it or "", ", ".join(resolved + unresolved)]
    return ". ".join(p for p in parts if p).strip()[:MAX_DOCUMENT_CHARS]


def embed_texts(texts: Sequence[str], *, model: Any | None = None) -> list[list[float]]:
    """Embed a batch. Returns plain lists, which is what pgvector's binding wants."""
    if not texts:
        return []
    encoder = model if model is not None else get_model()
    return [list(map(float, vector)) for vector in encoder.embed(list(texts))]


def product_name_map(db) -> dict[int, str]:
    from sqlalchemy import select

    return {p.id: p.name_it for p in db.scalars(select(Product))}


def embed_recipes(
    db,
    recipes: Iterable[Recipe],
    *,
    model: Any | None = None,
    product_names: dict[int, str] | None = None,
) -> int:
    """Compute and attach vectors for the given recipes. Returns how many were embedded.

    Does not commit — the caller decides the transaction boundary, which is what lets the
    job commit per batch and stay interruptible.
    """
    batch = list(recipes)
    if not batch:
        return 0
    names = product_names if product_names is not None else product_name_map(db)
    documents = [ingredient_document(recipe, names) for recipe in batch]
    vectors = embed_texts(documents, model=model)
    for recipe, vector in zip(batch, vectors, strict=True):
        recipe.embedding = vector
    return len(batch)
