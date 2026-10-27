import { useEffect, useState } from 'react'
import { Link, useLocation, useNavigate, useParams } from 'react-router-dom'

import { Alert } from '../components/Alert'
import { EmptyState } from '../components/EmptyState'
import { HistoryList } from '../components/HistoryList'
import { Spinner } from '../components/Spinner'
import {
  analyzeRecurrence,
  errorMessage,
  getComplaint,
  getComplaintHistory,
} from '../services/api'
import { formatDateTime, formatScore, statusLabel } from '../format'
import type {
  Complaint,
  ComplaintHistoryItem,
  RecurrenceAnalysisResponse,
  RecurrenceRelation,
} from '../types'

interface FlashState {
  flash?: string
}

// Parse the priority_rationale JSON string safely.
function parsePriorityRationale(raw: string | null): {
  rationale: string[]
  uncertainty_flags: string[]
  missing_fields: string[]
  components: Record<string, number>
} | null {
  if (!raw) return null
  try {
    const parsed = JSON.parse(raw) as unknown
    if (typeof parsed !== 'object' || parsed === null) return null
    const p = parsed as Record<string, unknown>
    return {
      rationale: Array.isArray(p.rationale) ? (p.rationale as string[]) : [],
      uncertainty_flags: Array.isArray(p.uncertainty_flags)
        ? (p.uncertainty_flags as string[])
        : [],
      missing_fields: Array.isArray(p.missing_fields) ? (p.missing_fields as string[]) : [],
      components: typeof p.components === 'object' && p.components !== null
        ? (p.components as Record<string, number>)
        : {},
    }
  } catch {
    return null
  }
}

const CONFIDENCE_LABEL_CLASS: Record<string, string> = {
  high: 'badge badge-error',
  medium: 'badge badge-warning',
  low: 'badge badge-info',
}

const RELATION_TYPE_LABEL: Record<string, string> = {
  exact_source: 'Exact source match',
  potential_recurrence: 'Potential recurrence',
  related_candidate: 'Related candidate',
}

