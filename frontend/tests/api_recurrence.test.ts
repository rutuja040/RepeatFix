import { afterEach, describe, expect, it, vi } from 'vitest'
import { analyzeRecurrence } from '../src/services/api'

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('analyzeRecurrence', () => {
  it('POSTs to /recurrence/analyze with complaint_id and max_candidates', async () => {
    const mockResponse = {
      complaint_id: 'C-1',
      analyzed_at: '2026-01-01T00:00:00+00:00',
      relations: [],
      warnings: [],
      assumptions: [],
      missing_data: [],
      has_reliable_coords: false,
      has_reliable_time: false,
    }
    const fetchMock = vi.fn(async () =>
      new Response(JSON.stringify(mockResponse), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    )
    vi.stubGlobal('fetch', fetchMock)

    const result = await analyzeRecurrence('C-1', 50)

    expect(result.complaint_id).toBe('C-1')
    expect(result.relations).toHaveLength(0)
    expect(result.has_reliable_coords).toBe(false)

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(url).toContain('/recurrence/analyze')
    expect(init.method).toBe('POST')
    const body = JSON.parse(init.body as string) as { complaint_id: string; max_candidates: number }
    expect(body.complaint_id).toBe('C-1')
    expect(body.max_candidates).toBe(50)
  })

  it('uses default max_candidates of 50 when not specified', async () => {
    const fetchMock = vi.fn(async () =>
      new Response(
        JSON.stringify({
          complaint_id: 'C-2',
          analyzed_at: '2026-01-01T00:00:00+00:00',
          relations: [],
          warnings: [],
          assumptions: [],
          missing_data: [],
          has_reliable_coords: true,
          has_reliable_time: true,
        }),
        { status: 200 },
      ),
    )
    vi.stubGlobal('fetch', fetchMock)

    await analyzeRecurrence('C-2')
    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    const body = JSON.parse(init.body as string) as { max_candidates: number }
    expect(body.max_candidates).toBe(50)
  })

  it('throws ApiError on non-200 response', async () => {
    vi.stubGlobal('fetch', async () =>
      new Response(JSON.stringify({ detail: 'Complaint not found' }), { status: 404 }),
    )
    await expect(analyzeRecurrence('MISSING')).rejects.toMatchObject({ status: 404 })
  })

  it('throws ApiError with status 0 on network failure', async () => {
    vi.stubGlobal('fetch', async () => { throw new TypeError('Failed to fetch') })
    await expect(analyzeRecurrence('C-3')).rejects.toMatchObject({ status: 0 })
  })

  it('returns relations array when backend finds matches', async () => {
    const rel = {
      related_complaint_id: 'C-OTHER',
      relation_type: 'potential_recurrence',
      confidence_explained: 0.72,
      confidence_label: 'medium',
      reasons: [
        { code: 'text_similarity', description: 'Similar text', weight_contribution: 0.4, evidence: {} },
      ],
      evidence_summary: 'Similar text and nearby location',
      requires_human_review: true,
      is_auto_merge_candidate: false,
    }
    vi.stubGlobal('fetch', async () =>
      new Response(
        JSON.stringify({
          complaint_id: 'C-1',
          analyzed_at: '2026-01-01T00:00:00+00:00',
          relations: [rel],
          warnings: [],
          assumptions: [],
          missing_data: [],
          has_reliable_coords: true,
          has_reliable_time: true,
        }),
        { status: 200 },
      ),
    )
    const result = await analyzeRecurrence('C-1')
    expect(result.relations).toHaveLength(1)
    expect(result.relations[0].related_complaint_id).toBe('C-OTHER')
    expect(result.relations[0].requires_human_review).toBe(true)
    expect(result.relations[0].is_auto_merge_candidate).toBe(false)
  })
})
