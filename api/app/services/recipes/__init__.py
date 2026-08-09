"""Module 3 — the recommendation engine, in the order a reader should meet it.

    jsonld.py      a page's schema.org/Recipe, parsed out of its <script> tags
    ingredients.py ingredient strings -> canonical product ids (one matcher, shared)
    crawler.py     robots-obeying, rate-limited, resumable corpus building
    embed.py       384-dim vectors over the ingredient set, not the prose
    retrieval.py   inverted index first, vector only for near-misses
    graph.py       the fridge graph — explanation, not ranking
    rank.py        urgency-weighted coverage — this is what decides
    unlock.py      the one purchase that completes the most at-risk recipes
    generate.py    Italian phrasing, grounded and constrained, with a deterministic floor

`generate.py` is the only part that needs an API key, and it degrades to the ranked list
without one. Everything above it works on an empty `.env`.
"""

from app.services.recipes.generate import PhrasedSuggestion, phrase_suggestion
from app.services.recipes.graph import FridgeGraph, GraphEdge, GraphNode, build_graph
from app.services.recipes.ingredients import ParsedIngredient, parse_line, resolve_product_id
from app.services.recipes.jsonld import ParsedRecipe, parse_html
from app.services.recipes.rank import RecipeMatch, rank_recipes
from app.services.recipes.retrieval import Candidate, candidate_recipes, retrieve
from app.services.recipes.unlock import UnlockSuggestion, best_unlocks

__all__ = [
    "Candidate",
    "FridgeGraph",
    "GraphEdge",
    "GraphNode",
    "ParsedIngredient",
    "ParsedRecipe",
    "PhrasedSuggestion",
    "RecipeMatch",
    "UnlockSuggestion",
    "best_unlocks",
    "build_graph",
    "candidate_recipes",
    "parse_html",
    "parse_line",
    "phrase_suggestion",
    "rank_recipes",
    "resolve_product_id",
    "retrieve",
]
