# WasteWise — Implementation Plan

## Context

WasteWise is a system to reduce household food waste and optimize grocery spending from real personal purchase and consumption data. The brief lives in [CLAUDE.md](CLAUDE.md).

**Phase 0 is built and running** (schema, auth, manual entry, consumption logging, 52 tests). This revision covers Phase 1 onward, after two decisions on 2026-08-05 that changed what gets built: *use free APIs instead of paid ones*, and *receipt photography isn't worth the friction*. The second is the larger one — it removes Module 1 as the brief conceived it and replaces it with barcode scanning, which is a better fit for the same job.

The decisions that shape this plan and override parts of the original brief:

| Decision | Effect on the brief |
|---|---|
| **Cloud APIs, minimal local compute** | Donut / Qwen2-VL LoRA fine-tuning and local Llama 3.2 3B are **out**. `scikit-learn` / `lifelines` stay — they're CPU-friendly. |
| **Explicit logging + repurchase fallback** | Adds a first-class consumption-logging surface and an `consumption_events` table. This is the label source for the survival model *and* the numerator for euros-saved. |
| **Portfolio / job-hunting project** | Breadth across all 4 modules + a demoable dashboard is the priority. Validation is honest but light: one evaluation notebook, not an ablation study. |
| **Free APIs only** (2026-08-05) | Anthropic API is **out**. Text generation runs on **Groq** (free tier, no card) with **Gemini** free tier as failover. No paid inference anywhere in the system. |
| **Receipt OCR dropped** (2026-08-05) | You judged receipt photography not worth the friction. Module 1 becomes **barcode scanning + Open Food Facts**. The `receipts` / `receipt_lines` tables are removed. |
| **Prices are optional and manual** (2026-08-05) | One optional price field at add time. Euros stay *measured*, never estimated — and every money figure is reported alongside its price-coverage percentage. |
| **Recipes: scraped Italian corpus** (2026-08-05) | RecipeNLG (English) is **out**. A crawler builds a local Italian recipe corpus from schema.org/Recipe JSON-LD. Ranking is by ingredient coverage, not strict all-match. |

**The build order is driven by one constraint:** the survival model needs resolved outcomes (consumed vs. wasted) that don't exist yet. So manual data entry ships in week 1 — logging cannot wait on the acquisition pipeline.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│  React + Vite + TS (mobile-first PWA)                       │
│  Scan · Kitchen · Recipes · Dashboard                       │
└───────────────────────────┬─────────────────────────────────┘
                            │ REST + JWT
┌───────────────────────────▼─────────────────────────────────┐
│  FastAPI                                                     │
│  ┌──────────┬──────────────┬──────────┬──────────────────┐  │
│  │ scan     │ inventory    │ risk     │ recommend        │  │
│  │ (M1)     │ + logging    │ (M2)     │ + savings (M3)   │  │
│  └────┬─────┴──────┬───────┴────┬─────┴────────┬─────────┘  │
└───────┼────────────┼────────────┼──────────────┼────────────┘
        │            │            │              │
   Open Food    PostgreSQL   lifelines/    Groq/Gemini
   Facts API    + pgvector   sklearn       + scraped IT
   (+ LLM tail)              (nightly job)  recipe corpus
```

### Stack

| Layer | Choice | Why |
|---|---|---|
| API | FastAPI, Python 3.12, SQLAlchemy 2.0, Alembic | Per brief; async, good Pydantic integration for structured LLM outputs |
| DB | PostgreSQL 16 + `pgvector` | Relational core + recipe embeddings in one place |
| Product identity | **Open Food Facts** `/api/v2/product/{ean}.json` | Free, no key. v2 (not v3) because it returns `product_name_it` and a parsed pack size — verified against the live API |
| Barcode capture | `BarcodeDetector` → **`zxing-wasm`** fallback | Native API on Android/Chrome; WASM decoder for iOS Safari, which still has no `BarcodeDetector` |
| LLM (text only) | **Groq** `openai/gpt-oss-120b` (strict JSON schema), **Gemini 2.5 Flash** failover | Free tiers, no card. Used for category-taxonomy tails and Italian recipe generation |
| Embeddings | `sentence-transformers/all-MiniLM-L6-v2` (CPU) | ~80 MB, runs fine on Mac, no API cost, offline |
| Survival | `lifelines` (CoxPH) + `scikit-learn` (GBM) | Per brief; CPU-only |
| Jobs | APScheduler in-process | One user, one machine — Celery is overkill |
| Frontend | React 18 + Vite + TS + TanStack Query + Recharts | Per brief |
| Deploy | `docker compose` (postgres + api + web) | Reproducible; demoable on any machine |

**Inference cost: €0.** Groq's free tier needs no credit card and runs at ~30 RPM / 1K RPD per model; `openai/gpt-oss-*` are the models with **strict** constrained decoding, so a JSON schema is guaranteed rather than hoped for. Gemini 2.5 Flash's free tier (`responseSchema`, similar guarantee) is the failover when Groq 429s. Both are used only for the taxonomy tail and recipe text — nothing user-facing blocks on them.

**Open Food Facts** is free and keyless but rate-limited (**~100 req/min** for barcode GETs, 10 req/min for search) and requires a custom `User-Agent` of the form `WasteWise/0.1 (email)`. Exceeding the limits gets the IP banned, so the client enforces its own throttle — and since every lookup is cached permanently in `product_barcodes`, steady-state traffic is near zero anyway.

---

## Data model

Core tables (`api/app/models/`). The two that matter most are `inventory_items` (the unit of observation for the survival model) and `consumption_events` (the labels).

```
users(id, email, password_hash)

