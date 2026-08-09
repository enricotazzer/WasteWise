"""Every number the README claims, regenerated from the database.

Run it: `make eval` (or `python -m app.eval.report`). Add `--include-demo` to see what the
synthetic household produces; the default is real data only, because that is what a reported
figure has to mean.

**The default matters more than anything else here.** `make demo` fills the app with eight
weeks of plausible synthetic outcomes so the screens can be judged before real logging
exists. Those rows are labelled `is_demo` and are excluded from every figure below unless
asked for, because the two things this project claims to measure — how well waste is
predicted, and how many euros it costs — are only meaningful over outcomes that actually
happened. A precision figure computed from a generator measures the generator.

Where a number cannot be computed yet, it is reported as *unavailable with a reason*, never
as zero. "No barcode has been scanned" and "the zero-typing rate is 0%" are different claims
and only one of them is true.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models.barcode import ProductBarcode
from app.models.enums import MatchMethod
from app.models.inventory import InventoryItem
from app.models.recipe import Recipe
from app.models.user import User
from app.services.risk import survival
from app.services.savings import aggregate

#: Methods that cost the user no typing. `MANUAL` is charged once per barcode rather than
#: once per scan — binding a code by hand is a one-off, and every later scan of it is free,
#: so the rate rises as the catalog fills in. `UNRESOLVED` is charged every time, because it
#: prompted and produced nothing.
FREE_METHODS = (MatchMethod.CACHE, MatchMethod.OFF, MatchMethod.LLM)


@dataclass
class Section:
    """One block of findings, with whatever caveat makes its numbers readable."""

    title: str
    rows: list[tuple[str, Any]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def add(self, label: str, value: Any) -> None:
        self.rows.append((label, value))


@dataclass
class EvalReport:
    generated_at: datetime
    include_demo: bool
    sections: list[Section] = field(default_factory=list)

    def section(self, title: str) -> Section:
        s = Section(title)
        self.sections.append(s)
        return s

    def render(self) -> str:
        out = [
            "WasteWise — evaluation",
            f"generated {self.generated_at:%Y-%m-%d %H:%M} UTC",
            (
                "SOURCE: synthetic demo rows INCLUDED — these are not measurements"
                if self.include_demo
                else "SOURCE: real logged data only (is_demo excluded)"
            ),
        ]
        for s in self.sections:
            out.append("")
            out.append(s.title)
            out.append("-" * len(s.title))
            width = max((len(label) for label, _ in s.rows), default=0)
            for label, value in s.rows:
                out.append(f"  {label:<{width}}  {value}")
            for note in s.notes:
                out.append(f"  · {note}")
        return "\n".join(out)


def _fmt_eur(value: Decimal | None) -> str:
    return "—" if value is None else f"{value:.2f} EUR"


def _fmt_rate(value: float | None, *, of: int | None = None) -> str:
    if value is None:
        return "—"
    text = f"{value * 100:.1f}%"
    return f"{text} (n={of})" if of is not None else text


def acquisition(db: Session, report: EvalReport) -> None:
    """Module 1's scorecard: what fraction of scans cost no typing.

    Not `len(cache) / len(all)`. A barcode bound by hand is typed **once** and free forever
    after, so naming is charged once per barcode while its later scans are free; an
    unresolved scan is charged every time. Get this backwards and the rate *falls* as the
    catalog warms up, which is precisely the opposite of what it measures.
    """
    section = report.section("Module 1 — barcode acquisition")

    rows = db.execute(
        select(
            ProductBarcode.match_method,
            func.count(ProductBarcode.id),
            func.coalesce(func.sum(ProductBarcode.lookup_count), 0),
        ).group_by(ProductBarcode.match_method)
    ).all()

    if not rows:
        section.add("barcodes in catalog", 0)
        section.notes.append(
            "No barcode has ever been scanned, so there is no zero-typing rate yet. "
            "That is missing data, not a rate of 0%."
        )
        return

    total_scans = 0
    free_scans = 0
    for method, barcodes, scans in rows:
        section.add(f"{method.value:<12} barcodes / scans", f"{barcodes} / {scans}")
        total_scans += scans
        if method in FREE_METHODS:
            free_scans += scans
        elif method is MatchMethod.MANUAL:
            # One typing cost per barcode, not per scan.
            free_scans += max(0, scans - barcodes)

    section.add("total scans", total_scans)
    section.add("zero-typing rate", _fmt_rate(free_scans / total_scans if total_scans else None))


def corpus(db: Session, report: EvalReport) -> None:
    """Module 3's corpus, and the number the crawl exists to produce.

    The ingredient match rate is reported per source because the seed corpus was written
    against this catalog and therefore matches perfectly by construction. Averaging it in
    with crawled pages would flatter the crawler with its own test set.
    """
    section = report.section("Module 3 — recipe corpus")

    rows = db.execute(
        select(
            Recipe.source_site,
            func.count(Recipe.id),
            func.coalesce(func.sum(Recipe.n_ingredients), 0),
            func.count(Recipe.embedding),
        )
        .group_by(Recipe.source_site)
        .order_by(func.count(Recipe.id).desc())
    ).all()

    if not rows:
        section.notes.append("Corpus is empty — run `make seed-recipes` and `make crawl`.")
        return

    matched_by_site = dict(
        db.execute(
            select(
                Recipe.source_site,
                func.coalesce(
                    func.sum(func.coalesce(func.array_length(Recipe.ingredient_product_ids, 1), 0)),
                    0,
                ),
            ).group_by(Recipe.source_site)
        ).all()
    )

    total_recipes = total_lines = total_matched = total_embedded = 0
    for site, n, lines, embedded in rows:
        matched = int(matched_by_site.get(site, 0))
        rate = matched / lines if lines else None
        section.add(f"{site:<28} recipes", f"{n:>5}   ingredients matched {_fmt_rate(rate)}")
        total_recipes += n
        total_lines += int(lines)
        total_matched += matched
        total_embedded += embedded

    section.add("TOTAL recipes", total_recipes)
    section.add(
        "TOTAL ingredient match rate",
        _fmt_rate(total_matched / total_lines if total_lines else None),
    )
    section.add("embedded (384-dim)", f"{total_embedded} / {total_recipes}")
    section.notes.append(
        "The seed corpus matches 100% by construction — it was written against this catalog. "
        "The crawled rate is the honest one: real ingredient strings are messy, and every "
        "unmatched string is a missing ProductAlias rather than an unusable recipe."
    )


def ledger(db: Session, user_id: int, report: EvalReport) -> None:
    """Module 3's money, and the two things that keep it honest.

    Every euro is reported with its price coverage, because prices are optional and typed by
    hand — a total computed over an unknown fraction of items is not a measurement. And the
    save rate is printed next to `unaccounted`, because the rate is computed over *resolved*
    items only and unresolved food is overwhelmingly food that was quietly forgotten.
    """
    section = report.section("Modules 2–3 — consumption ledger")
    ov = aggregate.overview(db, user_id=user_id, include_demo=report.include_demo)

    section.add("items in the kitchen", ov.active_items)
    section.add("resolved outcomes", ov.resolved_total)
    section.add("  consumed / wasted", f"{ov.consumed_total} / {ov.wasted_total}")
    section.add("save rate (count-based)", _fmt_rate(ov.save_rate, of=ov.resolved_total))
    section.add(
        "save rate (euro-weighted)",
        f"{_fmt_rate(ov.save_rate_eur)}  over {_fmt_rate(ov.save_rate_eur_coverage)} with a price",
    )

    for label, figure in (("spent", ov.spend), ("used", ov.used), ("wasted", ov.wasted)):
        section.add(
            f"{label} (EUR)",
            f"{_fmt_eur(figure.eur):>12}   coverage {_fmt_rate(figure.coverage)}"
            f" ({figure.priced_entries}/{figure.entries})",
        )
    section.add(
        "wasted (kg)",
        f"{'—' if ov.wasted_kg.kg is None else f'{ov.wasted_kg.kg:.3f} kg':>12}"
        f"   coverage {_fmt_rate(ov.wasted_kg.coverage)}",
    )

    for domain in ov.domains:
        section.add(f"unaccounted [{domain.domain.value}]", _fmt_eur(domain.unaccounted.eur))

    section.notes.append(
        "Mass is only known for items bought by weight, so the kg coverage is a different "
        "fraction from the price coverage and is reported separately."
    )
    section.notes.append(
        "The save rate counts resolved items only. Read it next to `unaccounted`: food that "
        "was never logged is overwhelmingly food that was forgotten, which biases the rate "
        "upward by an unknown amount."
    )


def risk(db: Session, user_id: int, report: EvalReport) -> None:
    """Module 2's detector, against realised outcomes.

    The split is by acquisition date, never at random — a random split leaks future
    consumption into the past and inflates every metric on this page.
    """
    section = report.section("Module 2 — waste-risk detection")
    ev = survival.evaluate(db, user_id=user_id, include_demo=report.include_demo)

    section.add("data gate for v1", ev.gate.summary)
    section.add("rows evaluated", f"{ev.total_rows} ({ev.demo_rows} synthetic)")
    section.add("time split", ev.split_on or "—")
    section.add("  train / test", f"{ev.train_rows} / {ev.test_rows}")

    if not ev.models:
        section.notes.append("No model could be scored — there are no resolved outcomes yet.")
        return

    for m in ev.models:
        section.add(
            m.name,
            f"c-index {m.concordance if m.concordance is None else round(m.concordance, 3)!s:<6}"
            f" precision {_fmt_rate(m.precision)} recall {_fmt_rate(m.recall)}"
            f" F1 {_fmt_rate(m.f1)}  over {m.evaluated} items ({m.positives} wasted)",
        )

    if ev.v1_beats_v0 is False:
        section.notes.append(
            "v1 did NOT beat the v0 heuristic. The scorer stays on v0 — a more complex model "
            "that buys nothing is a finding worth reporting, not a failure worth hiding."
        )
    elif ev.v1_beats_v0 is None:
        section.notes.append("No fitted v1 model — v0 is the live scorer. Run `make risk-fit`.")
    for note in ev.notes:
        section.notes.append(note)


def build(db: Session, *, include_demo: bool = False, user_id: int | None = None) -> EvalReport:
    report = EvalReport(generated_at=datetime.now(UTC), include_demo=include_demo)
    if user_id is None:
        user = db.scalars(select(User).order_by(User.id)).first()
        if user is None:
            report.section("No user").notes.append("Run `make seed` first.")
            return report
        user_id = user.id

    acquisition(db, report)
    ledger(db, user_id, report)
    risk(db, user_id, report)
    corpus(db, report)

    real_items = db.scalar(
        select(func.count(InventoryItem.id)).where(InventoryItem.is_demo.is_(False))
    )
    if not include_demo and not real_items:
        # Two different empty states, and the difference is the whole point of this banner:
        # it has to name the *actual* reason a figure is missing. Saying "the only rows are
        # synthetic" when the table is empty is both false and useless advice — it sends the
        # reader to --include-demo, which would print exactly as much nothing.
        demo_items = db.scalar(
            select(func.count(InventoryItem.id)).where(InventoryItem.is_demo.is_(True))
        )
        note = (
            "Every figure below is empty because the only rows in this database are "
            f"synthetic ({demo_items} demo items). Re-run with --include-demo to exercise "
            "the report; the numbers it prints then describe a generator, not a household."
            if demo_items
            else "Nothing has been logged at all — no real rows and no demo rows. Add the "
            "shopping and log what gets eaten or thrown away, and these figures start "
            "filling in on their own. `make demo` puts the synthetic household back if "
            "you want to see the shape of the report before then."
        )
        report.sections.insert(
            0, Section(title="Nothing has been logged for real yet", notes=[note])
        )
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--include-demo",
        action="store_true",
        help="include synthetic rows — for exercising the report, never for reporting",
    )
    args = parser.parse_args()

    with SessionLocal() as db:
        print(build(db, include_demo=args.include_demo).render())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
