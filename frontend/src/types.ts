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

export interface ImportIssue {
  row_number: number
  field: string | null
  code: string
  message: string
  status: string
}

export interface ImportLimits {
  max_size_mb: number
  max_rows: number
  supported_types: string[]
}

export interface ImportFieldInfo {
  name: string
  required: boolean
  max_length: number | null
  description: string
  aliases: string[]
}

export interface ImportFieldsResponse {
  fields: ImportFieldInfo[]
  required_fields: string[]
  limits: ImportLimits
}

export interface ImportSummary {
  status: string
  dry_run: boolean
  total_rows: number
  accepted_rows: number
  rejected_rows: number
  skipped_rows: number
  flagged_rows: number
  exact_duplicate_rows: number
  potential_duplicate_rows: number
  issues_truncated: boolean
  import_id: string | null
  source_filename: string | null
  source_label: string | null
  dataset_identity: string | null
  content_sha256: string | null
  created_at: string | null
  mapping: Record<string, string>
  detected_columns: string[]
  unmatched_columns: string[]
  ambiguous: Record<string, string[]>
  missing_required: string[]
  issues: ImportIssue[]
  message: string | null
}

export interface ImportListItem {
  import_id: string
  status: string
  source_filename: string | null
  source_label: string | null
  dataset_identity: string | null
  created_at: string | null
  completed_at: string | null
  total_rows: number
  accepted_rows: number
  rejected_rows: number
  skipped_rows: number
  flagged_rows: number
  exact_duplicate_rows: number
  potential_duplicate_rows: number
  message: string | null
}
