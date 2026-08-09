import { useQuery } from '@tanstack/react-query'

import { api, type Stats } from '../../api'
import { useDemoMode } from '../../lib/demoMode'
import { Money, type MoneyFigure, SkeletonTiles, Tile } from '../../ui'

/**
 * KPI row.
 *
 * Each tile is a single current value, which is exactly the case where a chart is the wrong
 * form — a one-bar bar chart says nothing a number doesn't. Values carry a status colour
 * *and* a dot, so state is never colour-alone.
 *
 * Rendered by the Kitchen screen rather than globally above every tab. Above the scan form
 * or the recipe graph these numbers were chrome standing between the user and the thing
 * they opened the app to do; above the board they describe exactly what's underneath.
 */
export function StatsBar() {
  // The board below counts demo items whenever they are what's in the database, so this row
  // has to be asked the same question — the endpoint's `include_demo=false` default is what
  // made it read "0 in casa" over 69 visible items. The badge that labels the result lives
  // in the top bar, where it is on screen for every route rather than just this one.
  const { includeDemo, ready } = useDemoMode()

  const { data } = useQuery({
    queryKey: ['stats', 'inventory', includeDemo],
    queryFn: () => api.stats(includeDemo),
    enabled: ready,
  })

  // Reserving the row's height instead of returning null: the KPI row used to pop in after
  // the board had already painted, shoving every item card down by ~90px — under a thumb
  // that was on its way to a "consumato" button.
  if (!data) return <SkeletonTiles count={4} />

  // Save rate is the headline metric: share of resolved food items that were eaten
  // rather than binned. Null until at least one food item has been resolved — showing
  // 0% or 100% off a single data point would be misleading.
  const saveRate = data.save_rate === null ? '—' : `${Math.round(data.save_rate * 100)}%`

  return (
    <div className="stats">
      <Tile value={data.active_items} label="in casa" />

      <Tile
        value={data.expiring_within_3d}
        label="entro 3 giorni"
        tone={data.expiring_within_3d > 0 ? 'warn' : undefined}
      />

      <Tile
        value={data.expired_still_active}
        label="da buttare"
        tone={data.expired_still_active > 0 ? 'alert' : undefined}
      />

      <Tile
        value={saveRate}
        label="consumato"
        tone={data.save_rate !== null && data.save_rate >= 0.8 ? 'good' : undefined}
      />

      {Number(data.eur_wasted) > 0 && (
        <Money figure={wastedFigure(data)} label="sprecati" variant="tile" />
      )}
    </div>
  )
}

/**
 * `/inventory/stats` still returns a flat total plus a separate coverage float. MoneyFigure
 * is the shape it's growing into, so the adaptation happens here, at the edge — the tile is
 * already reading the final contract and won't need touching when the endpoint catches up.
 */
function wastedFigure(data: Stats): MoneyFigure {
  const coverage = data.price_coverage
  return {
    eur: data.eur_wasted,
    entries: data.resolved_total,
    priced_entries: coverage === null ? 0 : Math.round(coverage * data.resolved_total),
    coverage,
    basis: coverage === null || coverage === 0 ? 'unpriced' : 'measured',
  }
}
