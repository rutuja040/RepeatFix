export type ComplaintStatus = 'open' | 'in_progress' | 'resolved' | 'closed'

export interface Complaint {
  id: number
  complaint_id: string
  title: string
  description: string | null
  category: string | null
  latitude: number | null
  longitude: number | null
  location_label: string | null
  administrative_area: string | null
  status: ComplaintStatus
  assigned_department: string | null
  source: string | null
  synthetic: boolean
  evidence_metadata: string | null
  severity_label: string | null
  data_quality_flags: string | null
  created_at: string | null
  last_updated_at: string | null
  resolved_at: string | null
  raw_imported_values: string | null
  priority_score: number | null
  priority_rationale: string | null
}

export interface ComplaintHistoryItem {
  id: number
  complaint_id: string
  field_name: string
  old_value: string | null
  new_value: string | null
  changed_at: string | null
  changed_by: string | null
}

export interface CategoryCount {
  category: string
  count: number
}

export interface ComplaintMeta {
  total: number
  statuses: string[]
  categories: CategoryCount[]
}

export interface ComplaintInput {
  complaint_id?: string
  title?: string
  description?: string | null
  category?: string | null
  latitude?: number | null
  longitude?: number | null
  location_label?: string | null
  administrative_area?: string | null
  status?: ComplaintStatus
  assigned_department?: string | null
  severity_label?: string | null
  data_quality_flags?: string | null
  synthetic?: boolean
  created_at?: string | null
}

export interface ListParams {
  status?: string
  category?: string
  location?: string
  q?: string
  created_from?: string
  created_to?: string
  resolved?: boolean
  synthetic?: boolean
  sort_by?: string
  sort_dir?: 'asc' | 'desc'
  skip?: number
  limit?: number
}
