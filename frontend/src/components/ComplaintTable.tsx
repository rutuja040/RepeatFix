import { Link } from 'react-router-dom'

import { formatScore, formatDateTime, statusLabel } from '../format'
import type { Complaint } from '../types'

interface ComplaintTableProps {
  items: Complaint[]
}

export function ComplaintTable({ items }: ComplaintTableProps) {
  return (
    <div className="table-wrap">
      <table className="data-table">
        <thead>
          <tr>
            <th scope="col">ID</th>
            <th scope="col">Title</th>
            <th scope="col">Category</th>
            <th scope="col">Status</th>
            <th scope="col">Location</th>
            <th scope="col">Created</th>
            <th scope="col">Priority</th>
          </tr>
        </thead>
        <tbody>
          {items.map((c) => (
            <tr key={c.id}>
              <td className="mono">
                <Link to={`/complaints/${encodeURIComponent(c.complaint_id)}`}>
                  {c.complaint_id}
                </Link>
              </td>
              <td>
                {c.title}
                {c.synthetic && <span className="badge badge-synthetic">synthetic</span>}
              </td>
              <td>{c.category ?? '—'}</td>
              <td>
                <span className={`status status-${c.status}`}>{statusLabel(c.status)}</span>
              </td>
              <td>{c.location_label ?? c.administrative_area ?? '—'}</td>
              <td>{formatDateTime(c.created_at)}</td>
              <td className="mono">{formatScore(c.priority_score)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
