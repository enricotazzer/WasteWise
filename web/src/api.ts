/** Typed API client. Mirrors the Pydantic schemas in api/app/schemas/. */

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

  stats: () => request<Stats>('/api/inventory/stats'),
}
