import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'

import { Alert } from '../components/Alert'
import { EmptyState } from '../components/EmptyState'
import { ImportMappingEditor } from '../components/ImportMappingEditor'
import { ImportSummaryPanel } from '../components/ImportSummaryPanel'
import { Pagination } from '../components/Pagination'
import { Spinner } from '../components/Spinner'
import { formatDateTime } from '../format'
import { PAGE_SIZE } from '../filtersUrl'
import {
  errorMessage,
  getImport,
  getImportFields,
  importCsv,
  importErrorDetails,
  listImports,
} from '../services/api'
import type { ImportFieldsResponse, ImportListItem, ImportSummary } from '../types'

type Busy = null | 'preview' | 'import' | 'history'

function importStatusClass(status: string): string {
  if (status === 'completed') return 'status-resolved'
  if (status === 'failed') return 'status-closed'
  return 'status-in_progress'
}

export function ImportsPage() {
  const [fields, setFields] = useState<ImportFieldsResponse | null>(null)
  const [fieldsError, setFieldsError] = useState<string | null>(null)

  const [file, setFile] = useState<File | null>(null)
  const [sourceLabel, setSourceLabel] = useState('')
  const [synthetic, setSynthetic] = useState(false)
  const [mapping, setMapping] = useState<Record<string, string>>({})

  const [preview, setPreview] = useState<ImportSummary | null>(null)
  const [result, setResult] = useState<ImportSummary | null>(null)
  const [busy, setBusy] = useState<Busy>(null)
  const [error, setError] = useState<string | null>(null)

  const [history, setHistory] = useState<ImportListItem[]>([])
  const [historyTotal, setHistoryTotal] = useState(0)
  const [skip, setSkip] = useState(0)
  const [historyLoading, setHistoryLoading] = useState(true)
  const [historyError, setHistoryError] = useState<string | null>(null)

  useEffect(() => {
    let active = true
    getImportFields()
      .then((res) => {
        if (active) setFields(res)
      })
      .catch((e) => {
        if (active) setFieldsError(errorMessage(e))
      })
    return () => {
      active = false
    }
  }, [])

  const loadHistory = useCallback(
    async (nextSkip: number) => {
      setHistoryLoading(true)
      try {
        const res = await listImports({ skip: nextSkip, limit: PAGE_SIZE })
        setHistory(res.items)
        setHistoryTotal(res.total)
        setHistoryError(null)
      } catch (e) {
        setHistoryError(errorMessage(e))
        setHistory([])
        setHistoryTotal(0)
      } finally {
        setHistoryLoading(false)
      }
    },
    [],
  )

  useEffect(() => {
    void loadHistory(skip)
  }, [loadHistory, skip])

  function onFileChange(selected: File | null) {
    setFile(selected)
    setPreview(null)
    setResult(null)
    setMapping({})
    setError(null)
  }

  async function runImport(dryRun: boolean) {
    if (!file || busy) return
    setBusy(dryRun ? 'preview' : 'import')
    setError(null)
    try {
      const summary = await importCsv(file, {
        mapping,
        sourceLabel: sourceLabel.trim() || undefined,
        synthetic,
        dryRun,
      })
      if (dryRun) {
        setPreview(summary)
        setMapping(summary.mapping)
      } else {
        setResult(summary)
        setSkip(0)
        await loadHistory(0)
      }
    } catch (e) {
      const details = importErrorDetails(e)
      setError(errorMessage(e))
      if (details) {
        const missing = details.missing_required
        if (Array.isArray(missing) && missing.length) {
          setError(`${errorMessage(e)} (unmapped: ${missing.join(', ')})`)
        }
      }
    } finally {
      setBusy(null)
    }
  }

  async function openHistoryItem(importId: string) {
    setError(null)
    try {
      const summary = await getImport(importId)
      setResult(summary)
      setPreview(null)
      window.scrollTo({ top: 0, behavior: 'smooth' })
    } catch (e) {
      setError(errorMessage(e))
    }
  }

  const previewing = busy === 'preview'
  const importing = busy === 'import'
  const columns = preview?.detected_columns ?? []

  return (
    <div className="page">
      <div className="page-head">
        <h1>Import data</h1>
        <Link className="btn btn-secondary" to="/complaints">
          View complaints
        </Link>
      </div>

      <p className="muted">
        Upload a UTF-8 CSV of complaints. Column headers are auto-detected and can be overridden.
        Nothing is written until you run the import; use <strong>Preview</strong> to validate first.
        Rows that fail validation are reported individually — an import never silently drops or
        merges records.
      </p>

      {fieldsError && <Alert kind="error">Could not load import metadata: {fieldsError}</Alert>}
      {!fields && !fieldsError && <Spinner label="Loading import options…" />}

      {fields && (
        <p className="muted small">
          Limits: up to {fields.limits.max_rows.toLocaleString()} rows and{' '}
          {fields.limits.max_size_mb} MB per file · types: {fields.limits.supported_types.join(', ')}
        </p>
      )}

      {error && (
        <Alert kind="error" onClose={() => setError(null)}>
          {error}
        </Alert>
      )}

      <section className="card" aria-labelledby="upload-heading">
        <h2 id="upload-heading">1. Choose a file</h2>
        <div className="form-grid">
          <label className="field field-wide">
            <span>CSV file</span>
            <input
              type="file"
              accept=".csv,text/csv"
              onChange={(e) => onFileChange(e.target.files?.[0] ?? null)}
            />
          </label>

          <label className="field">
            <span>Source label (dataset identity)</span>
            <input
              type="text"
              value={sourceLabel}
              onChange={(e) => setSourceLabel(e.target.value)}
              placeholder="e.g. nyc-311-2025"
            />
          </label>

          <label className="field checkbox-field">
            <input
              type="checkbox"
              checked={synthetic}
              onChange={(e) => setSynthetic(e.target.checked)}
            />
            <span>Mark rows as synthetic demonstration data</span>
          </label>
        </div>
        <p className="muted small">
          The source label groups records for idempotent re-imports. Without a label, identity is
          derived from the column names.
        </p>
        <div className="form-actions">
          <button
            type="button"
            className="btn btn-secondary"
            disabled={!file || !!busy}
            onClick={() => runImport(true)}
          >
            {previewing ? 'Previewing…' : 'Preview (dry run)'}
          </button>
          <button
            type="button"
            className="btn btn-primary"
            disabled={!file || !!busy}
            onClick={() => runImport(false)}
          >
            {importing ? 'Importing…' : 'Import'}
          </button>
        </div>
      </section>

      {preview && columns.length > 0 && (
        <section className="card" aria-labelledby="mapping-heading">
          <h2 id="mapping-heading">2. Review column mapping</h2>
          <ImportMappingEditor
            fields={fields?.fields ?? []}
            columns={columns}
            value={mapping}
            onChange={setMapping}
            disabled={!!busy}
          />
          <div className="form-actions">
            <button
              type="button"
              className="btn btn-secondary"
              disabled={!!busy}
              onClick={() => setMapping(preview.mapping)}
            >
              Reset to detected
            </button>
          </div>
        </section>
      )}

      {preview && (
        <section className="card" aria-labelledby="preview-heading">
          <h2 id="preview-heading">Preview</h2>
          <ImportSummaryPanel summary={preview} />
        </section>
      )}

      {result && (
        <section className="card" aria-labelledby="result-heading">
          <h2 id="result-heading">Import result</h2>
          <ImportSummaryPanel summary={result} />
        </section>
      )}

      <section className="card" aria-labelledby="history-heading">
        <h2 id="history-heading">Import history</h2>
        {historyError && (
          <Alert kind="error">
            Could not load history: {historyError}
            <button type="button" className="link-btn" onClick={() => void loadHistory(skip)}>
              Retry
            </button>
          </Alert>
        )}
        {historyLoading && <Spinner label="Loading history…" />}
        {!historyLoading && !historyError && history.length === 0 && (
          <EmptyState title="No imports yet" message="Imported files will be listed here." />
        )}
        {!historyLoading && history.length > 0 && (
          <>
            <div className="table-wrap">
              <table className="data-table">
                <thead>
                  <tr>
                    <th scope="col">When</th>
                    <th scope="col">File</th>
                    <th scope="col">Label</th>
                    <th scope="col">Status</th>
                    <th scope="col">Total</th>
                    <th scope="col">Accepted</th>
                    <th scope="col">Rejected</th>
                    <th scope="col">Skipped</th>
                    <th scope="col">Flagged</th>
                    <th scope="col" />
                  </tr>
                </thead>
                <tbody>
                  {history.map((item) => (
                    <tr key={item.import_id}>
                      <td>{formatDateTime(item.created_at)}</td>
                      <td>{item.source_filename ?? '—'}</td>
                      <td>{item.source_label ?? '—'}</td>
                      <td>
                        <span className={`status ${importStatusClass(item.status)}`}>
                          {item.status}
                        </span>
                      </td>
                      <td className="mono">{item.total_rows}</td>
                      <td className="mono">{item.accepted_rows}</td>
                      <td className="mono">{item.rejected_rows}</td>
                      <td className="mono">{item.skipped_rows}</td>
                      <td className="mono">{item.flagged_rows}</td>
                      <td>
                        <button
                          type="button"
                          className="link-btn"
                          onClick={() => void openHistoryItem(item.import_id)}
                        >
                          View
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <Pagination skip={skip} limit={PAGE_SIZE} total={historyTotal} onPageChange={setSkip} />
          </>
        )}
      </section>
    </div>
  )
}