categories(id, code, name_it, name_en,
           default_shelf_life_days, storage)      -- pantry|fridge|freezer, seeded

products(id, name_it, name_en, category_id,
         shelf_life_days, is_perishable, unit,
         is_staple)                                -- canonical vocabulary
                                                   -- is_staple: olio/sale/farina...
                                                   -- assumed present, never "missing"

product_aliases(id, raw_text_norm UNIQUE, product_id,
                source, confidence, confirmed_at)  -- ⭐ the learning table

product_barcodes(id, barcode UNIQUE, product_id,          -- ⭐ NEW in phase 1
                 off_name, off_brand, off_quantity,
                 off_categories JSONB, off_payload JSONB, -- verbatim OFF response
                 match_method, confirmed_by_user,         -- off|llm|manual
                 first_seen_at, lookup_count)

inventory_items(id, user_id, product_id, barcode NULL,
                quantity, unit, acquired_at,
                expiry_date, expiry_source,        -- label|product|category
                opened_at NULL, status,            -- active|consumed|wasted|partial
                resolved_at NULL)

consumption_events(id, inventory_item_id, event_type,  -- consumed|wasted|partial
                   fraction_used, occurred_at,
                   source, note)                       -- manual|repurchase_inferred

price_observations(id, product_id, store, unit_price_eur,
                   observed_at, inventory_item_id)   -- written when a price is entered

risk_scores(id, inventory_item_id, computed_at, model_version,
            p_consume_3d, p_consume_7d, risk_band)      -- low|medium|high

recipes(id, source_url UNIQUE, source_site, title_it,     -- scraped, Italian-native
        ingredients_raw JSONB, ingredient_product_ids int[],
        servings, total_minutes, instructions,
        embedding vector(384), scraped_at)

recipe_suggestions(id, user_id, created_at, matched_item_ids,
                   text_it, model_id, accepted)
```

**Multi-user from day one** (`user_id` everywhere) even though there's one real user — it costs nothing now and reads better in a portfolio review.

**Schema changes in Phase 1.** `receipts` and `receipt_lines` are dropped, `inventory_items.receipt_line_id` becomes `barcode`, `price_observations.receipt_line_id` becomes `inventory_item_id`, and `recipes` switches from the RecipeNLG shape to the scraped-Italian shape. The database holds **no real data and the repo has no commits**, so the cleanest path is to **regenerate the single initial migration** rather than stack a second migration that immediately undoes the first. Everything else from Phase 0 — `inventory_items`, `consumption_events`, the seeded categories and shelf-life table — is unchanged.

---

## Module 1 — Barcode acquisition

Receipt OCR is out. Groceries enter the system by **scanning the barcode while unpacking the shopping**, resolved against Open Food Facts. This trades a hard vision problem for a database lookup, and gets *better* product identity than OCR ever could: OFF returns the real product name, brand, pack size and a category taxonomy, where a receipt gives you `MOZZAREL. BUF. 125G`.

**Flow:** camera → EAN-13 → cache/OFF lookup → map to canonical product → one-tap confirm → `inventory_items`.

### Capture (the part that decides whether this is used)

```
BarcodeDetector available?  ──yes──►  native, ~0 cost
         │no
         ▼
   zxing-wasm decoder on a <video> frame loop
