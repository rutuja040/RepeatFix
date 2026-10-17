import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { ImportMappingEditor } from '../src/components/ImportMappingEditor'
import { ImportSummaryPanel } from '../src/components/ImportSummaryPanel'
import type { ImportFieldInfo, ImportSummary } from '../src/types'

const fields: ImportFieldInfo[] = [
  {
    name: 'complaint_id',
    required: true,
    max_length: 100,
    description: 'External id',
    aliases: ['complaint_id', 'request_id'],
  },
  {
    name: 'title',
    required: true,
    max_length: 500,
    description: 'Title',
    aliases: ['title', 'subject'],
  },
  {
    name: 'category',
    required: false,
    max_length: 200,
    description: 'Category',
    aliases: ['category', 'complaint_type'],
  },
]

const summary: ImportSummary = {
  status: 'completed',
  dry_run: true,
  total_rows: 3,
  accepted_rows: 1,
  rejected_rows: 1,
  skipped_rows: 1,
  flagged_rows: 1,
  exact_duplicate_rows: 0,
  potential_duplicate_rows: 0,
  issues_truncated: false,
  import_id: null,
  source_filename: 'demo.csv',
  source_label: null,
  dataset_identity: 'cols:abcd',
  content_sha256: 'abcdef0123456789',
  created_at: '2026-01-01T00:00:00+00:00',
  mapping: { complaint_id: 'request_id', title: 'subject' },
  detected_columns: ['request_id', 'subject'],
  unmatched_columns: ['notes'],
  ambiguous: {},
  missing_required: [],
  issues: [
    {
      row_number: 4,
      field: 'latitude',
      code: 'out_of_range',
      message: "'latitude' must be between -90.0 and 90.0.",
      status: 'rejected',
    },
  ],
  message: null,
}

describe('ImportMappingEditor', () => {
  it('renders one selector per target field', () => {
    render(
      <ImportMappingEditor
        fields={fields}
        columns={['request_id', 'subject', 'extra']}
        value={{}}
        onChange={() => {}}
      />,
    )
    expect(screen.getByLabelText('Source column for complaint_id')).toBeInTheDocument()
    expect(screen.getByLabelText('Source column for title')).toBeInTheDocument()
  })

  it('reports a mapping change for the selected target', () => {
    const onChange = vi.fn()
    render(
      <ImportMappingEditor
        fields={fields}
        columns={['request_id', 'subject']}
        value={{}}
        onChange={onChange}
      />,
    )
    fireEvent.change(screen.getByLabelText('Source column for title'), {
      target: { value: 'subject' },
    })
    expect(onChange).toHaveBeenCalledWith({ title: 'subject' })
  })

  it('removes the mapping when set to not mapped', () => {
    const onChange = vi.fn()
    render(
      <ImportMappingEditor
        fields={fields}
        columns={['request_id', 'subject']}
        value={{ title: 'subject' }}
        onChange={onChange}
      />,
    )
    fireEvent.change(screen.getByLabelText('Source column for title'), {
      target: { value: '' },
    })
    expect(onChange).toHaveBeenCalledWith({})
  })
})

describe('ImportSummaryPanel', () => {
  it('renders counts, identity, and row issues', () => {
    render(<ImportSummaryPanel summary={summary} />)
    expect(screen.getByText('Accepted')).toBeInTheDocument()
    expect(screen.getByText(/Dry-run preview — status:/)).toBeInTheDocument()
    expect(screen.getByText('out_of_range')).toBeInTheDocument()
    expect(screen.getByText('rejected')).toBeInTheDocument()
    expect(screen.getByText('cols:abcd')).toBeInTheDocument()
  })
})
