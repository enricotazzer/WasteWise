import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { type FormEvent, useMemo, useState } from 'react'

import { api, type Unit } from '../../api'
import { Button, Card } from '../../ui'

const UNITS: Unit[] = ['pz', 'kg', 'g', 'l', 'ml']

/**
 * Manual entry, and the only path for loose produce.
 *
 * Fruit and vegetables carry no barcode and are the largest waste category, so this screen
 * is not a fallback for the scanner — it's the half of the shop the scanner can't reach.
 * The recents strip exists because re-adding the same vegetables every week is the single
 * most repeated action in the app.
 */
export function AddItem() {
  const qc = useQueryClient()
  const [search, setSearch] = useState('')
  const [productId, setProductId] = useState<number | ''>('')
  const [quantity, setQuantity] = useState('1')
  const [unit, setUnit] = useState<Unit | ''>('')
  const [price, setPrice] = useState('')
  const [expiry, setExpiry] = useState('')
  const [flash, setFlash] = useState<string | null>(null)

  const { data: products = [] } = useQuery({
    queryKey: ['products', search],
    queryFn: () => api.products(search || undefined),
  })

  const { data: recents = [] } = useQuery({
    queryKey: ['recentProducts'],
    queryFn: api.recentProducts,
  })

  const quickAdd = useMutation({
    mutationFn: (product: { product_id: number; product_name: string }) =>
      api.createItem({ product_id: product.product_id, quantity: 1 }),
    onSuccess: (item) => {
      qc.invalidateQueries({ queryKey: ['items'] })
      qc.invalidateQueries({ queryKey: ['stats'] })
      qc.invalidateQueries({ queryKey: ['recentProducts'] })
      setFlash(`${item.product_name} aggiunto`)
    },
  })

  const selected = useMemo(
    () => products.find((p) => p.id === productId),
    [products, productId],
  )

  const create = useMutation({
    mutationFn: () =>
      api.createItem({
        product_id: Number(productId),
        quantity: Number(quantity),
        unit: (unit || undefined) as Unit | undefined,
        unit_price_eur: price ? Number(price) : null,
        expiry_date: expiry || null,
      }),
    onSuccess: (item) => {
      qc.invalidateQueries({ queryKey: ['items'] })
      qc.invalidateQueries({ queryKey: ['stats'] })
      setFlash(
        `${item.product_name} aggiunto` +
          (item.expiry_date ? ` · scade il ${formatDate(item.expiry_date)}` : ''),
      )
      // Keep the product selected: adding several units of the same thing is the common
      // case right after a shop.
      setQuantity('1')
      setPrice('')
      setExpiry('')
    },
  })

  function submit(e: FormEvent) {
    e.preventDefault()
    if (productId === '') return
    setFlash(null)
    create.mutate()
  }

  return (
    <>
      {recents.length > 0 && (
        <Card title="Ricompra">
          <div className="recents">
            {recents.map((r) => (
              <button
                key={r.product_id}
                className="chip"
                disabled={quickAdd.isPending}
                onClick={() =>
                  quickAdd.mutate({ product_id: r.product_id, product_name: r.product_name })
                }
              >
                {r.product_name}
                <em>×{r.times_bought}</em>
              </button>
            ))}
          </div>
          <div className="hint">Un tocco aggiunge 1 unità, senza prezzo né scadenza.</div>
        </Card>
      )}

      <form className="card" onSubmit={submit}>
        {flash && <div className="ok">{flash}</div>}
        {create.isError && (
          <div className="error">
            {create.error instanceof Error ? create.error.message : 'Errore'}
          </div>
        )}

        <div className="field">
          <label htmlFor="search">Cerca prodotto</label>
          <input
            id="search"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="es. mozzarella"
          />
        </div>

        <div className="field">
          <label htmlFor="product">Prodotto</label>
          <select
            id="product"
            value={productId}
            onChange={(e) => {
              setProductId(e.target.value ? Number(e.target.value) : '')
              setUnit('')
            }}
            required
          >
            <option value="">— scegli —</option>
            {products.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name_it}
              </option>
            ))}
          </select>
          {products.length === 0 && (
            <div className="hint">Nessun prodotto trovato. Prova un altro termine di ricerca.</div>
          )}
        </div>

        <div className="row field">
          <div>
            <label htmlFor="qty">Quantità</label>
            <input
              id="qty"
              type="number"
              step="0.001"
              min="0.001"
              value={quantity}
              onChange={(e) => setQuantity(e.target.value)}
              required
            />
          </div>
          <div>
            <label htmlFor="unit">Unità</label>
            <select id="unit" value={unit} onChange={(e) => setUnit(e.target.value as Unit)}>
              <option value="">{selected ? selected.default_unit : 'auto'}</option>
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
            <label htmlFor="price">Prezzo unitario €</label>
            <input
              id="price"
              type="number"
              step="0.01"
              min="0"
              value={price}
              onChange={(e) => setPrice(e.target.value)}
              placeholder="opzionale"
            />
          </div>
          <div>
            <label htmlFor="expiry">Scadenza</label>
            <input
              id="expiry"
              type="date"
              value={expiry}
              onChange={(e) => setExpiry(e.target.value)}
            />
          </div>
        </div>

        <Button variant="primary" type="submit" disabled={productId === '' || create.isPending}>
          {create.isPending ? 'Aggiungo…' : 'Aggiungi'}
        </Button>

        <div className="hint">
          Il prezzo alimenta il calcolo degli euro sprecati: senza, l'articolo conta solo come
          numero. Se lasci vuota la scadenza viene stimata dalla vita media del prodotto.
        </div>
      </form>
    </>
  )
}

function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString('it-IT', { day: 'numeric', month: 'short' })
}
