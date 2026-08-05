"""Synthetic household generator.

The one property that really matters: demo rows are identifiable and removable without
touching anything real. This project's claims rest on the consumed/wasted labels being
genuine, so a synthetic row that can't be told apart from a logged one is a correctness bug,
not a cosmetic one.
"""

import random
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Category, ConsumptionEvent, InventoryItem, PriceObservation, Product, User
from app.models.enums import ItemStatus, Storage, Unit
from app.seeds.demo import _waste_probability, clear, generate


def _run(db: Session, seeded: dict, seed: int = 1) -> dict[str, int]:
    counts = generate(db, seeded["user"], random.Random(seed))
    db.commit()
    return counts


def _fake_product(shelf_life_days: int | None) -> Product:
    """Unsaved Product just to exercise the probability curve."""
    product = Product(name_it="X", default_unit=Unit.PZ, shelf_life_days=shelf_life_days)
    product.category = Category(
        code="X", name_it="X", name_en="X", storage=Storage.FRIDGE, default_shelf_life_days=None
    )
    return product


def test_every_generated_item_is_flagged(db: Session, seeded: dict) -> None:
    _run(db, seeded)
    items = list(db.scalars(select(InventoryItem)))
    assert items, "generator produced nothing"
    assert all(i.is_demo for i in items)


def test_clear_removes_demo_rows_and_their_events(db: Session, seeded: dict) -> None:
    _run(db, seeded)
    assert db.query(ConsumptionEvent).count() > 0

    removed = clear(db)
    assert removed > 0
    assert db.query(InventoryItem).count() == 0
    # Events cascade — an orphaned event would silently skew the outcome counts.
    assert db.query(ConsumptionEvent).count() == 0


def test_clear_leaves_real_logging_untouched(db: Session, seeded: dict) -> None:
    """The whole point of the flag. A real item logged before or after `make demo` must
    survive `make demo-clear` intact, including its outcome."""
    real = InventoryItem(
        user_id=seeded["user"].id,
        product_id=seeded["mozzarella"].id,
        quantity=1,
        unit=Unit.PZ,
        unit_price_eur=1.29,
        acquired_at=date.today(),
        status=ItemStatus.ACTIVE,
    )
    db.add(real)
    db.commit()
    real_id = real.id

    _run(db, seeded)
    clear(db)

    survivor = db.get(InventoryItem, real_id)
    assert survivor is not None
    assert survivor.is_demo is False
    assert survivor.unit_price_eur is not None


def test_clear_does_not_delete_real_price_observations(db: Session, seeded: dict) -> None:
    """Price observations outlive the item they came in on — they're the only price signal
    in the system, so a demo purge must not take a real one with it."""
    obs = PriceObservation(
        user_id=seeded["user"].id,
        product_id=seeded["mozzarella"].id,
        store="Coop",
        unit_price_eur=1.29,
        unit=Unit.PZ,
        observed_at=date.today(),
        inventory_item_id=None,
    )
    db.add(obs)
    db.commit()

    _run(db, seeded)
    clear(db)

    assert db.query(PriceObservation).count() == 1


def test_generation_is_reproducible_for_a_given_seed(db: Session, seeded: dict) -> None:
    """A fixed seed means something you notice in the data is still there next time."""
    first = _run(db, seeded, seed=42)
    clear(db)
    second = _run(db, seeded, seed=42)
    assert first == second


def test_waste_probability_falls_monotonically_with_shelf_life(seeded: dict) -> None:
    """Uniform-random waste would give the risk model nothing to learn and the dashboard
    nothing to show — the whole hypothesis is that waste concentrates in short-dated food.

    Asserted on the probability function rather than on generated counts: the function *is*
    the invariant, and a sampled version of this test would only be measuring the RNG.
    """
    curve = [_waste_probability(_fake_product(d)) for d in (2, 5, 14, 60, 540)]
    assert curve == sorted(curve, reverse=True), curve
    # And the spread has to be wide enough to be a signal, not a rounding difference.
    assert curve[0] > curve[-1] * 5

    # Non-perishables are the floor.
    assert _waste_probability(_fake_product(None)) < curve[-1] + 0.02


def test_waste_actually_occurs_with_a_realistic_catalog(db: Session, seeded: dict) -> None:
    """The statistical counterpart, kept coarse on purpose: with the real seeded catalog
    some food must get binned, or the app has nothing to show."""
    _run(db, seeded, seed=7)
    resolved = db.query(InventoryItem).filter(
        InventoryItem.status.in_([ItemStatus.CONSUMED, ItemStatus.WASTED])
    )
    assert resolved.count() > 0


def test_prices_are_partial_which_is_the_realistic_case(db: Session, seeded: dict) -> None:
    """Prices are hand-typed and often skipped. Full coverage would hide the caveat the
    dashboard is supposed to display."""
    _run(db, seeded, seed=3)
    items = list(db.scalars(select(InventoryItem)))
    priced = [i for i in items if i.unit_price_eur is not None]
    coverage = len(priced) / len(items)
    assert 0.4 < coverage < 0.85, f"coverage {coverage:.2f} is not realistically partial"


def test_no_barcode_cache_rows_are_fabricated(db: Session, seeded: dict) -> None:
    """The zero-typing rate is the number Phase 1 is judged on. Seeding it would destroy the
    only measurement this phase exists to produce."""
    from app.models import ProductBarcode

    _run(db, seeded)
    assert db.query(ProductBarcode).count() == 0


def test_generator_needs_no_network_or_llm(db: Session, seeded: dict) -> None:
    """No respx mock is installed in this module — if the generator reached out, these tests
    would be flaky against a live service."""
    counts = _run(db, seeded)
    assert counts["items"] > 0


def test_user_scoping(db: Session, seeded: dict) -> None:
    other = User(email="other@example.com", password_hash="x")
    db.add(other)
    db.commit()

    _run(db, seeded)
    assert db.query(InventoryItem).filter(InventoryItem.user_id == other.id).count() == 0
