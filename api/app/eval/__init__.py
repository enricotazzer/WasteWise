"""Evaluation: the numbers this project claims, computed from the database.

One module, two consumers — `python -m app.eval.report` and `notebooks/eval.ipynb`. That is
deliberate. A README figure that exists only in prose is a figure nobody can re-derive, and a
notebook that recomputes things its own way is a second implementation that will disagree
with the app by the time anyone checks.

Nothing is re-exported here on purpose: `from app.eval import build` would import
`app.eval.report` while `python -m app.eval.report` is still loading it, which Python warns
about and which can bind two copies of the module. Import from `app.eval.report` directly.
"""
