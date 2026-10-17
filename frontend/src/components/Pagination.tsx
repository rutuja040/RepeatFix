interface PaginationProps {
  skip: number
  limit: number
  total: number
  onPageChange: (skip: number) => void
}

export function Pagination({ skip, limit, total, onPageChange }: PaginationProps) {
  const totalPages = Math.max(1, Math.ceil(total / limit))
  const currentPage = Math.min(totalPages, Math.floor(skip / limit) + 1)
  const start = total === 0 ? 0 : skip + 1
  const end = Math.min(skip + limit, total)

  return (
    <nav className="pagination" aria-label="Pagination">
      <button
        type="button"
        onClick={() => onPageChange(Math.max(0, skip - limit))}
        disabled={skip <= 0}
      >
        ← Previous
      </button>
      <span className="pagination-info">
        {start}–{end} of {total} · page {currentPage} / {totalPages}
      </span>
      <button
        type="button"
        onClick={() => onPageChange(skip + limit)}
        disabled={skip + limit >= total}
      >
        Next →
      </button>
    </nav>
  )
}
