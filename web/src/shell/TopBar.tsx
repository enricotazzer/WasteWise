import { useDemoMode } from '../lib/demoMode'
import { Badge, Button } from '../ui'

interface Props {
  /** Label of the current route. The one h1 on the page. */
  title: string
  onLogout: () => void
}

/**
 * Static, not sticky. On a phone a pinned header costs ~56px of the only axis that matters
 * while scrolling a long column of items, and the bottom bar is already the persistent
 * navigation — nothing up here needs to stay reachable.
 *
 * The brand shows only below 1000px; above it, SideNav is already carrying it.
 */
export function TopBar({ title, onLogout }: Props) {
  const { includeDemo, demoEntries } = useDemoMode()

  return (
    <header className="topbar">
      <div className="topbar-lead">
        <span className="topbar-brand">WasteWise</span>
        <h1>{title}</h1>
      </div>

      <div className="topbar-actions">
        {/* Every screen, not just the dashboard. Numbers computed over synthetic outcomes
            must never appear unlabelled, and the Kitchen board is 69 demo items — the badge
            belongs where it cannot be scrolled past or missed. It is driven by the response
            (`demo_entries` > 0 and the resolved preference), never by a build flag. */}
        {includeDemo && demoEntries > 0 && (
          <Badge tone="warn" className="demo-badge">
            dati dimostrativi
          </Badge>
        )}
        {/* The label is wrapped so the narrowest phones can drop it and keep the icon: the
            accessible name comes from the text either way, since it stays in the DOM. */}
        <Button variant="ghost" icon="logout" onClick={onLogout}>
          <span>Esci</span>
        </Button>
      </div>
    </header>
  )
}
