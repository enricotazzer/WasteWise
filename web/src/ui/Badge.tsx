import type { ReactNode } from 'react'

interface Props {
  /** `neutral` is the default for a reason — see below. */
  tone?: 'accent' | 'danger' | 'warn' | 'neutral'
  className?: string
  children: ReactNode
}

/**
 * A small inline status token.
 *
 * Only two tones carry chroma. `neutral` exists because most badges in this app report
 * something true but unurgent ("12g", "manca 2"), and painting all of them would leave the
 * two that mean *decide now* with nothing left to stand out against.
 */
export function Badge({ tone = 'accent', className, children }: Props) {
  const classes = ['pill', tone !== 'accent' && tone, className].filter(Boolean).join(' ')
  return <span className={classes}>{children}</span>
}
