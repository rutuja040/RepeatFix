import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  ApiError,
  errorMessage,
  getImport,
  getImportFields,
  importCsv,
  importErrorDetails,
  listImports,
} from '../src/services/api'

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('getImportFields', () => {
  it('requests the fields endpoint', async () => {
    const fetchMock = vi.fn(async () => new Response('{"fields":[]}', { status: 200 }))
    vi.stubGlobal('fetch', fetchMock)
    await getImportFields()
    expect(String(fetchMock.mock.calls[0][0])).toContain('/imports/fields')
  })
})

describe('importCsv', () => {
  it('posts multipart form data with file, flags, and mapping', async () => {
    const fetchMock = vi.fn(
      async () => new Response('{"status":"completed","accepted_rows":1}', { status: 200 }),
    )
    vi.stubGlobal('fetch', fetchMock)

    const file = new File(['a,b\n1,2\n'], 'data.csv', { type: 'text/csv' })
    await importCsv(file, {
      mapping: { title: 'subject' },
      sourceLabel: 'ds-1',
      synthetic: true,
      dryRun: true,
    })

    const [url, init] = fetchMock.mock.calls[0]
    expect(String(url)).toContain('/imports/csv')
    expect((init as RequestInit).method).toBe('POST')
    const body = (init as RequestInit).body as FormData
    expect(body.get('file')).toBe(file)
    expect(body.get('source_label')).toBe('ds-1')
    expect(body.get('synthetic')).toBe('true')
    expect(body.get('dry_run')).toBe('true')
    expect(body.get('mapping')).toBe(JSON.stringify({ title: 'subject' }))
  })

  it('omits mapping when empty and defaults flags to false', async () => {
    const fetchMock = vi.fn(async () => new Response('{}', { status: 200 }))
    vi.stubGlobal('fetch', fetchMock)
    const file = new File(['x'], 'data.csv')
    await importCsv(file)
    const body = (fetchMock.mock.calls[0][1] as RequestInit).body as FormData
    expect(body.get('mapping')).toBeNull()
    expect(body.get('synthetic')).toBe('false')
    expect(body.get('dry_run')).toBe('false')
    expect(body.get('source_label')).toBeNull()
  })
})

describe('listImports', () => {
  it('reads total from the X-Total-Count header', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        new Response(JSON.stringify([{ import_id: 'i1' }]), {
          status: 200,
          headers: { 'X-Total-Count': '3' },
        }),
      ),
    )
    const result = await listImports({ skip: 0, limit: 10 })
    expect(result.total).toBe(3)
    expect(result.items).toHaveLength(1)
  })
})

describe('getImport', () => {
  it('encodes the import id', async () => {
    const fetchMock = vi.fn(async () => new Response('{}', { status: 200 }))
    vi.stubGlobal('fetch', fetchMock)
    await getImport('a b')
    expect(String(fetchMock.mock.calls[0][0])).toContain('/imports/a%20b')
  })
})

describe('import error envelope', () => {
  it('surfaces the message from the { message, details } detail', () => {
    const err = new ApiError(422, 'Request failed (422)', {
      message: 'title is required and not mapped.',
      details: { missing_required: ['title'] },
    })
    expect(errorMessage(err)).toBe('title is required and not mapped.')
    expect(importErrorDetails(err)).toEqual({ missing_required: ['title'] })
  })

  it('returns null details for unrelated errors', () => {
    expect(importErrorDetails(new ApiError(500, 'Request failed (500)', 'boom'))).toBeNull()
  })
})
