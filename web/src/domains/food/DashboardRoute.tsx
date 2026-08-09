import { Suspense, lazy } from 'react'

import { Skeleton } from '../../ui'

// Recharts is ~395 kB of the bundle and lives on exactly one route — and not the landing
// one. Loading it eagerly would make the daily loop (open app, log what got eaten) pay for
// a summary screen you visit occasionally. Split out, the first paint of `/kitchen` skips
// it entirely and the dashboard fetches it while its own queries are still in flight.
const Dashboard = lazy(() => import('./Dashboard').then((m) => ({ default: m.Dashboard })))

/**
 * Its own module rather than a wrapper inside `index.tsx`: that file exports the domain
 * registry entry, and mixing a component into it costs fast refresh for the whole domain.
 */
export function DashboardRoute() {
  // A block the height of the hero card, so arriving on the route doesn't jump the layout
  // once the chunk lands.
  return (
    <Suspense fallback={<Skeleton height="220px" radius="var(--radius)" />}>
      <Dashboard />
    </Suspense>
  )
}
