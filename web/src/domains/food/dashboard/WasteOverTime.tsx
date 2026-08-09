import type { Bucket, TimeseriesResponse } from '../../../api'
import { EmptyState, formatEur, formatKg } from '../../../ui'
import { ChartFrame, StackedBars, type LegendItem, type StackDatum, type StackSeries, type TableTwin } from '../../../ui/charts'
import { bucketLabel, bucketTick, dayLabel, eurTick, kgTick, pct, trimLeadingEmpty } from './format'
import type { CategoryScale } from './useSeriesScale'

interface Props {
  data: TimeseriesResponse | undefined
  bucket: Bucket
  categories: CategoryScale
  busy: boolean
}

/**
 * Waste over time, as TWO charts.
 *
 * Euros and kilograms are not two views of one quantity — they are measured over different
 * subsets of the same rows. 25 wasted items might have 14 prices and 19 weights, so the two
 * series disagree for a reason that has nothing to do with the household. Putting them on
 * one plot with two y-axes would let the renderer choose an alignment and thereby invent a
 * relationship between two coverages; each chart therefore carries its own coverage note
 * and its own axis, and neither is drawn against the other.
 */
export function WasteOverTime({ data, bucket, categories, busy }: Props) {
  const euro = build(data, bucket, categories, 'eur')
  const mass = build(data, bucket, categories, 'kg')

  return (
    <>
      <ChartFrame
        title="Sprecato nel tempo — euro"
        hint="Valore buttato per periodo, diviso per categoria."
        caption={coverageCaption(data, 'eur')}
        legend={euro.legend}
        table={euro.table}
        busy={busy}
        empty={
          euro.empty && (
            <EmptyState
              icon="chart"
              title="Nessuno spreco con prezzo in questo periodo"
              description="Le barre si riempiono quando un articolo buttato aveva un prezzo. Senza prezzo lo spreco resta nel grafico in kg qui sotto."
            />
          )
        }
      >
        <StackedBars
          data={euro.rows}
          series={euro.series}
          format={(v) => formatEur(v)}
          formatTick={eurTick}
        />
      </ChartFrame>

      <ChartFrame
        title="Sprecato nel tempo — kg"
        hint="Peso buttato per periodo, diviso per categoria."
        caption={coverageCaption(data, 'kg')}
        legend={mass.legend}
        table={mass.table}
        busy={busy}
        empty={
          mass.empty && (
            <EmptyState
              icon="chart"
              title="Nessuno spreco a peso in questo periodo"
              description="Solo gli articoli comprati a kg o g hanno un peso: una confezione o un pezzo non entra in questo grafico."
            />
          )
        }
      >
        <StackedBars
          data={mass.rows}
          series={mass.series}
          format={(v) => formatKg(v)}
          formatTick={kgTick}
        />
      </ChartFrame>
    </>
  )
}

function coverageCaption(data: TimeseriesResponse | undefined, measure: 'eur' | 'kg') {
  if (!data) return null
  const points = trimLeadingEmpty(data.points)
  const window =
    points.length > 0
      ? `${dayLabel(points[0].bucket_start)} – ${dayLabel(data.end)}`
      : dayLabel(data.end)

  if (measure === 'eur') {
    const c = data.price_coverage
    return c === null
      ? `Nessun prezzo inserito nel periodo · ${window}`
      : `Copertura prezzi ${pct(c)} degli articoli buttati · ${window}. Quello che è stato buttato senza prezzo non è in questi numeri.`
  }
  const c = data.mass_coverage
  return c === null
    ? `Nessun articolo a peso nel periodo · ${window}`
    : `Copertura peso ${pct(c)} degli articoli buttati · ${window}. Confezioni e pezzi non hanno un kg e non sono in questi numeri.`
}

interface Built {
  rows: StackDatum[]
  series: StackSeries[]
  legend: LegendItem[]
  table: TableTwin
  empty: boolean
}

function build(
  data: TimeseriesResponse | undefined,
  bucket: Bucket,
  categories: CategoryScale,
  measure: 'eur' | 'kg',
): Built {
  const blank: Built = {
    rows: [],
    series: [],
    legend: [],
    table: { columns: [], rows: [] },
    empty: true,
  }
  if (!data) return blank

  const points = trimLeadingEmpty(data.points)
  if (points.length === 0) return blank

  const inWindow = new Set(points.map((p) => p.bucket_start))
  const totals = new Map<string, number>()
  const perBucket = new Map<string, Map<string, number>>()

  for (const row of data.by_category) {
    if (!inWindow.has(row.bucket_start)) continue
    const raw = measure === 'eur' ? row.waste_eur : row.waste_kg
    // `null` means unknown, not zero — folding it in as 0 would understate the stack while
    // looking complete. It is left out and counted in the per-bucket note instead.
    if (raw === null) continue
    const value = Number(raw)
    if (!Number.isFinite(value) || value <= 0) continue

    const key = categories.scale.foldOf(row.category_code)
    totals.set(key, (totals.get(key) ?? 0) + value)
    const bucketMap = perBucket.get(row.bucket_start) ?? new Map<string, number>()
    bucketMap.set(key, (bucketMap.get(key) ?? 0) + value)
    perBucket.set(row.bucket_start, bucketMap)
  }

  if (totals.size === 0) return blank

  // Stacking order follows the scale's slot order, not this window's magnitudes — the same
  // reason the colours do. A category that had a quiet month must not jump layers.
  const series: StackSeries[] = categories.scale.keys
    .filter((key) => totals.has(key))
    .map((key) => ({
      key,
      label: categories.labelOf(key),
      color: categories.scale.colorOf(key),
    }))

  const rows: StackDatum[] = points.map((p) => {
    const values = perBucket.get(p.bucket_start) ?? new Map<string, number>()
    const unpriced = p.waste_entries - p.waste_priced_entries
    return {
      x: bucketTick(p.bucket_start, bucket),
      label: bucketLabel(p.bucket_start, bucket),
      note:
        measure === 'eur' && unpriced > 0
          ? `${unpriced} ${unpriced === 1 ? 'articolo buttato senza prezzo' : 'articoli buttati senza prezzo'}`
          : undefined,
      values: Object.fromEntries(series.map((s) => [s.key, values.get(s.key) ?? null])),
    }
  })

  const fmt = measure === 'eur' ? (v: number) => formatEur(v) : (v: number) => formatKg(v)
  const table: TableTwin = {
    columns: ['Periodo', ...series.map((s) => s.label), 'Totale'],
    rows: rows.map((r) => {
      const cells = series.map((s) => {
        const v = r.values[s.key]
        return typeof v === 'number' ? fmt(v) : '—'
      })
      const total = series.reduce((sum, s) => sum + (r.values[s.key] ?? 0), 0)
      return [r.label, ...cells, fmt(total)]
    }),
  }

  return {
    rows,
    series,
    legend: series.map((s) => ({ key: s.key, label: s.label, color: s.color, mark: 'rect' })),
    table,
    empty: false,
  }
}