```

iOS Safari still has no `BarcodeDetector` in 2026 and every iOS browser is WebKit underneath, so the WASM path is not an edge case — it is the path on iPhone. `zxing-wasm` is the same decoder behind the common polyfills; wire it directly rather than pulling in a scanner UI kit.

**Camera access needs a secure context.** `localhost` qualifies, but `http://192.168.x.x:5173` from your phone does **not** — `getUserMedia` will simply refuse. Phase 1 therefore adds `mkcert`-issued local certs and runs Vite over HTTPS on the LAN. Without this the feature cannot be tested on the device it is designed for, so it is part of the phase, not an afterthought.

**Continuous scan mode:** the camera stays open and items accumulate in a list as you scan, with a distinct beep for hit / unknown. Unpacking twelve items should be twelve scans, not twelve navigations.

### Resolution (four tiers, cheapest first)

1. **Known barcode** — `product_barcodes` hit. Instant, offline, free. After the first shop this is most of a regular basket.
2. **Open Food Facts** — `GET /api/v2/product/{ean}.json?fields=product_name,product_name_it,brands,quantity,product_quantity,product_quantity_unit,categories_tags&lc=it&cc=it`. The response is cached permanently in `product_barcodes.off_payload`, so a given barcode is fetched from the network exactly once, ever.
3. **Category mapping** — OFF's `categories_tags` (`en:mozzarella`, `en:fresh-cheeses`, …) → one of the 31 seeded categories, by a hand-written rules table for the common tags, falling back to a **Groq call with a strict JSON schema** for the tail. The LLM proposes `{category_code, shelf_life_days}`; it never invents a category outside the seeded set.
4. **Manual** — unknown barcode and no OFF record: you name it once, and the barcode is bound to the product forever.

Tier 4 matters more than it looks. **Store-weighed produce carries an internal EAN starting with `2`** that is meaningless outside that supermarket and absent from OFF. Detect the `2`-prefix, skip the network call entirely, and go straight to naming — then that store's zucchini label resolves instantly on every future shop. Same alias-learning idea as the receipt plan, keyed on digits instead of text.

### Loose produce has no barcode at all

Fruit and vegetables are the largest waste category and often arrive with no code on them. Barcode scanning cannot be the only entry path, so Phase 1 also upgrades the Phase 0 add form into a **quick-add**: recent products first, one tap to re-add, no category browsing. Realistically the split is barcodes for packaged goods and quick-add for produce, and both need to be fast.

### Price

One optional `€` field on the confirm step, blank by default. A filled price writes a `price_observations` row and feeds the waste ledger and anomaly detection; a blank one costs nothing. **Every money figure in the app is reported with its price coverage** (`€4.80 wasted — across the 62% of items with a known price`), because a euro total computed over an unknown fraction of items is not a measurement.

---

## Module 2 — Consumption logging + risk model

### Logging ("Kitchen" screen)

The highest-friction part of the whole system, so it gets the most UI care. Design targets: under 10 seconds/day, thumb-reachable, no typing.

- Items sorted by risk band, then by expiry date.
- One tap → `consumed` / `wasted` / `half used`.
- Swipe-to-dismiss for "still have it" (records nothing, just moves it down).
- Weekly nudge: "these 6 items are past their estimated expiry — what happened?"

**Repurchase inference** (nightly job): if a product is bought again while an older unresolved item of the same product is still `active`, close the old one as `consumed` with `source='repurchase_inferred'`. These labels are flagged and **weighted lower** in training — they conflate consumption with waste, which is exactly the distinction the project is about.

### Risk model

**Unit of observation:** one `inventory_item`. `T` = days from `acquired_at` to resolution. Event of interest = consumption; `wasted` and `still active` are handled as competing risk / right-censoring respectively.

**Output:** `P(consumed before expiry_date)`. Low probability + near expiry ⇒ **at risk of waste**.

Ship in two stages, and be explicit in the README about which one is live:

| Stage | Model | Gate |
|---|---|---|
| **v0** | Heuristic: days-to-expiry × category perishability × household historical consumption rate for that product | Ships immediately, works on day 1 |
| **v1** | Cox PH (`lifelines`) with GBM comparison (`sklearn`) | Needs **≥150 resolved items and ≥40 waste events** |

**v0 is also the evaluation baseline.** If v1 doesn't beat it on precision/recall, that's a finding worth reporting, not a failure to hide.

**Cold start** — hierarchical fallback so the model degrades gracefully instead of erroring on unseen products:

