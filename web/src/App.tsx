import { useQueryClient } from '@tanstack/react-query'
import { useEffect, useState } from 'react'

import { auth } from './api'
import { Login } from './auth/Login'
import { AppShell } from './shell/AppShell'

/**
 * The auth gate, and nothing else.
 *
 * Login renders *outside* the shell deliberately: there is no navigation to offer someone
 * who isn't signed in, and a sidebar full of links that all bounce back to a login form is
 * worse than no chrome at all.
 */
export default function App() {
  const qc = useQueryClient()
  const [token, setToken] = useState<string | null>(auth.get())

  // api.ts fires this when any request comes back 401, so an expired token drops
  // straight to the login screen rather than leaving a half-broken UI.
  useEffect(() => {
    const onUnauthorized = () => setToken(null)
    window.addEventListener('wastewise:unauthorized', onUnauthorized)
    return () => window.removeEventListener('wastewise:unauthorized', onUnauthorized)
  }, [])

  if (!token) {
    return (
      <Login
        onSuccess={() => {
          qc.clear()
          setToken(auth.get())
        }}
      />
    )
  }

  return (
    <AppShell
      onLogout={() => {
        auth.clear()
        qc.clear()
        setToken(null)
      }}
    />
  )
}
