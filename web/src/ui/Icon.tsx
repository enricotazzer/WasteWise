import sprite from './icons.svg?raw'

/**
 * Icons come from one sprite, inlined into the document once.
 *
 * The obvious version of this — leaving the sprite in `public/` and pointing `<use>` at
 * `/icons.svg#name` — renders nothing in Chromium: external references in `<use>` have
 * never been supported there, and they fail silently, as a 0×0 box with no console error.
 * Verified against the running app before rewriting it this way.
 *
 * So the sprite is imported as text at build time and injected once as a hidden `<svg>`.
 * That keeps one editable sprite file, costs no extra request, and makes the references
 * same-document — which is also what makes `currentColor` and the `.icon` stroke rule
 * inherit into the cloned symbols.
 */
export type IconName =
  | 'check'
  | 'half'
  | 'trash'
  | 'close'
  | 'plus'
  | 'warning'
  | 'clock'
  | 'dot'
  | 'home'
  | 'pot'
  | 'scan'
  | 'chart'
  | 'settings'
  | 'logout'
  | 'chevron-right'
  | 'chevron-down'
  | 'chevron-left'
  | 'trend-up'
  | 'trend-down'
  | 'calendar'

// Self-installing rather than an <IconSprite/> somebody has to remember to mount: a screen
// that forgot it would lose every icon with nothing to explain why.
let installed = false
function installSprite() {
  if (installed || typeof document === 'undefined') return
  installed = true
  const host = document.createElement('div')
  host.setAttribute('aria-hidden', 'true')
  // Not `hidden`/`display:none` on the <svg> itself — a symbol is never painted directly,
  // but the host has to stay out of layout without collapsing the reference.
  host.style.cssText = 'position:absolute;width:0;height:0;overflow:hidden'
  host.innerHTML = sprite
  document.body.prepend(host)
}

interface Props {
  name: IconName
  /** Edge length in px. Icons are square and optically centred on a 24px grid. */
  size?: number
  className?: string
}

/**
 * Always `aria-hidden`. An icon here is never the only carrier of meaning — every icon
 * button keeps its own `aria-label`, and every urgency band ships icon + heading + count.
 */
export function Icon({ name, size = 20, className }: Props) {
  installSprite()
  return (
    <svg
      className={className ? `icon ${className}` : 'icon'}
      width={size}
      height={size}
      aria-hidden="true"
      focusable="false"
    >
      <use href={`#i-${name}`} />
    </svg>
  )
}
