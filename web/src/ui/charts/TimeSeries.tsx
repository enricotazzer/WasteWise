import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'

import { Readout, type ReadoutRow } from './Readout'

export interface LineSeries {
  key: string
  label: string
  color: string
}

export interface TimeDatum {
  x: string
  label: string
  note?: string
  /** `null` is a genuine gap — the line breaks rather than interpolating across it. */
  values: Record<string, number | null>
}

interface Props {
  data: TimeDatum[]
  series: LineSeries[]
  format: (value: number) => string
  formatTick: (value: number) => string
  /** Fixes the y scale when the measure has natural bounds, e.g. a rate in [0,1]. */
  domain?: [number, number]
  height?: number
}

interface Row {
  x: string
  label: string
  note?: string
  [seriesKey: string]: string | number | null | undefined
}

interface DotProps {
  cx?: number
  cy?: number
  index?: number
  lastIndex?: number
  /**
   * The series colour, passed explicitly. Recharts hands a cloned dot the *line's* `fill`,
   * which defaults to white — taking it from there paints an invisible marker on a light
   * surface, which is exactly what happened the first time.
   */
  color?: string
}

/**
 * The end-dot, and only the end-dot.
 *
 * A marker on all 26 points turns a line into a dotted mess and re-states what the line
 * already says; one at the current value anchors the eye where the reader looks first. The
 * 2px surface ring is what keeps it legible where two lines cross.
 */
function EndDot({ cx, cy, index, lastIndex, color }: DotProps) {
  if (index !== lastIndex || cx === undefined || cy === undefined) return null
  return <circle className="chart-enddot" cx={cx} cy={cy} r={4} fill={color} />
}

/**
 * Two rates over time, on one axis.
 *
 * One axis is not a stylistic choice: a second y-scale would let the two save rates be
 * aligned at any offset the renderer felt like, inventing agreement or disagreement that
 * isn't in the data. Both series here are shares in [0,1], so they genuinely share a scale —
 * which is exactly the condition that makes plotting them together legitimate.
 */
export function TimeSeries({ data, series, format, formatTick, domain, height = 240 }: Props) {
  const rows: Row[] = data.map((d) => {
    const row: Row = { x: d.x, label: d.label, note: d.note }
    for (const s of series) row[s.key] = d.values[s.key] ?? null
    return row
  })
  const lastIndex = rows.length - 1

  return (
    <div className="chart-plot">
      <ResponsiveContainer width="100%" height={height}>
        <LineChart data={rows} margin={{ top: 8, right: 10, bottom: 0, left: 0 }}>
          <CartesianGrid vertical={false} />
          <XAxis dataKey="x" tickLine={false} interval="preserveStartEnd" minTickGap={12} />
          <YAxis
            tickLine={false}
            axisLine={false}
            width={48}
            domain={domain}
            tickFormatter={formatTick}
          />
          <Tooltip
            // The crosshair finds the X: readers aim at a week, never at a 2px line.
            cursor={{ className: 'chart-crosshair' }}
            isAnimationActive={false}
            content={(props) => {
              const row = props.payload?.[0]?.payload as Row | undefined
              if (!row) return null
              const readout: ReadoutRow[] = series.map((s) => ({
                key: s.key,
                label: s.label,
                color: s.color,
                // One tooltip lists every series at that X, including the ones with no
                // value — "—" is information, and hiding the row would read as agreement.
                value: typeof row[s.key] === 'number' ? format(row[s.key] as number) : '—',
              }))
              return <Readout title={row.label} rows={readout} note={row.note} />
            }}
          />
          {series.map((s) => (
            <Line
              key={s.key}
              type="monotone"
              dataKey={s.key}
              name={s.label}
              stroke={s.color}
              strokeWidth={2}
              // A missing bucket is unknown, not zero: the line breaks there.
              connectNulls={false}
              isAnimationActive={false}
              dot={<EndDot lastIndex={lastIndex} color={s.color} />}
              activeDot={{ r: 4, fill: s.color, className: 'chart-activedot' }}
            />
          ))}
        </LineChart>
      </ResponsiveContainer>
    </div>
  )
}
