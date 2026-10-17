import { useEffect, useState } from 'react'
import { Link, useLocation, useNavigate, useParams } from 'react-router-dom'

import { Alert } from '../components/Alert'
import { EmptyState } from '../components/EmptyState'
import { HistoryList } from '../components/HistoryList'
import { Spinner } from '../components/Spinner'
import { errorMessage, getComplaint, getComplaintHistory } from '../services/api'
import { formatDateTime, formatScore, statusLabel } from '../format'
import type { Complaint, ComplaintHistoryItem } from '../types'

interface FlashState {
  flash?: string
}

export function ComplaintDetailPage() {
  const { complaintId = '' } = useParams()
  const location = useLocation()
  const navigate = useNavigate()
  const flash = (location.state as FlashState | null)?.flash

  const [complaint, setComplaint] = useState<Complaint | null>(null)
  const [history, setHistory] = useState<ComplaintHistoryItem[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [showFlash, setShowFlash] = useState(!!flash)

  useEffect(() => {
    let active = true
    setLoading(true)
    Promise.all([getComplaint(complaintId), getComplaintHistory(complaintId)])
      .then(([c, h]) => {
        if (!active) return
        setComplaint(c)
        setHistory(h)
        setError(null)
      })
      .catch((e) => {
        if (!active) return
        setError(errorMessage(e))
        setComplaint(null)
      })
      .finally(() => {
        if (active) setLoading(false)
      })
    return () => {
      active = false
    }
  }, [complaintId])

  if (loading) return <Spinner label="Loading complaint…" />

  if (error || !complaint) {
    return (
      <EmptyState
        title="Complaint not available"
        message={error ?? 'The complaint could not be found.'}
        action={
          <Link className="btn btn-secondary" to="/complaints">
            Back to complaints
          </Link>
        }
      />
    )
  }

  const c = complaint

  return (
    <div className="page">
      <div className="breadcrumb">
        <Link to="/complaints">← All complaints</Link>
      </div>

      {showFlash && flash && (
        <Alert kind="success" onClose={() => setShowFlash(false)}>
          {flash}
        </Alert>
      )}

      <div className="page-head">
        <div>
          <h1>{c.title}</h1>
          <p className="muted mono">{c.complaint_id}</p>
        </div>
        <div className="page-head-actions">
          {c.synthetic && <span className="badge badge-synthetic">synthetic demo data</span>}
          <Link className="btn btn-primary" to={`/complaints/${encodeURIComponent(c.complaint_id)}/edit`}>
            Edit
          </Link>
        </div>
      </div>

      <section className="card">
        <h2>Details</h2>
        <dl className="detail-grid">
          <div>
            <dt>Status</dt>
            <dd>
              <span className={`status status-${c.status}`}>{statusLabel(c.status)}</span>
            </dd>
          </div>
          <div>
            <dt>Category</dt>
            <dd>{c.category ?? '—'}</dd>
          </div>
          <div>
            <dt>Location label</dt>
            <dd>{c.location_label ?? '—'}</dd>
          </div>
          <div>
            <dt>Administrative area</dt>
            <dd>{c.administrative_area ?? '—'}</dd>
          </div>
          <div>
            <dt>Coordinates</dt>
            <dd className="mono">
              {c.latitude != null && c.longitude != null
                ? `${c.latitude}, ${c.longitude}`
                : 'Not provided'}
            </dd>
          </div>
          <div>
            <dt>Assigned department</dt>
            <dd>{c.assigned_department ?? '—'}</dd>
          </div>
          <div>
            <dt>Severity label (human assessment)</dt>
            <dd>{c.severity_label ?? 'Not assessed'}</dd>
          </div>
          <div>
            <dt>Source</dt>
            <dd>{c.source ?? '—'}</dd>
          </div>
          <div>
            <dt>Created</dt>
            <dd>{formatDateTime(c.created_at)}</dd>
          </div>
          <div>
            <dt>Last updated</dt>
            <dd>{formatDateTime(c.last_updated_at)}</dd>
          </div>
          <div>
            <dt>Resolved</dt>
            <dd>{formatDateTime(c.resolved_at)}</dd>
          </div>
          <div>
            <dt>Data-quality flags</dt>
            <dd>{c.data_quality_flags ?? '—'}</dd>
          </div>
        </dl>
        {c.description && (
          <>
            <h3>Description</h3>
            <p className="prewrap">{c.description}</p>
          </>
        )}
      </section>

      <section className="card">
        <h2>Priority explanation</h2>
        <p>
          Priority score: <strong>{formatScore(c.priority_score)}</strong>
        </p>
        {c.priority_rationale ? (
          <p className="prewrap">{c.priority_rationale}</p>
        ) : (
          <p className="muted">
            This complaint has not been scored yet. Prioritization is implemented as a transparent,
            deterministic backend module and will be surfaced here in a later phase. Missing
            severity or location information is treated as uncertainty, not as low severity.
          </p>
        )}
      </section>

      <section className="card">
        <h2>History</h2>
        <HistoryList items={history} />
      </section>

      <div className="page-foot">
        <button type="button" className="btn btn-secondary" onClick={() => navigate('/complaints')}>
          Back to list
        </button>
      </div>
    </div>
  )
}
