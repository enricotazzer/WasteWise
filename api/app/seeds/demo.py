"""Synthetic household data, so the app isn't empty before real logging starts.

⚠️  Every row this writes is flagged `is_demo=True`. That flag is not cosmetic: this
project's two headline numbers — the at-risk detector's precision/recall and euros saved —
are only meaningful if the consumed/wasted labels are *real*. Synthetic outcomes must never
reach the survival model or a reported figure. `make demo-clear` removes them all.

What it does NOT fabricate: barcode-cache rows. The zero-typing rate is the number Phase 1
is judged on, and inventing it would make the one measurement this phase exists to produce
worthless.

The generation is deliberately not uniform-random. Uniform waste would be useless to look
at, because the whole hypothesis is that waste concentrates in fresh, short-dated items. So:

  - Shopping happens in weekly bursts, not evenly spread days.
  - Waste probability is driven by shelf life and category, not a flat coin flip.
  - Prices are present on ~60% of items, because they're typed by hand and often skipped —
    which is what makes the price-coverage caveat worth showing in the first place.
"""

import random
import sys
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.db import SessionLocal
from app.jobs.savings import rebuild
from app.models import (
    ConsumptionEvent,
    InventoryItem,
    PriceObservation,
    Product,
    User,
)
from app.models.enums import EventSource, EventType, ItemStatus
from app.services.shelf_life import estimate_expiry

WEEKS_OF_HISTORY = 8

# Typical Italian supermarket prices, per the product's default unit (€/kg, €/l or €/pz).
# Rough but plausible — they only need to make the euro figures believable, and every one of
# them is a guess, which is precisely why real prices get typed in by hand.
TYPICAL_PRICE: dict[str, float] = {
    "Zucchine": 2.40,
    "Pomodori": 2.90,
    "Insalata": 1.30,
    "Carote": 1.20,
    "Cipolle": 1.50,
    "Patate": 1.40,
    "Aglio": 0.90,
    "Peperoni": 3.20,
    "Melanzane": 2.60,
    "Spinaci": 2.20,
    "Funghi": 2.50,
    "Broccoli": 2.30,
    "Mele": 2.10,
    "Banane": 1.70,
    "Arance": 1.90,
    "Limoni": 2.40,
    "Fragole": 3.50,
    "Uva": 2.80,
    "Pere": 2.30,
    "Basilico": 1.50,
    "Prezzemolo": 1.20,
    "Rosmarino": 1.30,
    "Petto di pollo": 9.90,
    "Macinato di manzo": 12.50,
    "Salsiccia": 8.90,
    "Salmone": 22.00,
    "Prosciutto crudo": 3.80,
    "Prosciutto cotto": 2.90,
    "Latte intero": 1.35,
    "Yogurt bianco": 1.10,
    "Mozzarella": 1.29,
    "Ricotta": 2.20,
    "Parmigiano": 4.50,
    "Uova": 2.80,
    "Burro": 2.60,
    "Pasta": 1.35,
    "Riso": 2.20,
    "Passata di pomodoro": 1.10,
    "Pelati": 1.20,
    "Olio extravergine": 8.90,
    "Aceto": 1.60,
    "Tonno in scatola": 2.40,
    "Ceci": 1.10,
    "Fagioli": 1.10,
    "Farina": 0.95,
    "Zucchero": 1.10,
    "Sale": 0.50,
    "Pepe nero": 1.80,
    "Caffè": 3.60,
    "Pane": 3.20,
    "Pane in cassetta": 1.90,
    "Detersivo piatti": 2.10,
    "Carta igienica": 4.50,
}

# How often each product lands in the basket. Staples every week, treats occasionally.
# Drives which products dominate the inventory, and therefore which ones dominate the waste.
WEEKLY_BASKET = [
    ("Latte intero", 0.95),
    ("Pane", 0.90),
    ("Uova", 0.70),
    ("Pasta", 0.55),
    ("Mozzarella", 0.65),
    ("Yogurt bianco", 0.60),
    ("Parmigiano", 0.35),
    ("Insalata", 0.70),
    ("Pomodori", 0.75),
    ("Zucchine", 0.60),
    ("Carote", 0.45),
    ("Cipolle", 0.40),
    ("Patate", 0.40),
    ("Spinaci", 0.30),
    ("Funghi", 0.30),
    ("Melanzane", 0.25),
    ("Peperoni", 0.30),
    ("Broccoli", 0.25),
    ("Mele", 0.65),
    ("Banane", 0.70),
    ("Arance", 0.40),
    ("Fragole", 0.20),
    ("Uva", 0.25),
    ("Pere", 0.25),
    ("Limoni", 0.30),
    ("Petto di pollo", 0.55),
    ("Macinato di manzo", 0.35),
    ("Salsiccia", 0.20),
    ("Salmone", 0.20),
    ("Prosciutto cotto", 0.40),
    ("Prosciutto crudo", 0.25),
    ("Ricotta", 0.25),
    ("Burro", 0.25),
    ("Passata di pomodoro", 0.45),
    ("Pelati", 0.30),
    ("Tonno in scatola", 0.30),
    ("Ceci", 0.20),
    ("Fagioli", 0.20),
    ("Riso", 0.25),
    ("Basilico", 0.25),
    ("Prezzemolo", 0.20),
    ("Caffè", 0.30),
    ("Pane in cassetta", 0.30),
    ("Olio extravergine", 0.12),
    ("Farina", 0.15),
    ("Zucchero", 0.10),
    ("Detersivo piatti", 0.15),
    ("Carta igienica", 0.20),
]