```
item history → product history → category Kaplan-Meier → seeded shelf-life prior
```

**Features:** category, product, quantity vs. typical purchase, days-to-expiry, historical consumption rate for the product, month (seasonality), count of same-category active items, `is_opened`, days since purchase.

Nightly job recomputes `risk_scores` for all active items.

---

## Module 3 — Recipes + savings

### Recipe corpus — scraped, Italian-native

RecipeNLG is out. An English corpus forced a bilingual join and produced recipes nobody in an Italian kitchen would cook. Instead, a one-off crawler builds a local Italian corpus (~5–15K recipes) and everything downstream runs offline.

**Why scraping is clean here:** essentially every Italian recipe site embeds `schema.org/Recipe` as JSON-LD for Google's rich results. No HTML reverse-engineering, no brittle CSS selectors — parse `<script type="application/ld+json">` and read `name`, `recipeIngredient`, `recipeInstructions`, `recipeYield`, `totalTime`.

Crawler rules, non-negotiable: obey `robots.txt`, one request per 2s per host, identifying `User-Agent`, resume from a persisted URL frontier, store `source_url` and show attribution in the UI. Personal, non-redistributed corpus.

Each `recipeIngredient` string (`"320 g di pasta"`) is parsed into quantity + unit + ingredient text, then mapped to a canonical product **through the exact same `normalize_text` + `product_aliases` pipeline the barcode path uses**. One vocabulary, one matcher, and every barcode confirmation improves recipe matching for free.

### Ranking — coverage, not strict all-match

Strict "recipe contains every ingredient I have logged" returns an empty screen almost every day. Instead:

```
score = (# of my at-risk items used)        <- primary, urgency-weighted
      - (# of missing non-staple ingredients)
```

**Pantry staples** (olio, sale, pepe, aglio, cipolla, farina, zucchero, …) are assumed present and never counted as missing — a seeded `is_staple` flag on products. Each suggestion shows what it uses and what's missing:

```
Zucchine (2gg) · Mozzarella (1gg) · Uova

1. Frittata di zucchine        3/3 ✓
2. Parmigiana leggera          2/3   manca: pomodoro
3. Pasta zucchine e uova       2/3   manca: pasta
```

Retrieval is hybrid: an inverted index on canonical ingredient IDs does the real work (exact, fast, and it is literally the question being asked), with a **pgvector rerank** on the embedded ingredient set for tie-breaking and near-misses. A Groq call then writes 2–3 suggestions in Italian, grounded on the retrieved recipes and constrained to what is actually in inventory — generation phrases and adapts, it never invents the recipe.

### Savings module

- **Price anomalies:** per `(product, store)`, flag when `unit_price_eur` deviates > 1.5σ from the trailing median. Needs ≥5 observations before it says anything.
- **Batch-purchase advice:** only fires when unit price drops materially *and* the modelled `P(consume before expiry)` for the larger quantity stays above threshold — otherwise "buy the big pack" is advice to waste more, which would be embarrassing in a food-waste app.
- **Waste ledger:** every `wasted` event × the unit price actually paid → euros wasted, from `price_observations`. Measured, not estimated — and always shown with its price-coverage percentage, since prices are optional.

---

## Module 4 — Dashboard

Recharts, four views:
1. **Waste over time** — kg and € per week, split by category.
2. **Spend by category** — trend + month-over-month.
3. **Save rate** — % of purchased items consumed rather than wasted, the headline metric.
4. **Model performance** — precision/recall of the at-risk flag vs. the v0 baseline, updated weekly.

View 4 is unusual for a consumer app and is exactly the thing that makes this read as an ML project rather than a CRUD app. Keep it.

---

## Validation

Scoped to portfolio weight — honest, reproducible, not a paper.

**Detection quality:** weekly precision/recall/F1 for the at-risk flag against realised waste events, with the v0 heuristic as baseline. One `notebooks/eval.ipynb` that regenerates every number in the README from the database.

**Acquisition:** zero-typing rate — scans that cost no typing ÷ total scans — split by tier (`cache` / `off` / `llm` / `manual`). Naming is charged **once per barcode, not per scan**: binding a code by hand is a one-off and every later scan of it is free, so the rate rises as the catalog fills in. An unresolved scan is charged every time, because it prompted and produced nothing. Reproducible from `product_barcodes.match_method` via `make scan-stats`.

