import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useCallback, useState } from 'react'

import { api, type ScanResult, type Unit } from '../api'
import { beep } from '../lib/barcode'
import { Scanner } from './Scanner'

const UNITS: Unit[] = ['pz', 'kg', 'g', 'l', 'ml']

interface SessionItem {
  name: string
  quantity: number
  unit: Unit
  method: string
}

/**
 * The scan flow: camera -> resolve -> confirm -> inventory.
 *
 * Everything a scan can produce is handled here, including the cases where nothing is
 * known: an unknown barcode opens the naming form instead of an error, because binding it
 * once is what makes every future scan of that product instant.
 */
export function ScanScreen() {
  const qc = useQueryClient()
  const [scanning, setScanning] = useState(false)
  const [pending, setPending] = useState<ScanResult | null>(null)
  const [session, setSession] = useState<SessionItem[]>([])
  const [error, setError] = useState<string | null>(null)

  const { data: stats } = useQuery({ queryKey: ['scanStats'], queryFn: api.scanStats })

  const resolve = useMutation({
    mutationFn: api.scan,
    onSuccess: (result) => {
      beep(result.product_id ? 'hit' : 'miss')
      setError(null)
      setPending(result)
    },
    onError: (err) => setError(err instanceof Error ? err.message : 'Errore di lettura'),
  })

  // useCallback: Scanner's effect depends on this, and a new identity every render would
  // reopen the camera continuously.
  const onDetected = useCallback(
    (barcode: string) => {
      if (resolve.isPending) return
      resolve.mutate(barcode)
    },
    [resolve],
  )

  function finish(item: SessionItem) {
    setSession((s) => [item, ...s])
    setPending(null)
    qc.invalidateQueries({ queryKey: ['items'] })
    qc.invalidateQueries({ queryKey: ['stats'] })
    qc.invalidateQueries({ queryKey: ['scanStats'] })
    qc.invalidateQueries({ queryKey: ['recentProducts'] })
  }

  return (
    <div className="scan-screen">
      {scanning ? (
        <Scanner
          onDetected={onDetected}
          paused={pending !== null || resolve.isPending}
          onClose={() => setScanning(false)}
        />
      ) : (
        <div className="card">
          <button className="primary big" onClick={() => setScanning(true)}>
            Scansiona la spesa
          </button>
          <div className="hint">
            La fotocamera resta aperta: scansiona un prodotto dopo l'altro mentre svuoti le
            borse. Frutta e verdura sfusa non hanno codice — usa <strong>Aggiungi</strong>.
          </div>
          {stats && stats.total_scans > 0 && (
            <div className="scan-stats">
              <strong>{formatPercent(stats.zero_typing_rate)}</strong> dei codici risolti senza
              digitare · {stats.total_scans} scansioni · {stats.unresolved} da nominare
            </div>
          )}
        </div>
      )}

      {error && <div className="error">{error}</div>}
      {resolve.isPending && <div className="card muted">Cerco il prodotto…</div>}

      {pending && (
        <ConfirmSheet
          result={pending}
          onCancel={() => setPending(null)}
          onDone={finish}
        />
      )}

      {session.length > 0 && (
        <div className="card">
          <h2>Aggiunti ora ({session.length})</h2>
          <ul className="session-list">
            {session.map((s, i) => (
              <li key={i}>
                <span>{s.name}</span>
                <span className="muted">
                  {s.quantity} {s.unit} · {s.method}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}

function ConfirmSheet({
  result,
  onCancel,
  onDone,
}: {
  result: ScanResult
  onCancel: () => void
  onDone: (item: SessionItem) => void
}) {
  const known = result.product_id !== null
  return known ? (
    <AddPanel result={result} onCancel={onCancel} onDone={onDone} />
  ) : (
    <BindPanel result={result} onCancel={onCancel} onDone={onDone} />
  )
}

/** Known product: quantity, optional price, one tap. */
function AddPanel({
  result,
  onCancel,
  onDone,
}: {
  result: ScanResult
  onCancel: () => void
  onDone: (item: SessionItem) => void
}) {
  const [quantity, setQuantity] = useState(String(result.suggested_quantity ?? 1))
  const [unit, setUnit] = useState<Unit>(result.suggested_unit ?? 'pz')
  const [price, setPrice] = useState('')
  const [expiry, setExpiry] = useState('')

  const add = useMutation({
    mutationFn: () =>
      api.addFromScan({
        barcode: result.barcode,
        product_id: result.product_id!,
        quantity: Number(quantity),
        unit,
        unit_price_eur: price ? Number(price) : null,
        expiry_date: expiry || null,
      }),
    onSuccess: () =>
      onDone({
        name: result.product_name ?? result.barcode,
        quantity: Number(quantity),
        unit,
        method: result.match_method,
      }),
  })

  return (
    <div className="card scan-sheet">
      <div className="scan-sheet-head">
        <div>
          <h2>{result.product_name}</h2>
          <div className="muted">
            {result.off_brand && <>{result.off_brand} · </>}
            {result.category_name}
            {result.off_quantity && <> · {result.off_quantity}</>}
          </div>
        </div>
        <span className={`pill method-${result.match_method}`}>{result.match_method}</span>
      </div>

      <div className="row field">
        <div>
          <label htmlFor="sq">Quantità</label>
          <input
            id="sq"
            type="number"
            step="0.001"
            min="0.001"
            value={quantity}
            onChange={(e) => setQuantity(e.target.value)}
          />
        </div>
        <div>
          <label htmlFor="su">Unità</label>
          <select id="su" value={unit} onChange={(e) => setUnit(e.target.value as Unit)}>
            {UNITS.map((u) => (
              <option key={u} value={u}>
                {u}
              </option>
            ))}
          </select>
        </div>
      </div>

      <div className="row field">
        <div>
          <label htmlFor="sp">Prezzo €</label>
          <input
            id="sp"
            type="number"
            step="0.01"
            min="0"
            inputMode="decimal"
            value={price}
            onChange={(e) => setPrice(e.target.value)}
            placeholder="opzionale"
          />
        </div>
        <div>
          <label htmlFor="se">Scadenza</label>
          <input id="se" type="date" value={expiry} onChange={(e) => setExpiry(e.target.value)} />
        </div>
      </div>

      {add.isError && <div className="error">Non sono riuscito ad aggiungerlo.</div>}

      <div className="scan-sheet-actions">
        <button onClick={onCancel}>Annulla</button>
        <button className="primary" onClick={() => add.mutate()} disabled={add.isPending}>
          {add.isPending ? 'Aggiungo…' : 'Aggiungi'}
        </button>
      </div>
    </div>
  )
}

/**
 * Unknown barcode: name it once.
 *
 * This is the path for store-weighed produce labels and small local brands Open Food Facts
 * has never heard of. It costs one round of typing and then never again — the binding is
 * permanent, so the same label resolves instantly on every future shop.
 */
function BindPanel({
  result,
  onCancel,
  onDone,
}: {
  result: ScanResult
  onCancel: () => void
  onDone: (item: SessionItem) => void
}) {
  const [name, setName] = useState(result.off_name ?? '')
  const [categoryId, setCategoryId] = useState<number | ''>('')
  const [existingId, setExistingId] = useState<number | ''>('')
  const [search, setSearch] = useState('')

  const { data: categories = [] } = useQuery({ queryKey: ['categories'], queryFn: api.categories })
  const { data: matches = [] } = useQuery({
    queryKey: ['products', search],
    queryFn: () => api.products(search || undefined),
    enabled: search.length > 1,
  })

  const bind = useMutation({
    mutationFn: async () => {
      const bound = existingId
        ? await api.bindBarcode(result.barcode, { product_id: Number(existingId) })
        : await api.bindBarcode(result.barcode, {
            new_product_name: name.trim(),
            category_id: Number(categoryId),
          })
      await api.addFromScan({
        barcode: result.barcode,
        product_id: bound.product_id!,
        quantity: 1,
      })
      return bound
    },
    onSuccess: (bound) =>
      onDone({
        name: bound.product_name ?? name,
        quantity: 1,
        unit: bound.suggested_unit ?? 'pz',
        method: 'manual',
      }),
  })

  const canSubmit = existingId !== '' || (name.trim() !== '' && categoryId !== '')

  return (
    <div className="card scan-sheet">
      <div className="scan-sheet-head">
        <div>
          <h2>Codice sconosciuto</h2>
          <div className="muted">{result.barcode}</div>
        </div>
        <span className="pill method-unresolved">nuovo</span>
      </div>

      <div className="hint">
        {result.barcode.startsWith('2')
          ? 'È un\'etichetta del supermercato (prodotto sfuso pesato). Non esiste in nessun database: dagli un nome una volta e verrà riconosciuta per sempre.'
          : 'Open Food Facts non conosce questo codice. Collegalo una volta e non dovrai più digitarlo.'}
      </div>

      <div className="field">
        <label htmlFor="bs">Collega a un prodotto esistente</label>
        <input
          id="bs"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="cerca… es. mele"
        />
        {matches.length > 0 && (
          <select
            value={existingId}
            onChange={(e) => setExistingId(e.target.value ? Number(e.target.value) : '')}
          >
            <option value="">— nessuno, ne creo uno nuovo —</option>
            {matches.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name_it}
              </option>
            ))}
          </select>
        )}
      </div>

      {existingId === '' && (
        <>
          <div className="field">
            <label htmlFor="bn">Nome nuovo prodotto</label>
            <input
              id="bn"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="es. Mele Golden sfuse"
            />
          </div>
          <div className="field">
            <label htmlFor="bc">Categoria</label>
            <select
              id="bc"
              value={categoryId}
              onChange={(e) => setCategoryId(e.target.value ? Number(e.target.value) : '')}
            >
              <option value="">— scegli —</option>
              {categories.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name_it}
                </option>
              ))}
            </select>
            <div className="hint">
              La categoria determina la scadenza stimata, quindi vale la pena azzeccarla.
            </div>
          </div>
        </>
      )}

      {bind.isError && <div className="error">Non sono riuscito a collegarlo.</div>}

      <div className="scan-sheet-actions">
        <button onClick={onCancel}>Salta</button>
        <button
          className="primary"
          onClick={() => bind.mutate()}
          disabled={!canSubmit || bind.isPending}
        >
          {bind.isPending ? 'Collego…' : 'Collega e aggiungi'}
        </button>
      </div>
    </div>
  )
}

function formatPercent(value: number | null): string {
  return value === null ? '—' : `${Math.round(value * 100)}%`
}
