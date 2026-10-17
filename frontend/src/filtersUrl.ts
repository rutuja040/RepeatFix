import type { FilterState } from './components/ComplaintFilters'
import { EMPTY_FILTERS } from './components/ComplaintFilters'
import type { ListParams } from './types'
import { dateInputToUtcEnd, dateInputToUtcStart } from './format'

export const PAGE_SIZE = 10

type ParamReader = { get(key: string): string | null }

function boolParam(value: string | null): '' | 'true' | 'false' {
  if (value === 'true' || value === 'false') return value
  return ''
}

/** Read filter state from URL search params (filters are shareable/bookmarkable). */
export function filtersFromParams(params: ParamReader): FilterState {
  return {
    q: params.get('q') ?? '',
    status: params.get('status') ?? '',
    category: params.get('category') ?? '',
    location: params.get('location') ?? '',
    created_from: params.get('created_from') ?? '',
    created_to: params.get('created_to') ?? '',
    resolved: boolParam(params.get('resolved')),
    synthetic: boolParam(params.get('synthetic')),
    sort_by: params.get('sort_by') ?? EMPTY_FILTERS.sort_by,
    sort_dir: params.get('sort_dir') === 'asc' ? 'asc' : 'desc',
  }
}

/** Serialize filter state into URL search params (omits empty values). */
export function searchParamsFromFilters(filters: FilterState): URLSearchParams {
  const sp = new URLSearchParams()
  if (filters.q) sp.set('q', filters.q)
  if (filters.status) sp.set('status', filters.status)
  if (filters.category) sp.set('category', filters.category)
  if (filters.location) sp.set('location', filters.location)
  if (filters.created_from) sp.set('created_from', filters.created_from)
  if (filters.created_to) sp.set('created_to', filters.created_to)
  if (filters.resolved) sp.set('resolved', filters.resolved)
  if (filters.synthetic) sp.set('synthetic', filters.synthetic)
  if (filters.sort_by) sp.set('sort_by', filters.sort_by)
  if (filters.sort_dir) sp.set('sort_dir', filters.sort_dir)
  return sp
}

/** Convert UI filter state + pagination into backend list params. */
export function listParamsFromFilters(
  filters: FilterState,
  skip: number,
  limit: number = PAGE_SIZE,
): ListParams {
  const params: ListParams = {
    sort_by: filters.sort_by,
    sort_dir: filters.sort_dir,
    skip,
    limit,
  }
  if (filters.q) params.q = filters.q
  if (filters.status) params.status = filters.status
  if (filters.category) params.category = filters.category
  if (filters.location) params.location = filters.location
  if (filters.created_from) params.created_from = dateInputToUtcStart(filters.created_from)
  if (filters.created_to) params.created_to = dateInputToUtcEnd(filters.created_to)
  if (filters.resolved) params.resolved = filters.resolved === 'true'
  if (filters.synthetic) params.synthetic = filters.synthetic === 'true'
  return params
}
