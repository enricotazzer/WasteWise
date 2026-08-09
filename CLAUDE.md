System to reduce household food waste and optimize grocery spending, based on real personal purchase and consumption data.

PROBLEM: households throw away food because they lose track of what they have at home and its expiration dates. This is a measurable economic and environmental problem, not just an inconvenience.

ARCHITECTURE (4 modules):
1. Data acquisition — barcode scanning in the browser (BarcodeDetector, with a zxing-wasm fallback for iOS Safari), resolved against the Open Food Facts API for product name, brand, pack size and category taxonomy. OFF categories are mapped onto a seeded canonical category set by a rules table, with an LLM call for the tail. Unknown barcodes are bound to a product once by hand and resolve instantly forever after. Loose produce has no barcode and is handled by a fast quick-add form.
2. Predictive model — survival analysis (Cox proportional hazards or gradient boosting) to estimate the probability of consumption within N days for each product, based on personal history, category, and seasonality.
3. Recommendation engine — a locally crawled Italian recipe corpus (schema.org/Recipe JSON-LD), retrieved by ingredient coverage over expiring items and phrased by an LLM; savings module that compares personal historical prices and flags anomalies or the convenience of batch purchasing.
4. Dashboard — kg/euros of food saved over time, spending trends by category.

SCOPE: WasteWise is the first module of a wider personal-savings system that will also cover utility bills and other saving concerns. Two things are built for that from the start and would be expensive to retrofit: a domain-agnostic UI shell + design system (`web/src/ui`, `web/src/shell`, with a route registry a domain contributes to), and a cross-domain `savings_entries` ledger keyed by `domain`. Food is the only writer today; adding bills is an enum value and a writer, not a migration.

STACK: FastAPI + PostgreSQL/pgvector, Open Food Facts, Groq free tier (Gemini free tier as failover) for LLM calls, fastembed for embeddings, scikit-learn/lifelines, React + Recharts.

CONSTRAINTS: no paid inference — every external service is a free tier or a free API. No model fine-tuning; the original Donut/Qwen2-VL receipt OCR plan was dropped because the hardware isn't there and receipt photography proved too much friction to sustain. Embeddings run on fastembed/onnxruntime rather than sentence-transformers: the same all-MiniLM-L6-v2 vectors at 384 dims, without pulling torch (~2 GB) into the image to do it.

CRAWLING: the recipe corpus is personal and never redistributed. Obey robots.txt, one request per 2s per host, identifying User-Agent, store and display `source_url`. Sites whose robots.txt blocks AI crawlers are skipped even when the bot's own name is not on the list — the corpus is handed to an LLM to phrase, which is the use those lists are drawn around. The audit of what was crawled and what was rejected lives in `api/app/services/recipes/sites.py`.

VALIDATION: precision/recall of "at risk of waste" detection verified manually, euros saved measured (not estimated) over a period of real use vs. a period without the system. Prices are entered by hand and optional, so every money figure is reported alongside its price coverage. The before/after comparison is sequential, not a true A/B test, and is reported as such.

Every reported number is regenerated from the database by `app/eval/report.py` — the single source behind `make eval` and `notebooks/eval.ipynb`. A figure that exists only in prose is one nobody can re-derive. Where a number cannot be computed yet it is reported as unavailable with a reason, never as zero: "no barcode has been scanned" and "the zero-typing rate is 0%" are different claims.

The v1 survival model refuses to fit below ≥150 resolved items and ≥40 waste events, and reports the shortfall instead of producing a caveated number. If v1 does not beat the v0 heuristic, that is reported and enforced — the scorer will not load a model that lost.

DATA: personal consumption data collected directly by the user, logged through the app from the first week so the predictive model has real data from the outset.

`make demo` generates a synthetic 8-week household so the screens can be judged before real logging exists. Every such row carries `is_demo=True` and is excluded from every reported figure by default, in the API, the eval report and the UI — which labels anything demo-derived rather than showing it plain. Synthetic outcomes must never reach model training or a published number: a precision figure computed from a generator measures the generator.
