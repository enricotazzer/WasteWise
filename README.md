# WasteWise

Reduce household food waste and optimize grocery spending using real personal purchase and
consumption data.

Households throw away food because they lose track of what they have and when it expires.
This is a measurable economic and environmental problem, so WasteWise measures it: euros
wasted are computed from prices actually paid, and the "at risk of waste" detector is scored
against realised outcomes rather than asserted.

**Status: all five phases built.** Scan the shopping with a phone camera, resolve products
against Open Food Facts, log what gets eaten or binned on an urgency board, see which recipes
rescue what's about to go off, and read the whole thing back as a dashboard. 378 tests.
See [PLAN.md](PLAN.md) for the roadmap and the reasoning behind each choice.

**Every number below is regenerated from the database, not written down.** Run `make eval` or
[`notebooks/eval.ipynb`](notebooks/eval.ipynb) — both call the same module the API does.

**Everything external is free.** No paid inference anywhere: Open Food Facts is a free API,
and the two LLM providers are free tiers with no credit card. The app works with no API keys
configured at all.

> ## What is measured, and what is not
>
> There is **no real logging in this database yet**, so the honest reported value of every
> headline number is *unavailable*. `make demo` fills the app with eight weeks of plausible
> synthetic outcomes so the screens can be judged, and every one of those rows carries
> `is_demo=True` and is excluded from every reported figure by default.
>
> That is not a formality. The two things this project claims to measure — how well waste is
> predicted, and how many euros it costs — are only meaningful over outcomes that actually
> happened. A precision figure computed from a generator measures the generator. Where a
> figure below comes from demo data it is labelled **demo**, in the UI as well as here.

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
make test            # 378 tests, in-container against a throwaway Postgres
make eval            # regenerate every number in this README from the database
make demo            # fill the app with a synthetic 8-week household
make demo-clear      # remove it again

make seed-recipes    # 82 hand-written Italian recipes
make crawl           # crawl more (robots.txt, 1 req/2s per host, resumable)
make embed           # embed the corpus for pgvector reranking — CPU, offline

make jobs            # the nightly chain: repurchase -> savings-rebuild -> risk-score
make risk-fit        # fit v1; refuses below the data gate and says how far short
make scan-stats      # how many barcodes resolved without typing
make reset           # DESTRUCTIVE: drop the volume and rebuild
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

| # | Module | State |
| --- | --- | --- |
| 1 | **Data acquisition** — barcode → Open Food Facts → canonical product → inventory | ✅ built; never scanned for real yet |
| 2 | **Predictive model** — survival analysis for P(consumed before expiry) | ✅ v0 live · v1 built but below its data gate |
| 3 | **Recommendation** — crawled corpus, fridge graph, coverage ranking, Italian phrasing | ✅ built · 1,666 recipes |
| 4 | **Dashboard** — €/kg over time, spend by category, save rate, model performance | ✅ built |

Plus a **cross-domain savings ledger**: WasteWise is the first module of a wider personal-savings
system, so every euro-relevant fact lands in one `savings_entries` table keyed by `domain`. Food
is the only writer today; utilities and subscriptions are enum values away, not a migration.

**Stack.** FastAPI · PostgreSQL 16 + pgvector · SQLAlchemy 2 · Alembic · React 19 + Vite + TS ·
TanStack Query · Recharts · Open Food Facts · Groq (Gemini failover) · lifelines/scikit-learn ·
fastembed. Everything ML-side is CPU-friendly — there is no local GPU here, which is why the
original brief's Donut/Qwen2-VL LoRA fine-tune and local Llama 3.2 were dropped. Embeddings run
on onnxruntime rather than sentence-transformers for the same reason: identical
all-MiniLM-L6-v2 vectors at 384 dims, ~50 MB in the image instead of torch's ~2 GB.

### How a scan resolves

Four tiers, cheapest first. The tier that served each scan is recorded, which is where the
module's quality number comes from.

| Tier | Source | Cost |
| --- | --- | --- |
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

```text
api/app/
├─ models/          13 tables (schema stable; savings_entries is the cross-domain ledger)
├─ routers/         auth · catalog · inventory · scan · recipes · stats · risk
├─ services/
│  ├─ barcode/      off_client.py · category_map.py · resolver.py
│  ├─ llm/          groq.py · gemini.py · router.py (failover)
│  ├─ risk/         features.py · baseline.py (v0) · survival.py (v1) · scoring.py
│  ├─ savings/      rules.py · writer.py · aggregate.py · anomaly.py
│  ├─ recipes/      jsonld.py · crawler.py · frontier.py · sites.py · ingredients.py
│  │                embed.py · retrieval.py · generate.py · graph.py · rank.py · unlock.py
│  └─ shelf_life.py · normalize.py
├─ jobs/            savings · repurchase · scoring · fit · crawl · embed  (all idempotent)
├─ eval/            report.py — every README number, one source of truth
├─ seeds/           31 categories, 56 products, 82 recipes, demo.py generator
└─ tests/           378 tests, no network anywhere (respx + saved HTML fixtures)
web/src/
├─ ui/              tokens.css · Card Tile Button Badge Money Skeleton EmptyState Segmented
│                   DateRange Icon · charts/ (ChartFrame TimeSeries StackedBars Sparkline)
├─ shell/           AppShell (sidebar ≥1000px, bottom tab bar below) · routes.tsx registry
├─ domains/food/    Kitchen · Recipes · ScanScreen · AddItem · Dashboard · FridgeGraph
└─ lib/             barcode.ts (BarcodeDetector + wasm) · urgency.ts · forceLayout.ts
```

