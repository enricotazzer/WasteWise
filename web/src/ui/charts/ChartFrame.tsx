import { useId, useState } from 'react'
import type { ReactNode } from 'react'

import { Button } from '../Button'
import { Card } from '../Card'

export interface LegendItem {
  key: string
  label: string
  color: string
  /** Mirrors the mark: a swatch for bars and areas, a stroke for lines. */
  mark?: 'rect' | 'line'
}

export interface TableTwin {
  columns: string[]
  /** Pre-formatted cells. The table is a reading surface, not a second formatting layer. */
  rows: ReactNode[][]
}

interface Props {
  title: string
  /** What the chart says, in one line. Rendered before the plot. */
  hint?: ReactNode
  /**
   * Provenance: coverage, window, sample size. Rendered *under* the plot, in the muted
   * voice, because it qualifies a number the reader has already seen.
   */
  caption?: ReactNode
  legend?: LegendItem[]
  /**
   * Not optional. A chart whose only route to a value is a hover is a chart half its
   * readers cannot use, and the light-mode palette's sub-3:1 slots are legal *because*
   * this exists — the validator calls it "relief required", and this is the relief.
   */
  table: TableTwin
  /**
   * Renders instead of the plot and the table when there is nothing to draw. Callers pass
   * `condition && <EmptyState/>`, so `false` is a normal value here and has to be treated
   * as "not empty" — `??` would render it as a child and silently swallow the chart.
   */
  empty?: ReactNode
  /** Refetch holds the previous render at reduced opacity — no skeleton flash, no jump. */
  busy?: boolean
  children: ReactNode
}

/**
 * The frame every chart on the dashboard sits in.
 *
 * It owns the four things a plot must never own: the title, the legend, the coverage note,
 * and the table twin. Keeping the legend outside the SVG is not a style preference — a
 * legend Recharts lays out inside the plot area steals height from the bars at narrow
 * widths and wraps into them, which is one of the two ways a chart card breaks on a phone.
 */
export function ChartFrame({ title, hint, caption, legend, table, empty, busy, children }: Props) {
  const [view, setView] = useState<'chart' | 'table'>('chart')
  const bodyId = useId()

  const isEmpty = Boolean(empty)
  const legendVisible = !isEmpty && view === 'chart' && legend && legend.length >= 2

  return (
    <Card
      className="chart-card"
      title={title}
      hint={hint}
      actions={
        !isEmpty && (
          <Button
            className="chart-toggle"
            aria-pressed={view === 'table'}
            aria-controls={bodyId}
            onClick={() => setView((v) => (v === 'chart' ? 'table' : 'chart'))}
            variant="ghost"
          >
            {view === 'chart' ? 'Tabella' : 'Grafico'}
          </Button>
        )
      }
    >
      <div id={bodyId} className={busy ? 'chart-body is-busy' : 'chart-body'}>
        {isEmpty ? empty : view === 'chart' ? children : <Twin title={title} table={table} />}
      </div>

      {legendVisible && (
        <ul className="chart-legend">
          {legend.map((item) => (
            <li key={item.key}>
              <span
                className={item.mark === 'line' ? 'key line' : 'key rect'}
                style={{ background: item.color }}
                aria-hidden="true"
              />
              {item.label}
            </li>
          ))}
        </ul>
      )}

      {caption && <p className="chart-caption">{caption}</p>}
    </Card>
  )
}

function Twin({ title, table }: { title: string; table: TableTwin }) {
  return (
    <div className="chart-table-wrap">
      <table className="chart-table">
        <caption className="sr-only">{title}</caption>
        <thead>
          <tr>
            {table.columns.map((c) => (
              <th key={c} scope="col">
                {c}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {table.rows.map((row, i) => (
            <tr key={i}>
              {row.map((cell, j) =>
                j === 0 ? (
                  <th key={j} scope="row">
                    {cell}
                  </th>
                ) : (
                  <td key={j}>{cell}</td>
                ),
              )}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
