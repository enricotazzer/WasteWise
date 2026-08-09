import type { ReactNode } from 'react'

import { Icon, type IconName } from './Icon'

export type Tone = 'alert' | 'warn' | 'good'

interface Props {
  /** A single current value. If it needs a chart it doesn't belong in a tile. */
  value: ReactNode
  label: ReactNode
  /** Provenance or scope of the number — coverage, period, sample size. Never decoration. */
  caption?: ReactNode
  tone?: Tone
  icon?: IconName
}

/**
 * One KPI.
 *
 * A tile is the right form precisely when a chart is the wrong one: a single current value,
 * where a one-bar bar chart would say nothing the number doesn't. Tone colours the value and
 * repeats itself as a dot, so state is never hue-alone.
 *
 * Values use proportional figures deliberately — `tabular-nums` makes a large standalone
 * number look loose, and nothing in the KPI row aligns vertically.
 */
export function Tile({ value, label, caption, tone, icon }: Props) {
  return (
    <div className={tone ? `stat ${tone}` : 'stat'}>
      <div className="n">{value}</div>
      <div className="l">
        {tone && <span className="dot" aria-hidden="true" />}
        {icon && <Icon name={icon} size={13} className="stat-icon" />}
        {label}
      </div>
      {caption && <div className="stat-caption">{caption}</div>}
    </div>
  )
}
