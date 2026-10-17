import { useState } from 'react'

import type { Complaint, ComplaintInput, ComplaintStatus } from '../types'

interface ComplaintFormProps {
  mode: 'create' | 'edit'
  initial?: Complaint | null
  categories: string[]
  statuses: string[]
  busy?: boolean
  serverFieldErrors?: Record<string, string>
  onSubmit: (payload: ComplaintInput) => void
  onCancel?: () => void
}

interface FormState {
  complaint_id: string
  title: string
  description: string
  category: string
  location_label: string
  administrative_area: string
  latitude: string
  longitude: string
  status: ComplaintStatus
  assigned_department: string
  severity_label: string
  data_quality_flags: string
  synthetic: boolean
}

function initialState(mode: 'create' | 'edit', initial?: Complaint | null): FormState {
  return {
    complaint_id: initial?.complaint_id ?? '',
    title: initial?.title ?? '',
    description: initial?.description ?? '',
    category: initial?.category ?? '',
    location_label: initial?.location_label ?? '',
    administrative_area: initial?.administrative_area ?? '',
    latitude: initial?.latitude != null ? String(initial.latitude) : '',
    longitude: initial?.longitude != null ? String(initial.longitude) : '',
    status: (initial?.status ?? 'open') as ComplaintStatus,
    assigned_department: initial?.assigned_department ?? '',
    severity_label: initial?.severity_label ?? '',
    data_quality_flags: initial?.data_quality_flags ?? '',
    synthetic: mode === 'create' ? (initial?.synthetic ?? false) : (initial?.synthetic ?? false),
  }
}

function parseCoord(value: string): number | null | 'invalid' {
  const trimmed = value.trim()
  if (!trimmed) return null
  const num = Number(trimmed)
  if (!Number.isFinite(num)) return 'invalid'
  return num
}

