import type { ImportFieldInfo } from '../types'

export const UNMAPPED = ''

interface ImportMappingEditorProps {
  fields: ImportFieldInfo[]
  columns: string[]
  value: Record<string, string>
  onChange: (next: Record<string, string>) => void
  disabled?: boolean
}

/**
 * Lets the operator review and override the auto-detected column mapping.
 * Required targets must be mapped; the backend rejects ambiguous or missing
 * required mappings rather than guessing silently.
 */
export function ImportMappingEditor({
  fields,
  columns,
  value,
  onChange,
  disabled,
}: ImportMappingEditorProps) {
  function setTarget(target: string, column: string) {
    const next = { ...value }
    if (column === UNMAPPED) {
      delete next[target]
    } else {
      next[target] = column
    }
    onChange(next)
  }

  function unusedColumnsFor(target: string): string[] {
    const usedElsewhere = new Set(
      Object.entries(value)
        .filter(([t]) => t !== target)
        .map(([, col]) => col),
    )
    return columns.filter((c) => !usedElsewhere.has(c) || value[target] === c)
  }

  return (
    <div className="table-wrap">
      <table className="data-table mapping-table">
        <thead>
          <tr>
            <th scope="col">Target field</th>
            <th scope="col">Required</th>
            <th scope="col">Source column</th>
            <th scope="col">Accepted column names</th>
          </tr>
        </thead>
        <tbody>
          {fields.map((field) => (
            <tr key={field.name}>
              <th scope="row" className="mono">
                {field.name}
              </th>
              <td>{field.required ? <strong>required</strong> : 'optional'}</td>
              <td>
                <select
                  aria-label={`Source column for ${field.name}`}
                  value={value[field.name] ?? UNMAPPED}
                  disabled={disabled}
                  onChange={(e) => setTarget(field.name, e.target.value)}
                >
                  <option value={UNMAPPED}>— not mapped —</option>
                  {unusedColumnsFor(field.name).map((col) => (
                    <option key={col} value={col}>
                      {col}
                    </option>
                  ))}
                </select>
              </td>
              <td className="muted small">{field.aliases.join(', ')}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
