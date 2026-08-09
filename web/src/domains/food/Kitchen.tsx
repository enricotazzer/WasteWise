import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'

import { api, type InventoryItem } from '../../api'
import { BANDS, type Band, groupByUrgency } from '../../lib/urgency'
import { Badge, Button, EmptyState, Icon, Skeleton, formatEur } from '../../ui'
import { StatsBar } from './StatsBar'

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
 *
 * This is also where the KPI row lives now. It used to sit above the tabs on every screen;
 * the numbers it reports are a summary of exactly this board, and nowhere else did they
 * earn the three rows they cost.
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

  if (isLoading) return <BoardSkeleton />

  if (items.length === 0) {
    return (
      <>
        <StatsBar />
        <EmptyState
          icon="home"
          title="Niente in casa."
          description={
            <>
              Aggiungi la spesa dalla scheda <strong>Aggiungi</strong> per iniziare a raccogliere
              dati.
            </>
          }
        />
      </>
    )
  }

  const groups = groupByUrgency(items)

  const actions = (item: InventoryItem) => (
    <div className="actions">
      <Button
        variant="icon"
        icon="check"
        title="Consumato tutto"
        aria-label={`${item.product_name}: consumato`}
        disabled={logEvent.isPending}
        onClick={() => logEvent.mutate({ id: item.id, event_type: 'consumed' })}
      />
      {/* The one glyph worth keeping. "½" states the fraction exactly; every icon that
          tries to draw "half used" is a picture you have to decode first. */}
      <button
        className="iconbtn"
        title="Usato a metà"
        aria-label={`${item.product_name}: usato a metà`}
        disabled={logEvent.isPending}
        onClick={() => logEvent.mutate({ id: item.id, event_type: 'partial', fraction_used: 0.5 })}
      >
        ½
      </button>
      <Button
        variant="icon"
        tone="danger"
        icon="trash"
        title="Buttato"
        aria-label={`${item.product_name}: buttato`}
        disabled={logEvent.isPending}
        onClick={() => setWasting(item)}
      />
    </div>
  )

  return (
    <>
      <StatsBar />

      <div className="board">
        {BANDS.map((band) => (
          <UrgencyColumn key={band.key} band={band} items={groups[band.key]} actions={actions} />
        ))}
      </div>

      {groups.undated.length > 0 && (
        <details className="undated">
          {/* Pasta, detergent, tinned goods. Real inventory, but they never need a decision,
              so they'd only dilute the columns that do. */}
          <summary>
            <Icon name="chevron-right" size={16} className="undated-caret" />
            Senza scadenza ({groups.undated.length})
          </summary>
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
        <Icon name={band.icon} size={15} />
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
          {item.unit_price_eur && ` · ${formatEur(item.unit_price_eur)}`}
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
  if (d < 0) return <Badge tone="danger">scaduto da {-d}g</Badge>
  if (d === 0) return <Badge tone="danger">scade oggi</Badge>
  if (d <= 3) return <Badge tone="warn">{d}g</Badge>
  return <Badge tone="neutral">{d}g</Badge>
}

/**
 * Four columns of card-shaped placeholders. Loading the board used to be one centred
 * "Caricamento…" line, so the whole page height changed the moment the query landed.
 */
function BoardSkeleton() {
  return (
    <>
      <StatsBar />
      <div className="board" aria-busy="true" aria-label="Caricamento dispensa">
        {BANDS.map((band) => (
          <section className="column" key={band.key}>
            <header className="column-head">
              <Skeleton width="10em" height="var(--text-md)" />
            </header>
            {[0, 1, 2].map((i) => (
              <Skeleton
                key={i}
                height="58px"
                radius="var(--radius-sm)"
                className="skeleton-item"
              />
            ))}
          </section>
        ))}
      </div>
    </>
  )
}

function formatQty(item: InventoryItem): string {
  // Trim trailing zeros from the Numeric(10,3) string: "2.000" -> "2"
  const n = Number(item.quantity)
  return `${n} ${item.unit}`
}