export function ComplaintForm({
  mode,
  initial,
  categories,
  statuses,
  busy,
  serverFieldErrors,
  onSubmit,
  onCancel,
}: ComplaintFormProps) {
  const [form, setForm] = useState<FormState>(() => initialState(mode, initial))
  const [errors, setErrors] = useState<Record<string, string>>({})

  function set<K extends keyof FormState>(key: K, value: FormState[K]) {
    setForm((prev) => ({ ...prev, [key]: value }))
  }

  function validate(): { payload?: ComplaintInput; errors: Record<string, string> } {
    const next: Record<string, string> = {}
    if (mode === 'create' && !form.complaint_id.trim()) {
      next.complaint_id = 'Complaint ID is required'
    }
    if (!form.title.trim()) {
      next.title = 'Title is required'
    }
    const lat = parseCoord(form.latitude)
    if (lat === 'invalid' || (typeof lat === 'number' && (lat < -90 || lat > 90))) {
      next.latitude = 'Latitude must be a number between -90 and 90'
    }
    const lon = parseCoord(form.longitude)
    if (lon === 'invalid' || (typeof lon === 'number' && (lon < -180 || lon > 180))) {
      next.longitude = 'Longitude must be a number between -180 and 180'
    }
    if (Object.keys(next).length) return { errors: next }

    const emptyToNull = (v: string) => (v.trim() === '' ? null : v.trim())
    const payload: ComplaintInput = {
      title: form.title.trim(),
      description: emptyToNull(form.description),
      category: emptyToNull(form.category),
      location_label: emptyToNull(form.location_label),
      administrative_area: emptyToNull(form.administrative_area),
      latitude: lat as number | null,
      longitude: lon as number | null,
      status: form.status,
      assigned_department: emptyToNull(form.assigned_department),
      severity_label: emptyToNull(form.severity_label),
      data_quality_flags: emptyToNull(form.data_quality_flags),
    }
    if (mode === 'create') {
      payload.complaint_id = form.complaint_id.trim()
      payload.synthetic = form.synthetic
    }
    return { payload, errors: {} }
  }

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    const result = validate()
    setErrors(result.errors)
    if (result.payload) onSubmit(result.payload)
  }

  function fieldError(name: string): string | undefined {
    return errors[name] ?? serverFieldErrors?.[name]
  }

  return (
    <form className="form" onSubmit={handleSubmit} noValidate>
      <div className="form-grid">
        {mode === 'create' && (
          <label className="field">
            <span>
              Complaint ID <em>required</em>
            </span>
            <input
              type="text"
              value={form.complaint_id}
              onChange={(e) => set('complaint_id', e.target.value)}
              aria-invalid={!!fieldError('complaint_id')}
            />
            {fieldError('complaint_id') && (
              <small className="field-error">{fieldError('complaint_id')}</small>
            )}
          </label>
        )}

        <label className="field">
          <span>
            Title <em>required</em>
          </span>
          <input
            type="text"
            value={form.title}
            onChange={(e) => set('title', e.target.value)}
            aria-invalid={!!fieldError('title')}
          />
          {fieldError('title') && <small className="field-error">{fieldError('title')}</small>}
        </label>

        <label className="field">
          <span>Category</span>
          <input
            type="text"
            list="category-options"
            value={form.category}
            onChange={(e) => set('category', e.target.value)}
          />
          <datalist id="category-options">
            {categories.map((c) => (
              <option key={c} value={c} />
            ))}
          </datalist>
        </label>

        <label className="field">
          <span>Status</span>
          <select
            value={form.status}
            onChange={(e) => set('status', e.target.value as ComplaintStatus)}
          >
            {statuses.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
        </label>

        <label className="field field-wide">
          <span>Description</span>
          <textarea
            rows={3}
            value={form.description}
            onChange={(e) => set('description', e.target.value)}
          />
        </label>

        <label className="field">
          <span>Location label</span>
          <input
            type="text"
            value={form.location_label}
            onChange={(e) => set('location_label', e.target.value)}
          />
        </label>

        <label className="field">
          <span>Administrative area</span>
          <input
            type="text"
            value={form.administrative_area}
            onChange={(e) => set('administrative_area', e.target.value)}
          />
        </label>

        <label className="field">
          <span>Latitude</span>
          <input
            type="text"
            inputMode="decimal"
            value={form.latitude}
            onChange={(e) => set('latitude', e.target.value)}
            aria-invalid={!!fieldError('latitude')}
          />
          {fieldError('latitude') && (
            <small className="field-error">{fieldError('latitude')}</small>
          )}
        </label>

        <label className="field">
          <span>Longitude</span>
          <input
            type="text"
            inputMode="decimal"
            value={form.longitude}
            onChange={(e) => set('longitude', e.target.value)}
            aria-invalid={!!fieldError('longitude')}
          />
          {fieldError('longitude') && (
            <small className="field-error">{fieldError('longitude')}</small>
          )}
        </label>

        <label className="field">
          <span>Assigned department</span>
          <input
            type="text"
            value={form.assigned_department}
            onChange={(e) => set('assigned_department', e.target.value)}
          />
        </label>

        <label className="field">
          <span>Severity label (human assessment)</span>
          <input
            type="text"
            value={form.severity_label}
            onChange={(e) => set('severity_label', e.target.value)}
            placeholder="e.g. high, medium, low"
          />
        </label>

        <label className="field field-wide">
          <span>Data-quality flags (optional)</span>
          <input
            type="text"
            value={form.data_quality_flags}
            onChange={(e) => set('data_quality_flags', e.target.value)}
            placeholder="Comma-separated notes about missing or uncertain data"
          />
        </label>

        {mode === 'create' && (
          <label className="field checkbox-field field-wide">
            <input
              type="checkbox"
              checked={form.synthetic}
              onChange={(e) => set('synthetic', e.target.checked)}
            />
            <span>
              Mark as <strong>synthetic demonstration data</strong> (fabricated, not a real
              civic complaint)
            </span>
          </label>
        )}
      </div>

      <div className="form-actions">
        <button type="submit" className="btn btn-primary" disabled={busy}>
          {busy ? 'Saving…' : mode === 'create' ? 'Create complaint' : 'Save changes'}
        </button>
        {onCancel && (
          <button type="button" className="btn btn-secondary" onClick={onCancel} disabled={busy}>
            Cancel
          </button>
        )}
      </div>
    </form>
  )
}
