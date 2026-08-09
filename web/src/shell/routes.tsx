import type { ReactElement } from 'react'

import { foodDomain } from '../domains/food'
import type { IconName } from '../ui/Icon'

/**
 * The domain registry.
 *
 * WasteWise is becoming one module of a personal-savings framework — bills and whatever
 * else follows are siblings of food, not children of it. So the shell knows nothing about
 * any particular domain: a domain hands over a label, an icon and a list of routes, and the
 * navigation, the page title and the URL space fall out of that. Adding bills later is
 * appending one entry to `DOMAINS`, not editing SideNav, BottomBar and TopBar.
 *
 * The shell imports domains rather than domains registering themselves into the shell:
 * a mutable registry filled by import side-effects would make route order depend on module
 * evaluation order, which is exactly the kind of thing that breaks under a bundler split.
 */
export interface AppRoute {
  /**
   * Absolute, and deep-linkable — dashboard filter state belongs in the URL, not useState.
   * Deliberately not namespaced under the domain id: `/dashboard` is a savings question
   * that happens to be answered from food data today, and forcing `/food/dashboard` now
   * would mean moving it the moment bills also have one.
   */
  path: string
  label: string
  icon: IconName
  element: ReactElement
  /** Reachable, but not offered in the nav. */
  hidden?: boolean
  /**
   * Caps the content column at --content-narrow. For screens that are one column of
   * controls by nature: a form read at 1240px is worse, not better.
   */
  narrow?: boolean
}

export interface Domain {
  id: string
  label: string
  icon: IconName
  routes: AppRoute[]
}

export const DOMAINS: Domain[] = [foodDomain]

export const ROUTES: AppRoute[] = DOMAINS.flatMap((d) => d.routes)

/**
 * Landing route. The board, not the dashboard: the daily loop is "what do I need to eat
 * tonight", and a summary screen is something you go and look at on purpose.
 */
export const HOME_PATH = '/kitchen'
