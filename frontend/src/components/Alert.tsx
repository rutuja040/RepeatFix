import type { ReactNode } from 'react'

export type AlertKind = 'success' | 'error' | 'info' | 'warning'

interface AlertProps {
  kind: AlertKind
  children: ReactNode
  onClose?: () => void
}

export function Alert({ kind, children, onClose }: AlertProps) {
  return (
    <div className={`alert alert-${kind}`} role={kind === 'error' ? 'alert' : 'status'}>
      <div className="alert-body">{children}</div>
      {onClose && (
        <button type="button" className="alert-close" onClick={onClose} aria-label="Dismiss">
          ×
        </button>
      )}
    </div>
  )
}
