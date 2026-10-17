import { Link } from 'react-router-dom'

import { getApiBase } from '../services/api'

export function HomePage() {
  return (
    <div className="page">
      <section className="hero">
        <h1>RepeatFix</h1>
        <p className="lead">
          A research-oriented decision-support tool for prioritizing civic complaints using
          recurrence-aware, risk-based ranking.
        </p>
        <p className="muted">
          Scores support — and never replace — decisions by authorized personnel. This system does
          not declare any complaint dangerous or an emergency.
        </p>
        <div className="hero-actions">
          <Link className="btn btn-primary" to="/complaints">
            View complaints
          </Link>
          <Link className="btn btn-secondary" to="/complaints/new">
            Create a complaint
          </Link>
        </div>
      </section>

      <section className="card">
        <h2>What you can do today</h2>
        <ul>
          <li>Create, view, filter, and update civic complaints.</li>
          <li>Track an auditable history of every meaningful change.</li>
          <li>Distinguish synthetic demonstration data from real records.</li>
        </ul>
        <p className="muted">
          Prioritization, recurrence analysis, dataset import, and evaluation are planned for later
          phases. Priority scores shown as “Not computed” have not been calculated yet.
        </p>
      </section>

      <section className="card">
        <h2>Development notes</h2>
        <p className="muted">
          The backend is the single source of truth for all persisted data. API base:{' '}
          <code>{getApiBase()}</code>
        </p>
      </section>
    </div>
  )
}
