import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'

import { api, type RecipeMatch } from '../api'
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

  if (graphLoading || matchesLoading) return <div className="empty">Caricamento…</div>

  if (!graph || graph.nodes.length === 0) {
    return (
      <div className="empty">
        Niente in casa.
        <br />
        Aggiungi la spesa e qui compariranno le ricette che salvano quello che sta scadendo.
      </div>
    )
  }

  return (
    <div className="recipes-layout">
      <div className="card graph-card">
        <h2>La tua dispensa</h2>
        <div className="hint">
          Ogni collegamento è una ricetta che usa entrambi gli ingredienti. Le dispense
          (olio, sale, aglio) sono escluse: comparendo quasi ovunque renderebbero il grafo
          illeggibile.
        </div>
        <FridgeGraph
          data={graph}
          highlight={selected?.use_ids}
          onSelectProduct={() => setSelected(null)}
        />
      </div>

      <div className="recipes-side">
        {unlocks.length > 0 && (
          <div className="card unlock-card">
            <h2>Con una cosa sola</h2>
            <div className="hint">
              Comprando questo, altre ricette diventano cucinabili subito. È la domanda a cui
              il conteggio degli ingredienti non sa rispondere.
            </div>
            {unlocks.map((u) => (
              <div key={u.product_id} className="unlock">
                <div className="unlock-head">
                  <strong>{u.name}</strong>
                  <span className="pill">+{u.unlocks} ricette</span>
                </div>
                <div className="muted">
                  salva {u.rescues.slice(0, 4).join(', ')}
                  {u.rescues.length > 4 && ` +${u.rescues.length - 4}`}
                </div>
              </div>
            ))}
          </div>
        )}

        <div className="card">
          <h2>Cosa cucino stasera</h2>
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
                      <span className="pill">cucinabile</span>
                    ) : (
                      <span className="pill neutral">manca {m.missing.length}</span>
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
        </div>
      </div>
    </div>
  )
}
