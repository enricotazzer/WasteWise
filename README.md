# WasteWise

Reduce household food waste and optimize grocery spending using real personal purchase and
consumption data.

Households throw away food because they lose track of what they have and when it expires.
This is a measurable economic and environmental problem, so WasteWise measures it: euros
wasted are computed from prices actually paid, and the "at risk of waste" detector is scored
against realised outcomes rather than asserted.

**Status: Phase 1.5 — board & recipe graph.** Scan the shopping with a phone camera, resolve
products against Open Food Facts, log what gets eaten or binned on an urgency board, and see
which recipes rescue what's about to go off. The risk model and the dashboard are not built
yet. See [PLAN.md](PLAN.md) for the roadmap and the reasoning
behind each choice.

**Everything external is free.** No paid inference anywhere: Open Food Facts is a free API,
and the two LLM providers are free tiers with no credit card. The app works with no API keys
configured at all.

---

## Quick start

Requires Docker and Node.

```bash
cp .env.example .env        # then set SECRET_KEY and FIRST_USER_PASSWORD
make setup                  # build, migrate, seed
make web-install && make web
```

- App — http://localhost:5173
- API docs — http://localhost:8000/docs
- Log in with `FIRST_USER_EMAIL` / `FIRST_USER_PASSWORD` from `.env`

### Scanning from your phone

The scanner needs a **secure context** — `getUserMedia` refuses on a plain-http LAN address,
silently, which looks exactly like a broken camera. So:

```bash
brew install mkcert && mkcert -install   # once
make dev-https                           # issues certs, prints the URL
```

Then open `https://<your-lan-ip>:5173` on the phone. Add that same origin to `CORS_ORIGINS`
in `.env`.

```bash
make test          # 175 tests, in-container against a throwaway Postgres
make scan-stats    # how many barcodes resolved without typing
make seed-recipes  # load the starter recipe corpus; reports its match rate
make demo          # fill the app with a synthetic 8-week household
make demo-clear    # remove it again
make reset         # DESTRUCTIVE: drop the volume and rebuild
```

### About `make demo`

An empty app is hard to judge, so the generator produces ~220 items over 8 weeks: a weekly
shop, plausible Italian prices, waste concentrated in short-dated food, and reasons weighted
toward "dimenticato". It's shaped rather than uniform-random, because uniform waste would
give the risk model nothing to learn and the dashboard nothing to show.

