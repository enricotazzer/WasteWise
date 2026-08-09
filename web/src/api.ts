/** Typed API client. Mirrors the Pydantic schemas in api/app/schemas/. */

// The one shape that is already declared elsewhere. `ui/moneyFigure` owns `MoneyFigure`
// because the formatters that read it live beside it, and a euro figure is never rendered
// without them; duplicating the interface here would let the two drift. Type-only, so
// nothing from the UI layer ends up in the client's bundle.
import type { MassFigure } from './ui/massFigure'
import type { MoneyFigure } from './ui/moneyFigure'

export type { MassFigure, MoneyFigure }

const TOKEN_KEY = 'wastewise_token'

export const auth = {
  get: () => localStorage.getItem(TOKEN_KEY),
  set: (t: string) => localStorage.setItem(TOKEN_KEY, t),
  clear: () => localStorage.removeItem(TOKEN_KEY),
}

export class ApiError extends Error {
  // Explicit field + assignment rather than a TS parameter property: the Vite template
  // enables `erasableSyntaxOnly`, which rejects parameter properties.
  status: number

  constructor(status: number, message: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const token = auth.get()
  const headers = new Headers(init.headers)
  if (token) headers.set('Authorization', `Bearer ${token}`)
  if (init.body && !headers.has('Content-Type')) headers.set('Content-Type', 'application/json')

  const res = await fetch(path, { ...init, headers })

  if (res.status === 401) {
    // Expired or revoked token: drop it so the app falls back to the login screen
    // instead of looping on failed requests.
    auth.clear()
    window.dispatchEvent(new Event('wastewise:unauthorized'))
    throw new ApiError(401, 'Sessione scaduta')
  }
  if (!res.ok) {
    let detail = res.statusText
    try {
      const body = await res.json()
      if (typeof body.detail === 'string') detail = body.detail
    } catch {
      /* non-JSON error body — keep statusText */
    }
    throw new ApiError(res.status, detail)
  }
  if (res.status === 204) return undefined as T
  return res.json() as Promise<T>
}

// --- Types ---

export type Storage = 'pantry' | 'fridge' | 'freezer' | 'nonfood'
export type Unit = 'kg' | 'g' | 'l' | 'ml' | 'pz'
export type ItemStatus = 'active' | 'consumed' | 'wasted' | 'partial'
export type EventType = 'consumed' | 'wasted' | 'partial'
/** How a barcode resolved. `manual` is the only one that cost the user any typing. */
export type MatchMethod = 'cache' | 'off' | 'llm' | 'manual' | 'unresolved'

export interface Category {
  id: number
  code: string
  name_it: string
  name_en: string
  storage: Storage
  default_shelf_life_days: number | null
  is_food: boolean
  is_perishable: boolean
  sort_order: number
}

export interface Product {
  id: number
  name_it: string
  name_en: string | null
  category_id: number
  default_unit: Unit
  shelf_life_days: number | null
  shelf_life_days_opened: number | null
  is_perishable: boolean
  is_staple: boolean
}

export interface ScanResult {
  barcode: string
  match_method: MatchMethod
  product_id: number | null
  product_name: string | null
  category_code: string | null
  category_name: string | null
  suggested_quantity: number | null
  suggested_unit: Unit | null
  off_name: string | null
  off_brand: string | null
  off_quantity: string | null
  from_cache: boolean
  lookup_count: number
  /** True when the UI must show the confirm sheet before anything is added. */
  needs_confirmation: boolean
}

export interface ScanStats {
  total_barcodes: number
  by_method: Partial<Record<MatchMethod, number>>
  resolved: number
  unresolved: number
  total_scans: number
  zero_typing_rate: number | null
}

export interface GraphNode {
  product_id: number
  name: string
  category_code: string
  quantity: number
  unit: string
  days_to_expiry: number | null
  is_staple: boolean
  at_risk: boolean
  /** How many other in-house products share a recipe with this one. Drives node radius. */
  degree: number
  recipe_count: number
}

export interface GraphEdge {
  source: number
  target: number
  /** Shared-recipe count. Drives stroke width only. */
  weight: number
  recipe_ids: number[]
}

export interface FridgeGraph {
  nodes: GraphNode[]
  edges: GraphEdge[]
  /** In-house products no recipe mentions — usually a missing alias, so it's surfaced. */
  isolated: string[]
}

export interface RecipeMatch {
  recipe_id: number
  title: string
  servings: number | null
  total_minutes: number | null
  source_url: string
  instructions: string | null
  uses: string[]
  use_ids: number[]
  rescues: string[]
  missing: string[]
  score: number
  cookable: boolean
}

export interface Unlock {
  product_id: number
  name: string
  category_code: string
  unlocks: number
  rescues: string[]
  recipe_titles: string[]
}

export interface RecentProduct {
  product_id: number
  product_name: string
  category_code: string
  default_unit: Unit
  times_bought: number
  last_bought: string
}

export interface InventoryItem {
  id: number
  product_id: number
  product_name: string
  category_code: string
  barcode: string | null
  quantity: string
  unit: Unit
  unit_price_eur: string | null
  acquired_at: string
  expiry_date: string | null
  expiry_source: 'label' | 'product' | 'category' | null
  opened_at: string | null
  status: ItemStatus
  fraction_remaining: number
  days_to_expiry: number | null
  note: string | null
}

export interface Stats {
  active_items: number
  expiring_within_3d: number
  expired_still_active: number
  resolved_total: number
  consumed_total: number
  wasted_total: number
  eur_wasted: string
  eur_consumed: string
  save_rate: number | null
  /**
   * Fraction of resolved food items that had a price. The euro figures above are computed
   * over this share only — prices are optional, so showing a total without it would imply
   * a completeness the data doesn't have.
   */
  price_coverage: number | null
  /**
   * Whether the counters above include the seeded demo ledger. Echoed by the server rather
   * than assumed by the caller: a number computed over synthetic outcomes is not a
   * measurement, and every screen that renders one has to be able to say so.
   */
  demo_included: boolean
  /**
   * Demo rows present in the ledger, reported whether or not they were counted. This is
   * what lets the app decide, from the response alone, that there is nothing but demo data
   * to show — see `lib/demoMode.ts`. `/api/inventory/stats` does not send it; the field is
   * optional here and the dashboard's overview is the endpoint that does.
   */
  demo_entries?: number
}

// --- Savings ledger / reporting -----------------------------------------------------
// Mirrors api/app/schemas/stats.py. Every euro total in here is a MoneyFigure and every
// mass a MassFigure, because the two coverages are genuinely different numbers: only kg/g
// items have a mass at all, so 90% price coverage and 20% mass coverage over the identical
// rows is normal, not a bug.

export type SavingsDomainId = 'food' | 'utilities' | 'subscriptions'

export interface DomainTotal {
  domain: SavingsDomainId
  spend: MoneyFigure
  used: MoneyFigure
  waste: MoneyFigure
  /** Always empty for food, by backend design — the food domain writes no counterfactuals. */
  avoided: MoneyFigure
  /** SPEND minus its partitions: value bought and never resolved to an outcome. */
  unaccounted: MoneyFigure
  waste_kg: MassFigure
}

export interface StatsOverview {
  generated_at: string
  demo_included: boolean
  /** Demo rows in the ledger, reported whether or not they were included in the figures. */
  demo_entries: number

