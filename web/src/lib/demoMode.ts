import { useQuery } from '@tanstack/react-query'
import { useSyncExternalStore } from 'react'

import { api } from '../api'

/* Whether the app is looking at the seeded demo ledger, and whether it says so.
 *
 * THE BUG THIS EXISTS TO FIX
 * `/api/inventory/stats` and every `/api/stats/*` endpoint default to `include_demo=false`.
 * The database currently holds nothing but demo rows, so the KPI row read "0 in casa" above
 * a Kitchen board listing 69 items. Two numbers from the same database disagreeing on the
 * same screen is worse than either being wrong.
 *
 * THE FIX, AND WHY IT ISN'T A CONSTANT
 * The decision is taken from the response, never from a frontend flag: every stats endpoint
 * reports `demo_entries` whether or not it counted them, so one probe answers "is there
 * anything but demo data here". If there is demo data and the user hasn't said otherwise,
 * the app includes it — and the moment real logging starts, `demo_entries` stops being the
 * only thing in the ledger and the user can turn it off for good with the dashboard toggle.
 * Nothing needs redeploying for that transition.
 *
 * WHY THE PREFERENCE IS NOT REACT STATE
 * The toggle lives on the dashboard but its effect is app-wide — the Kitchen KPI row has to
 * agree with the dashboard hero, or we are back to the original bug with extra steps. A
 * module-level store read through `useSyncExternalStore` gives every screen the same value
 * without threading a provider through a shell that deliberately knows nothing about food.
 */

export type DemoPreference = 'auto' | 'on' | 'off'

const KEY = 'wastewise_demo'
const listeners = new Set<() => void>()

function read(): DemoPreference {
  const raw = localStorage.getItem(KEY)
  return raw === 'on' || raw === 'off' ? raw : 'auto'
}

let preference: DemoPreference = read()

export function setDemoPreference(next: DemoPreference) {
  preference = next
  if (next === 'auto') localStorage.removeItem(KEY)
  else localStorage.setItem(KEY, next)
  for (const fn of listeners) fn()
}

function subscribe(fn: () => void): () => void {
  listeners.add(fn)
  return () => listeners.delete(fn)
}

export interface DemoState {
  /** What to pass as `include_demo`. Undefined until the probe answers — see `ready`. */
  includeDemo: boolean
  /** Demo rows in the ledger, regardless of whether they are being counted. */
  demoEntries: number
  /** False while the probe is in flight. Dependent queries must wait on it. */
  ready: boolean
  preference: DemoPreference
}

/**
 * One probe per session, shared by every caller through the query cache.
 *
 * It asks for the overview with demo *excluded* on purpose: that response is simultaneously
 * the "real data only" figure and the carrier of `demo_entries`, so the question costs one
 * request and the answer is never a guess about the server's default.
 */
export function useDemoMode(): DemoState {
  const pref = useSyncExternalStore(subscribe, () => preference, () => preference)

  const { data } = useQuery({
    queryKey: ['stats', 'overview', false],
    queryFn: () => api.statsOverview(false),
    // The ledger doesn't grow demo rows while the app is open.
    staleTime: 5 * 60_000,
  })

  const demoEntries = data?.demo_entries ?? 0

  return {
    includeDemo: pref === 'on' ? true : pref === 'off' ? false : demoEntries > 0,
    demoEntries,
    ready: data !== undefined,
    preference: pref,
  }
}
