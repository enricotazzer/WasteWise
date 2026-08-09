import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'

import { api, type RecipeMatch } from '../../api'
import { Badge, Card, EmptyState, Skeleton } from '../../ui'
import { FridgeGraph } from './FridgeGraph'

/**
 * Cosa cucino — the graph explains, the list answers.
 *
 * The split matters. Ranking recipes by how much at-risk food they rescue is plain coverage
 * counting, and the graph adds nothing to that calculation. What the graph adds is the
 * *why*: select a recipe and its ingredients light up, so a suggestion stops being an opaque
 * verdict. It also produces the one thing counting can't — which single purchase would make
 * several at-risk recipes cookable at once.
 */
export function Recipes() {
  const [selected, setSelected] = useState<RecipeMatch | null>(null)

  const { data: graph, isLoading: graphLoading } = useQuery({
    queryKey: ['fridgeGraph'],
    queryFn: () => api.fridgeGraph(false),
  })
  const { data: matches = [], isLoading: matchesLoading } = useQuery({
    queryKey: ['suggestions'],
    queryFn: api.suggestions,
  })
  const { data: unlocks = [] } = useQuery({ queryKey: ['unlock'], queryFn: api.unlock })

  if (graphLoading || matchesLoading) return <RecipesSkeleton />

  if (!graph || graph.nodes.length === 0) {
    return (
      <EmptyState
        icon="pot"
        title="Niente in casa."
        description="Aggiungi la spesa e qui compariranno le ricette che salvano quello che sta scadendo."
      />
    )
  }

  return (
    <div className="recipes-layout">
      <Card
        title="La tua dispensa"
        hint="Ogni collegamento è una ricetta che usa entrambi gli ingredienti. Le dispense (olio, sale, aglio) sono escluse: comparendo quasi ovunque renderebbero il grafo illeggibile."
      >
        <FridgeGraph
          data={graph}
          highlight={selected?.use_ids}
          onSelectProduct={() => setSelected(null)}
        />
      </Card>

      <div className="recipes-side">
        {unlocks.length > 0 && (
          <Card
            title="Con una cosa sola"
            hint="Comprando questo, altre ricette diventano cucinabili subito. È la domanda a cui il conteggio degli ingredienti non sa rispondere."
          >
            {unlocks.map((u) => (
              <div key={u.product_id} className="unlock">
                <div className="unlock-head">
                  <strong>{u.name}</strong>
                  <Badge>+{u.unlocks} ricette</Badge>
                </div>
                <div className="muted">
                  salva {u.rescues.slice(0, 4).join(', ')}
                  {u.rescues.length > 4 && ` +${u.rescues.length - 4}`}
                </div>
              </div>
            ))}
          </Card>
        )}

        <Card title="Cosa cucino stasera">
          {matches.length === 0 ? (
            <div className="column-empty">
              Nessuna ricetta del corpus usa quello che hai in casa.
            </div>
          ) : (
            <ul className="match-list">
              {matches.map((m) => (
                <li
                  key={m.recipe_id}
                  className={`match ${selected?.recipe_id === m.recipe_id ? 'is-selected' : ''}`}
                >
                  <button
                    className="match-head"
                    onClick={() => setSelected(selected?.recipe_id === m.recipe_id ? null : m)}
                    aria-expanded={selected?.recipe_id === m.recipe_id}
                  >
                    <span className="match-title">{m.title}</span>
                    {m.cookable ? (
                      <Badge>cucinabile</Badge>
                    ) : (
                      <Badge tone="neutral">manca {m.missing.length}</Badge>
                    )}
                  </button>

                  <div className="match-meta">
                    {m.rescues.length > 0 && (
                      <span className="rescues">salva {m.rescues.join(', ')}</span>
                    )}
                    {m.missing.length > 0 && (
                      <span className="missing">manca {m.missing.join(', ')}</span>
                    )}
                    {m.total_minutes && <span className="muted">{m.total_minutes} min</span>}
                  </div>

                  {selected?.recipe_id === m.recipe_id && (
                    <div className="match-body">
                      <div className="muted">Usa: {m.uses.join(', ')}</div>
                      {m.instructions && <p>{m.instructions}</p>}
                      {m.servings && <div className="muted">Per {m.servings} persone</div>}
                    </div>
                  )}
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>
    </div>
  )
}

/**
 * Matches the real layout's two columns, so the graph card doesn't leap from the middle of
 * the page to the left half when the query lands.
 */
function RecipesSkeleton() {
  return (
    <div className="recipes-layout" aria-busy="true" aria-label="Caricamento ricette">
      <div className="card">
        <Skeleton width="9em" height="var(--text-lg)" />
        <Skeleton height="320px" radius="var(--radius-sm)" className="skeleton-item" />
      </div>
      <div className="recipes-side">
        <div className="card">
          <Skeleton width="11em" height="var(--text-lg)" />
          {[0, 1, 2, 3].map((i) => (
            <Skeleton key={i} height="34px" className="skeleton-item" />
          ))}
        </div>
      </div>
    </div>
  )
}