**Every row is flagged `is_demo=True`, and that flag is load-bearing.** This project's two
headline numbers — detection precision/recall and euros saved — mean nothing unless the
consumed/wasted labels are real. Synthetic outcomes are visible in the UI but must never
reach Phase 3 training or a reported figure. `make demo-clear` removes them and leaves real
logging untouched (there's a test for exactly that).

It deliberately does **not** fabricate barcode-cache rows: the zero-typing rate is the one
measurement Phase 1 exists to produce, and seeding it would make it worthless.

---

## Architecture

Four modules, built in dependency order.

| # | Module | Phase | State |
|---|---|---|---|
| 1 | **Data acquisition** — barcode → Open Food Facts → canonical product → inventory | 1 | ✅ built |
| 2 | **Predictive model** — survival analysis for P(consumed before expiry) | 3 | not started |
| 3 | **Recommendation** — fridge graph + coverage ranking over a recipe corpus | 4 | ✅ graph & ranking built; crawler + price flags pending |
| 4 | **Dashboard** — kg/€ saved over time, spend by category, model performance | 5 | stat counters only |

**Stack.** FastAPI · PostgreSQL 16 + pgvector · SQLAlchemy 2 · Alembic · React + Vite + TS ·
TanStack Query · Open Food Facts · Groq (Gemini failover) · lifelines/scikit-learn.
Everything ML-side is CPU-friendly — there is no local GPU here, which is why the original
brief's Donut/Qwen2-VL LoRA fine-tune and local Llama 3.2 were dropped.

### How a scan resolves

Four tiers, cheapest first. The tier that served each scan is recorded, which is where the
module's quality number comes from.

| Tier | Source | Cost |
|---|---|---|
| 1 | `product_barcodes` cache hit | instant, offline |
| 2 | Open Food Facts + a rules table over its category taxonomy | one HTTP call, then cached forever |
| 3 | Open Food Facts + an LLM for categories the rules miss | one free-tier call |
| 4 | You name it once; bound permanently | your time — the only expensive resource |

Every tier degrades into the next, and tier 4 always works. Open Food Facts down, no API key,
unknown barcode — all of it ends at "type a name", never at an error screen.

### The data model in one paragraph

`inventory_items` is the unit of observation: one row per physical thing in the house, with
`acquired_at`, an inferred `expiry_date`, and a `status`. `consumption_events` records what
actually happened to it — consumed, wasted, or partially used. That pair is the whole
foundation: `T = resolved_at - acquired_at` with consumption as the event and waste as a
competing risk is the survival model, and wasted items × the price actually paid is the
euros-wasted ledger. Everything else (`products`, `product_barcodes`, `product_aliases`,
`price_observations`, `risk_scores`, `recipes`) feeds one of those two.

---

## Design decisions worth knowing

**Why barcodes instead of receipt OCR.** The brief specified receipt photography with a
fine-tuned document model. That was dropped for two reasons: the hardware for fine-tuning
isn't here, and photographing every receipt is friction nobody sustains. Barcodes trade a
hard vision problem for a database lookup and give *better* identity than OCR could — Open
Food Facts returns a real product name, brand, pack size and category taxonomy, where a
receipt gives you `MOZZAREL. BUF. 125G`. The cost is that receipts were the only automatic
source of prices; see below.

**Prices are optional, and every euro figure says so.** Without receipts, prices are typed by
hand or not at all. Rather than estimate them, the app reports what fraction of resolved
items actually had a price alongside every money figure — `€4.80 wasted, across the 62% of
items with a known price`. A total computed over an unknown share of the data is not a
measurement.

**Loose produce is the honest weak spot.** Fruit and vegetables are the largest waste
category and carry no barcode. Two things cover it: a one-tap quick-add of recent products,
and binding store-printed weighed-produce labels. Those labels are EAN-13 codes starting
with `2`, meaningless outside one supermarket and never in Open Food Facts — so they skip the
network entirely and go straight to naming, after which that store's label resolves
instantly forever.

**iOS has no `BarcodeDetector`, and never has.** Safari doesn't implement it, and since every
iOS browser is WebKit underneath, no browser on iPhone has it. The WebAssembly decoder isn't
a fallback, it's the iPhone code path. It's also lazily loaded, so Android never downloads
the 1 MB binary.

**Aliases are the learning loop.** `product_aliases` maps normalized product text to
canonical products, and `product_barcodes` does the same keyed on digits. Both grow from
ordinary use with no retraining. Normalization strips accents, punctuation and pack sizes so
`LATTE 1L`, `LATTE 500ML` and `LATTE 2X500G` collapse to one key. The same matcher resolves
recipe ingredients — one vocabulary serves both, and the corpus loader reports its match rate
(currently 420/420) so a silently-lost ingredient can't inflate the graph.

**The graph explains; counting decides.** For ranking recipes, coverage counting is already
the right algorithm and the graph adds nothing to it — claiming otherwise would be dressing
up a lookup as ML. What the graph genuinely adds is the *why* (select a recipe and its
ingredients light up), cluster structure, and the one question counting can't answer: which
single purchase would make several at-risk recipes cookable at once. Staples are excluded
from the graph by default, because olive oil appears in most of the corpus and would make the
highest-degree node always olive oil — visually dominant, informationally empty.

**Urgency colours were measured, not chosen.** The old `scaduto` red and `entro 3 giorni`
amber sat at ΔE 12.2 (light) / 9.8 (dark), below the 15 normal-vision floor and 4.8–6.2 under
deutan colourblindness — genuinely hard to tell apart. The current steps measure 17.9 / 18.4
and 11.6 / 13.0. Only the two urgent bands carry colour at all; the rest are neutral, and
every band ships as column + heading + count + icon so hue is never the only cue.

**The LLM is never on the critical path.** It is used only to categorize products the rules
table doesn't cover. If Groq rate-limits, Gemini takes over; if both fail, the product lands
in "Altro" and you fix it in one tap. Nothing user-facing blocks on a model.

**Expiry is inferred, and the system records how.** The chain is `user-typed label → product
shelf life → category default`, and `expiry_source` is stored on every item so Phase 3 can
weight or exclude weakly-grounded dates. Opening usually matters more than the printed date,
so `shelf_life_days_opened` overrides the rest once an item is opened.

**Non-food is tracked but excluded from waste metrics.** Detergent gets an inventory row;
`is_food=False` keeps it out of every euro figure and out of the save rate.

**Resolution is an event, not a mutation.** Marking something consumed writes a
`consumption_events` row and is rejected if the item is already resolved. `DELETE` exists
only for mistyped entries — deleting a resolved item destroys a training label.

**Inferred labels are marked as such.** Phase 2's repurchase-inference job will close stale
items automatically, but those rows carry `source='repurchase_inferred'` and are
down-weighted in training, because they conflate consumption with waste — precisely the
distinction this project measures.

---

## What's actually built

```
api/app/
├─ models/          12 tables covering all 5 phases (schema stable; features are not)
├─ routers/         auth · catalog · inventory · scan
├─ services/
│  ├─ barcode/      off_client.py · category_map.py · resolver.py
│  ├─ llm/          groq.py · gemini.py · router.py (failover)
│  ├─ recipes/      graph.py · rank.py · unlock.py
│  └─ shelf_life.py · normalize.py
├─ seeds/           31 categories, 56 products, 82 recipes, demo.py generator
└─ tests/           175 tests, no network
web/src/
├─ lib/             barcode.ts (BarcodeDetector + wasm) · urgency.ts · forceLayout.ts
└─ components/      Login · StatsBar · Kitchen · Recipes · FridgeGraph · Scanner · AddItem
```

The **Kitchen** screen is still the one that matters: items sorted by urgency, one tap to
record consumed / half / wasted, waste asking a single follow-up ("why?"). Its design target
is under 10 seconds a day, because every downstream module depends on that logging actually
happening.

---

## Validation approach

Three numbers, and the honest caveats on all of them.

**Acquisition** — the share of scans that resolved with zero typing, split by tier. Read it
with `make scan-stats` or `GET /api/scan/stats`. It's computed from
`product_barcodes.match_method`, so it's reproducible rather than remembered.

**Detection quality** — weekly precision/recall of the at-risk flag against realised waste,
with the v0 days-to-expiry heuristic as the baseline the learned model has to beat. The
learned model does not start until there are ≥150 resolved items and ≥40 waste events; below
that, a Cox fit would be noise.

**Euros saved** — a baseline period (logging only) versus an intervention period (full
system), reported with its price coverage. The original brief called this an A/B comparison.
It is not: one household cannot run both arms concurrently, so it is confounded by season,
learning effects, and the observer effect of logging itself. It will be reported as
indicative, with those confounds named.

---

## Documentation

- [PLAN.md](PLAN.md) — full roadmap, phase checkpoints, module designs, open questions
- [CLAUDE.md](CLAUDE.md) — project brief
- http://localhost:8000/docs — live OpenAPI reference

Product data from [Open Food Facts](https://world.openfoodfacts.org), used under ODbL.
