import type { Bucket, TimeseriesResponse } from '../../../api'
import { EmptyState } from '../../../ui'
import { ChartFrame, TimeSeries, useChartTokens, type TimeDatum } from '../../../ui/charts'
import { bucketLabel, bucketTick, pct, trimLeadingEmpty } from './format'

const COUNT = 'count'
const VALUE = 'value'

/**
 * The save rate over time, both ways of counting it.
 *
 * THE TWO SERIES DISAGREE, AND THAT IS THE POINT.
 * The count series asks "what share of articles ended up eaten"; the value series asks
 * "what share of the money worked out". They answer different questions over different
 * samples — the euro one only sees articles that had a price typed — so an expensive item
 * binned in a week of cheap ones drags them apart. A legend reading just "tasso di consumo"
 * would present that gap as an inconsistency; both series are therefore named in full,
 * everywhere, including in the tooltip and the table.
 *
 * ONE AXIS. Both are shares in [0,1], which is the only condition under which plotting two
 * measures together is honest: the scale is genuinely shared, not aligned by choice.
 *
 * COLOUR IS EMPHASIS, NOT IDENTITY. The count-based rate is the headline and wears the
 * app's "good" token — the same colour the KPI tile for this metric already uses; the euro
 * variant sits beside it in the de-emphasis gray. Neither takes a categorical slot, so
 * neither can be mistaken for a category in the charts above.
 */
export function SaveRateTrend({
  data,
  bucket,
  busy,
}: {
  data: TimeseriesResponse | undefined
  bucket: Bucket
  busy: boolean
}) {
  const tokens = useChartTokens()

  const series = [
    { key: COUNT, label: 'Consumato — quota di articoli', color: tokens.accent },
    { key: VALUE, label: 'Consumato — quota di valore (€)', color: tokens.muted },
  ]

  // Same window rule as the waste charts, so the two read against the same x-extent.
  const rows: TimeDatum[] = trimLeadingEmpty(data?.points ?? []).map((p) => {
    const resolved = p.used_entries + p.waste_entries
    const usedEur = p.used_eur === null ? null : Number(p.used_eur)
    const wasteEur = p.waste_eur === null ? null : Number(p.waste_eur)
    const money = usedEur === null || wasteEur === null ? null : usedEur + wasteEur

    return {
      x: bucketTick(p.bucket_start, bucket),
      label: bucketLabel(p.bucket_start, bucket),
      note:
        resolved > 0
          ? `${resolved} ${resolved === 1 ? 'articolo risolto' : 'articoli risolti'}`
          : 'nessun articolo risolto',
      values: {
        // A bucket with nothing resolved has no rate at all. Zero would read as "everything
        // was binned", which is the opposite of "nothing happened".
        [COUNT]: resolved > 0 ? p.used_entries / resolved : null,
        [VALUE]: money !== null && money > 0 ? (usedEur as number) / money : null,
      },
    }
  })

  // Two points is the minimum that makes a line mean anything; one is a dot pretending.
  const drawable = rows.filter((r) => r.values[COUNT] !== null).length >= 2

  return (
    <ChartFrame
      title="Andamento del consumo"
      hint="Quota di quello che è stato risolto e non è finito nella spazzatura."
      caption="Le due misure rispondono a domande diverse e non devono coincidere: la prima conta articoli, la seconda pesa gli euro sui soli articoli con un prezzo. Dove manca il dato la linea si interrompe invece di scendere a zero."
      legend={series.map((s) => ({ key: s.key, label: s.label, color: s.color, mark: 'line' }))}
      busy={busy}
      empty={
        !drawable && (
          <EmptyState
            icon="chart"
            title="Non ci sono ancora abbastanza periodi"
            description="Servono almeno due periodi con articoli risolti perché una linea dica qualcosa."
          />
        )
      }
      table={{
        columns: ['Periodo', 'Quota di articoli', 'Quota di valore (€)', 'Articoli risolti'],
        rows: rows.map((r) => [
          r.label,
          r.values[COUNT] === null ? '—' : pct(r.values[COUNT]),
          r.values[VALUE] === null ? '—' : pct(r.values[VALUE]),
          r.note ?? '—',
        ]),
      }}
    >
      <TimeSeries
        data={rows}
        series={series}
        format={pct}
        formatTick={pct}
        domain={[0, 1]}
      />
    </ChartFrame>
  )
}
