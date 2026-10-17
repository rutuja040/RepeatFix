import { useEffect, useState } from 'react'
import { NavLink, Outlet } from 'react-router-dom'

import { getApiBase, getHealth } from '../services/api'

type Status = 'loading' | 'ok' | 'error'

export function Layout() {
  const [status, setStatus] = useState<Status>('loading')

  useEffect(() => {
    let cancelled = false
    getHealth()
      .then((h) => {
        if (!cancelled) setStatus(h.status === 'ok' ? 'ok' : 'error')
      })
      .catch(() => {
        if (!cancelled) setStatus('error')
      })
    return () => {
      cancelled = true
    }
  }, [])

  return (
    <div className="app-shell">
      <header className="app-header">
        <div className="app-header-inner">
          <div className="brand">
            <span className="brand-mark">RF</span>
            <div>
              <div className="brand-name">RepeatFix</div>
              <div className="brand-sub">Civic complaint prioritization</div>
            </div>
          </div>
          <nav className="app-nav" aria-label="Main navigation">
            <NavLink to="/" end>
              Home
            </NavLink>
            <NavLink to="/complaints">Complaints</NavLink>
            <NavLink to="/complaints/new">New complaint</NavLink>
            <NavLink to="/imports">Import</NavLink>
          </nav>
          <div
            className={`conn conn-${status}`}
            title={`API base: ${getApiBase()}`}
            aria-label={`Backend status: ${status}`}
          >
            <span className="conn-dot" />
            {status === 'loading' && 'Checking backend…'}
            {status === 'ok' && 'Backend connected'}
            {status === 'error' && 'Backend unavailable'}
          </div>
        </div>
      </header>
      <main className="app-main">
        <Outlet />
      </main>
      <footer className="app-footer">
        Decision-support tool. Scores support — and do not replace — authorized human judgement.
      </footer>
    </div>
  )
}
