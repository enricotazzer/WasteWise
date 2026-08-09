import { useMemo, useState } from 'react'

import type { FridgeGraph as GraphData, GraphNode } from '../../api'
import { layout } from '../../lib/forceLayout'
import { EmptyState } from '../../ui'

const W = 640
const H = 460

interface Props {
  data: GraphData
  /** Product ids belonging to the selected recipe. Everything else recedes. */
  highlight?: number[]
  onSelectProduct?: (productId: number | null) => void
}

/**
 * The fridge as a graph: products you own, joined where a recipe uses both.
 *
 * **Emphasis, not categorical.** At-risk items take the alarm colours; everything else is
 * the de-emphasis gray. Colouring 30 products with 30 hues would be unreadable and would
 * bury the four that actually matter — which is precisely the thing this screen exists to
 * surface.
 *
 * The ranked list beside this is the table twin: every value here is also readable there, so
 * nothing is gated behind a hover.
 */
export function FridgeGraph({ data, highlight, onSelectProduct }: Props) {
  const [hovered, setHovered] = useState<GraphNode | null>(null)

  // Connected nodes only. An unconnected product has nothing pulling it back, so repulsion
  // flings it into a corner and the whole cluster gets squeezed into the opposite one —
  // half the canvas wasted to show four dots that carry no relationship. They're already
  // named in the note underneath, which is the more useful place for them.
  const connected = useMemo(() => data.nodes.filter((n) => n.degree > 0), [data.nodes])

  const positions = useMemo(
    () =>
      layout(
        connected.map((n) => n.product_id),
        data.edges,
        { width: W, height: H },
      ),
    [connected, data.edges],
  )

  if (data.nodes.length === 0) {
    return (
      <EmptyState
        title="Niente in casa da collegare."
        description="Aggiungi qualcosa e il grafo si costruisce da solo."
      />
    )
  }

  const highlighted = highlight && highlight.length > 0 ? new Set(highlight) : null
  const maxDegree = Math.max(1, ...connected.map((n) => n.degree))

  // Radius carries degree — how many of your other ingredients this one cooks with. Kept in
  // a narrow band so a hub never swamps the canvas.
  const radiusOf = (n: GraphNode) => 6 + (n.degree / maxDegree) * 9

  // Direct-label selectively: everything at risk (that's the point of the screen) plus the
  // few biggest hubs. Labelling all 30 nodes produces an unreadable pile in the middle —
  // the tooltip and the ranked list carry the rest.
  const labelled = new Set([
    ...connected.filter((n) => n.at_risk).map((n) => n.product_id),
    ...[...connected]
      .sort((a, b) => b.degree - a.degree)
      .slice(0, 5)
      .map((n) => n.product_id),
  ])

  return (
    <div className="graph-wrap">
      <svg
        viewBox={`0 0 ${W} ${H}`}
        className="graph"
        role="img"
        aria-label={`Grafo della dispensa: ${data.nodes.length} ingredienti, ${data.edges.length} collegamenti`}
      >
        <g className="graph-edges">
          {data.edges.map((e, i) => {
            const a = positions.get(e.source)
            const b = positions.get(e.target)
            if (!a || !b) return null
            const dim = highlighted && !(highlighted.has(e.source) && highlighted.has(e.target))
            return (
              <line
                key={i}
                x1={a.x}
                y1={a.y}
                x2={b.x}
                y2={b.y}
                strokeWidth={Math.min(3, 1 + e.weight * 0.35)}
                className={dim ? 'edge dim' : 'edge'}
              />
            )
          })}
        </g>

        <g className="graph-nodes">
          {connected.map((n) => {
            const p = positions.get(n.product_id)
            if (!p) return null
            const r = radiusOf(n)
            const dim = highlighted ? !highlighted.has(n.product_id) : false
            const tone = n.at_risk
              ? (n.days_to_expiry ?? 0) < 0
                ? 'critical'
                : 'serious'
              : 'calm'

            return (
              <g
                key={n.product_id}
                className={`node ${tone} ${dim ? 'dim' : ''}`}
                onMouseEnter={() => setHovered(n)}
                onMouseLeave={() => setHovered(null)}
                onFocus={() => setHovered(n)}
                onBlur={() => setHovered(null)}
                onClick={() => onSelectProduct?.(n.product_id)}
                tabIndex={0}
                role="button"
                aria-label={`${n.name}, in ${n.recipe_count} ricette${
                  n.days_to_expiry !== null ? `, ${n.days_to_expiry} giorni` : ''
                }`}
              >
                {/* Invisible generous hit target — the visible dot is 12-30px but a pointer
                    shouldn't have to land dead-centre on it. */}
                <circle cx={p.x} cy={p.y} r={Math.max(16, r + 8)} className="hit" />
                <circle cx={p.x} cy={p.y} r={r} className="dot" />
                {labelled.has(n.product_id) && (
                  <text x={p.x} y={p.y - r - 5} className="label">
                    {n.name}
                  </text>
                )}
              </g>
            )
          })}
        </g>
      </svg>

      {hovered && (
        <div className="graph-tip" role="status">
          <strong>{hovered.name}</strong>
          <span>
            {hovered.days_to_expiry === null
              ? 'senza scadenza'
              : hovered.days_to_expiry < 0
                ? `scaduto da ${-hovered.days_to_expiry}g`
                : `${hovered.days_to_expiry}g alla scadenza`}
            {' · '}
            {hovered.recipe_count} ricette · si combina con {hovered.degree} dei tuoi ingredienti
          </span>
        </div>
      )}

      <div className="graph-legend">
        <span>
          <i className="swatch critical" /> scaduto
        </span>
        <span>
          <i className="swatch serious" /> entro 3 giorni
        </span>
        <span>
          <i className="swatch calm" /> il resto
        </span>
        <span className="muted">dimensione = quanti tuoi ingredienti combina</span>
      </div>

      {data.isolated.length > 0 && (
        <div className="hint">
          Non compaiono in nessuna ricetta del corpus: {data.isolated.join(', ')}. Di solito
          significa che manca un alias, non che l'ingrediente sia inutilizzabile.
        </div>
      )}
    </div>
  )
}
