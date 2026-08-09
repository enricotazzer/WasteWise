import { type FormEvent, useState } from 'react'

import { api, auth } from '../api'
import { Button } from '../ui'

export function Login({ onSuccess }: { onSuccess: () => void }) {
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  async function submit(e: FormEvent) {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      auth.set(await api.login(email, password))
      onSuccess()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Errore di accesso')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="login">
      <h1>WasteWise</h1>
      <p>Traccia la spesa, riduci gli sprechi.</p>
      <form onSubmit={submit}>
        {error && <div className="error">{error}</div>}
        <div className="field">
          <label htmlFor="email">Email</label>
          <input
            id="email"
            type="email"
            autoComplete="username"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            required
          />
        </div>
        <div className="field">
          <label htmlFor="password">Password</label>
          <input
            id="password"
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
          />
        </div>
        <Button variant="primary" type="submit" disabled={busy}>
          {busy ? 'Accesso…' : 'Entra'}
        </Button>
      </form>
      <p className="hint login-note">
        Le credenziali sono quelle in <code>.env</code> (FIRST_USER_EMAIL / FIRST_USER_PASSWORD).
      </p>
    </div>
  )
}
