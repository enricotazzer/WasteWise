"""Test fixtures.

Tests run against a real Postgres (a throwaway `<db>_test` database), not SQLite: the
schema uses JSONB and pgvector, so an SQLite stand-in would test a different schema than
the one that ships. The test database is created once per session and dropped at the end;
tables are truncated between tests so each one starts clean.
"""

import psycopg
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings
from app.db import get_db
from app.main import app
from app.models import Base, Category, Product, User
from app.models.enums import Storage, Unit
from app.security import hash_password

TEST_DB = "wastewise_test"


def _admin_dsn() -> str:
    """libpq DSN for the maintenance database (can't CREATE DATABASE from inside it)."""
    url = settings.database_url.replace("postgresql+psycopg://", "postgresql://")
    return url.rsplit("/", 1)[0] + "/postgres"


def _test_url() -> str:
    return settings.database_url.rsplit("/", 1)[0] + f"/{TEST_DB}"


@pytest.fixture(scope="session")
def engine():
    with psycopg.connect(_admin_dsn(), autocommit=True) as conn:
        conn.execute(f'DROP DATABASE IF EXISTS "{TEST_DB}" WITH (FORCE)')
        conn.execute(f'CREATE DATABASE "{TEST_DB}"')

    eng = create_engine(_test_url(), future=True)
    with eng.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    Base.metadata.create_all(eng)

    yield eng

    eng.dispose()
    with psycopg.connect(_admin_dsn(), autocommit=True) as conn:
        conn.execute(f'DROP DATABASE IF EXISTS "{TEST_DB}" WITH (FORCE)')


@pytest.fixture
def db(engine) -> Session:
    maker = sessionmaker(bind=engine, autoflush=False, future=True)
    session = maker()
    yield session
    session.close()
    # Truncate rather than drop/recreate — much faster, and RESTART IDENTITY keeps IDs
    # predictable across tests.
    tables = ", ".join(f'"{t.name}"' for t in reversed(Base.metadata.sorted_tables))
    with engine.begin() as conn:
        conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))


@pytest.fixture
def seeded(db: Session) -> dict:
    """Minimal fixture data: two categories, three products spanning the shelf-life chain."""
    fridge = Category(
        code="FORMAGGIO_FRESCO",
        name_it="Formaggio fresco",
        name_en="Fresh cheese",
        storage=Storage.FRIDGE,
        default_shelf_life_days=5,
    )
    pantry = Category(
        code="PASTA_RISO",
        name_it="Pasta e riso",
        name_en="Pasta and rice",
        storage=Storage.PANTRY,
        default_shelf_life_days=540,
        is_perishable=False,
    )
    nonfood = Category(
        code="DETERGENTI",
        name_it="Detergenti",
        name_en="Cleaning",
        storage=Storage.NONFOOD,
        default_shelf_life_days=None,
        is_food=False,
        is_perishable=False,
    )
    # Target of the `en:tomato-sauces` rule — the resolver tests map onto this.
    passata = Category(
        code="PASSATA",
        name_it="Passata e pelati",
        name_en="Tomato passata",
        storage=Storage.PANTRY,
        default_shelf_life_days=540,
        is_perishable=False,
    )
    # FALLBACK_CATEGORY: where an unmappable product lands when no LLM is configured.
    altro = Category(
        code="ALTRO",
        name_it="Altro",
        name_en="Other",
        storage=Storage.NONFOOD,
        default_shelf_life_days=None,
        is_food=False,
        is_perishable=False,
    )
    db.add_all([fridge, pantry, nonfood, passata, altro])
    db.flush()

    mozzarella = Product(
        name_it="Mozzarella",
        name_en="mozzarella",
        category_id=fridge.id,
        default_unit=Unit.PZ,
        shelf_life_days=5,
        shelf_life_days_opened=2,
    )
    # No product-level shelf life -> must fall back to the category default.
    ricotta = Product(
        name_it="Ricotta", name_en="ricotta", category_id=fridge.id, default_unit=Unit.PZ
    )
    pasta = Product(
        name_it="Pasta",
        name_en="pasta",
        category_id=pantry.id,
        default_unit=Unit.KG,
        shelf_life_days=540,
        is_perishable=False,
    )
    detersivo = Product(
        name_it="Detersivo", name_en="detergent", category_id=nonfood.id, default_unit=Unit.PZ
    )
    db.add_all([mozzarella, ricotta, pasta, detersivo])

    user = User(
        email="test@example.com",
        password_hash=hash_password("testpassword"),
        display_name="Test",
    )
    db.add(user)
    db.commit()

    return {
        "user": user,
        "mozzarella": mozzarella,
        "ricotta": ricotta,
        "pasta": pasta,
        "detersivo": detersivo,
        "fridge": fridge,
        "pantry": pantry,
        "nonfood": nonfood,
        "passata": passata,
        "altro": altro,
    }


@pytest.fixture
def no_llm(monkeypatch):
    """Force the LLM tail to be unavailable.

    Default for every test: the suite must never depend on a network model, and the
    no-provider path is also the one that runs on a machine with no API keys — which is
    exactly how a reviewer will first clone this repo.
    """
    from app.services.llm import router as llm_router

    monkeypatch.setattr(llm_router, "_cached", None)
    monkeypatch.setattr(settings, "groq_api_key", None)
    monkeypatch.setattr(settings, "gemini_api_key", None)
    yield
    llm_router._cached = None


@pytest.fixture
def client(db: Session, seeded: dict) -> TestClient:
    app.dependency_overrides[get_db] = lambda: db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def auth_client(client: TestClient) -> TestClient:
    resp = client.post(
        "/api/auth/token", data={"username": "test@example.com", "password": "testpassword"}
    )
    assert resp.status_code == 200, resp.text
    client.headers["Authorization"] = f"Bearer {resp.json()['access_token']}"
    return client