The **Kitchen** screen is still the one that matters: items sorted by urgency, one tap to
record consumed / half / wasted, waste asking a single follow-up ("why?"). Its design target
is under 10 seconds a day, because every downstream module depends on that logging actually
happening.

The shell is **domain-agnostic on purpose.** `ui/` knows nothing about food, and a domain
contributes `{id, label, icon, routes[]}` to a registry. Adding a bills module means appending
one entry, not editing the navigation.

---

## Results

Regenerate all of it with `make eval`. Nothing here is typed by hand.

### Recipe corpus — the one module with real, non-synthetic numbers

The crawler obeys `robots.txt`, waits 2s per host, identifies itself, and resumes from a
persisted frontier. **1,666 recipes, all embedded** (384-dim, 21s on CPU).

| Source | Recipes | Ingredients matched to the catalog |
| --- | ---: | ---: |
| misya.info | 626 | 50.4% |
| fattoincasadabenedetta.it | 603 | 49.4% |
| ricettedellanonna.net | 355 | 55.1% |
| hand-written seed corpus | 82 | 100.0% |
| **total** | **1,666** | **52.2%** |

**52% is the finding, not a failure.** The seed corpus scores 100% because it was written
against this catalog — averaging it in would flatter the crawler with its own test set. Real
ingredient strings are messy, and each of the 1,784 distinct unmatched strings is a missing
`ProductAlias`: a one-line fix, ranked by frequency in the notebook.

**Two large Italian recipe sites were deliberately skipped.** GialloZafferano and Cookaround
both publish clean JSON-LD and both list `ClaudeBot` / `GPTBot` / `anthropic-ai` in
`robots.txt`. A literal reading lets `WasteWiseBot` through, since it is on nobody's block
list. We are not taking the literal reading: this corpus is retrieved over and handed to an
LLM to phrase, which is exactly the use that list is drawn around. The full audit — including
the sites rejected because their `robots.txt` was unreadable — is in
[`sites.py`](api/app/services/recipes/sites.py).

### Waste-risk detection — v1 did not beat v0

The gate for fitting v1 is **≥150 resolved items and ≥40 waste events**. Against real data it
stands at **0/150 and 0/40**, so `make risk-fit` refuses to fit and says how far short it is.
Forced onto demo data (`--force`, which stamps `-ungated` on every figure it produces):

| model | c-index | precision | recall | F1 |
| --- | ---: | ---: | ---: | ---: |
| v0 heuristic | **0.639** | 0.375 | 0.333 | 0.353 |
| v1 Cox PH | 0.605 | 0.333 | 0.444 | 0.381 |
| v1 GBM | 0.304 | 1.000 | 0.111 | 0.200 |

Over 29 resolved test items, 9 of them wasted. **The Cox model ranks worse than the heuristic
it was meant to improve on**, and that is enforced rather than merely reported: the scorer
refuses to load a persisted model that lost, so v0 stays live. A more complex model that buys
nothing is a result worth publishing.

The split is **by acquisition date, never at random** — a random split puts later purchases in
the training set and leaks future consumption into the past. Scoring is as-at-purchase, which
is harder than the deployed operating point but is the only leakage-free version of the
question.

### Euros — measured, never estimated

Prices are optional and typed by hand, so **every euro figure carries its price coverage** and
kg figures carry a separate mass coverage (mass is only known for items bought by weight).
A total computed over an unknown share of the data is not a measurement.

The dashboard shows the count-based save rate beside **`unaccounted`** — spend that never
became either consumption or waste. That pairing is deliberate: the save rate is computed over
*resolved* items only, and food that was never logged is overwhelmingly food that was
forgotten, so a high rate next to a large unaccounted figure is an unmeasured result rather
than a good one.

**The before/after comparison is not an A/B test**, whatever the brief called it. One household
cannot run both arms concurrently, so it is sequential and confounded by season, by learning
effects, and by the observer effect of logging itself — that last one is not a nuisance to
control for but plausibly a large part of any effect, and it is inseparable from the
intervention by construction. It will be reported as indicative with those confounds named,
and it needs at least two weeks of baseline before it says anything at all.

### Acquisition

Zero-typing rate, split by tier, from `product_barcodes.match_method` — reproducible rather
than remembered. **No barcode has been scanned yet, so there is no rate.** That is missing
data, not a rate of 0%, and the report says so in those words.

---

## Documentation

- [PLAN.md](PLAN.md) — full roadmap, phase checkpoints, module designs, open questions
- [CLAUDE.md](CLAUDE.md) — project brief
- http://localhost:8000/docs — live OpenAPI reference

Product data from [Open Food Facts](https://world.openfoodfacts.org), used under ODbL.
