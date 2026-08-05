System to reduce household food waste and optimize grocery spending, based on real personal purchase and consumption data.

PROBLEM: households throw away food because they lose track of what they have at home and its expiration dates. This is a measurable economic and environmental problem, not just an inconvenience.

ARCHITECTURE (4 modules):
1. Data acquisition — barcode scanning in the browser (BarcodeDetector, with a zxing-wasm fallback for iOS Safari), resolved against the Open Food Facts API for product name, brand, pack size and category taxonomy. OFF categories are mapped onto a seeded canonical category set by a rules table, with an LLM call for the tail. Unknown barcodes are bound to a product once by hand and resolve instantly forever after. Loose produce has no barcode and is handled by a fast quick-add form.
2. Predictive model — survival analysis (Cox proportional hazards or gradient boosting) to estimate the probability of consumption within N days for each product, based on personal history, category, and seasonality.
3. Recommendation engine — a locally crawled Italian recipe corpus (schema.org/Recipe JSON-LD), retrieved by ingredient coverage over expiring items and phrased by an LLM; savings module that compares personal historical prices and flags anomalies or the convenience of batch purchasing.
4. Dashboard — kg/euros of food saved over time, spending trends by category.

STACK: FastAPI + PostgreSQL/pgvector, Open Food Facts, Groq free tier (Gemini free tier as failover) for LLM calls, sentence-transformers for embeddings, scikit-learn/lifelines, React + Recharts.

CONSTRAINTS: no paid inference — every external service is a free tier or a free API. No model fine-tuning; the original Donut/Qwen2-VL receipt OCR plan was dropped because the hardware isn't there and receipt photography proved too much friction to sustain.

VALIDATION: precision/recall of "at risk of waste" detection verified manually, euros saved measured (not estimated) over a period of real use vs. a period without the system. Prices are entered by hand and optional, so every money figure is reported alongside its price coverage. The before/after comparison is sequential, not a true A/B test, and is reported as such.

DATA: personal consumption data collected directly by the user, logged through the app from the first week so the predictive model has real data from the outset.
