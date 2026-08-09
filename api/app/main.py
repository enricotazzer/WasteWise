from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.config import settings
from app.db import engine
from app.routers import auth, catalog, inventory, recipes, risk, scan, stats

app = FastAPI(
    title="WasteWise API",
    version="0.2.0",
    description=(
        "Household food-waste tracking and grocery optimization. "
        "Phase 1: barcode acquisition via Open Food Facts + consumption logging."
    ),
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(catalog.router)
app.include_router(inventory.router)
app.include_router(scan.router)
app.include_router(recipes.router)
app.include_router(stats.router)
app.include_router(risk.router)


@app.get("/api/health", tags=["meta"])
def health() -> dict[str, str]:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        db_status = "ok"
    except Exception as exc:  # pragma: no cover - surfaced in the response, not swallowed
        db_status = f"error: {exc.__class__.__name__}"
    return {"status": "ok", "database": db_status, "phase": "1"}


@app.get("/", include_in_schema=False)
def root() -> dict[str, str]:
    return {"service": "wastewise", "docs": "/docs"}
