import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { ImportsPage } from '../src/pages/ImportsPage'
import * as api from '../src/services/api'
import type { ImportFieldsResponse, ImportSummary } from '../src/types'

vi.mock('../src/services/api', () => ({
  getImportFields: vi.fn(),
  listImports: vi.fn(),
  importCsv: vi.fn(),
  getImport: vi.fn(),
  errorMessage: (e: unknown) => (e instanceof Error ? e.message : String(e)),
  importErrorDetails: () => null,
}))

const mocked = vi.mocked(api)

const fields: ImportFieldsResponse = {
  fields: [
    {
      name: 'complaint_id',
      required: true,
      max_length: 100,
      description: 'id',
      aliases: ['complaint_id'],
    },
    { name: 'title', required: true, max_length: 500, description: 't', aliases: ['title'] },
  ],
  required_fields: ['complaint_id', 'title'],
  limits: { max_size_mb: 100, max_rows: 50000, supported_types: ['text/csv'] },
}

const completed: ImportSummary = {
  status: 'completed',
  dry_run: false,
  total_rows: 1,
  accepted_rows: 1,
  rejected_rows: 0,
  skipped_rows: 0,
  flagged_rows: 0,
  exact_duplicate_rows: 0,
  potential_duplicate_rows: 0,
  issues_truncated: false,
  import_id: 'imp-1',
  source_filename: 'demo.csv',
  source_label: null,
  dataset_identity: 'cols:abcd',
  content_sha256: null,
  created_at: '2026-01-01T00:00:00+00:00',
  mapping: {},
  detected_columns: [],
  unmatched_columns: [],
  ambiguous: {},
  missing_required: [],
  issues: [],
  message: null,
}

function renderPage() {
  return render(
    <MemoryRouter>
      <ImportsPage />
    </MemoryRouter>,
  )
}

beforeEach(() => {
  vi.clearAllMocks()
  mocked.getImportFields.mockResolvedValue(fields)
  mocked.listImports.mockResolvedValue({ items: [], total: 0 })
})

describe('ImportsPage', () => {
  it('shows configured limits and history empty state', async () => {
    renderPage()
    expect(await screen.findByText(/50,000 rows/)).toBeInTheDocument()
    expect(await screen.findByText('No imports yet')).toBeInTheDocument()
  })

  it('keeps import actions disabled until a file is chosen', async () => {
    renderPage()
    const preview = await screen.findByRole('button', { name: /Preview/ })
    const importBtn = screen.getByRole('button', { name: 'Import' })
    expect(preview).toBeDisabled()
    expect(importBtn).toBeDisabled()

    const file = new File(['complaint_id,title\nA,B\n'], 'demo.csv', { type: 'text/csv' })
    fireEvent.change(screen.getByLabelText(/CSV file/), { target: { files: [file] } })

    await waitFor(() => expect(preview).toBeEnabled())
    expect(importBtn).toBeEnabled()
  })

  it('imports the file and renders the result', async () => {
    mocked.importCsv.mockResolvedValue(completed)
    renderPage()

    const file = new File(['complaint_id,title\nA,B\n'], 'demo.csv', { type: 'text/csv' })
    fireEvent.change(await screen.findByLabelText(/CSV file/), { target: { files: [file] } })

    fireEvent.click(screen.getByRole('button', { name: 'Import' }))

    expect(await screen.findByText('Import result')).toBeInTheDocument()
    expect(mocked.importCsv).toHaveBeenCalledTimes(1)
    expect(mocked.importCsv.mock.calls[0][1]).toMatchObject({ dryRun: false })
  })

  it('previews without persisting and reveals the mapping editor', async () => {
    mocked.importCsv.mockResolvedValue({
      ...completed,
      dry_run: true,
      import_id: null,
      detected_columns: ['complaint_id', 'title'],
      mapping: { complaint_id: 'complaint_id', title: 'title' },
    })
    renderPage()

    const file = new File(['complaint_id,title\nA,B\n'], 'demo.csv', { type: 'text/csv' })
    fireEvent.change(await screen.findByLabelText(/CSV file/), { target: { files: [file] } })
    fireEvent.click(screen.getByRole('button', { name: /Preview/ }))

    expect(await screen.findByText(/Review column mapping/)).toBeInTheDocument()
    expect(mocked.importCsv.mock.calls[0][1]).toMatchObject({ dryRun: true })
  })
})