function RecurrenceRelationCard({ rel }: { rel: RecurrenceRelation }) {
  const [expanded, setExpanded] = useState(false)
  const confidenceClass = CONFIDENCE_LABEL_CLASS[rel.confidence_label] ?? 'badge'
  const typeLabel = RELATION_TYPE_LABEL[rel.relation_type] ?? rel.relation_type

  return (
    <div className="recurrence-card">
      <div className="recurrence-card-header">
        <div className="recurrence-card-id">
          <Link to={`/complaints/${encodeURIComponent(rel.related_complaint_id)}`}>
            {rel.related_complaint_id}
          </Link>
        </div>
        <div className="recurrence-card-badges">
          <span className={confidenceClass}>{rel.confidence_label}</span>
          <span className="badge">{typeLabel}</span>
          {rel.requires_human_review && (
            <span className="badge badge-warning">human review required</span>
          )}
        </div>
      </div>
      <p className="recurrence-summary">{rel.evidence_summary}</p>
      {rel.reasons.length > 0 && (
        <button
          type="button"
          className="btn-link"
          onClick={() => setExpanded((v) => !v)}
          aria-expanded={expanded}
        >
          {expanded ? '▲ Hide evidence details' : '▼ Show evidence details'}
        </button>
      )}
      {expanded && (
        <table className="data-table recurrence-reasons-table">
          <thead>
            <tr>
              <th scope="col">Signal</th>
              <th scope="col">Description</th>
              <th scope="col">Contribution</th>
            </tr>
          </thead>
          <tbody>
            {rel.reasons.map((r, i) => (
              <tr key={i}>
                <td className="mono">{r.code}</td>
                <td>{r.description}</td>
                <td className="mono">{r.weight_contribution.toFixed(3)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  )
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

  // Recurrence analysis state
  const [recurrence, setRecurrence] = useState<RecurrenceAnalysisResponse | null>(null)
  const [recurrenceLoading, setRecurrenceLoading] = useState(false)
  const [recurrenceError, setRecurrenceError] = useState<string | null>(null)
  const [recurranceRan, setRecurrenceRan] = useState(false)

  useEffect(() => {
    let active = true
    setLoading(true)
    // Reset recurrence when navigating to a different complaint
    setRecurrence(null)
    setRecurrenceError(null)
    setRecurrenceRan(false)
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

  function handleAnalyzeRecurrence() {
    if (recurrenceLoading) return
    setRecurrenceLoading(true)
    setRecurrenceError(null)
    analyzeRecurrence(complaintId)
      .then((res) => {
        setRecurrence(res)
        setRecurrenceRan(true)
      })
      .catch((e) => {
        setRecurrenceError(errorMessage(e))
        setRecurrenceRan(true)
      })
      .finally(() => setRecurrenceLoading(false))
  }

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
  const priorityInfo = parsePriorityRationale(c.priority_rationale)

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

      {/* ---- Priority explanation ---- */}
      <section className="card">
        <h2>Priority score</h2>
        <p>
          Score: <strong className="mono">{formatScore(c.priority_score)}</strong>
          {priorityInfo && priorityInfo.uncertainty_flags.includes('manual_review_recommended') && (
            <span className="badge badge-warning" style={{ marginLeft: '10px' }}>
              manual review recommended
            </span>
          )}
        </p>
        {priorityInfo ? (
          <>
            {priorityInfo.uncertainty_flags.length > 0 && (
              <p className="muted" style={{ fontSize: '0.9em' }}>
                <strong>Uncertainty flags:</strong>{' '}
                {priorityInfo.uncertainty_flags.join(', ')}
              </p>
            )}
            {priorityInfo.missing_fields.length > 0 && (
              <p className="muted" style={{ fontSize: '0.9em' }}>
                <strong>Missing fields:</strong>{' '}
                {priorityInfo.missing_fields.join(', ')}
              </p>
            )}
            <details>
              <summary className="btn-link" style={{ cursor: 'pointer' }}>
                Score components
              </summary>
              <table className="data-table" style={{ marginTop: '8px' }}>
                <thead>
                  <tr>
                    <th scope="col">Component</th>
                    <th scope="col">Value</th>
                  </tr>
                </thead>
                <tbody>
                  {Object.entries(priorityInfo.components).map(([k, v]) => (
                    <tr key={k}>
                      <td className="mono">{k}</td>
                      <td className="mono">{v.toFixed(4)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </details>
            <p className="muted" style={{ fontSize: '0.85em', marginTop: '8px' }}>
              This is a transparent baseline score — not a calibrated probability. Missing
              severity or location is treated as uncertainty, not as low severity.
            </p>
          </>
        ) : (
          <p className="muted">
            This complaint has not been scored yet. Missing severity or location information is
            treated as uncertainty, not as low severity.
          </p>
        )}
      </section>

      {/* ---- Recurrence analysis ---- */}
      <section className="card">
        <h2>Recurrence analysis</h2>
        <p className="muted" style={{ fontSize: '0.9em', marginBottom: '12px' }}>
          Checks for similar complaints nearby, in the same category, and within a 30-day window.
          Results require human review — no automatic merge or action is performed.
        </p>

        {!recurranceRan && !recurrenceLoading && (
          <button
            id="btn-analyze-recurrence"
            type="button"
            className="btn btn-secondary"
            onClick={handleAnalyzeRecurrence}
          >
            Analyse recurrence
          </button>
        )}

        {recurrenceLoading && <Spinner label="Analysing recurrence…" />}

        {recurrenceError && (
          <Alert kind="error" onClose={() => setRecurrenceError(null)}>
            {recurrenceError}
          </Alert>
        )}

        {recurranceRan && !recurrenceLoading && recurrence && (
          <>
            {recurrence.warnings.length > 0 && (
              <Alert kind="warning">
                {recurrence.warnings.join(' ')}
              </Alert>
            )}
            {recurrence.missing_data.length > 0 && (
              <p className="muted" style={{ fontSize: '0.9em' }}>
                <strong>Missing data for analysis:</strong>{' '}
                {recurrence.missing_data.join(', ')}
              </p>
            )}

            {recurrence.relations.length === 0 ? (
              <EmptyState
                title="No similar complaints found"
                message="No matching complaints were found within the similarity, location, and time thresholds."
              />
            ) : (
              <div className="recurrence-list">
                <p style={{ fontSize: '0.9em', marginBottom: '8px' }}>
                  Found <strong>{recurrence.relations.length}</strong> similar complaint
                  {recurrence.relations.length !== 1 ? 's' : ''}.
                  {' '}All require human review before any action is taken.
                </p>
                {recurrence.relations.map((rel) => (
                  <RecurrenceRelationCard key={rel.related_complaint_id} rel={rel} />
                ))}
              </div>
            )}

            <p className="muted" style={{ fontSize: '0.85em', marginTop: '12px' }}>
              Analysed at {formatDateTime(recurrence.analyzed_at)}.
              Coordinates reliable: {recurrence.has_reliable_coords ? 'yes' : 'no'}.
              Timestamps reliable: {recurrence.has_reliable_time ? 'yes' : 'no'}.
              {' '}
              <button
                type="button"
                className="btn-link"
                onClick={handleAnalyzeRecurrence}
                disabled={recurrenceLoading}
              >
                Re-run analysis
              </button>
            </p>
          </>
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
