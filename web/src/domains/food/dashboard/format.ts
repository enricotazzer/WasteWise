import type { Bucket, TimeseriesPoint } from '../../../api'

/** Presentation helpers shared by the dashboard's four views: number and date formatting,
 *  and the one windowing rule every time-based chart here applies. Italian throughout, like
 *  every other string in this UI. */

/**
 * Drop the leading run of buckets in which nothing at all happened.
 *
 * The endpoint zero-fills on purpose — a missing bucket makes a line interpolate straight
 * across a gap, which reads as "nothing was wasted" when it means "nothing was logged". But
 * asking for 12 months when logging started three months ago squeezes the real data into
 * the last quarter of the plot to prove the app wasn't running yet, and every chart caption
 * already states the window it actually drew.
 *
 * Only the *leading* run goes. A quiet week in the middle is a real zero and stays.
 */
export function trimLeadingEmpty(points: TimeseriesPoint[]): TimeseriesPoint[] {
  const first = points.findIndex(
    (p) => p.spend_entries > 0 || p.used_entries > 0 || p.waste_entries > 0,
  )
  return first < 0 ? [] : points.slice(first)
}

const EUR_TICK = new Intl.NumberFormat('it-IT', { maximumFractionDigits: 0 })
const KG_TICK = new Intl.NumberFormat('it-IT', { maximumFractionDigits: 1 })

/** Axis ticks are read at a glance, so they drop the cents the tooltip keeps. */
export function eurTick(value: number): string {
  return `${EUR_TICK.format(value)} €`
}

export function kgTick(value: number): string {
  return KG_TICK.format(value)
}

export function pct(value: number): string {
  return `${Math.round(value * 100)}%`
}

/** Signed, and never rendered for a `null` delta — see `SpendByCategory`. */
export function signedPct(value: number): string {
  const rounded = Math.round(value * 100)
  return `${rounded > 0 ? '+' : ''}${rounded}%`
}

// ISO date → local calendar date. `new Date('2026-05-18')` is parsed as UTC midnight, which
// west of Greenwich renders as the 17th; the buckets are calendar dates, not instants.
function parseIso(iso: string): Date {
  const [y, m, d] = iso.split('-').map(Number)
  return new Date(y, m - 1, d)
}

const DAY_MONTH = new Intl.DateTimeFormat('it-IT', { day: 'numeric', month: 'short' })
const MONTH_SHORT = new Intl.DateTimeFormat('it-IT', { month: 'short', year: '2-digit' })
const MONTH_LONG = new Intl.DateTimeFormat('it-IT', { month: 'long', year: 'numeric' })
const DAY_MONTH_YEAR = new Intl.DateTimeFormat('it-IT', {
  day: 'numeric',
  month: 'short',
  year: 'numeric',
})

/** The short form on an axis tick. */
export function bucketTick(iso: string, bucket: Bucket): string {
  const d = parseIso(iso)
  return bucket === 'month' ? MONTH_SHORT.format(d) : DAY_MONTH.format(d)
}

/** The long form in a tooltip title, where there is room to say which year it was. */
export function bucketLabel(iso: string, bucket: Bucket): string {
  const d = parseIso(iso)
  return bucket === 'month'
    ? MONTH_LONG.format(d)
    : `settimana del ${DAY_MONTH_YEAR.format(d)}`
}

export function dayLabel(iso: string): string {
  return DAY_MONTH_YEAR.format(parseIso(iso))
}

/**
 * Whether a bucket is still running.
 *
 * Matters because a month-to-date bucket compared against a full previous month produces a
 * −60% that means "it is the 6th" and reads as "spending collapsed". Anywhere that number
 * would be shown, it is suppressed instead.
 */
export function bucketInProgress(iso: string, bucket: Bucket, today = new Date()): boolean {
  const start = parseIso(iso)
  const end = new Date(start)
  if (bucket === 'month') end.setMonth(end.getMonth() + 1)
  else end.setDate(end.getDate() + 7)
  return today < end
}