**Euros saved:** sequential before/after — baseline period (logging only, no risk surfacing) vs. intervention period (full system). Reported with price coverage, since prices are optional. The brief calls this an A/B comparison; **it is not.** A single household cannot run both arms concurrently, so it is confounded by season, learning effects, and the observer effect of logging itself. State that plainly in the README and report it as an indicative figure with the confounds named. Overclaiming here is the fastest way to lose a technical reviewer's trust; naming the limitation is the thing that earns it.

---

## Working agreement — phase-by-phase with review gates

**One phase at a time. I stop at the end of each phase and wait for your review before starting the next.**

At every checkpoint I will hand you:
1. **What was built** — a short summary of the files added/changed.
2. **How to verify it yourself** — the exact commands to run and what you should see.
3. **Anything I decided that you might disagree with** — assumptions made, corners cut, things deferred.
4. **What Phase N+1 will do**, so you can redirect before I've written any of it.

I will not roll ahead into the next phase, and I will not silently expand a phase's scope. If something in a later phase turns out to be needed early, I'll flag it at the checkpoint rather than just doing it.

Within a phase, work runs uninterrupted — the gate is at the boundary, not at every file.

## Phases

Each phase ends with something runnable **and a checkpoint you sign off on**.

| Phase | Scope | ⛳ Checkpoint — what you review |
|---|---|---|
| **0 · Foundations** ✅ done | Repo, `docker compose`, schema + Alembic, seeded categories & shelf-life table, JWT auth, **manual item-entry form**, 52 tests | Stack comes up clean; **you review the DB schema and the seeded shelf-life table**. Superseded in part by the Phase 1 schema change below. |
| **1 · Barcode acquisition** (~wk 2) | Schema rework (drop receipts, add `product_barcodes`), OFF client + cache, category mapping w/ Groq tail, LLM provider layer, scanner UI (native + WASM), HTTPS on LAN, quick-add | **Scan your actual shopping.** You review hit rate (how many barcodes resolve without you typing) and whether scanning is faster than typing. If it isn't, we cut it and keep quick-add. |
| **1.5 · Board & graph** ✅ done | Kitchen as an urgency board, validated alarm colours, KPI row, seeded Italian recipe corpus, fridge knowledge graph + coverage ranking. Pulls Phase 4's corpus forward; **no LLM** | You judge whether the board makes tonight's decision obvious, and whether the graph's suggestions are actually cookable. |
| **2 · Kitchen** (~wk 3) | Consumption logging UI, repurchase inference job, waste ledger | **The friction gate.** You log for 2–3 days and tell me if it's sustainable. If it isn't, we redesign here — everything downstream depends on you actually logging. |
| **3 · Risk** (~wk 4–5) | v0 heuristic → nightly scoring → v1 Cox/GBM once the data gate is met | You review v0's at-risk rankings against your own judgment. **v1 is a separate sub-checkpoint** — it only starts once the ≥150 items / ≥40 waste events gate is met, and I'll report if it isn't. |
| **4 · Recommend** (~wk 6) | Italian recipe crawler + JSON-LD parser, ingredient→product mapping, embeddings, coverage ranking, Italian generation, price anomalies | You judge whether the suggestions are actually cookable, and whether the price flags are real signal. |
| **5 · Dashboard** (~wk 7) | All four chart views, eval notebook, README with real numbers | Full walkthrough on your phone; you review the eval numbers and the README's claims before anything is public. |

**Stretch (post-5):** fridge/counter-photo fallback via a free vision model, for loose produce that has no barcode — this is the one place a VLM still earns its keep. Optionally, fine-tune a small model on the accumulated barcode→category mappings and benchmark it against the Groq tail call.

---

## Repository layout

```
WasteWise/
├─ docker-compose.yml
├─ CLAUDE.md · PLAN.md · README.md
├─ api/
│  ├─ app/
│  │  ├─ main.py  config.py  deps.py
│  │  ├─ models/            # SQLAlchemy
│  │  ├─ schemas/           # Pydantic (incl. LLM output schemas)
│  │  ├─ routers/           # scan inventory catalog risk recipes stats auth
│  │  ├─ services/
│  │  │  ├─ llm/  base.py groq.py gemini.py     # provider layer + failover
│  │  │  ├─ barcode/  off_client.py resolver.py category_map.py
│  │  │  ├─ normalize.py  shelf_life.py         # existing, reused
│  │  │  ├─ risk/  baseline.py survival.py features.py
│  │  │  ├─ recipes/ crawler.py jsonld.py retrieval.py generate.py
│  │  │  └─ savings.py
│  │  └─ jobs/              # nightly scoring, repurchase inference
│  ├─ alembic/  seeds/  tests/
├─ web/  (React + Vite)
└─ notebooks/  eval.ipynb
```

