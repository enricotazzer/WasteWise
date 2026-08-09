import type { SegmentedOption } from './Segmented'

/**
 * Presets only, no free date pickers.
 *
 * Every question this app answers is "how am I doing lately" — a period compared against
 * the one before it. Arbitrary start/end dates would be a picker nobody opens, and the
 * before/after waste comparison in the brief is sequential periods, not custom windows.
 */
export type RangeKey = '7d' | '30d' | '90d' | '12m' | 'all'

export interface ResolvedRange {
  key: RangeKey
  /** ISO date (YYYY-MM-DD), inclusive. `null` for `all` — no lower bound. */
  from: string | null
  /** ISO date (YYYY-MM-DD), inclusive. */
  to: string
  /** Length in days, `null` for `all`. A comparison period is the same length, ending at `from`. */
  days: number | null
}

const RANGE_DAYS: Record<RangeKey, number | null> = {
  '7d': 7,
  '30d': 30,
  '90d': 90,
  '12m': 365,
  all: null,
}

export const RANGE_OPTIONS: SegmentedOption<RangeKey>[] = [
  { value: '7d', label: '7g', title: '7 giorni' },
  { value: '30d', label: '30g', title: '30 giorni' },
  { value: '90d', label: '90g', title: '90 giorni' },
  { value: '12m', label: '12m', title: '12 mesi' },
  { value: 'all', label: 'tutto', title: 'Da sempre' },
]

function isoDate(d: Date): string {
  // Local calendar date, not toISOString(): at 01:00 in Rome the UTC date is still
  // yesterday, and "oggi" in a food app has to mean the day the user is standing in.
  return [
    d.getFullYear(),
    String(d.getMonth() + 1).padStart(2, '0'),
    String(d.getDate()).padStart(2, '0'),
  ].join('-')
}

/** `today` is injectable so a test doesn't depend on the wall clock. */
export function resolveRange(key: RangeKey, today = new Date()): ResolvedRange {
  const days = RANGE_DAYS[key]
  if (days === null) return { key, from: null, to: isoDate(today), days: null }
  const from = new Date(today)
  from.setDate(from.getDate() - (days - 1))
  return { key, from: isoDate(from), to: isoDate(today), days }
}
