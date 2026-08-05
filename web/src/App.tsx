import { useQueryClient } from '@tanstack/react-query'
import { useEffect, useState } from 'react'

import { auth } from './api'
import { AddItem } from './components/AddItem'
import { Kitchen } from './components/Kitchen'
import { Login } from './components/Login'
import { Recipes } from './components/Recipes'
import { ScanScreen } from './components/ScanScreen'
import { StatsBar } from './components/StatsBar'

type Tab = 'kitchen' | 'recipes' | 'scan' | 'add'

// Scanning and the add form are single columns of controls; they read worse stretched to
// 1240px. The board and the graph are the screens that actually want the space.
const NARROW: Tab[] = ['scan', 'add']

export default function App() {
  const qc = useQueryClient()
  const [token, setToken] = useState<string | null>(auth.get())
  const [tab, setTab] = useState<Tab>('kitchen')

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
    <div className={`app ${NARROW.includes(tab) ? 'narrow' : ''}`}>
      <header className="topbar">
        <div>
          <h1>WasteWise</h1>
          <div className="sub">Fase 1.5 · board e grafo ricette</div>
        </div>
        <button
          onClick={() => {
            auth.clear()
            qc.clear()
            setToken(null)
          }}
        >
          Esci
        </button>
      </header>

      <StatsBar />

      <nav className="tabs">
        <button aria-selected={tab === 'kitchen'} onClick={() => setTab('kitchen')}>
          In casa
        </button>
        <button aria-selected={tab === 'recipes'} onClick={() => setTab('recipes')}>
          Cosa cucino
        </button>
        <button aria-selected={tab === 'scan'} onClick={() => setTab('scan')}>
          Scansiona
        </button>
        <button aria-selected={tab === 'add'} onClick={() => setTab('add')}>
          Aggiungi
        </button>
      </nav>

      {tab === 'kitchen' && <Kitchen />}
      {tab === 'recipes' && <Recipes />}
      {tab === 'scan' && <ScanScreen />}
      {tab === 'add' && <AddItem />}
    </div>
  )
}