WASTE_REASON_BY_SHELF_LIFE = [
    (4, ["scaduto", "dimenticato", "dimenticato", "troppo"]),
    (14, ["dimenticato", "scaduto", "avanzo", "troppo"]),
    (999, ["troppo", "avanzo", "non_piaciuto", "scaduto"]),
]


def _waste_probability(product: Product) -> float:
    """Short shelf life and fresh categories get binned far more often.

    Uniform randomness here would produce a dataset where the risk model has nothing to
    learn, and a dashboard that tells you nothing.
    """
    shelf = product.effective_shelf_life_days
    if shelf is None:
        return 0.02  # non-perishables: essentially never thrown out
    if shelf <= 3:
        return 0.30
    if shelf <= 7:
        return 0.22
    if shelf <= 21:
        return 0.10
    if shelf <= 90:
        return 0.04
    return 0.015


def _waste_reason(product: Product, rng: random.Random) -> str:
    shelf = product.effective_shelf_life_days or 999
    for threshold, reasons in WASTE_REASON_BY_SHELF_LIFE:
        if shelf <= threshold:
            return rng.choice(reasons)
    return "altro"


def _quantity(product: Product, rng: random.Random) -> Decimal:
    unit = product.default_unit.value
    if unit == "kg":
        return Decimal(str(rng.choice([0.3, 0.5, 0.5, 0.75, 1.0])))
    if unit == "l":
        return Decimal(str(rng.choice([0.5, 1.0, 1.0, 1.5])))
    return Decimal(str(rng.choice([1, 1, 1, 2, 2, 3])))


def _price(product: Product, rng: random.Random) -> Decimal | None:
    """~60% coverage, with a little week-to-week variation so Phase 3's anomaly detector has
    a distribution to work against rather than a constant."""
    base = TYPICAL_PRICE.get(product.name_it)
    if base is None or rng.random() > 0.60:
        return None
    return Decimal(str(round(base * rng.uniform(0.88, 1.15), 2)))


def _shopping_days(today: date, rng: random.Random) -> list[date]:
    """Weekly big shop plus the occasional midweek top-up."""
    days: list[date] = []
    for week in range(WEEKS_OF_HISTORY, -1, -1):
        anchor = today - timedelta(days=week * 7)
        # Saturday-ish, jittered by a day.
        big = (
            anchor - timedelta(days=(anchor.weekday() - 5) % 7) + timedelta(days=rng.randint(-1, 1))
        )
        if big <= today:
            days.append(big)
        if rng.random() < 0.55:
            top_up = big + timedelta(days=rng.randint(2, 4))
            if top_up <= today:
                days.append(top_up)
    return sorted(set(days))


