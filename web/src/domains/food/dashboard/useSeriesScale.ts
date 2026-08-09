import { useQuery } from '@tanstack/react-query'
import { useMemo } from 'react'

import { api } from '../../../api'
import { buildScale, OTHER_KEY, useSeriesPalette, type SeriesScale } from '../../../ui/charts'

/* Which category is which colour — decided once, for the whole dashboard.
 *
 * THE RULE THIS IMPLEMENTS
 * Colour follows the entity, never its rank. A reader who learns that orange is Frutta must
 * not find orange meaning Pane after switching the period from 6 mesi to 3 mesi. The
 * tempting implementation — rank whatever the current chart is showing — is exactly the
 * recolour-on-filter bug, and it is invisible in testing because every individual render
 * looks correct.
 *
 * So the ranking is computed from a query the on-screen filters cannot reach: the whole
 * ledger, at the endpoint's maximum window, keyed only on the demo flag. Period and bucket
 * provably cannot repaint anything, because they are not inputs to it. Toggling demo data
 * *can*, and should: that is a different universe of rows, not a different view of one.
 *
 * WHY WASTE FIRST, THEN SPEND
 * The dashboard's subject is waste, so the categories that get a hue are the ones actually
 * wasted, ranked by entry count — a count, not a euro figure, because ranking by money
 * would quietly rank by price coverage instead. Leftover slots then go to the biggest
 * spenders, so the spend chart still shows identities rather than a wall of gray. One scale
 * across every chart on the screen: a category has one colour here, or it has none.
 */

const MAX_MONTHS = 36

export interface CategoryScale {
  scale: SeriesScale
  /** Italian display name for a category code, or the folded "Altro". */
  labelOf: (key: string) => string
  ready: boolean
}

export function useSeriesScale(includeDemo: boolean, enabled: boolean): CategoryScale {
  const palette = useSeriesPalette()

  const categories = useQuery({
    queryKey: ['categories'],
    queryFn: api.categories,
    staleTime: Infinity,
  })

  const scope = useQuery({
    queryKey: ['stats', 'by-category', 'palette-scope', includeDemo],
    queryFn: () => api.statsByCategory({ bucket: 'month', periods: MAX_MONTHS, includeDemo }),
    staleTime: 5 * 60_000,
    enabled,
  })

  const ranked = useMemo(() => {
    const rows = scope.data?.rows ?? []
    const waste = new Map<string, number>()
    const spend = new Map<string, number>()
    for (const row of rows) {
      waste.set(row.category_code, (waste.get(row.category_code) ?? 0) + row.waste.entries)
      spend.set(row.category_code, (spend.get(row.category_code) ?? 0) + row.spend.entries)
    }
    // Code as the last tie-break so two categories with identical counts can never swap
    // hues between renders — Map iteration order is stable, the API's row order is not.
    const byCount = (counts: Map<string, number>) => (a: string, b: string) =>
      (counts.get(b) ?? 0) - (counts.get(a) ?? 0) || a.localeCompare(b)

    const wasted = [...waste.keys()].filter((c) => (waste.get(c) ?? 0) > 0).sort(byCount(waste))
    const rest = [...spend.keys()].filter((c) => !wasted.includes(c)).sort(byCount(spend))
    return [...wasted, ...rest]
  }, [scope.data])

  const names = useMemo(() => {
    const map = new Map<string, string>()
    for (const c of categories.data ?? []) map.set(c.code, c.name_it)
    return map
  }, [categories.data])

  return useMemo(
    () => ({
      scale: buildScale(ranked, palette.series, palette.other),
      labelOf: (key) => (key === OTHER_KEY ? 'Altro' : (names.get(key) ?? key)),
      ready: scope.data !== undefined && categories.data !== undefined,
    }),
    [ranked, palette, names, scope.data, categories.data],
  )
}
