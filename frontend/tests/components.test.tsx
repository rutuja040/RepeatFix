import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it } from 'vitest'

import { ComplaintTable } from '../src/components/ComplaintTable'
import { HistoryList } from '../src/components/HistoryList'
import { EmptyState } from '../src/components/EmptyState'
import type { Complaint, ComplaintHistoryItem } from '../src/types'

const complaint: Complaint = {
  id: 1,
  complaint_id: 'SYN-1',
  title: 'Demo pothole',
  description: null,
  category: 'Roads',
  latitude: null,
  longitude: null,
  location_label: 'Downtown',
  administrative_area: null,
  status: 'open',
  assigned_department: null,
  source: null,
  synthetic: true,
  evidence_metadata: null,
  severity_label: null,
  data_quality_flags: null,
  created_at: '2026-01-01T00:00:00+00:00',
  last_updated_at: null,
  resolved_at: null,
  raw_imported_values: null,
  priority_score: null,
  priority_rationale: null,
}

describe('ComplaintTable', () => {
  it('renders a synthetic badge and a link to the detail page', () => {
    render(
      <MemoryRouter>
        <ComplaintTable items={[complaint]} />
      </MemoryRouter>,
    )
    expect(screen.getByText('synthetic')).toBeInTheDocument()
    const link = screen.getByRole('link', { name: 'SYN-1' })
    expect(link).toHaveAttribute('href', '/complaints/SYN-1')
    expect(screen.getByText('Demo pothole')).toBeInTheDocument()
  })

  it('shows "Not computed" when there is no priority score', () => {
    render(
      <MemoryRouter>
        <ComplaintTable items={[complaint]} />
      </MemoryRouter>,
    )
    expect(screen.getByText('Not computed')).toBeInTheDocument()
  })
})

describe('HistoryList', () => {
  it('shows an empty state with no entries', () => {
    render(<HistoryList items={[]} />)
    expect(screen.getByText('No history yet')).toBeInTheDocument()
  })

  it('renders change rows with readable field labels', () => {
    const items: ComplaintHistoryItem[] = [
      {
        id: 1,
        complaint_id: 'SYN-1',
        field_name: 'status',
        old_value: 'open',
        new_value: 'resolved',
        changed_at: '2026-01-02T00:00:00+00:00',
        changed_by: 'api',
      },
    ]
    render(<HistoryList items={items} />)
    expect(screen.getByText('Status')).toBeInTheDocument()
    expect(screen.getByText('open')).toBeInTheDocument()
    expect(screen.getByText('resolved')).toBeInTheDocument()
  })
})

describe('EmptyState', () => {
  it('renders a title and message', () => {
    render(<EmptyState title="Nothing here" message="Add something" />)
    expect(screen.getByText('Nothing here')).toBeInTheDocument()
    expect(screen.getByText('Add something')).toBeInTheDocument()
  })
})
