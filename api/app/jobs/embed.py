"""Embed recipes that have no vector yet.

Idempotent and resumable by construction: the work queue is literally
`WHERE embedding IS NULL`, so a run that is interrupted has already committed everything it
finished, and re-running picks up exactly the remainder. Nothing needs to remember where it
was. The crawler nulls a recipe's vector whenever it rewrites its ingredients, which is how
a corrected recipe finds its way back into this queue.

The first run downloads all-MiniLM-L6-v2 (~50 MB) into the fastembed cache; every run after
that is offline and CPU-only.

**No pgvector index, deliberately.** Two reasons, and the second is the decisive one:

  1. At a few thousand rows the whole matrix is a handful of megabytes and an exact scan is
     already sub-10ms. An IVFFlat index needs `lists` tuned to the row count and rebuilt as
     the corpus grows, and would trade exactness for speed we do not need.
  2. More importantly, this system never runs a top-k over the whole table. `retrieval.py`
     uses the vector *only* to rerank a candidate set the inverted index on
     `ingredient_product_ids` has already reduced to a few dozen rows. An ANN index over the
     full table would not be used by that query at all.

If the corpus ever reaches a size where a whole-table nearest-neighbour search is the primary
retriever, that is the moment to add HNSW — and the moment the design has changed.
"""

import argparse
import logging
import sys
import time

from sqlalchemy import func, select

from app.config import settings
from app.db import SessionLocal
from app.models import Recipe
from app.services.recipes.embed import embed_recipes, get_model, product_name_map

log = logging.getLogger(__name__)

DEFAULT_BATCH = 64


def pending_count(db) -> int:
    return (
        db.scalar(select(func.count()).select_from(Recipe).where(Recipe.embedding.is_(None))) or 0
    )


def run(db, *, batch_size: int = DEFAULT_BATCH, limit: int | None = None, model=None) -> int:
    """Embed every recipe with a NULL vector. Returns how many were written.

    Commits per batch. A batch is a few seconds of CPU, so an interrupted run loses at most
    that — and leaves the table in a state the next run reads correctly without any
    reconciliation.
    """
    names = product_name_map(db)
    written = 0

    while True:
        remaining = None if limit is None else limit - written
        if remaining is not None and remaining <= 0:
            break
        size = batch_size if remaining is None else min(batch_size, remaining)

        batch = list(
            db.scalars(
                select(Recipe).where(Recipe.embedding.is_(None)).order_by(Recipe.id).limit(size)
            )
        )
        if not batch:
            break

        embed_recipes(db, batch, model=model, product_names=names)
        db.commit()
        written += len(batch)
        log.info("embedded %d recipes (%d total)", len(batch), written)

    return written


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH)
    parser.add_argument("--limit", type=int, default=None, help="stop after this many recipes")
    args = parser.parse_args()

    with SessionLocal() as db:
        total = db.scalar(select(func.count()).select_from(Recipe)) or 0
        pending = pending_count(db)
        if total == 0:
            print("no recipes in the corpus — run `make seed-recipes` or `make crawl` first")
            return 1
        print(f"{total} recipes, {pending} without a vector")
        if pending == 0:
            print("nothing to do (this job is idempotent — that is the expected second run)")
            return 0

        print(f"model: {settings.embedding_model} ({settings.embedding_dim} dims)")
        started = time.monotonic()
        try:
            get_model()
        except Exception as exc:  # a first-run model download can fail in several ways
            print(f"could not load the embedding model: {exc}", file=sys.stderr)
            return 1
        print(f"  loaded in {time.monotonic() - started:.1f}s")

        started = time.monotonic()
        written = run(db, batch_size=args.batch_size, limit=args.limit)
        elapsed = time.monotonic() - started

        rate = written / max(elapsed, 0.001)
        print(f"\nembedded {written} recipes in {elapsed:.1f}s ({rate:.1f}/s)")
        print(f"  {pending_count(db)} still without a vector")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
