import { Bar, BarChart, CartesianGrid, Rectangle, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'

import { Readout, type ReadoutRow } from './Readout'

export interface StackSeries {
  key: string
  label: string
  color: string
}

export interface StackDatum {
  /** Axis tick text. Kept short — the tooltip carries the long form. */
  x: string
  /** Tooltip title. */
  label: string
  /** An honesty line for this bucket specifically, e.g. how many entries had no price. */
  note?: string
  /** Series key → value. `null` is a gap (unknown), which is not the same as 0. */
  values: Record<string, number | null>
}

interface Props {
  data: StackDatum[]
  series: StackSeries[]
  /** Tooltip and direct-label formatting. */
  format: (value: number) => string
  /** Axis ticks. Shorter than `format` — ticks are read at a glance, not studied. */
  formatTick: (value: number) => string
  height?: number
}

/**
 * Ticks a person would have chosen.
 *
 * Recharts' own scale is left to itself here and produces 0 / 0,8 / 1,5 / 2,3 / 3 on a kg
 * axis — arithmetically correct and unreadable. Ticks carry the values that aren't directly
 * labelled, so they have to land on numbers the eye can interpolate between: steps from the
 * 1 / 2 / 2,5 / 5 family, always starting at zero because these are bar lengths.
 */
function niceTicks(max: number, count = 4): number[] {
  if (!(max > 0)) return [0, 1]
  const magnitude = 10 ** Math.floor(Math.log10(max / count))
  const normalised = max / count / magnitude
  const step =
    (normalised <= 1 ? 1 : normalised <= 2 ? 2 : normalised <= 2.5 ? 2.5 : normalised <= 5 ? 5 : 10) *
    magnitude
  const ticks: number[] = []
  // Float accumulation, so the guard is an epsilon rather than `<= max`: 0.1*3 is 0.30000000000000004.
  for (let v = 0; v < max - step * 1e-9; v += step) ticks.push(Number(v.toPrecision(12)))
  ticks.push(Number((ticks[ticks.length - 1] + step).toPrecision(12)))
  return ticks
}

// The flattened row Recharts actually consumes. `x`/`label`/`note`/`top` are reserved; every
// other key is a category code, which the seeded taxonomy guarantees is SCREAMING_SNAKE, so
// a collision is not possible.
interface Row {
  x: string
  label: string
  note?: string
  /** Which series sits on top of this bucket — the only segment that gets a rounded end. */
  top: string | null
  [seriesKey: string]: string | number | null | undefined
}

interface SegmentProps {
  seriesKey?: string
  payload?: Row
  x?: number
  y?: number
  width?: number
  height?: number
  fill?: string
}

/**
 * One segment of a stack.
 *
 * Exists only to round the *data-end* — the top of the topmost non-empty segment — and
 * leave every interior joint square. Giving `radius` to all of them would round the top of
 * segments that are not an end of anything, which reads as a pile of tiles rather than one
 * measured quantity. The 2px separation between segments is the surface-coloured stroke in
 * index.css, not a border: a contrasting outline would add ink that isn't data.
 */
function StackSegment({ seriesKey, payload, ...rect }: SegmentProps) {
  const isTop = payload?.top === seriesKey
  return <Rectangle {...rect} radius={isTop ? [4, 4, 0, 0] : 0} />
}

/**
 * Waste per bucket, split by category.
 *
 * Part-to-whole over time, which is what a stack is for: the total per bucket is the thing
 * you compare across buckets, and the split is the thing you read inside one. No dual axis
 * exists here or anywhere on this dashboard — euros and kilograms are two charts.
 */
export function StackedBars({ data, series, format, formatTick, height = 240 }: Props) {
  const rows: Row[] = data.map((d) => {
    // Topmost non-empty segment, in stacking order (Recharts stacks in declaration order,
    // so the last declared series with a value is the one that owns the visible top edge).
    let top: string | null = null
    for (const s of series) {
      const v = d.values[s.key]
      if (v !== null && v !== undefined && v > 0) top = s.key
    }
    const row: Row = { x: d.x, label: d.label, note: d.note, top }
    for (const s of series) row[s.key] = d.values[s.key] ?? null
    return row
  })

  const ticks = niceTicks(
    Math.max(0, ...rows.map((r) => series.reduce((sum, s) => sum + (Number(r[s.key]) || 0), 0))),
  )

  return (
    <div className="chart-plot">
      <ResponsiveContainer width="100%" height={height}>
        <BarChart data={rows} margin={{ top: 4, right: 4, bottom: 0, left: 0 }} barCategoryGap="22%">
          <CartesianGrid vertical={false} />
          <XAxis dataKey="x" tickLine={false} interval="preserveStartEnd" minTickGap={12} />
          <YAxis
            tickLine={false}
            axisLine={false}
            width={52}
            tickFormatter={formatTick}
            ticks={ticks}
            domain={[0, ticks[ticks.length - 1]]}
          />
          <Tooltip
            // The mark is the hit target on a bar chart, but a bucket-wide cursor is what
            // makes a 6px-wide segment reachable at all — the reader aims at the week, not
            // at the segment.
            cursor={{ className: 'chart-cursor' }}
            isAnimationActive={false}
            content={(props) => {
              const row = props.payload?.[0]?.payload as Row | undefined
              if (!row) return null
              const readout: ReadoutRow[] = series
                .filter((s) => {
                  const v = row[s.key]
                  return typeof v === 'number' && v > 0
                })
                .reverse()
                .map((s) => ({
                  key: s.key,
                  label: s.label,
                  color: s.color,
                  value: format(row[s.key] as number),
                }))
              return <Readout title={row.label} rows={readout} note={row.note} />
            }}
          />
          {series.map((s) => (
            <Bar
              key={s.key}
              dataKey={s.key}
              stackId="waste"
              fill={s.color}
              name={s.label}
              maxBarSize={24}
              isAnimationActive={false}
              shape={<StackSegment seriesKey={s.key} />}
            />
          ))}
        </BarChart>
      </ResponsiveContainer>
    </div>
  )
}
