"""Background jobs.

Each job is a plain function plus a `python -m app.jobs.<name>` entry point, and is
idempotent — running one twice must converge on the same state rather than double-count.
That property is what lets them be invoked from a Makefile target, a scheduler, or a test
without any of the three needing to know about the others.

Deliberately not started from a FastAPI lifespan: `tests/conftest.py` uses
`with TestClient(app)`, which fires startup, so a lifespan-mounted scheduler would spin up
in every test run.
"""
