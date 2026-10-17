import { EmptyState } from './EmptyState'
import { formatDateTime } from '../format'
import type { ComplaintHistoryItem } from '../types'

const FIELD_LABELS: Record<string, string> = {
  status: 'Status',
  resolved_at: 'Resolution time',
  title: 'Title',
  description: 'Description',
  category: 'Category',
  latitude: 'Latitude',
  longitude: 'Longitude',
  location_label: 'Location label',
  administrative_area: 'Administrative area',
  assigned_department: 'Assigned department',
  severity_label: 'Severity label',
  data_quality_flags: 'Data-quality flags',
}

function label(field: string): string {
  return FIELD_LABELS[field] ?? field
}

function display(value: string | null): string {
  if (value === null || value === '') return '—'
  return value
}

export function HistoryList({ items }: { items: ComplaintHistoryItem[] }) {
  if (!items.length) {
    return <EmptyState title="No history yet" message="Changes to this complaint will appear here." />
  }
  return (
    <div className="table-wrap">
      <table className="data-table">
        <thead>
          <tr>
            <th scope="col">When</th>
            <th scope="col">Field</th>
            <th scope="col">From</th>
            <th scope="col">To</th>
            <th scope="col">By</th>
          </tr>
        </thead>
        <tbody>
          {items.map((h) => (
            <tr key={h.id}>
              <td>{formatDateTime(h.changed_at)}</td>
              <td>{label(h.field_name)}</td>
              <td className="mono">{display(h.old_value)}</td>
              <td className="mono">{display(h.new_value)}</td>
              <td>{h.changed_by ?? '—'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
