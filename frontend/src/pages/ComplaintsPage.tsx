import { useEffect, useMemo, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'

import { Alert } from '../components/Alert'
import { ComplaintFilters, EMPTY_FILTERS } from '../components/ComplaintFilters'
import type { FilterState } from '../components/ComplaintFilters'
import { ComplaintTable } from '../components/ComplaintTable'
import { EmptyState } from '../components/EmptyState'
import { Pagination } from '../components/Pagination'
import { Spinner } from '../components/Spinner'
import { errorMessage, getComplaintMeta, listComplaints } from '../services/api'
import {
  PAGE_SIZE,
  filtersFromParams,
  listParamsFromFilters,
  searchParamsFromFilters,
} from '../filtersUrl'
import type { Complaint, ComplaintMeta } from '../types'

export function ComplaintsPage() {
  const [searchParams, setSearchParams] = useSearchParams()

  const filters = useMemo<FilterState>(() => filtersFromParams(searchParams), [searchParams])
  const skip = Math.max(0, Number(searchParams.get('skip') ?? 0) || 0)
  const listParams = useMemo(
    () => listParamsFromFilters(filters, skip, PAGE_SIZE),
    [filters, skip],
  )
  const queryKey = JSON.stringify(listParams)

  const [meta, setMeta] = useState<ComplaintMeta | null>(null)
  const [items, setItems] = useState<Complaint[]>([])
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let active = true
    getComplaintMeta()
      .then((m) => {
        if (active) setMeta(m)
      })
      .catch(() => {
        /* meta is optional for the list; a failure only affects filter options */
      })
    return () => {
      active = false
    }
  }, [])

  useEffect(() => {
    let active = true
    setLoading(true)
    const timer = setTimeout(() => {
      listComplaints(listParams)
        .then((res) => {
          if (!active) return
          setItems(res.items)
          setTotal(res.total)
          setError(null)
        })
        .catch((e) => {
          if (!active) return
          setError(errorMessage(e))
          setItems([])
          setTotal(0)
        })
        .finally(() => {
          if (active) setLoading(false)
        })
    }, 250)
    return () => {
      active = false
      clearTimeout(timer)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [queryKey])

  function updateFilters(patch: Partial<FilterState>) {
    const next = { ...filters, ...patch }
    const sp = searchParamsFromFilters(next)
    sp.set('skip', '0')
    setSearchParams(sp)
  }

  function resetFilters() {
    setSearchParams(searchParamsFromFilters(EMPTY_FILTERS))
  }

  function changePage(newSkip: number) {
    const sp = new URLSearchParams(searchParams)
    sp.set('skip', String(Math.max(0, newSkip)))
    setSearchParams(sp)
  }

  const hasActiveFilters =
    !!filters.q ||
    !!filters.status ||
    !!filters.category ||
    !!filters.location ||
    !!filters.created_from ||
    !!filters.created_to ||
    !!filters.resolved ||
    !!filters.synthetic

  return (
    <div className="page">
      <div className="page-head">
        <h1>Complaints</h1>
        <Link className="btn btn-primary" to="/complaints/new">
          + New complaint
        </Link>
      </div>

      <ComplaintFilters
        value={filters}
        meta={meta}
        onChange={updateFilters}
        onReset={resetFilters}
      />

      {error && (
        <Alert kind="error">
          Could not load complaints: {error}
          <button type="button" className="link-btn" onClick={() => changePage(skip)}>
            Retry
          </button>
        </Alert>
      )}

      {loading && <Spinner label="Loading complaints…" />}

      {!loading && !error && items.length === 0 && (
        <EmptyState
          title="No complaints match"
          message={
            hasActiveFilters
              ? 'Try clearing or widening your filters.'
              : 'No complaints have been recorded yet.'
          }
          action={
            hasActiveFilters ? (
              <button type="button" className="btn btn-secondary" onClick={resetFilters}>
                Clear filters
              </button>
            ) : (
              <Link className="btn btn-primary" to="/complaints/new">
                Create the first complaint
              </Link>
            )
          }
        />
      )}

      {!loading && items.length > 0 && (
        <>
          <ComplaintTable items={items} />
          <Pagination skip={skip} limit={PAGE_SIZE} total={total} onPageChange={changePage} />
          <p className="muted small">
            Synthetic rows are clearly marked; they are demonstration data, not real civic
            complaints.
          </p>
        </>
      )}
    </div>
  )
}
