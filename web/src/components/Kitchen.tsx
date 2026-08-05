import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'

import { api, type InventoryItem } from '../api'
import { BANDS, type Band, groupByUrgency } from '../lib/urgency'

/**
 * The Kitchen screen — the highest-friction part of the whole system, and the one every
 * downstream module depends on. Every tap here produces a training label for the survival
 * model and a row in the waste ledger.
 *
 * Laid out as an urgency board rather than one flat list: with 70+ active items a single
 * ordered list buries the four things that actually need eating tonight under everything
 * that's fine for a fortnight. Columns on a laptop, stacked sections on a phone.
 *
 * Design target from PLAN.md: under 10 seconds a day, no typing, thumb-reachable.
 * Waste is the only action that asks a follow-up question, because "why" is the one field
 * that can't be reconstructed later.
 */
export function Kitchen() {
  const qc = useQueryClient()
  const [wasting, setWasting] = useState<InventoryItem | null>(null)

  const { data: items = [], isLoading } = useQuery({
    queryKey: ['items'],
    queryFn: () => api.items(true),
  })

  const logEvent = useMutation({
    mutationFn: ({
      id,
      event_type,
      fraction_used,
      reason,
    }: {
      id: number
      event_type: 'consumed' | 'wasted' | 'partial'
      fraction_used?: number
      reason?: string | null
    }) => api.logEvent(id, { event_type, fraction_used, reason }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['items'] })
      qc.invalidateQueries({ queryKey: ['stats'] })
      qc.invalidateQueries({ queryKey: ['fridgeGraph'] })
      qc.invalidateQueries({ queryKey: ['suggestions'] })
    },
  })

  if (isLoading) return <div className="empty">Caricamento…</div>

  if (items.length === 0) {
    return (
      <div className="empty">
        Niente in casa.
        <br />
        Aggiungi la spesa dalla scheda <strong>Aggiungi</strong> per iniziare a raccogliere dati.
      </div>
    )
  }

  const groups = groupByUrgency(items)

  const actions = (item: InventoryItem) => (
    <div className="actions">
      <button
        className="iconbtn"
        title="Consumato tutto"
        aria-label={`${item.product_name}: consumato`}
        disabled={logEvent.isPending}
        onClick={() => logEvent.mutate({ id: item.id, event_type: 'consumed' })}
      >
        ✓
      </button>
      <button
        className="iconbtn"
        title="Usato a metà"
        aria-label={`${item.product_name}: usato a metà`}
        disabled={logEvent.isPending}
        onClick={() => logEvent.mutate({ id: item.id, event_type: 'partial', fraction_used: 0.5 })}
      >
        ½
      </button>
      <button
        className="iconbtn danger"
        title="Buttato"
        aria-label={`${item.product_name}: buttato`}
        disabled={logEvent.isPending}
        onClick={() => setWasting(item)}
      >
        ✕
      </button>
    </div>
  )

  return (
    <>
      <div className="board">
        {BANDS.map((band) => (
          <UrgencyColumn key={band.key} band={band} items={groups[band.key]} actions={actions} />
        ))}
      </div>

      {groups.undated.length > 0 && (
        <details className="undated">
          {/* Pasta, detergent, tinned goods. Real inventory, but they never need a decision,
              so they'd only dilute the columns that do. */}
          <summary>Senza scadenza ({groups.undated.length})</summary>
          <div className="grid">
            {groups.undated.map((item) => (
              <ItemCard key={item.id} item={item} actions={actions} />
            ))}
          </div>
        </details>
      )}

      {wasting && (
        <WasteSheet
          item={wasting}
          onClose={() => setWasting(null)}
          onPick={(reason) => {
            logEvent.mutate({ id: wasting.id, event_type: 'wasted', reason })
            setWasting(null)
          }}
        />
      )}
    </>
  )
}

// Urgent columns are never truncated — you must see everything that needs a decision. The
// calm ones are capped, because 42 cards of "fine for a month" is scrolling that buries the
// four items the board exists to surface.
const CALM_PREVIEW = 8

function UrgencyColumn({
  band,
  items,
  actions,
}: {
  band: Band
  items: InventoryItem[]
  actions: (item: InventoryItem) => React.ReactNode
}) {
  const [expanded, setExpanded] = useState(false)
  const capped = band.tone === 'calm' && !expanded && items.length > CALM_PREVIEW
  const shown = capped ? items.slice(0, CALM_PREVIEW) : items

  return (
    <section
      className={`column ${band.tone} ${items.length === 0 ? 'is-empty' : ''}`}
      aria-label={`${band.title}: ${items.length}`}
    >
      {/* Icon + title + count, so the band is identified three ways over. Colour is the
          fourth cue, never the only one. */}
      <header className="column-head">
        <span className="icon" aria-hidden="true">
          {band.icon}
        </span>
        <span className="title">{band.title}</span>
        <span className="count">{items.length}</span>
      </header>

      {items.length === 0 ? (
        <div className="column-empty">{band.empty}</div>
      ) : (
        shown.map((item) => <ItemCard key={item.id} item={item} actions={actions} />)
      )}

      {capped && (
        <button className="column-more" onClick={() => setExpanded(true)}>
          mostra altri {items.length - CALM_PREVIEW}
        </button>
      )}
      {expanded && items.length > CALM_PREVIEW && (
        <button className="column-more" onClick={() => setExpanded(false)}>
          mostra meno
        </button>
      )}
    </section>
  )
}

function ItemCard({
  item,
  actions,
}: {
  item: InventoryItem
  actions: (item: InventoryItem) => React.ReactNode
}) {
  return (
    <div className="item">
      <div className="body">
        <div className="name">
          {item.product_name} <ExpiryPill item={item} />
        </div>
        <div className="meta">
          {formatQty(item)}
          {item.unit_price_eur && ` · €${item.unit_price_eur}`}
          {item.fraction_remaining < 1 && ` · ${Math.round(item.fraction_remaining * 100)}% rimasto`}
          {item.opened_at && ' · aperto'}
        </div>
      </div>
      {actions(item)}
    </div>
  )
}

function WasteSheet({
  item,
  onPick,
  onClose,
}: {
  item: InventoryItem
  onPick: (reason: string) => void
  onClose: () => void
}) {
  const { data: reasons = [] } = useQuery({
    queryKey: ['waste-reasons'],
    queryFn: api.wasteReasons,
    staleTime: Infinity,
  })

  return (
    <div className="backdrop" onClick={onClose} role="presentation">
      <div className="sheet" onClick={(e) => e.stopPropagation()}>
        <h3>{item.product_name}</h3>
        <div className="hint">Perché è stato buttato?</div>
        <div className="reasons">
          {reasons.map((r) => (
            <button key={r.code} onClick={() => onPick(r.code)}>
              {r.label}
            </button>
          ))}
        </div>
      </div>
    </div>
  )
}

function ExpiryPill({ item }: { item: InventoryItem }) {
  const d = item.days_to_expiry
  if (d === null) return null
  if (d < 0) return <span className="pill red">scaduto da {-d}g</span>
  if (d === 0) return <span className="pill red">scade oggi</span>
  if (d <= 3) return <span className="pill amber">{d}g</span>
  return <span className="pill neutral">{d}g</span>
}

function formatQty(item: InventoryItem): string {
  // Trim trailing zeros from the Numeric(10,3) string: "2.000" -> "2"
  const n = Number(item.quantity)
  return `${n} ${item.unit}`
}