---

## Verification

**Phase 1**
```bash
make setup && make test                    # stack up, migrated, seeded, 60+ tests green
curl -s localhost:8000/api/scan/8076809513692 | jq   # OFF resolution, no auth-free path in prod
make dev-https                             # mkcert + Vite over TLS on the LAN
```
Then on the **phone**, over `https://<lan-ip>:5173`: scan a real product, confirm, see it in `In casa`. Scan the same barcode again — the second lookup must not touch the network (assert `lookup_count` incremented, `off_payload` unchanged). Scan a `2`-prefixed store produce label — it must skip OFF entirely and go straight to naming. Then scan a full shop and record the **hit rate**: barcodes resolved with zero typing ÷ items scanned. That single number is the Phase 1 checkpoint and the README's headline for this module.

**Phase 2** — log a week of real outcomes; assert `consumption_events` rows have correct `source` attribution; verify the repurchase job closes stale items and only flags them as inferred.

**Phase 3** — `pytest api/tests/test_risk.py` for the heuristic; `notebooks/eval.ipynb` for the model: time-based train/test split (never random — it would leak future consumption into the past), concordance index, precision/recall at the operating threshold, v1 vs. v0 side by side.

**Phase 4** — unit-test the JSON-LD parser against saved HTML fixtures (no network in tests); seed inventory with known expiring items and assert suggestions reference only in-inventory or staple ingredients; unit-test anomaly detection against synthetic price series.

**Phase 5** — full manual walkthrough on a phone: photo → review → log → see risk → get recipe → see it in the dashboard. Charts render correctly with empty data and with a full month.

---

---

## Phase 1 — implementation notes

**Files created**

| Path | Purpose |
|---|---|
| `api/app/models/barcode.py` | `ProductBarcode` — the cache *and* the learning table |
| `api/app/services/barcode/off_client.py` | `httpx` client: `User-Agent: WasteWise/0.1 (enrico.tappo00@gmail.com)`, token-bucket throttle, 5s timeout, retry-once, returns `None` on `status: 0` (unknown barcode) rather than raising |
| `api/app/services/barcode/category_map.py` | `OFF_TAG_TO_CATEGORY` rules table → one of the 31 seeded codes; LLM only for the tail |
| `api/app/services/barcode/resolver.py` | The 4-tier orchestration; the only module routers call |
| `api/app/services/llm/base.py` | `LLMProvider` protocol: `complete_json(system, user, schema) -> dict` |
| `api/app/services/llm/groq.py` | `openai/gpt-oss-120b`, `response_format={"type":"json_schema", strict: true}` |
| `api/app/services/llm/gemini.py` | `gemini-2.5-flash` + `responseSchema`, used on Groq 429/5xx |
| `api/app/routers/scan.py` | `GET /api/scan/{barcode}` (resolve, no write) · `POST /api/scan/{barcode}/bind` (manual naming) |
| `web/src/components/Scanner.tsx` | `BarcodeDetector` with `zxing-wasm` fallback, continuous mode |
| `web/src/components/ScanConfirm.tsx` | Product + qty + optional price, one tap to add |

**Files modified:** `alembic/versions/*_initial_schema.py` (regenerated), `models/inventory.py`, `models/pricing.py`, `models/recipe.py`, `models/__init__.py`, `config.py` (Groq/Gemini keys replace `anthropic_api_key`), `.env.example`, `docker-compose.yml`, `Makefile` (`dev-https`), `web/src/api.ts`, `web/src/App.tsx` (third tab), `README.md`, and **`CLAUDE.md` + `PLAN.md`** — the brief still describes Donut/Qwen2-VL receipt OCR and RecipeNLG, which is no longer what gets built.

**Files deleted:** `api/app/models/receipt.py`.

**Reused, not rewritten:** `services/normalize.py` (feeds `product_aliases` from OFF names), `services/shelf_life.py` (unchanged expiry chain), `routers/catalog.py::create_product` (the manual-bind path creates products through it, so alias registration already happens).

**Failure behaviour, decided up front.** OFF down, Groq rate-limited, and Gemini also failing must all degrade to *manual naming*, never to an error screen — the scanner is a convenience over an add form that already works. `parse_status`-style tracking moves onto `product_barcodes.match_method` so the README can report what fraction of resolutions were free (`off`), LLM-assisted (`llm`), or typed (`manual`).

