import type {
  CategoryCount,
  Complaint,
  ComplaintHistoryItem,
  ComplaintInput,
  ComplaintMeta,
  ImportFieldsResponse,
  ImportListItem,
  ImportSummary,
  ListParams,
} from '../types'

const API_BASE = (import.meta.env.VITE_API_BASE_URL as string) || 'http://localhost:8000/api/v1'

export class ApiError extends Error {
  status: number
  detail?: unknown
  fieldErrors: Record<string, string>
  constructor(status: number, message: string, detail?: unknown) {
    super(message)
    this.status = status
    this.detail = detail
    this.fieldErrors = extractFieldErrors(detail)
  }
}

export function getApiBase(): string {
  return API_BASE
}

/** Build a query string from a params object, omitting null/undefined/empty values. */
export function buildQuery(params: Record<string, unknown> | undefined): string {
  if (!params) return ''
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null || value === '') continue
    if (Array.isArray(value)) {
      for (const v of value) search.append(key, String(v))
    } else {
      search.append(key, String(value))
    }
  }
  const qs = search.toString()
  return qs ? `?${qs}` : ''
}

/** Turn a FastAPI validation error body into a { field: message } map. */
export function extractFieldErrors(detail: unknown): Record<string, string> {
  const out: Record<string, string> = {}
  if (!Array.isArray(detail)) return out
  for (const item of detail) {
    if (!item || typeof item !== 'object') continue
    const loc = Array.isArray((item as { loc?: unknown }).loc)
      ? ((item as { loc: unknown[] }).loc as unknown[]).filter((p) => p !== 'body')
      : []
    const field = loc.length ? loc.join('.') : '_error'
    const msg = String((item as { msg?: unknown }).msg ?? 'Invalid value')
    if (!out[field]) out[field] = msg
  }
  return out
}

/** Human-readable message for any thrown error. */
export function errorMessage(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.status === 0) return err.message
    if (err.status === 404) return 'Not found'
    if (isDetailObject(err.detail) && typeof err.detail.message === 'string') {
      return err.detail.message
    }
    if (err.status === 422) {
      const fields = Object.entries(err.fieldErrors)
      if (fields.length) return fields.map(([f, m]) => `${f}: ${m}`).join('; ')
      return 'Validation failed'
    }
    if (typeof err.detail === 'string') return err.detail
    return `Request failed (${err.status})`
  }
  if (err instanceof Error) return err.message
  return 'Unexpected error'
}

/** True when an error detail is the import `{ message, details }` envelope. */
export function isDetailObject(detail: unknown): detail is { message: string; details?: unknown } {
  return !!detail && typeof detail === 'object' && 'message' in (detail as Record<string, unknown>)
}

/** Structured `details` payload from an import error, if present. */
export function importErrorDetails(err: unknown): Record<string, unknown> | null {
  if (err instanceof ApiError && isDetailObject(err.detail)) {
    const details = err.detail.details
    if (details && typeof details === 'object' && !Array.isArray(details)) {
      return details as Record<string, unknown>
    }
  }
  return null
}

async function rawRequest(path: string, init?: RequestInit): Promise<Response> {
  const url = `${API_BASE}${path}`
  try {
    return await fetch(url, {
      ...init,
      headers: { 'Content-Type': 'application/json', ...(init?.headers || {}) },
    })
  } catch (e) {
    throw new ApiError(0, 'Network error: could not reach the backend', e)
  }
}

async function parseBody(res: Response): Promise<unknown> {
  const text = await res.text()
  if (!text) return null
  try {
    return JSON.parse(text)
  } catch {
    return text
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await rawRequest(path, init)
  const data = await parseBody(res)
  if (!res.ok) {
    const detail = (data as { detail?: unknown })?.detail ?? data
    throw new ApiError(res.status, `Request failed (${res.status})`, detail)
  }
  return data as T
}

export interface ListResult<T> {
  items: T[]
  total: number
}

async function requestList<T>(path: string): Promise<ListResult<T>> {
  const res = await rawRequest(path)
  const data = await parseBody(res)
  if (!res.ok) {
    const detail = (data as { detail?: unknown })?.detail ?? data
    throw new ApiError(res.status, `Request failed (${res.status})`, detail)
  }
  const items = (Array.isArray(data) ? data : []) as T[]
  const header = res.headers.get('X-Total-Count')
  const total = header !== null ? Number(header) : items.length
  return { items, total: Number.isFinite(total) ? total : items.length }
}

export interface HealthResponse {
  status: string
}

export function getHealth(): Promise<HealthResponse> {
  return request<HealthResponse>('/health')
}

export function listComplaints(params?: ListParams): Promise<ListResult<Complaint>> {
  return requestList<Complaint>(`/complaints${buildQuery(params as Record<string, unknown>)}`)
}

export function getComplaint(complaintId: string): Promise<Complaint> {
  return request<Complaint>(`/complaints/${encodeURIComponent(complaintId)}`)
}

export function createComplaint(payload: ComplaintInput): Promise<Complaint> {
  return request<Complaint>('/complaints', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export function updateComplaint(
  complaintId: string,
  payload: ComplaintInput,
): Promise<Complaint> {
  return request<Complaint>(`/complaints/${encodeURIComponent(complaintId)}`, {
    method: 'PATCH',
    body: JSON.stringify(payload),
  })
}

export function getComplaintHistory(complaintId: string): Promise<ComplaintHistoryItem[]> {
  return request<ComplaintHistoryItem[]>(
    `/complaints/${encodeURIComponent(complaintId)}/history`,
  )
}

export function getComplaintMeta(): Promise<ComplaintMeta> {
  return request<ComplaintMeta>('/meta/complaints')
}

export function getImportFields(): Promise<ImportFieldsResponse> {
  return request<ImportFieldsResponse>('/imports/fields')
}

export interface ImportCsvOptions {
  mapping?: Record<string, string>
  sourceLabel?: string
  synthetic?: boolean
  dryRun?: boolean
}

async function requestForm<T>(path: string, form: FormData): Promise<T> {
  let res: Response
  try {
    res = await fetch(`${API_BASE}${path}`, { method: 'POST', body: form })
  } catch (e) {
    throw new ApiError(0, 'Network error: could not reach the backend', e)
  }
  const data = await parseBody(res)
  if (!res.ok) {
    const detail = (data as { detail?: unknown })?.detail ?? data
    throw new ApiError(res.status, `Request failed (${res.status})`, detail)
  }
  return data as T
}

export function importCsv(file: File, options: ImportCsvOptions = {}): Promise<ImportSummary> {
  const form = new FormData()
  form.append('file', file)
  if (options.mapping && Object.keys(options.mapping).length) {
    form.append('mapping', JSON.stringify(options.mapping))
  }
  if (options.sourceLabel) form.append('source_label', options.sourceLabel)
  form.append('synthetic', String(options.synthetic ?? false))
  form.append('dry_run', String(options.dryRun ?? false))
  return requestForm<ImportSummary>('/imports/csv', form)
}

export function listImports(params?: ListParams): Promise<ListResult<ImportListItem>> {
  return requestList<ImportListItem>(`/imports${buildQuery(params as Record<string, unknown>)}`)
}

export function getImport(importId: string): Promise<ImportSummary> {
  return request<ImportSummary>(`/imports/${encodeURIComponent(importId)}`)
}

export type { CategoryCount }
