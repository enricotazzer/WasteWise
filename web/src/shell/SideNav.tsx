import { NavLink } from 'react-router-dom'

import { Icon } from '../ui'
import { DOMAINS } from './routes'

/**
 * Desktop navigation. Hidden below 1000px, where BottomBar takes over.
 *
 * Domains render as labelled groups only once there's more than one of them — a lone "Cibo"
 * heading above four items would be chrome explaining a structure the user can't yet see.
 * The day bills lands, the grouping appears on its own.
 */
export function SideNav() {
  const grouped = DOMAINS.length > 1

  return (
    <nav className="sidenav" aria-label="Sezioni">
      <div className="sidenav-brand">
        <span className="sidenav-mark" aria-hidden="true" />
        WasteWise
      </div>

      {DOMAINS.map((domain) => (
        <div className="sidenav-group" key={domain.id}>
          {grouped && (
            <div className="sidenav-group-label" id={`nav-${domain.id}`}>
              {domain.label}
            </div>
          )}
          <ul aria-labelledby={grouped ? `nav-${domain.id}` : undefined}>
            {domain.routes
              .filter((r) => !r.hidden)
              .map((route) => (
                <li key={route.path}>
                  {/* NavLink sets aria-current="page" on the active route itself. */}
                  <NavLink to={route.path} className="sidenav-link">
                    <Icon name={route.icon} size={19} />
                    {route.label}
                  </NavLink>
                </li>
              ))}
          </ul>
        </div>
      ))}
    </nav>
  )
}