**Synthetic data is quarantined.** `make demo` fills the app with a plausible 8-week household so it can be judged before real logging exists, but every row carries `is_demo=True`. That flag is load-bearing: the detector's precision/recall and the euros-saved figure are only meaningful if the consumed/wasted labels are real, so **Phase 3 training and every reported number must filter it out**. `make demo-clear` removes it without touching real logging. The generator deliberately does not fabricate barcode-cache rows — the zero-typing rate is the one measurement Phase 1 exists to produce.

**Secrets:** `GROQ_API_KEY` and `GEMINI_API_KEY` in `.env` only, which is already gitignored. Both are free-tier keys with no card attached; get them at `console.groq.com` and `aistudio.google.com`.

---

## Phase 1.5 — Kitchen board & recipe knowledge graph

### Context

Two requests, 2026-08-05. The Kitchen screen is one flat list in a 560px column: with 73
active items it buries the five things that actually need eating tonight, and a laptop screen
sits 80% empty. Separately: build a **knowledge graph over the fridge, edges = shared
recipes**, so recommendations are grounded in what is actually cookable together.

The graph needs a recipe corpus, and `recipes` is empty — Phase 4 was going to fill it. Your
call: **seed a curated starter corpus now** into that same table, so the graph is real this
phase and the Phase 4 crawler later becomes just another writer behind the same interface.

**One honest scoping note.** For *ranking* recipes, coverage counting is already the right
algorithm and the graph adds nothing to it. What the graph adds is worth having anyway:
it **explains** a suggestion (you see which of your items connect), it finds the
**densest cluster** of expiring items, and it answers *"which single ingredient would unlock
the most recipes"* — which coverage counting cannot. So the graph is the explanation and
exploration layer; the ranked list stays the answer.

### Colour: measured, not chosen

Ran the dataviz validator against the app's own palette on its own surfaces. It found a real
defect **already shipping**: `.pill.red` (scaduto) and `.pill.amber` (≤3 giorni) — the two
most important signals in the app — are **ΔE 12.2 apart in light and 9.8 in dark**, under the
15 normal-vision floor, and **4.8–6.2 under deutan** CVD. Today only the text inside each pill
rescues them. Columns would lean on colour harder, so the steps get re-picked:

| Token | Now | New | Normal-vision ΔE | CVD ΔE |
|---|---|---|---|---|
| light danger / warn | `#b3261e` / `#a8641a` | `#b3261e` / **`#b07a00`** | 12.2 → **17.9** | 6.2 → **11.6** |
| dark danger / warn | `#f2837b` / `#e0a35e` | **`#ee8478`** / **`#9e7400`** | 9.8 → **18.4** | 4.8 → **13.0** |

Both new pairs PASS chroma, contrast and CVD. The dark pair fails only the *lightness-band*
check, deliberately: that check keeps a **categorical** set evenly weighted, whereas this is a
**status** encoding where "scaduto" is supposed to shout louder. Status is explicitly out of
that check's scope.

Per the skill's status rule, colour never carries meaning alone — every band ships as
**column + heading + count + icon**.

### Urgency bands (the columns)

| Column | Rule | Encoding |
|---|---|---|
| **Scaduti** | `days_to_expiry < 0` | critical |
| **Entro 3 giorni** | `0 ≤ d ≤ 3` | serious |
| **Questa settimana** | `4 ≤ d ≤ 7` | neutral + count |
| **Più avanti** | `d > 7` | neutral, recessive |
| **Senza scadenza** | `d === null` | collapsed by default |

Only the first two get chroma. That's the **emphasis** form — highlight what matters, gray
the rest — and it sidesteps the red/amber/green confusion entirely, since green never appears
as an urgency step. (The validator showed the app's accent green and muted gray are only ΔE
10.8 apart; they must never encode adjacent bands.)

### Backend

