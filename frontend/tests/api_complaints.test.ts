import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  ApiError,
  buildQuery,
  createComplaint,
  errorMessage,
  extractFieldErrors,
  getComplaint,
  listComplaints,
} from '../src/services/api'

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('buildQuery', () => {
  it('omits empty, null, and undefined values', () => {
    expect(buildQuery({ a: 1, b: '', c: null, d: undefined, e: 'x y' })).toBe('?a=1&e=x+y')
  })

  it('returns empty string when nothing to encode', () => {
    expect(buildQuery({})).toBe('')
    expect(buildQuery(undefined)).toBe('')
  })

  it('encodes booleans and numbers', () => {
    expect(buildQuery({ synthetic: true, skip: 0 })).toBe('?synthetic=true&skip=0')
  })
})

describe('extractFieldErrors', () => {
  it('maps FastAPI validation errors, dropping the body prefix', () => {
    const detail = [
      { loc: ['body', 'latitude'], msg: 'must be <= 90', type: 'less_than_equal' },
      { loc: ['body', 'title'], msg: 'Field required', type: 'missing' },
    ]
    expect(extractFieldErrors(detail)).toEqual({
      latitude: 'must be <= 90',
      title: 'Field required',
    })
  })

  it('returns empty object for non-array detail', () => {
    expect(extractFieldErrors('nope')).toEqual({})
  })
})

describe('errorMessage', () => {
  it('renders per-field 422 messages', () => {
    const err = new ApiError(422, 'Request failed (422)', [
      { loc: ['body', 'title'], msg: 'Field required' },
    ])
    expect(errorMessage(err)).toBe('title: Field required')
  })

  it('uses detail string for 400', () => {
    const err = new ApiError(400, 'Request failed (400)', 'Complaint ID already exists')
    expect(errorMessage(err)).toBe('Complaint ID already exists')
  })

  it('handles network errors', () => {
    expect(errorMessage(new ApiError(0, 'Network error: could not reach the backend'))).toContain(
      'Network error',
    )
  })
})

describe('listComplaints', () => {
  it('returns items and total from the X-Total-Count header', async () => {
    const fetchMock = vi.fn(async () =>
      new Response(JSON.stringify([{ complaint_id: 'A' }]), {
        status: 200,
        headers: { 'Content-Type': 'application/json', 'X-Total-Count': '7' },
      }),
    )
    vi.stubGlobal('fetch', fetchMock)

    const result = await listComplaints({ status: 'open', skip: 10, limit: 10 })
    expect(result.total).toBe(7)
    expect(result.items).toHaveLength(1)
    const calledUrl = String(fetchMock.mock.calls[0][0])
    expect(calledUrl).toContain('/complaints?')
    expect(calledUrl).toContain('status=open')
    expect(calledUrl).toContain('skip=10')
  })

  it('falls back to item count when the header is missing', async () => {
    vi.stubGlobal('fetch', async () =>
      new Response(JSON.stringify([{ complaint_id: 'A' }, { complaint_id: 'B' }]), {
        status: 200,
      }),
    )
    const result = await listComplaints()
    expect(result.total).toBe(2)
  })
})

describe('getComplaint', () => {
  it('encodes the complaint id in the path', async () => {
    const fetchMock = vi.fn(async () =>
      new Response(JSON.stringify({ complaint_id: 'A B' }), { status: 200 }),
    )
    vi.stubGlobal('fetch', fetchMock)
    await getComplaint('A B')
    expect(String(fetchMock.mock.calls[0][0])).toContain('/complaints/A%20B')
  })

  it('throws ApiError with parsed field errors on 422', async () => {
    vi.stubGlobal('fetch', async () =>
      new Response(JSON.stringify({ detail: [{ loc: ['body', 'title'], msg: 'Field required' }] }), {
        status: 422,
      }),
    )
    await expect(createComplaint({ title: '' })).rejects.toMatchObject({
      status: 422,
      fieldErrors: { title: 'Field required' },
    })
  })
})
