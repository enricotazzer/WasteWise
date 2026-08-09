/**
 * The money contract: the shape, and the two pure functions that read it.
 *
 * Separate from Money.tsx so the shape and its formatters can be imported by a chart, a
 * table cell or a test without dragging a React component along — and so the component file
 * stays hot-reloadable.
 */

/**
 * A euro figure and the evidence behind it.
 *
 * Prices in WasteWise are optional and typed by hand, so a total is always computed over
 * some fraction of the items it claims to cover. A euro figure without that fraction isn't a
 * measurement, it's a guess wearing a currency symbol — and the validation story in the
 * brief is specifically "euros saved *measured*, not estimated". So the pair travels
 * together as one value and `<Money>` is the only thing allowed to render it.
 *
 * Mirrors the backend `MoneyFigure` schema exactly.
 */
export interface MoneyFigure {
  /** Decimal serialises as a string; `null` when nothing priced contributed. */
  eur: string | null
  /** Items in scope of the question. */
  entries: number
  /** Of those, how many carried a price. */
  priced_entries: number
  /** priced_entries / entries. `null` when entries is 0 — unknown, not zero. */
  coverage: number | null
  basis: 'measured' | 'unpriced' | 'inferred_price' | 'modelled'
}

const EUR = new Intl.NumberFormat('it-IT', {
  style: 'currency',
  currency: 'EUR',
  maximumFractionDigits: 2,
})

/** Italian convention (12,40 €), because every other string in this UI is Italian. */
export function formatEur(value: string | number | null | undefined): string {
  if (value === null || value === undefined) return '—'
  const n = Number(value)
  return Number.isFinite(n) ? EUR.format(n) : '—'
}

/**
 * Never optional and never empty — that is the entire contract. Every branch returns a
 * sentence, including the ones where the honest answer is "we can't say".
 */
export function coverageCaption(figure: MoneyFigure): string {
  if (figure.basis === 'modelled') return 'proiezione, non una misura'
  if (figure.basis === 'unpriced' || figure.eur === null) {
    return figure.entries > 0
      ? `nessun prezzo su ${figure.entries} articoli`
      : 'nessun prezzo inserito'
  }
  const prefix = figure.basis === 'inferred_price' ? 'prezzo stimato · ' : ''
  if (figure.coverage === null) return `${prefix}copertura sconosciuta`
  if (figure.coverage >= 1) return `${prefix}su tutti i ${figure.entries} articoli`
  return `${prefix}su ${Math.round(figure.coverage * 100)}% con prezzo (${figure.priced_entries}/${figure.entries})`
}
