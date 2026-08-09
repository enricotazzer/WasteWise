import type { StatsOverview, TimeseriesResponse } from '../../../api'
import { Card, Icon, Skeleton, formatEur } from '../../../ui'
import { Sparkline, useChartTokens } from '../../../ui/charts'
import { pct } from './format'

interface Props {
  overview: StatsOverview | undefined
  /** Only for the sparkline's shape. Every value in it is also in the trend chart below. */
  trend: TimeseriesResponse | undefined
}

/**
 * The one number the dashboard leads with — and the one caveat that has to travel with it.
 *
 * The save rate is count-based: an outcome either was logged or it wasn't, so unlike every
 * euro figure on this screen it needs no coverage note. What it *does* need is the
 * unaccounted figure beside it, because the rate is defined over resolved items only. Value
 * that was bought and never resolved is excluded from the denominator, and unresolved food
 * is overwhelmingly food that was forgotten — so a high save rate next to a large
 * unaccounted figure is an unmeasured result, not a good one.
 *
 * That is why the two sit in one card rather than the rate leading and the caveat living in
 * a KPI tile three columns over. Splitting them would let the headline be read alone, which
 * is precisely the misreading the pairing exists to prevent.
 */
export function Hero({ overview, trend }: Props) {
  const tokens = useChartTokens()

  if (!overview) {
    return (
      <Card className="dash-hero">
        <div className="hero-main">
          <Skeleton width="9ch" height="56px" />
          <Skeleton width="24ch" height="18px" />
        </div>
      </Card>
    )
  }

  const rate = overview.save_rate
  const food = overview.domains.find((d) => d.domain === 'food')
  const unaccounted = food?.unaccounted
  const spendEur = Number(overview.spend.eur ?? 0)
  const unaccountedEur = Number(unaccounted?.eur ?? 0)
  const share = spendEur > 0 ? unaccountedEur / spendEur : null

  // Consumed share per bucket, count-based — the same definition as the headline, so the
  // shape under the figure is the figure's own history rather than a second metric.
  //
  // Leading buckets from before logging started are dropped for the same reason the charts
  // below drop them: a sparkline whose first two thirds are a gap is 300px of card spent
  // saying "we weren't running yet", and it visually detaches the line from its own label.
  const points = trend?.points ?? []
  const first = points.findIndex((p) => p.used_entries + p.waste_entries > 0)
  const spark =
    first < 0
      ? []
      : points.slice(first).map((p) => {
          const resolved = p.used_entries + p.waste_entries
          return resolved > 0 ? p.used_entries / resolved : null
        })
  const hasSpark = spark.filter((v) => v !== null).length >= 2

  return (
    <Card className="dash-hero">
      <div className="hero-main">
        <p className="hero-label">Consumato invece che buttato</p>
        <p className="hero-figure">{rate === null ? '—' : pct(rate)}</p>
        <p className="hero-sub">
          {rate === null
            ? 'nessun articolo ancora risolto'
            : `${overview.consumed_total} articoli su ${overview.resolved_total} risolti · da sempre`}
        </p>
        {hasSpark && (
          <Sparkline
            values={spark}
            color={tokens.accent}
            domain={[0, 1]}
            label="Andamento della quota consumata, per periodo"
          />
        )}
      </div>

      {unaccountedEur > 0 && (
        <div className="hero-flag">
          <p className="hero-flag-head">
            <Icon name="warning" size={17} />
            <strong>{formatEur(unaccounted?.eur)}</strong> mai risolti
          </p>
          <p className="hero-flag-body">
            Spesa registrata che non è ancora diventata né consumo né spreco
            {share !== null && ` — il ${pct(share)} del totale`}. Il tasso qui accanto è
            calcolato solo sugli articoli risolti, quindi questa quota non ci entra: finché
            resta alta, il numero grande è una misura parziale, non un buon risultato.
          </p>
        </div>
      )}
    </Card>
  )
}
