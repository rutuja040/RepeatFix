import { useEffect, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'

import { Alert } from '../components/Alert'
import { ComplaintForm } from '../components/ComplaintForm'
import { EmptyState } from '../components/EmptyState'
import { Spinner } from '../components/Spinner'
import {
  ApiError,
  errorMessage,
  getComplaint,
  getComplaintMeta,
  updateComplaint,
} from '../services/api'
import type { Complaint, ComplaintInput } from '../types'

const FALLBACK_STATUSES = ['open', 'in_progress', 'resolved', 'closed']

export function EditComplaintPage() {
  const { complaintId = '' } = useParams()
  const navigate = useNavigate()

  const [complaint, setComplaint] = useState<Complaint | null>(null)
  const [categories, setCategories] = useState<string[]>([])
  const [statuses, setStatuses] = useState<string[]>(FALLBACK_STATUSES)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({})

  useEffect(() => {
    let active = true
    setLoading(true)
    Promise.all([getComplaint(complaintId), getComplaintMeta().catch(() => null)])
      .then(([c, meta]) => {
        if (!active) return
        setComplaint(c)
        if (meta) {
          setCategories(meta.categories.map((x) => x.category))
          if (meta.statuses.length) setStatuses(meta.statuses)
        }
        setLoadError(null)
      })
      .catch((e) => {
        if (active) setLoadError(errorMessage(e))
      })
      .finally(() => {
        if (active) setLoading(false)
      })
    return () => {
      active = false
    }
  }, [complaintId])

  function handleSubmit(payload: ComplaintInput) {
    setBusy(true)
    setError(null)
    setFieldErrors({})
    updateComplaint(complaintId, payload)
      .then((updated) => {
        navigate(`/complaints/${encodeURIComponent(updated.complaint_id)}`, {
          state: { flash: `Complaint ${updated.complaint_id} updated.` },
        })
      })
      .catch((e) => {
        setError(errorMessage(e))
        if (e instanceof ApiError) setFieldErrors(e.fieldErrors)
      })
      .finally(() => setBusy(false))
  }

  if (loading) return <Spinner label="Loading complaint…" />

  if (loadError || !complaint) {
    return (
      <EmptyState
        title="Complaint not available"
        message={loadError ?? 'The complaint could not be found.'}
        action={
          <Link className="btn btn-secondary" to="/complaints">
            Back to complaints
          </Link>
        }
      />
    )
  }

  return (
    <div className="page">
      <div className="page-head">
        <h1>Edit complaint</h1>
      </div>
      <p className="muted mono">{complaint.complaint_id}</p>
      {complaint.synthetic && <Alert kind="info">This is synthetic demonstration data.</Alert>}
      {error && <Alert kind="error">{error}</Alert>}
      <p className="muted">
        The complaint ID, original creation time, and provenance cannot be changed. Every change you
        save is recorded in the complaint history.
      </p>
      <ComplaintForm
        mode="edit"
        initial={complaint}
        categories={categories}
        statuses={statuses}
        busy={busy}
        serverFieldErrors={fieldErrors}
        onSubmit={handleSubmit}
        onCancel={() => navigate(`/complaints/${encodeURIComponent(complaint.complaint_id)}`)}
      />
    </div>
  )
}
