/**
 * The mass contract — the exact twin of `moneyFigure.ts`, and separate from it on purpose.
 *
 * Price coverage and mass coverage are different numbers over the identical rows: only
 * kg/g items have a weight at all, so a household that buys mostly packs and pieces can sit
 * at 90% price coverage and 20% mass coverage. A screen that reports "12,4 kg sprecati"
 * under the euro figure's coverage note is quietly claiming evidence it doesn't have, which
 * is why the two figures never share a caption.
 *
 * Mirrors the backend `MassFigure` schema exactly.
 */
export interface MassFigure {
  /** Decimal serialises as a string; `null` when nothing in scope was weighed. */
  kg: string | null
  /** Items in scope of the question. */
  entries: number
  /** Of those, how many were bought by weight. */
  measured_entries: number
  /** measured_entries / entries. `null` when entries is 0 — unknown, not zero. */
  coverage: number | null
}

const KG = new Intl.NumberFormat('it-IT', { maximumFractionDigits: 2 })

export function formatKg(value: string | number | null | undefined): string {
  if (value === null || value === undefined) return '—'
  const n = Number(value)
  return Number.isFinite(n) ? `${KG.format(n)} kg` : '—'
}

/** Never optional and never empty, for the same reason `coverageCaption` isn't. */
export function massCoverageCaption(figure: MassFigure): string {
  if (figure.kg === null) {
    return figure.entries > 0
      ? `nessun articolo a peso su ${figure.entries}`
      : 'nessun articolo a peso'
  }
  if (figure.coverage === null) return 'copertura sconosciuta'
  if (figure.coverage >= 1) return `su tutti i ${figure.entries} articoli`
  return `su ${Math.round(figure.coverage * 100)}% a peso (${figure.measured_entries}/${figure.entries})`
}