| Path | Purpose |
|---|---|
| `api/app/seeds/recipes.py` | ~80 classic Italian recipes: title, ingredient strings, servings, minutes, steps. `source_site='seed'`, `source_url='seed://…'` so the crawler can replace them by provenance |
| `api/app/seeds/load_recipes.py` | Idempotent loader; resolves each `recipeIngredient` string through **the existing `normalize_text` + `ProductAlias` matcher** (`app/services/normalize.py`) into `recipes.ingredient_product_ids`. Reports its own match rate — an unmatched ingredient is a missing alias, not a silent drop |
| `api/app/services/recipes/graph.py` | `build_graph(db, user_id)` → nodes = products in active inventory, edges = pairs co-occurring in ≥1 recipe, `weight` = shared-recipe count |
| `api/app/services/recipes/rank.py` | Coverage ranking over at-risk items; `Product.is_staple` never counts as missing. Returns used + missing per recipe |
| `api/app/services/recipes/unlock.py` | Graph-derived: the single product not in inventory that would complete the most recipes |
| `api/app/routers/recipes.py` | `GET /graph` · `GET /suggestions` · `GET /unlock` |

All deterministic — **no LLM anywhere in this phase**. Italian phrasing stays Phase 4.

### Frontend

| Path | Purpose |
|---|---|
| `web/src/components/Kitchen.tsx` | Rewritten as a board: `groupByUrgency()` + `<UrgencyColumn>`. Card, actions and waste sheet reused as-is |
| `web/src/components/StatsBar.tsx` | Proper KPI row — value, label, status dot; hero-sized figures, proportional (not tabular) |
| `web/src/components/FridgeGraph.tsx` | Inline SVG, ~60-line deterministic force sim (fixed seed, no new dependency) |
| `web/src/components/Recipes.tsx` | New tab: graph left, ranked list right; selecting a recipe highlights its subgraph |
| `web/src/index.css` | `.app` 560px → `min(1240px, 100%)`; grid at 4 / 2 / 1 columns; new tokens |

**Graph spec** (from the dataviz method):
- **Emphasis, not categorical**: at-risk nodes take the alarm steps, everything else the
  de-emphasis gray. Fewer than 8 hues by construction.
- Node radius ← degree (how many recipes it participates in); edge width ← shared-recipe
  count, 1–3px; 2px surface ring on overlap; hit targets ≥24px.
- Hover/focus tooltip, keyboard-reachable — but **the ranked list beside it is the table
  twin**, so no value is hover-gated.
- Legend always present.

**Responsive**: ≥1100px four columns · ≥700px two · below that the same columns stack as
sections with sticky headers. The phone scanning flow is untouched.

**Found while looking at the rendered pages** (the validator checks colour, not layout):
- The force layout reliably parked the whole cluster in one corner and left half the canvas
  empty. Fixed by fitting the settled result to the viewBox afterwards — far simpler than
  tuning the forces to produce a centred result.
- Unconnected products got flung into the corners by repulsion with nothing pulling them
  back, squeezing everything else. They're now excluded from the canvas and named in the
  note beneath it instead.
- Labelling every node produced an unreadable pile. Now: everything at risk, plus the five
  biggest hubs.
- "Più avanti" held 42 cards of things that need no decision. Calm columns cap at 8 with a
  "mostra altri N"; the two urgent columns are never truncated.

### Verification

```bash
make seed-recipes    # reports ingredient match rate
make test            # existing 139 + new graph/rank/seed tests
make lint
cd web && npm run build
```

Then in the browser: Kitchen splits 73 items across the bands with counts that sum to the
total; narrow the window to 600px and confirm columns stack, nothing overflows horizontally.
On Ricette: a recipe using two expiring items ranks above one using a single item; selecting
it highlights exactly those nodes; `is_staple` products never appear as missing. Toggle OS
dark mode and re-check both alarm colours against the dark card surface.

Tests to add: band boundaries at `d = -1, 0, 3, 4, 7, 8, null`; graph edges appear only for
genuine recipe co-occurrence; ranking prefers higher at-risk coverage; staples excluded from
missing; unlock returns a product **not** already in inventory.

---

## Open items (resolve as you go, none block the start)

1. **Expiry dates come from the shelf-life table, not the package.** OFF does not carry best-before dates. The confirm step has an optional date field (`expiry_source='label'`) for when you can be bothered to read the packet.
2. **Loose produce has no barcode.** Covered by quick-add plus the `2`-prefix store-label binding, but it stays the weakest part of acquisition — and it is the largest waste category. The post-Phase-5 counter-photo VLM is aimed squarely at this.
3. **OFF coverage on Italian products is community-driven and uneven.** The Phase 1 hit-rate measurement is exactly what tells us whether tier 2 pulls its weight; if coverage is poor, tier 4 (bind once, resolve forever) still carries the feature.
4. **Baseline period length.** Two weeks of logging-only before switching on risk surfacing is the minimum for the before/after comparison to say anything at all. Longer is better; decide when you reach Phase 3.
