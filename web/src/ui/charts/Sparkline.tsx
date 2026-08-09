import { Line, LineChart, ResponsiveContainer, YAxis } from 'recharts'

interface Props {
  /** `null` breaks the line; the shape must not imply a value that was never recorded. */
  values: (number | null)[]
  color: string
  /** Natural bounds of the measure, so the shape isn't exaggerated by auto-scaling. */
  domain?: [number, number]
  height?: number
  /** Required: the shape is decorative to a screen reader unless someone says what it is. */
  label: string
}

/**
 * The trend shape inside a stat tile. No axes, no grid, no tooltip.
 *
 * Skipping the hover layer is the one case the interaction rule allows, and only because
 * every point in here is also a point in the "Andamento del consumo" chart below, which has
 * a full crosshair readout *and* a table twin. The sparkline is the shape; the values live
 * downstairs.
 */
export function Sparkline({ values, color, domain, height = 40, label }: Props) {
  const rows = values.map((v, i) => ({ i, v }))

  return (
    <div className="sparkline" role="img" aria-label={label}>
      <ResponsiveContainer width="100%" height={height}>
        <LineChart data={rows} margin={{ top: 3, right: 3, bottom: 3, left: 3 }}>
          <YAxis hide domain={domain ?? ['dataMin', 'dataMax']} />
          <Line
            type="monotone"
            dataKey="v"
            stroke={color}
            strokeWidth={2}
            dot={false}
            connectNulls={false}
            isAnimationActive={false}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  )
}
