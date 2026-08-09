import type { Bucket, ByCategoryResponse, CategoryBucket } from '../../../api'
import { EmptyState, Icon, formatEur } from '../../../ui'
import { ChartFrame } from '../../../ui/charts'
import { bucketInProgress, bucketLabel, pct, signedPct } from './format'
import type { CategoryScale } from './useSeriesScale'

interface Props {
  data: ByCategoryResponse | undefined
  bucket: Bucket
  categories: CategoryScale
  busy: boolean
}

/**
 * Where the money went, and how that moved.
 *
 * WHY THIS ONE IS HTML AND NOT RECHARTS.
 * Ranked magnitudes with long Italian names and a per-row delta is a table with a bar
 * column, not a plot. In a cartesian chart the names need ~150px of axis before the first
 * pixel of data, which at 390px leaves the bars a third of the card, and the deltas have
 * nowhere to live but a direct label that overflows its own bar. The marks still follow the
 * chart specs — thin, 4px rounded data-end, recessive track, entity colour.
 *
 * WHY THE BARS CARRY CATEGORY COLOUR.
 * Colouring nominal bars by their value would be wrong — bar length already says the value.
 * These are coloured by *identity*, from the same scale the waste charts use, so Frutta is
 * the same colour in all four views on this screen. That is the whole reason the scale is
 * computed once and not per chart.
 */
export function SpendByCategory({ data, bucket, categories, busy }: Props) {
  const view = pick(data, bucket)

  const all = view
    ? view.rows
        .filter((r) => r.spend.entries > 0)
        .sort((a, b) => Number(b.spend.eur ?? 0) - Number(a.spend.eur ?? 0))
    : []

  // A category whose entries all arrived without a price has no bar to draw — a zero-length
  // mark would read as "spent nothing", which is the one thing we know it doesn't mean. Its
  // rows stay in the table twin and its count goes in the caption, so it is reported rather
  // than dropped.
  const rows = all.filter((r) => r.spend.eur !== null)
  const unpricedEntries = all
    .filter((r) => r.spend.eur === null)
    .reduce((sum, r) => sum + r.spend.entries, 0)

  const max = Math.max(0, ...rows.map((r) => Number(r.spend.eur ?? 0)))
  // Italian needs the article to agree, so the two bucket words carry their own phrasing
  // rather than being interpolated into one template that reads wrong half the time.
  const unit =
    bucket === 'month'
      ? { noun: 'mese', prev: 'al mese precedente', before: 'un mese prima', short: 'mese prec.' }
      : {
          noun: 'settimana',
          prev: 'alla settimana precedente',
          before: 'una settimana prima',
          short: 'sett. prec.',
        }

  return (
    <ChartFrame
      title="Spesa per categoria"
      hint={
        view
          ? `${bucketLabel(view.bucketStart, bucket)}${view.inProgress ? ' · in corso' : ''}`
          : undefined
      }
      caption={
        view
          ? [
              view.inProgress
                ? `Questo ${unit.noun} non è ancora finito, quindi il confronto con il precedente è sospeso: paragonare mezzo ${unit.noun} con uno intero produce un calo che è solo il calendario.`
                : `Variazione rispetto ${unit.prev}. Dove non c'è ${unit.before} con cui confrontarsi la percentuale non viene inventata — un primo periodo non è uno zero.`,
              unpricedEntries > 0 &&
                `Altri ${unpricedEntries} articoli sono entrati in casa senza un prezzo: non hanno una barra qui, ma sono nella tabella.`,
            ]
              .filter(Boolean)
              .join(' ')
          : undefined
      }
      busy={busy}
      empty={
        rows.length === 0 && (
          <EmptyState
            icon="chart"
            title="Nessuna spesa registrata nel periodo"
            description="Le categorie compaiono qui appena un articolo entra in casa con un prezzo."
          />
        )
      }
      table={{
        columns: [
          'Categoria',
          'Spesa',
          `Δ spesa vs ${unit.short}`,
          'Sprecato',
          'Quota sprecata',
          `Δ spreco vs ${unit.short}`,
        ],
        // The table keeps every category, including the ones with no price to plot.
        rows: all.map((r) => [
          categories.labelOf(r.category_code),
          formatEur(r.spend.eur),
          deltaText(r.spend_delta_pct, view?.inProgress ?? false),
          formatEur(r.waste.eur),
          r.waste_share === null ? '—' : pct(r.waste_share),
          deltaText(r.waste_delta_pct, view?.inProgress ?? false),
        ]),
      }}
    >
      <ul className="cat-list">
        {rows.map((r) => {
          const spend = Number(r.spend.eur ?? 0)
          const color = categories.scale.colorOf(r.category_code)
          return (
            <li className="cat-row" key={r.category_code}>
              <div className="cat-head">
                <span className="key rect" style={{ background: color }} aria-hidden="true" />
                <span className="cat-name">{categories.labelOf(r.category_code)}</span>
                <span className="cat-value">{formatEur(r.spend.eur)}</span>
              </div>
              <div className="cat-track">
                <span
                  className="cat-bar"
                  style={{
                    width: max > 0 ? `${Math.max((spend / max) * 100, spend > 0 ? 2 : 0)}%` : 0,
                    background: color,
                  }}
                />
              </div>
              <div className="cat-meta">
                <Delta value={r.spend_delta_pct} suspended={view?.inProgress ?? false} />
                {r.waste_share !== null && (
                  <span className="cat-waste">
                    {formatEur(r.waste.eur)} sprecati · {pct(r.waste_share)}
                  </span>
                )}
              </div>
            </li>
          )
        })}
      </ul>
    </ChartFrame>
  )
}

