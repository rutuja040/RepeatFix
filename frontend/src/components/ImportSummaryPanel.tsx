import { formatDateTime } from '../format'
import type { ImportSummary } from '../types'

interface ImportSummaryPanelProps {
  summary: ImportSummary
}

function IssueStatusBadge({ status }: { status: string }) {
  return <span className={`status status-issue-${status}`}>{status}</span>
}

/** Renders counts, mapping, and row-level issues for an import or dry-run. */
export function ImportSummaryPanel({ summary }: ImportSummaryPanelProps) {
  const counts: Array<[string, number]> = [
    ['Total rows', summary.total_rows],
    ['Accepted', summary.accepted_rows],
    ['Rejected', summary.rejected_rows],
    ['Skipped', summary.skipped_rows],
    ['Flagged', summary.flagged_rows],
    ['Exact duplicates', summary.exact_duplicate_rows],
    ['Potential duplicates', summary.potential_duplicate_rows],
  ]

  return (
    <div className="import-summary">
      <div className={`alert alert-${summary.status === 'failed' ? 'error' : 'info'}`}>
        <div className="alert-body">
          {summary.dry_run ? 'Dry-run preview' : 'Import result'} — status:{' '}
          <strong>{summary.status}</strong>
          {summary.message ? ` — ${summary.message}` : ''}
        </div>
      </div>

      <dl className="summary-grid">
        {counts.map(([label, value]) => (
          <div key={label} className="summary-cell">
            <dt>{label}</dt>
            <dd className="mono">{value}</dd>
          </div>
        ))}
      </dl>

      <p className="muted small">
        Dataset identity: <span className="mono">{summary.dataset_identity ?? '—'}</span>
        {summary.content_sha256 && (
          <>
            {' · '}content sha256: <span className="mono">{summary.content_sha256.slice(0, 12)}…</span>
          </>
        )}
      </p>

      {Object.keys(summary.mapping).length > 0 && (
        <details className="import-details">
          <summary>Column mapping used</summary>
          <ul>
            {Object.entries(summary.mapping).map(([target, column]) => (
              <li key={target}>
                <span className="mono">{target}</span> ← <span className="mono">{column}</span>
              </li>
            ))}
          </ul>
        </details>
      )}

      {summary.unmatched_columns.length > 0 && (
        <p className="muted small">
          Unmapped source columns (preserved verbatim):{' '}
          <span className="mono">{summary.unmatched_columns.join(', ')}</span>
        </p>
      )}

      <h3>Row issues</h3>
      {summary.issues.length === 0 ? (
        <p className="muted">No row issues.</p>
      ) : (
        <>
          <div className="table-wrap">
            <table className="data-table issues-table">
              <thead>
                <tr>
                  <th scope="col">Row</th>
                  <th scope="col">Field</th>
                  <th scope="col">Code</th>
                  <th scope="col">Message</th>
                  <th scope="col">Status</th>
                </tr>
              </thead>
              <tbody>
                {summary.issues.map((issue, i) => (
                  <tr key={`${issue.row_number}-${issue.code}-${i}`}>
                    <td className="mono">{issue.row_number}</td>
                    <td className="mono">{issue.field ?? '—'}</td>
                    <td className="mono">{issue.code}</td>
                    <td>{issue.message}</td>
                    <td>
                      <IssueStatusBadge status={issue.status} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {summary.issues_truncated && (
            <p className="muted small">
              Only the first {summary.issues.length} issues are shown; the stored record may be
              truncated.
            </p>
          )}
        </>
      )}

      {summary.import_id && (
        <p className="muted small">
          Import ID: <span className="mono">{summary.import_id}</span> ·{' '}
          {formatDateTime(summary.created_at)}
        </p>
      )}
    </div>
  )
}
