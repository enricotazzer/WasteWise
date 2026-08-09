import type { ReactNode } from 'react'

export interface ReadoutRow {
  key: string
  label: string
  color: string
  value: string
}

interface Props {
  title: string
  rows: ReadoutRow[]
  /** An extra line under the rows — coverage, a missing-price count, anything qualifying. */
  note?: ReactNode
}

/**
 * The hover readout, shared by every chart.
 *
 * Two deliberate inversions of the legend's hierarchy. The **value leads and the label
 * follows**, because a reader who has already aimed at a series knows what it is and wants
 * the number. And the series is keyed with a short **stroke**, not a filled box: at this
 * density a swatch is data-weight ink doing a label's job.
 *
 * Labels arrive from the API (category codes resolved against the catalog), so they go in
 * as text nodes — React does that by construction here, which is the point of not building
 * this tooltip out of an HTML string.
 */
export function Readout({ title, rows, note }: Props) {
  return (
    <div className="chart-tip" role="tooltip">
      <p className="chart-tip-title">{title}</p>
      {rows.map((r) => (
        <p className="chart-tip-row" key={r.key}>
          <span className="key line" style={{ background: r.color }} aria-hidden="true" />
          <span className="v">{r.value}</span>
          <span className="l">{r.label}</span>
        </p>
      ))}
      {note && <p className="chart-tip-note">{note}</p>}
    </div>
  )
}