/**
 * A `null` delta is "there was no previous bucket", which is not zero and is emphatically
 * not "unchanged" — the strongest of the three claims and the only one we can't make. It
 * gets its own words, in the muted voice, with no arrow.
 */
function Delta({ value, suspended }: { value: number | null; suspended: boolean }) {
  if (suspended) return <span className="delta is-none">confronto sospeso</span>
  if (value === null) return <span className="delta is-none">primo periodo</span>
  if (Math.round(value * 100) === 0) return <span className="delta is-none">stabile</span>
  // Deliberately not coloured good/bad. Spending more on food is not a failure and spending
  // less is not a win — this dashboard measures waste, not frugality, and painting the
  // arrow green or red would assert a judgement the number can't support.
  return (
    <span className="delta">
      <Icon name={value > 0 ? 'trend-up' : 'trend-down'} size={13} />
      {signedPct(value)}
    </span>
  )
}

/** The same three cases as `Delta`, in words — the table must not say "+0%" where the chart
 *  says "stabile" for the identical row. */
function deltaText(value: number | null, suspended: boolean): string {
  if (suspended) return 'sospeso'
  if (value === null) return 'primo periodo'
  if (Math.round(value * 100) === 0) return 'stabile'
  return signedPct(value)
}

/**
 * Which bucket to show.
 *
 * The most recent one is usually still running, and a month-to-date total next to a full
 * previous month makes every category look like it collapsed. So the last *complete* bucket
 * wins; the running one is only used when it is all there is, and then its deltas are
 * suspended rather than shown.
 */
function pick(data: ByCategoryResponse | undefined, bucket: Bucket) {
  if (!data || data.rows.length === 0) return null
  const starts = [...new Set(data.rows.map((r) => r.bucket_start))].sort()
  const latest = starts[starts.length - 1]
  const running = bucketInProgress(latest, bucket)
  const bucketStart = running && starts.length > 1 ? starts[starts.length - 2] : latest
  const rows: CategoryBucket[] = data.rows.filter((r) => r.bucket_start === bucketStart)
  return { bucketStart, rows, inProgress: bucketInProgress(bucketStart, bucket) }
}