def generate(db: Session, user: User, rng: random.Random) -> dict[str, int]:
    today = date.today()
    products = {p.name_it: p for p in db.scalars(select(Product))}
    counts = {"items": 0, "consumed": 0, "wasted": 0, "active": 0, "forgotten": 0, "prices": 0}

    for shop_day in _shopping_days(today, rng):
        for name, frequency in WEEKLY_BASKET:
            product = products.get(name)
            if product is None or rng.random() > frequency:
                continue

            quantity = _quantity(product, rng)
            price = _price(product, rng)
            expiry, source = estimate_expiry(product=product, acquired_at=shop_day)

            item = InventoryItem(
                user_id=user.id,
                product_id=product.id,
                quantity=quantity,
                unit=product.default_unit,
                unit_price_eur=price,
                acquired_at=shop_day,
                expiry_date=expiry,
                expiry_source=source,
                status=ItemStatus.ACTIVE,
                fraction_remaining=1.0,
                is_demo=True,
            )
            db.add(item)
            db.flush()
            counts["items"] += 1

            if price is not None:
                db.add(
                    PriceObservation(
                        user_id=user.id,
                        product_id=product.id,
                        store=rng.choice(["Esselunga", "Coop", "Conad", "Lidl"]),
                        unit_price_eur=price,
                        unit=product.default_unit,
                        observed_at=shop_day,
                        inventory_item_id=item.id,
                    )
                )
                counts["prices"] += 1

            # Anything bought recently enough to plausibly still be in the house stays open.
            still_plausible = expiry is None or expiry >= today
            if still_plausible and rng.random() < 0.75:
                counts["active"] += 1
                continue

            # A small share of expired items never gets logged at all. That's not noise —
            # it's the most common real failure mode ("I forgot it was in there"), it's what
            # the weekly nudge exists to chase, and it's a *missing label*, which hurts the
            # model more than a wasted one. The UI must have some to show.
            if not still_plausible and expiry is not None and rng.random() < 0.08:
                counts["forgotten"] += 1
                continue

            wasted = rng.random() < _waste_probability(product)
            # Waste is discovered at or just after expiry; consumption happens before it.
            if wasted:
                when = (expiry or shop_day + timedelta(days=7)) + timedelta(days=rng.randint(0, 3))
            else:
                span = ((expiry or shop_day + timedelta(days=5)) - shop_day).days
                when = shop_day + timedelta(days=rng.randint(1, max(1, min(span, 21))))
            when = min(when, today)

            occurred = datetime.combine(when, time(hour=rng.randint(8, 21)), tzinfo=UTC)
            item.status = ItemStatus.WASTED if wasted else ItemStatus.CONSUMED
            item.resolved_at = occurred
            item.fraction_remaining = 0.0
            db.add(
                ConsumptionEvent(
                    inventory_item_id=item.id,
                    event_type=EventType.WASTED if wasted else EventType.CONSUMED,
                    fraction_used=1.0,
                    occurred_at=occurred,
                    source=EventSource.MANUAL,
                    reason=_waste_reason(product, rng) if wasted else None,
                )
            )
            counts["wasted" if wasted else "consumed"] += 1

    return counts


def clear(db: Session) -> int:
    """Remove every synthetic row. Real logging is untouched."""
    items = list(db.scalars(select(InventoryItem).where(InventoryItem.is_demo.is_(True))))
    ids = [i.id for i in items]
    if ids:
        # Price observations point at items; null the link before the items go, so a real
        # price never disappears just because a demo item did.
        for obs in db.scalars(
            select(PriceObservation).where(PriceObservation.inventory_item_id.in_(ids))
        ):
            db.delete(obs)
        for item in items:
            db.delete(item)  # cascades to consumption_events
    db.commit()
    return len(ids)


def main() -> int:
    args = sys.argv[1:]
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == settings.first_user_email))
        if user is None:
            print("no user found — run `make seed` first", file=sys.stderr)
            return 1

        if "--clear" in args:
            print(f"removed {clear(db)} demo items")
            return 0

        removed = clear(db)
        if removed:
            print(f"cleared {removed} previous demo items")

        # Fixed seed by default: the same command gives the same household, so anything you
        # notice in the data is still there when you look again. Pass --seed N to vary it.
        seed = 20260805
        if "--seed" in args:
            seed = int(args[args.index("--seed") + 1])
        rng = random.Random(seed)

        counts = generate(db, user, rng)
        db.commit()

        # The generator writes inventory directly, so nothing has derived its money rows.
        # Rebuilding here keeps the ledger from lagging a `make demo` until someone happens
        # to run the job; the entries inherit is_demo=True and stay out of every figure.
        entries = rebuild(db, user_id=user.id, since=None)

        resolved = counts["consumed"] + counts["wasted"]
        rate = counts["consumed"] / resolved if resolved else 0
        print(f"generated {counts['items']} demo items over ~{WEEKS_OF_HISTORY} weeks")
        print(
            f"  {counts['active']} still in the house"
            f" · {counts['forgotten']} expired and never logged"
        )
        print(
            f"  {counts['consumed']} consumed · {counts['wasted']} wasted  (save rate {rate:.0%})"
        )
        print(
            f"  {counts['prices']} with a price ({counts['prices'] / counts['items']:.0%} coverage)"
        )
        print(f"  {entries.entries_after} savings entries derived (all is_demo)")
        print("\n  All flagged is_demo=True. Remove with `make demo-clear`.")
        print("  Excluded from Phase 3 training — synthetic outcomes are not evidence.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
