import { useRef } from 'react'
import type { ReactNode } from 'react'

export interface SegmentedOption<T extends string> {
  value: T
  label: ReactNode
  /** Announced instead of `label` when the label is an abbreviation ("30g" -> "30 giorni"). */
  title?: string
}

interface Props<T extends string> {
  options: SegmentedOption<T>[]
  value: T
  onChange: (value: T) => void
  /** Required: a filter control with no accessible name is unusable by keyboard. */
  label: string
}

/**
 * A one-of-N filter switch.
 *
 * `radiogroup`, not `tablist` — a tab swaps a panel, this changes the parameters of the
 * panel you're already looking at, and screen-reader users are told the difference. That
 * also buys the roving tabindex below: a radiogroup is one tab stop, arrows move within it,
 * which is what makes a five-option range picker bearable on a keyboard.
 */
export function Segmented<T extends string>({ options, value, onChange, label }: Props<T>) {
  const ref = useRef<HTMLDivElement>(null)

  function onKeyDown(e: React.KeyboardEvent) {
    const delta = e.key === 'ArrowRight' || e.key === 'ArrowDown' ? 1 : e.key === 'ArrowLeft' || e.key === 'ArrowUp' ? -1 : 0
    if (delta === 0) return
    e.preventDefault()
    const i = options.findIndex((o) => o.value === value)
    const next = options[(i + delta + options.length) % options.length]
    onChange(next.value)
    // Focus follows selection, per the radiogroup pattern: the new value has to be where
    // the next arrow press starts from.
    const buttons = ref.current?.querySelectorAll('button')
    buttons?.[options.indexOf(next)]?.focus()
  }

  return (
    <div className="segmented" role="radiogroup" aria-label={label} ref={ref} onKeyDown={onKeyDown}>
      {options.map((o) => {
        const selected = o.value === value
        return (
          <button
            key={o.value}
            type="button"
            role="radio"
            aria-checked={selected}
            title={o.title}
            tabIndex={selected ? 0 : -1}
            onClick={() => onChange(o.value)}
          >
            {o.label}
          </button>
        )
      })}
    </div>
  )
}
