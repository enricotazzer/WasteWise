import { NavLink } from 'react-router-dom'

import { Icon } from '../ui'
import { ROUTES } from './routes'

/**
 * Phone navigation, and not a fallback for the sidebar.
 *
 * The core loop of this app — scanning a shopping bag, tapping what got eaten — happens
 * one-handed in a kitchen, standing up, with the other hand full. Thumb reach is the
 * requirement that decides this component exists; a top nav would put every primary action
 * at the far end of the screen from the only finger available.
 *
 * Flattened across domains rather than grouped: a phone tab bar holds about five items, so
 * the day a second domain arrives this becomes a domain switcher instead of growing.
 */
export function BottomBar() {
  return (
    <nav className="bottombar" aria-label="Sezioni">
      <ul>
        {ROUTES.filter((r) => !r.hidden).map((route) => (
          <li key={route.path}>
            <NavLink to={route.path} className="bottombar-link">
              <Icon name={route.icon} size={21} />
              <span>{route.label}</span>
            </NavLink>
          </li>
        ))}
      </ul>
    </nav>
  )
}