  active_items: number
  expiring_within_3d: number
  expired_still_active: number
  resolved_total: number
  consumed_total: number
  wasted_total: number

  /** The headline. Count-based over recorded outcomes, so it carries no coverage caveat. */
  save_rate: number | null
  /** The euro-weighted variant — a different question, over priced entries only. */
  save_rate_eur: number | null
  save_rate_eur_coverage: number | null

  wasted: MoneyFigure
  used: MoneyFigure
  spend: MoneyFigure
  wasted_kg: MassFigure

  /** Empty when nothing is in scope — not a domain row full of zeros. */
  domains: DomainTotal[]
}

export type Bucket = 'week' | 'month'

export interface TimeseriesPoint {
  bucket_start: string
  waste_eur: string | null
  used_eur: string | null
  spend_eur: string | null
  waste_kg: string | null
  waste_entries: number
  used_entries: number
  spend_entries: number
  /** Of `waste_entries`, how many carried a price. The gap is what makes `waste_eur` partial. */
  waste_priced_entries: number
}

/** Sparse: a (bucket, category) pair only appears when something was wasted in it. */
export interface CategoryPoint {
  bucket_start: string
  category_code: string
  waste_eur: string | null
  waste_kg: string | null
  waste_entries: number
}

export interface TimeseriesResponse {
  bucket: Bucket
  start: string
  end: string
  domain: SavingsDomainId | null
  demo_included: boolean
  demo_entries: number
  /** Contiguous and zero-filled, so a quiet week reads as zero rather than as a gap. */
  points: TimeseriesPoint[]
  by_category: CategoryPoint[]
  price_coverage: number | null
  mass_coverage: number | null
}

export interface CategoryBucket {
  category_code: string
  bucket_start: string
  spend: MoneyFigure
  waste: MoneyFigure
  waste_share: number | null
  /** `null` — never 0 — when there is no prior bucket. "Unknown", not "unchanged". */
  spend_delta_pct: number | null
  waste_delta_pct: number | null
}

/** How a risk number was produced. Travels with every response that depends on a model. */
export interface RiskProvenance {
  model_version: string
  model_stage: string
  trained_on_demo: boolean
  /** True when someone used the `--force` escape hatch to fit below the data gate. */
  fitted_below_gate: boolean
  demo_included: boolean
  demo_items: number
  /** Ready-to-render banner copy. Rendered verbatim rather than reworded on the client. */
  warning: string | null
}

/** Whether there is enough real history to fit v1 at all. */
export interface RiskGate {
  resolved: number
  waste_events: number
  min_resolved: number
  min_waste_events: number
  met: boolean
  resolved_short: number
  waste_short: number
  summary: string
}

export interface RiskModelMetrics {
  name: string
  concordance: number | null
  threshold: number
  true_positives: number
  false_positives: number
  false_negatives: number
  true_negatives: number
  precision: number | null
  recall: number | null
  f1: number | null
  flagged: number
  positives: number
  /** Items the precision/recall figures were computed over. Small — show it. */
  evaluated: number
  /** Items the concordance was ranked over; larger, because it uses censored rows too. */
  ranked: number
}

export interface RiskEvaluation {
  generated_at: string
  trained_on_demo: boolean
  provenance: RiskProvenance
  gate: RiskGate
  fitted: boolean
  threshold: number
  split_on: string | null
  train_rows: number
  test_rows: number
  total_rows: number
  demo_rows: number
  models: RiskModelMetrics[]
  best_model: string | null
  /** null when there is no v1 to compare against. */
  v1_beats_v0: boolean | null
  design_columns: string[]
  dropped_columns: string[]
  coefficients: Record<string, number>
  notes: string[]
}

export interface ByCategoryResponse {
  bucket: Bucket
  start: string
  end: string
  domain: SavingsDomainId | null
  demo_included: boolean
  demo_entries: number
  rows: CategoryBucket[]
}

export interface WasteReason {
  code: string
  label: string
}

// --- Endpoints ---

export const api = {
  async login(email: string, password: string): Promise<string> {
    const body = new URLSearchParams({ username: email, password })
    const res = await fetch('/api/auth/token', { method: 'POST', body })
    if (!res.ok) throw new ApiError(res.status, 'Email o password non validi')
    const { access_token } = (await res.json()) as { access_token: string }
    return access_token
  },

  me: () => request<{ id: number; email: string; display_name: string | null }>('/api/auth/me'),

  categories: () => request<Category[]>('/api/catalog/categories'),

  products: (q?: string) =>
    request<Product[]>(`/api/catalog/products${q ? `?q=${encodeURIComponent(q)}` : ''}`),

  createProduct: (payload: {
    name_it: string
    category_id: number
    default_unit?: Unit
    shelf_life_days?: number | null
  }) => request<Product>('/api/catalog/products', { method: 'POST', body: JSON.stringify(payload) }),

  // --- barcode ---

  scan: (barcode: string) => request<ScanResult>(`/api/scan/${encodeURIComponent(barcode)}`),

  bindBarcode: (
    barcode: string,
    payload: {
      product_id?: number
      new_product_name?: string
      category_id?: number
      shelf_life_days?: number | null
      default_unit?: Unit
    },
  ) =>
    request<ScanResult>(`/api/scan/${encodeURIComponent(barcode)}/bind`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  addFromScan: (payload: {
    barcode?: string | null
    product_id: number
    quantity: number
    unit?: Unit
    unit_price_eur?: number | null
    expiry_date?: string | null
    store?: string | null
  }) => request<InventoryItem>('/api/scan/add', { method: 'POST', body: JSON.stringify(payload) }),

  scanStats: () => request<ScanStats>('/api/scan/stats'),

  // --- recipes / graph ---

  fridgeGraph: (includeStaples = false) =>
    request<FridgeGraph>(`/api/recipes/graph?include_staples=${includeStaples}`),

  suggestions: () => request<RecipeMatch[]>('/api/recipes/suggestions?limit=12'),

  unlock: () => request<Unlock[]>('/api/recipes/unlock'),

  recentProducts: () => request<RecentProduct[]>('/api/inventory/recent-products'),

  wasteReasons: () => request<WasteReason[]>('/api/catalog/waste-reasons'),

  items: (activeOnly = true) =>
    request<InventoryItem[]>(`/api/inventory/items?active_only=${activeOnly}`),

  createItem: (payload: {
    product_id: number
    quantity: number
    unit?: Unit
    unit_price_eur?: number | null
    acquired_at?: string
    expiry_date?: string | null
    note?: string | null
  }) => request<InventoryItem>('/api/inventory/items', { method: 'POST', body: JSON.stringify(payload) }),

  logEvent: (
    itemId: number,
    payload: { event_type: EventType; fraction_used?: number; reason?: string | null },
  ) =>
    request<unknown>(`/api/inventory/items/${itemId}/events`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  openItem: (itemId: number) =>
    request<InventoryItem>(`/api/inventory/items/${itemId}/open`, {
      method: 'POST',
      body: JSON.stringify({}),
    }),

  deleteItem: (itemId: number) =>
    request<void>(`/api/inventory/items/${itemId}`, { method: 'DELETE' }),

  stats: (includeDemo = false) =>
    request<Stats>(`/api/inventory/stats?include_demo=${includeDemo}`),

  // --- savings ledger ---
  // Every one of these takes `include_demo`. It is a required argument rather than a
  // defaulted one: `false` is the server's default and it silently returns an empty
  // ledger, which is how the KPI row came to read "0 in casa" above a board of 69 items.
  // Making the caller state its intent means that mismatch cannot happen twice.

  statsOverview: (includeDemo: boolean) =>
    request<StatsOverview>(`/api/stats/overview?include_demo=${includeDemo}`),

  statsTimeseries: (params: { bucket: Bucket; periods: number; includeDemo: boolean }) =>
    request<TimeseriesResponse>(
      // `weeks` counts buckets, not weeks — the backend kept the parameter name after the
      // month bucket was added.
      `/api/stats/timeseries?bucket=${params.bucket}&weeks=${params.periods}&include_demo=${params.includeDemo}`,
    ),

  statsByCategory: (params: { bucket: Bucket; periods: number; includeDemo: boolean }) =>
    request<ByCategoryResponse>(
      `/api/stats/by-category?bucket=${params.bucket}&months=${params.periods}&include_demo=${params.includeDemo}`,
    ),

  // --- risk ---
  riskEvaluation: (includeDemo: boolean) =>
    request<RiskEvaluation>(`/api/risk/evaluation?include_demo=${includeDemo}`),
}
