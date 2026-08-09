import type { StatsOverview } from '../../../api'
import { Money, SkeletonTiles, Tile, formatKg, massCoverageCaption } from '../../../ui'
import { pct } from './format'

/**
 * Four current values, so four tiles rather than a chart — a bar chart of four unrelated
 * quantities would invite a comparison that isn't meaningful.
 *
 * Every one of them is lifetime, not windowed: `/api/stats/overview` takes no date range,
 * and the filter row deliberately sits *below* this block so it never looks as though it
 * scopes these numbers. The "da sempre" in the hero says the same thing in words.
 */
export function KpiRow({ overview }: { overview: StatsOverview | undefined }) {
  if (!overview) return <SkeletonTiles count={4} />

  const wastedEur = Number(overview.wasted.eur ?? 0)
  const rateEur = overview.save_rate_eur
  const coverage = overview.save_rate_eur_coverage

  return (
    <div className="stats">
      <Money
        figure={overview.wasted}
        label="sprecati"
        variant="tile"
        tone={wastedEur > 0 ? 'alert' : undefined}
      />

      {/* Its own coverage, not the price one: only kg/g items are weighed at all, so this
          denominator is a different set of rows from the euro figure beside it. */}
      <Tile
        value={formatKg(overview.wasted_kg.kg)}
        label="sprecati a peso"
        caption={massCoverageCaption(overview.wasted_kg)}
      />

      <Money figure={overview.spend} label="spesa registrata" variant="tile" />

      <Tile
        value={rateEur === null ? '—' : pct(rateEur)}
        label="consumato a valore"
        caption={
          coverage === null
            ? 'nessun articolo con prezzo'
            : `sul ${pct(coverage)} di articoli con prezzo`
        }
        tone={rateEur !== null && rateEur >= 0.8 ? 'good' : undefined}
      />
    </div>
  )
}
