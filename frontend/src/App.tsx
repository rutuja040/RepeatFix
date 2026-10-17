import { BrowserRouter, Link, Route, Routes } from 'react-router-dom'

import { Layout } from './components/Layout'
import { EmptyState } from './components/EmptyState'
import { HomePage } from './pages/HomePage'
import { ComplaintsPage } from './pages/ComplaintsPage'
import { ComplaintDetailPage } from './pages/ComplaintDetailPage'
import { NewComplaintPage } from './pages/NewComplaintPage'
import { EditComplaintPage } from './pages/EditComplaintPage'
import { ImportsPage } from './pages/ImportsPage'

function NotFoundPage() {
  return (
    <EmptyState
      title="Page not found"
      message="The page you requested does not exist."
      action={
        <Link className="btn btn-primary" to="/">
          Go home
        </Link>
      }
    />
  )
}

function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route element={<Layout />}>
          <Route index element={<HomePage />} />
          <Route path="complaints" element={<ComplaintsPage />} />
          <Route path="complaints/new" element={<NewComplaintPage />} />
          <Route path="complaints/:complaintId" element={<ComplaintDetailPage />} />
          <Route path="complaints/:complaintId/edit" element={<EditComplaintPage />} />
          <Route path="imports" element={<ImportsPage />} />
          <Route path="*" element={<NotFoundPage />} />
        </Route>
      </Routes>
    </BrowserRouter>
  )
}

export default App
