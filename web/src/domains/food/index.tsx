import type { Domain } from '../../shell/routes'
import { AddItem } from './AddItem'
import { DashboardRoute } from './DashboardRoute'
import { Kitchen } from './Kitchen'
import { Recipes } from './Recipes'
import { ScanScreen } from './ScanScreen'

/**
 * The food domain: everything the original four-tab app did, registered as one module.
 *
 * Order is the daily loop, not importance. "In casa" is where you land and where the
 * logging happens; the summary is at the end because it's something you go and look at,
 * not something you pass through.
 */
export const foodDomain: Domain = {
  id: 'food',
  label: 'Cibo',
  icon: 'pot',
  routes: [
    { path: '/kitchen', label: 'In casa', icon: 'home', element: <Kitchen /> },
    { path: '/recipes', label: 'Cosa cucino', icon: 'pot', element: <Recipes /> },
    { path: '/scan', label: 'Scansiona', icon: 'scan', element: <ScanScreen />, narrow: true },
    { path: '/add', label: 'Aggiungi', icon: 'plus', element: <AddItem />, narrow: true },
    { path: '/dashboard', label: 'Riepilogo', icon: 'chart', element: <DashboardRoute /> },
  ],
}
