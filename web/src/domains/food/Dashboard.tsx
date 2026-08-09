import { useQuery } from '@tanstack/react-query'

import { api } from '../../api'
import { Badge, EmptyState } from '../../ui'
import { Filters } from './dashboard/Filters'
import { Hero } from './dashboard/Hero'
import { KpiRow } from './dashboard/KpiRow'
import { ModelPerformance } from './dashboard/ModelPerformance'
import { SaveRateTrend } from './dashboard/SaveRateTrend'
import { SpendByCategory } from './dashboard/SpendByCategory'
import { WasteOverTime } from './dashboard/WasteOverTime'
import { useDashboardParams } from './dashboard/params'
import { useSeriesScale } from './dashboard/useSeriesScale'

/**
 * The summary screen.
 *
 * Reading order is the argument the screen makes, so the layout is fixed rather than a grid
 * of interchangeable cards: the headline rate and the caveat that qualifies it, then the
 * four lifetime totals, then the filters, then everything the filters scope. Nothing above
 * the filter row moves when a filter changes and nothing below it is unaffected, which is
 * the only way a reader can tell which numbers a control is talking about.
 *
 * Every query waits on `params.ready`. The demo probe is what decides `include_demo`, and
 * firing before it answers would fetch the wrong slice and then visibly replace it — the
 * loading state of a wrong number is still a wrong number.
 */
export function Dashboard() {
  const params = useDashboardParams()
  const { includeDemo, bucket, periods, ready } = params

  const overview = useQuery({
    queryKey: ['stats', 'overview', includeDemo],
    queryFn: () => api.statsOverview(includeDemo),
    enabled: ready,
  })

  const timeseries = useQuery({
    queryKey: ['stats', 'timeseries', bucket, periods, includeDemo],
    queryFn: () => api.statsTimeseries({ bucket, periods, includeDemo }),
    enabled: ready,
    // Holding the previous window while the next one loads is what stops every filter press
    // from collapsing the charts to an empty frame and back.
    placeholderData: (prev) => prev,
  })

  const byCategory = useQuery({
    queryKey: ['stats', 'by-category', bucket, periods, includeDemo],
    queryFn: () => api.statsByCategory({ bucket, periods, includeDemo }),
    enabled: ready,
    placeholderData: (prev) => prev,
  })

  const categories = useSeriesScale(includeDemo, ready)

  const demoOn = overview.data?.demo_included ?? false
  const empty =
    overview.data !== undefined &&
    overview.data.resolved_total === 0 &&
    overview.data.active_items === 0

  return (
    <>
      <Hero overview={overview.data} trend={timeseries.data} />

      {/* Repeated here as well as in the top bar. These tiles and charts are the part of the
          app most likely to be screenshotted or read out of context, and a label that lives
          only in a header survives neither. */}
      {demoOn && (
        <p className="dash-provenance">
          <Badge tone="warn">dati dimostrativi</Badge>
          <span>
            Le cifre qui sotto includono {overview.data?.demo_entries} righe di esempio,
            seminate per far partire il modello. Non sono misure di questa casa.
          </span>
        </p>
      )}

      <KpiRow overview={overview.data} />

      <Filters params={params} />

      {empty ? (
        <EmptyState
          icon="chart"
          title="Non c'è ancora niente da riassumere"
          description={
            params.demoEntries > 0
              ? 'Con i dati dimostrativi esclusi il registro è vuoto. Aggiungi la spesa da «Scansiona», oppure riattiva i dati di esempio dal filtro qui sopra per vedere come si comporta la schermata.'
              : 'Aggiungi la spesa e registra cosa finisce consumato o buttato: da lì in poi questa schermata si riempie da sola.'
          }
        />
      ) : (
        <div className="dash-grid">
          <WasteOverTime
            data={timeseries.data}
            bucket={bucket}
            categories={categories}
            busy={timeseries.isFetching}
          />
          {/* Full width, unlike the pair above it. Two converging lines over 26 buckets need
              horizontal room to stay separable, and half a row would have left a hole in the
              grid beside it — the two waste charts are an even pair, this is a single. */}
          <div className="dash-wide">
            <SaveRateTrend data={timeseries.data} bucket={bucket} busy={timeseries.isFetching} />
          </div>
          {/* Half width, paired with the risk placeholder. Full width, the spend bars ran
              1600px at 1440 — a 10px mark stretched across the screen is a lot of saturated
              ink for a value the row already prints, and it left the placeholder alone on a
              row of its own looking like a rendering failure. */}
          <SpendByCategory
            data={byCategory.data}
            bucket={bucket}
            categories={categories}
            busy={byCategory.isFetching}
          />
          <ModelPerformance includeDemo={includeDemo} />
        </div>
      )}
    </>
  )
}
