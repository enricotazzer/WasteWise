"""Hybrid retrieval: the exact inverted index does the work, the vector only fills gaps.

The ordering matters and is worth protecting with tests. An array-overlap hit is a *proof*
that the recipe uses something you own; a nearest-neighbour hit is a guess that it might be
relevant. Letting the vector become the primary retriever would be slower and worse, and the
failure would be invisible — you'd just get subtly worse suggestions forever.
"""

from app.models.recipe import Recipe
from app.services.recipes import retrieval


def make_recipe(db, title: str, product_ids: list[int], *, embedding=None) -> Recipe:
    row = Recipe(
        source_url=f"test://{title.lower().replace(' ', '-')}",
        source_site="test",
        title_it=title,
        ingredients_raw=[{"raw": "x", "text": "x"}],
        ingredient_product_ids=product_ids,
        n_ingredients=len(product_ids),
        instructions="…",
        embedding=embedding,
    )
    db.add(row)
    db.flush()
    return row


class TestOverlapping:
    def test_returns_only_recipes_sharing_at_least_one_product(self, db, seeded):
        mozza, pasta = seeded["mozzarella"], seeded["pasta"]
        hit = make_recipe(db, "Pasta e mozzarella", [pasta.id, mozza.id])
        miss = make_recipe(db, "Insalata", [seeded["ricotta"].id])

        found = retrieval.overlapping(db, [pasta.id])

        assert hit in found
        assert miss not in found

    def test_no_query_products_returns_nothing_rather_than_everything(self, db, seeded):
        # An empty kitchen must not retrieve the whole corpus — that would then be ranked
        # and shown as suggestions for food you do not have.
        make_recipe(db, "Qualcosa", [seeded["pasta"].id])
        assert retrieval.overlapping(db, []) == []

    def test_limit_none_means_exhaustive(self, db, seeded):
        # The graph and the unlock suggestion are exhaustive counts over the corpus. A
        # silently truncated pool makes them quietly wrong rather than merely slower.
        pasta = seeded["pasta"]
        for i in range(12):
            make_recipe(db, f"Ricetta {i}", [pasta.id])

        assert len(retrieval.overlapping(db, [pasta.id], limit=None)) == 12
        assert len(retrieval.overlapping(db, [pasta.id], limit=5)) == 5


class TestRetrieve:
    def test_every_exact_candidate_reports_its_true_overlap(self, db, seeded):
        mozza, pasta, ricotta = seeded["mozzarella"], seeded["pasta"], seeded["ricotta"]
        two = make_recipe(db, "Due", [pasta.id, mozza.id])
        one = make_recipe(db, "Uno", [pasta.id, ricotta.id])

        by_id = {c.recipe.id: c for c in retrieval.retrieve(db, [pasta.id, mozza.id])}

        assert by_id[two.id].overlap == 2
        assert by_id[one.id].overlap == 1

    def test_exact_hits_are_never_marked_as_vector_only(self, db, seeded):
        # `from_vector` is what tells the ranker "this has NOT been shown to use anything you
        # own". Mislabelling an exact hit would let the ranker discount a proven match.
        make_recipe(db, "Pasta", [seeded["pasta"].id])
        candidates = retrieval.retrieve(db, [seeded["pasta"].id])

        assert candidates and all(c.from_vector is False for c in candidates)

    def test_ordering_puts_the_most_overlap_first_so_truncation_keeps_the_best(self, db, seeded):
        mozza, pasta, ricotta = seeded["mozzarella"], seeded["pasta"], seeded["ricotta"]
        make_recipe(db, "Uno", [pasta.id])
        three = make_recipe(db, "Tre", [pasta.id, mozza.id, ricotta.id])

        candidates = retrieval.retrieve(db, [pasta.id, mozza.id, ricotta.id])
        assert candidates[0].recipe.id == three.id

    def test_the_vector_pass_is_skipped_when_the_exact_pass_suffices(self, db, seeded):
        # No embedding is supplied, so if the vector pass were mandatory this would return
        # nothing rather than the exact hits.
        make_recipe(db, "Pasta", [seeded["pasta"].id])
        assert retrieval.retrieve(db, [seeded["pasta"].id], query_embedding=None)

    def test_a_recipe_with_no_ingredient_ids_is_never_retrieved_exactly(self, db, seeded):
        # An unresolved corpus row (every ingredient string missed the catalog) has nothing
        # to overlap with, and must not be padded into results.
        make_recipe(db, "Sconosciuta", [])
        assert retrieval.overlapping(db, [seeded["pasta"].id]) == []
