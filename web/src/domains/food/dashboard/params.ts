import { useCallback, useEffect } from 'react'
import { useSearchParams } from 'react-router-dom'

import type { Bucket } from '../../../api'
import { setDemoPreference, useDemoMode, type DemoPreference } from '../../../lib/demoMode'
import type { SegmentedOption } from '../../../ui'

/* Dashboard filter state, in the URL.
 *
 * `AppRoute.path` promises the dashboard is deep-linkable, and this is where that promise
 * is kept: every control writes a query param, so a view worth sending to someone is a link
 * worth sending to someone. `useState` would have been fewer lines and would have made the
 * back button do nothing.
 */

export type PeriodKey = '3m' | '6m' | '12m' | 'all'

/**
 * Why not `ui/ranges.ts`.
 *
 * `RANGE_OPTIONS` is expressed in days, and the stats endpoints take a *bucket count*. At
 * weekly granularity "7 giorni" is one bar, which is not a trend, and rounding it up to a
 * usable four weeks would put a label on the control that contradicts what the chart draws.
 * These four presets convert exactly in both bucket modes, so the control never lies about
 * the window. `Segmented` — the control `DateRange` itself wraps — is reused unchanged.
 */
export const PERIOD_OPTIONS: SegmentedOption<PeriodKey>[] = [
  { value: '3m', label: '3m', title: '3 mesi' },
  { value: '6m', label: '6m', title: '6 mesi' },
  { value: '12m', label: '12m', title: '12 mesi' },
  { value: 'all', label: 'tutto', title: 'Da sempre' },
]

export const BUCKET_OPTIONS: SegmentedOption<Bucket>[] = [
  { value: 'week', label: 'settimane', title: 'Raggruppa per settimana' },
  { value: 'month', label: 'mesi', title: 'Raggruppa per mese' },
]

// Bucket counts per preset. `all` takes the endpoint's ceiling rather than an invented
// number — the API caps at 104 buckets, so "tutto" means "everything the API will give".
const PERIODS: Record<Bucket, Record<PeriodKey, number>> = {
  week: { '3m': 13, '6m': 26, '12m': 52, all: 104 },
  month: { '3m': 3, '6m': 6, '12m': 12, all: 36 },
}

export interface DashboardParams {
  period: PeriodKey
  bucket: Bucket
  /** Bucket count for the current (period, bucket) pair — what the endpoints actually take. */
  periods: number
  includeDemo: boolean
  demoEntries: number
  ready: boolean
  setPeriod: (value: PeriodKey) => void
  setBucket: (value: Bucket) => void
  setDemo: (value: DemoPreference) => void
}

function parse<T extends string>(raw: string | null, allowed: readonly T[], fallback: T): T {
  return allowed.includes(raw as T) ? (raw as T) : fallback
}

export function useDashboardParams(): DashboardParams {
  const [search, setSearch] = useSearchParams()
  const demo = useDemoMode()

  const period = parse(search.get('period'), ['3m', '6m', '12m', 'all'] as const, '6m')
  const bucket = parse(search.get('bucket'), ['week', 'month'] as const, 'week')
  const urlDemo = search.get('demo')

  // A pasted link carrying ?demo=off has to win over whatever this browser last chose, and
  // it has to win *everywhere* — the top bar badge and the Kitchen KPI row read the same
  // store, and a dashboard that disagreed with them would be the original bug again.
  useEffect(() => {
    if ((urlDemo === 'on' || urlDemo === 'off') && urlDemo !== demo.preference) {
      setDemoPreference(urlDemo)
    }
  }, [urlDemo, demo.preference])

  const patch = useCallback(
    (key: string, value: string) => {
      setSearch(
        (prev) => {
          const next = new URLSearchParams(prev)
          next.set(key, value)
          return next
        },
        { replace: true },
      )
    },
    [setSearch],
  )

  return {
    period,
    bucket,
    periods: PERIODS[bucket][period],
    includeDemo: demo.includeDemo,
    demoEntries: demo.demoEntries,
    ready: demo.ready,
    setPeriod: (value) => patch('period', value),
    setBucket: (value) => patch('bucket', value),
    setDemo: (value) => {
      setDemoPreference(value)
      patch('demo', value)
    },
  }
}
