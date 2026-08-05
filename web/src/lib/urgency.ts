import type { InventoryItem } from '../api'

/**
 * The urgency bands the Kitchen board is built from.
 *
 * Only the first two carry colour. "Questa settimana" and "Più avanti" are deliberately
 * neutral — this is the emphasis form: highlight the items that need a decision tonight and
 * let everything else recede. It also keeps green out of the urgency scale entirely, which
 * matters because the accent green and the muted gray measure only ΔE 10.8 apart and could
 * never have safely encoded two adjacent bands.
 */
export type BandKey = 'expired' | 'soon' | 'week' | 'later' | 'undated'

export interface Band {
  key: BandKey
  title: string
  icon: string
  /** Drives the column's border/heading treatment. Never the only cue. */
  tone: 'critical' | 'serious' | 'calm'
  empty: string
}

export const BANDS: Band[] = [
  {
    key: 'expired',
    title: 'Scaduti',
    icon: '⚠',
    tone: 'critical',
    empty: 'Niente scaduto.',
  },
  {
    key: 'soon',
    title: 'Entro 3 giorni',
    icon: '◔',
    tone: 'serious',
    empty: 'Niente in scadenza.',
  },
  {
    key: 'week',
    title: 'Questa settimana',
    icon: '·',
    tone: 'calm',
    empty: 'Niente questa settimana.',
  },
  {
    key: 'later',
    title: 'Più avanti',
    icon: '·',
    tone: 'calm',
    empty: 'Niente più avanti.',
  },
]

/** Boundaries are inclusive at both ends of each band; `null` means no expiry at all. */
export function bandOf(item: InventoryItem): BandKey {
  const d = item.days_to_expiry
  if (d === null) return 'undated'
  if (d < 0) return 'expired'
  if (d <= 3) return 'soon'
  if (d <= 7) return 'week'
  return 'later'
}

export function groupByUrgency(items: InventoryItem[]): Record<BandKey, InventoryItem[]> {
  const groups: Record<BandKey, InventoryItem[]> = {
    expired: [],
    soon: [],
    week: [],
    later: [],
    undated: [],
  }
  for (const item of items) groups[bandOf(item)].push(item)
  return groups
}
