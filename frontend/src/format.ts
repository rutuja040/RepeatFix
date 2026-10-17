/** Format an API timestamp (ISO-8601 with +00:00) for display in local time. */
export function formatDateTime(value: string | null | undefined): string {
  if (!value) return '—'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return date.toLocaleString()
}

export function formatDate(value: string | null | undefined): string {
  if (!value) return '—'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return date.toLocaleDateString()
}

export function formatNumber(value: number | null | undefined): string {
  if (value === null || value === undefined) return '—'
  return new Intl.NumberFormat().format(value)
}

export function formatScore(value: number | null | undefined): string {
  if (value === null || value === undefined) return 'Not computed'
  return value.toFixed(3)
}

export const STATUS_LABELS: Record<string, string> = {
  open: 'Open',
  in_progress: 'In progress',
  resolved: 'Resolved',
  closed: 'Closed',
}

export function statusLabel(status: string): string {
  return STATUS_LABELS[status] ?? status
}

/** Convert a date-only input value (`YYYY-MM-DD`) to an inclusive UTC bound. */
export function dateInputToUtcStart(value: string): string | undefined {
  if (!value) return undefined
  return `${value}T00:00:00+00:00`
}

export function dateInputToUtcEnd(value: string): string | undefined {
  if (!value) return undefined
  return `${value}T23:59:59.999999+00:00`
}
