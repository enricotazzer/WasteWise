import { Navigate, Route, Routes, matchPath, useLocation } from 'react-router-dom'

import { BottomBar } from './BottomBar'
import { SideNav } from './SideNav'
import { TopBar } from './TopBar'
import { HOME_PATH, ROUTES } from './routes'

/**
 * The frame every screen renders inside: sidebar at >=1000px, bottom tab bar below.
 *
 * Both navigations are always in the DOM and swapped by media query rather than by a JS
 * breakpoint hook — a `matchMedia` state would render the wrong nav for one frame on load,
 * which on a phone means the tab bar visibly jumping into place under the user's thumb.
 *
 * Safe-area insets are honoured throughout because index.html sets `viewport-fit=cover` on
 * purpose: the layout reaches under the notch when the app is saved to the home screen, so
 * the bottom bar has to sit above the home indicator rather than under it.
 */
export function AppShell({ onLogout }: { onLogout: () => void }) {
  const { pathname } = useLocation()
  const current = ROUTES.find((r) => matchPath({ path: r.path, end: true }, pathname))

  return (
    <div className="shell">
      <SideNav />

      <div className="shell-main">
        {/* Header and content share one column so the page title stays flush with the first
            card — including on the narrow screens, where the column pulls in to 620px. */}
        <div className={`shell-col ${current?.narrow ? 'narrow' : ''}`}>
          <TopBar title={current?.label ?? 'WasteWise'} onLogout={onLogout} />

          <main>
            <Routes>
              <Route path="/" element={<Navigate to={HOME_PATH} replace />} />
              {ROUTES.map((route) => (
                <Route key={route.path} path={route.path} element={route.element} />
              ))}
              {/* A stale bookmark from before the routes existed should land somewhere
                  usable, not on a dead end. */}
              <Route path="*" element={<Navigate to={HOME_PATH} replace />} />
            </Routes>
          </main>
        </div>
      </div>

      <BottomBar />
    </div>
  )
}
