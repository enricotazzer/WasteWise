import type { ReactNode } from 'react'

interface Props {
  /** Rendered as an h2. Card headings were previously bare `<h2>`s picking up UA defaults,
      which is why the same heading looked 24px on the scan screen and 15px in Recipes. */
  title?: ReactNode
  /** Sits under the title, before the content. The `.hint` voice: why this panel exists. */
  hint?: ReactNode
  /** Trailing slot in the heading row — a Segmented range picker, a count, an action. */
  actions?: ReactNode
  className?: string
  children: ReactNode
}

/** The one panel surface. Everything that isn't a full-bleed screen sits in one of these. */
export function Card({ title, hint, actions, className, children }: Props) {
  return (
    <section className={className ? `card ${className}` : 'card'}>
      {(title || actions) && (
        <div className="card-head">
          {title && <h2 className="card-title">{title}</h2>}
          {actions && <div className="card-actions">{actions}</div>}
        </div>
      )}
      {hint && <div className="hint card-hint">{hint}</div>}
      {children}
    </section>
  )
}
