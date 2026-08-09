import { useMemo, useSyncExternalStore } from 'react'

import { paletteFor, type ColorScheme } from './palette'

/* The one piece of chart colour JavaScript has to know about.
 *
 * Everything else a chart paints — gridlines, axis rules, tick text, the 2px surface spacer
 * inside a stack, the tooltip crosshair — is styled in index.css against the tokens that
 * already flip with `prefers-color-scheme`. Series fills can't be: the same hex also has to
 * reach a legend swatch and a tooltip line-key, which are ordinary DOM, and Recharts wants
 * it as a prop. So the scheme is observed once here and the palette is picked from it.
 *
 * `useSyncExternalStore` rather than a `useState` + effect: the effect version renders the
 * light palette for one frame before correcting itself, which on the dashboard is eight
 * bars visibly changing colour on load.
 */

const DARK_QUERY = '(prefers-color-scheme: dark)'

function subscribe(onChange: () => void): () => void {
  const mq = window.matchMedia(DARK_QUERY)
  mq.addEventListener('change', onChange)
  return () => mq.removeEventListener('change', onChange)
}

function getSnapshot(): ColorScheme {
  return window.matchMedia(DARK_QUERY).matches ? 'dark' : 'light'
}

export function useColorScheme(): ColorScheme {
  // The server snapshot never runs in this app (no SSR), but omitting it makes
  // useSyncExternalStore throw under React 19's stricter checks.
  return useSyncExternalStore(subscribe, getSnapshot, () => 'light')
}

/** The eight slots plus the fold gray, for whichever mode is on screen right now. */
export function useSeriesPalette(): { series: string[]; other: string } {
  const scheme = useColorScheme()
  return useMemo(() => paletteFor(scheme), [scheme])
}

export interface ChartTokens {
  accent: string
  muted: string
}

/**
 * The two app tokens a chart sometimes needs as a *value* rather than as CSS.
 *
 * Read off the document rather than copied into TS, because these are `--accent` and
 * `--muted` — existing colour tokens with their own measured justification in index.css,
 * and a second copy here would be a second source of truth for a decision that was already
 * taken. Only a series that genuinely means good/bad reaches for `accent`; identity series
 * take the categorical slots above.
 */
export function useChartTokens(): ChartTokens {
  const scheme = useColorScheme()
  return useMemo(() => {
    const style = getComputedStyle(document.documentElement)
    const token = (name: string, fallback: string) =>
      style.getPropertyValue(name).trim() || fallback
    // Fallbacks cover the one frame before the stylesheet lands; a chart that painted
    // nothing at all would look like a data bug rather than a loading state.
    return {
      accent: token('--accent', scheme === 'dark' ? '#6fc296' : '#2f6f4e'),
      muted: token('--muted', scheme === 'dark' ? '#a09889' : '#7a7268'),
    }
  }, [scheme])
}
