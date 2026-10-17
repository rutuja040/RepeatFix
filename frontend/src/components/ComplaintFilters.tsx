import type { ComplaintMeta } from '../types'

export interface FilterState {
  q: string
  status: string
  category: string
  location: string
  created_from: string
  created_to: string
  resolved: '' | 'true' | 'false'
  synthetic: '' | 'true' | 'false'
  sort_by: string
  sort_dir: 'asc' | 'desc'
}

export const EMPTY_FILTERS: FilterState = {
  q: '',
  status: '',
  category: '',
  location: '',
  created_from: '',
  created_to: '',
  resolved: '',
  synthetic: '',
  sort_by: 'created_at',
  sort_dir: 'desc',
}

interface ComplaintFiltersProps {
  value: FilterState
  meta: ComplaintMeta | null
  onChange: (patch: Partial<FilterState>) => void
  onReset: () => void
}

export function ComplaintFilters({ value, meta, onChange, onReset }: ComplaintFiltersProps) {
  return (
    <form
      className="filters"
      role="search"
      onSubmit={(e) => {
        e.preventDefault()
        // Filters apply live; submit is a no-op kept for accessibility.
      }}
    >
      <div className="filters-grid">
        <label className="field">
          <span>Search</span>
          <input
            type="search"
            placeholder="Title, description, ID, location"
            value={value.q}
            onChange={(e) => onChange({ q: e.target.value })}
          />
        </label>

        <label className="field">
          <span>Status</span>
          <select value={value.status} onChange={(e) => onChange({ status: e.target.value })}>
            <option value="">All</option>
            {(meta?.statuses ?? []).map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
        </label>

        <label className="field">
          <span>Category</span>
          <select
            value={value.category}
            onChange={(e) => onChange({ category: e.target.value })}
          >
            <option value="">All</option>
            {(meta?.categories ?? []).map((c) => (
              <option key={c.category} value={c.category}>
                {c.category} ({c.count})
              </option>
            ))}
          </select>
        </label>

        <label className="field">
          <span>Location</span>
          <input
            type="text"
            placeholder="Location label / area"
            value={value.location}
            onChange={(e) => onChange({ location: e.target.value })}
          />
        </label>

        <label className="field">
          <span>Created from</span>
          <input
            type="date"
            value={value.created_from}
            onChange={(e) => onChange({ created_from: e.target.value })}
          />
        </label>

        <label className="field">
          <span>Created to</span>
          <input
            type="date"
            value={value.created_to}
            onChange={(e) => onChange({ created_to: e.target.value })}
          />
        </label>

        <label className="field">
          <span>Resolution</span>
          <select
            value={value.resolved}
            onChange={(e) =>
              onChange({ resolved: e.target.value as FilterState['resolved'] })
            }
          >
            <option value="">All</option>
            <option value="false">Unresolved</option>
            <option value="true">Resolved / closed</option>
          </select>
        </label>

        <label className="field">
          <span>Data origin</span>
          <select
            value={value.synthetic}
            onChange={(e) =>
              onChange({ synthetic: e.target.value as FilterState['synthetic'] })
            }
          >
            <option value="">All</option>
            <option value="true">Synthetic (demo)</option>
            <option value="false">Non-synthetic</option>
          </select>
        </label>

        <label className="field">
          <span>Sort by</span>
          <select value={value.sort_by} onChange={(e) => onChange({ sort_by: e.target.value })}>
            <option value="created_at">Created time</option>
            <option value="last_updated_at">Last updated</option>
            <option value="complaint_id">Complaint ID</option>
            <option value="title">Title</option>
            <option value="status">Status</option>
            <option value="category">Category</option>
          </select>
        </label>

        <label className="field">
          <span>Direction</span>
          <select
            value={value.sort_dir}
            onChange={(e) =>
              onChange({ sort_dir: e.target.value as FilterState['sort_dir'] })
            }
          >
            <option value="desc">Descending</option>
            <option value="asc">Ascending</option>
          </select>
        </label>
      </div>

      <div className="filters-actions">
        <button type="button" className="btn btn-secondary" onClick={onReset}>
          Clear filters
        </button>
      </div>
    </form>
  )
}
