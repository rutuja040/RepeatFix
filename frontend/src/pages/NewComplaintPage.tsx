import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'

import { Alert } from '../components/Alert'
import { ComplaintForm } from '../components/ComplaintForm'
import { ApiError, createComplaint, errorMessage, getComplaintMeta } from '../services/api'
import type { ComplaintInput } from '../types'

const FALLBACK_STATUSES = ['open', 'in_progress', 'resolved', 'closed']

export function NewComplaintPage() {
  const navigate = useNavigate()
  const [categories, setCategories] = useState<string[]>([])
  const [statuses, setStatuses] = useState<string[]>(FALLBACK_STATUSES)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({})

  useEffect(() => {
    let active = true
    getComplaintMeta()
      .then((m) => {
        if (!active) return
        setCategories(m.categories.map((c) => c.category))
        if (m.statuses.length) setStatuses(m.statuses)
      })
      .catch(() => {
        /* fall back to defaults */
      })
    return () => {
      active = false
    }
  }, [])

  function handleSubmit(payload: ComplaintInput) {
    setBusy(true)
    setError(null)
    setFieldErrors({})
    createComplaint(payload)
      .then((created) => {
        navigate(`/complaints/${encodeURIComponent(created.complaint_id)}`, {
          state: { flash: `Complaint ${created.complaint_id} created.` },
        })
      })
      .catch((e) => {
        setError(errorMessage(e))
        if (e instanceof ApiError) setFieldErrors(e.fieldErrors)
      })
      .finally(() => setBusy(false))
  }

  return (
    <div className="page">
      <div className="page-head">
        <h1>New complaint</h1>
      </div>
      {error && <Alert kind="error">{error}</Alert>}
      <p className="muted">
        Fields marked required must be provided. Optional fields left blank are stored as unknown —
        missing severity is never treated as low severity.
      </p>
      <ComplaintForm
        mode="create"
        categories={categories}
        statuses={statuses}
        busy={busy}
        serverFieldErrors={fieldErrors}
        onSubmit={handleSubmit}
        onCancel={() => navigate('/complaints')}
      />
    </div>
  )
}
