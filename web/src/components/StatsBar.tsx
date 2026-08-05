import { useQuery } from '@tanstack/react-query'

import { api } from '../api'

/**
 * KPI row.
 *
 * Each tile is a single current value, which is exactly the case where a chart is the wrong
 * form — a one-bar bar chart says nothing a number doesn't. Values carry a status colour
 * *and* a dot, so state is never colour-alone.
 */
export function StatsBar() {
  const { data } = useQuery({ queryKey: ['stats'], queryFn: api.stats })
  if (!data) return null

  // Save rate is the headline metric: share of resolved food items that were eaten
  // rather than binned. Null until at least one food item has been resolved — showing
  // 0% or 100% off a single data point would be misleading.
  const saveRate = data.save_rate === null ? '—' : `${Math.round(data.save_rate * 100)}%`
  const wasted = Number(data.eur_wasted)

  return (
    <div className="stats">
      <Tile n={data.active_items} label="in casa" />

      <Tile
        n={data.expiring_within_3d}
        label="entro 3 giorni"
        tone={data.expiring_within_3d > 0 ? 'warn' : undefined}
      />

      <Tile
        n={data.expired_still_active}
        label="da buttare"
        tone={data.expired_still_active > 0 ? 'alert' : undefined}
      />

      <Tile
        n={saveRate}
        label="consumato"
        tone={data.save_rate !== null && data.save_rate >= 0.8 ? 'good' : undefined}
      />

      {wasted > 0 && (
        <Tile
          n={`€${data.eur_wasted}`}
          label={
            <>
              sprecati
              {/* Prices are optional, so this total covers only part of the data. Stating the
                  coverage is the difference between a measurement and a claim. */}
              {data.price_coverage !== null && data.price_coverage < 1 && (
                <em> · su {Math.round(data.price_coverage * 100)}% con prezzo</em>
              )}
            </>
          }
        />
      )}
    </div>
  )
}

function Tile({
  n,
  label,
  tone,
}: {
  n: number | string
  label: React.ReactNode
  tone?: 'alert' | 'warn' | 'good'
}) {
  return (
    <div className={`stat ${tone ?? ''}`}>
      <div className="n">{n}</div>
      <div className="l">
        {tone && <span className="dot" aria-hidden="true" />}
        {label}
      </div>
    </div>
  )
}
