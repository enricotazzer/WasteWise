/**
 * The primitive layer. Nothing in here knows what food is.
 *
 * This is the contract every screen — kitchen, dashboard, and whatever the bills module
 * turns out to be — builds against. If a component here starts needing an `InventoryItem`,
 * it belongs in a domain instead.
 *
 * PascalCase files are components; lowercase ones are the pure shapes and helpers behind
 * them, importable without pulling React in.
 */
export { Badge } from './Badge'
export { Button } from './Button'
export { Card } from './Card'
export { DateRange } from './DateRange'
export { RANGE_OPTIONS, resolveRange } from './ranges'
export type { RangeKey, ResolvedRange } from './ranges'
export { EmptyState } from './EmptyState'
export { Icon } from './Icon'
export type { IconName } from './Icon'
export { formatKg, massCoverageCaption } from './massFigure'
export type { MassFigure } from './massFigure'
export { Money } from './Money'
export { coverageCaption, formatEur } from './moneyFigure'
export type { MoneyFigure } from './moneyFigure'
export { Segmented } from './Segmented'
export type { SegmentedOption } from './Segmented'
export { Skeleton, SkeletonTiles } from './Skeleton'
export { Tile } from './Tile'
export type { Tone } from './Tile'
