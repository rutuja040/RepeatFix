import { describe, expect, it } from 'vitest'

import {
  EMPTY_FILTERS,
  type FilterState,
} from '../src/components/ComplaintFilters'
import {
  filtersFromParams,
  listParamsFromFilters,
  searchParamsFromFilters,
} from '../src/filtersUrl'

const base: FilterState = { ...EMPTY_FILTERS }

describe('filtersFromParams / searchParamsFromFilters', () => {
  it('round-trips active filters', () => {
    const filters: FilterState = {
      ...base,
      q: 'pothole',
      status: 'open',
      category: 'Roads',
      resolved: 'false',
      synthetic: 'true',
      sort_by: 'title',
      sort_dir: 'asc',
    }
    const params = searchParamsFromFilters(filters)
    const back = filtersFromParams(params)
    expect(back).toEqual(filters)
  })

  it('uses defaults for an empty param set', () => {
    const back = filtersFromParams(new URLSearchParams())
    expect(back).toEqual(EMPTY_FILTERS)
  })

  it('ignores unknown boolean values', () => {
    const back = filtersFromParams(new URLSearchParams('resolved=maybe'))
    expect(back.resolved).toBe('')
  })
})

describe('listParamsFromFilters', () => {
  it('converts date inputs to inclusive UTC bounds', () => {
    const filters: FilterState = {
      ...base,
      created_from: '2026-01-02',
      created_to: '2026-03-04',
    }
    const params = listParamsFromFilters(filters, 0)
    expect(params.created_from).toBe('2026-01-02T00:00:00+00:00')
    expect(params.created_to).toBe('2026-03-04T23:59:59.999999+00:00')
  })

  it('converts boolean selects and carries pagination', () => {
    const filters: FilterState = { ...base, resolved: 'true', synthetic: 'false' }
    const params = listParamsFromFilters(filters, 20, 10)
    expect(params.resolved).toBe(true)
    expect(params.synthetic).toBe(false)
    expect(params.skip).toBe(20)
    expect(params.limit).toBe(10)
  })

  it('omits unset optional filters', () => {
    const params = listParamsFromFilters(base, 0)
    expect(params.q).toBeUndefined()
    expect(params.status).toBeUndefined()
    expect(params.created_from).toBeUndefined()
    expect(params.resolved).toBeUndefined()
  })
})
