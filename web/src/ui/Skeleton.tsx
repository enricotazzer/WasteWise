import type { CSSProperties } from 'react'

interface Props {
  /** Any CSS length. Defaults to filling the parent. */
  width?: string
  height?: string
  radius?: string
  className?: string
}

/**
 * A loading placeholder that occupies the space its content will occupy.
 *
 * This is not decoration. Returning `null` while a query resolves — which is what the KPI
 * row used to do — means the row pops in and shoves the board down under the user's thumb
 * mid-tap. The point of a skeleton is that the layout never moves, so its dimensions have to
 * match the real thing rather than be approximately right.
 */
export function Skeleton({ width = '100%', height = '1em', radius, className }: Props) {
  const style: CSSProperties = { width, height }
  if (radius) style.borderRadius = radius
  return (
    <span
      className={className ? `skeleton ${className}` : 'skeleton'}
      style={style}
      aria-hidden="true"
    />
  )
}

/**
 * The KPI row's own skeleton. Lives here rather than in the food domain because the tile
 * geometry is the primitive's, and the dashboard's KPI row will want the identical shape.
 */
export function SkeletonTiles({ count = 4 }: { count?: number }) {
  return (
    <div className="stats" aria-busy="true" aria-label="Caricamento indicatori">
      {Array.from({ length: count }, (_, i) => (
        <div className="stat" key={i}>
          {/* Heights are the rendered metrics of `.stat .n` and `.stat .l`, not round
              numbers — an approximately-right skeleton still shifts the page. */}
          <Skeleton width="2.6em" height="31px" />
          <Skeleton width="72%" height="16px" />
        </div>
      ))}
    </div>
  )
}
