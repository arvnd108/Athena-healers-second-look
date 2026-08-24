import { Navigate, Route, Routes } from 'react-router-dom'
import CaseDashboard from './routes/CaseDashboard.jsx'
import ResearchQueue from './routes/ResearchQueue.jsx'
import FindingDetail from './routes/FindingDetail.jsx'
import { DEMO_CASE_ID, isFixtureBacked } from './api/client.js'

export default function App() {
  return (
    <div className="wrap">
      {isFixtureBacked() ? (
        // Honest about its own state, per docs/ui-flow.md: never hide that a
        // view is running on cached or fixture data. A demo that quietly looks
        // live is one question away from being embarrassing.
        <p className="nojs-note">
          Fixture-backed: Subsystem L (the REST API, issue #13) is not wired in.
          Set <code>VITE_API_BASE</code> to point these screens at a live API.
        </p>
      ) : null}
      <Routes>
        <Route path="/" element={<Navigate to={`/cases/${DEMO_CASE_ID}`} replace />} />
        <Route path="/cases/:id" element={<CaseDashboard />} />
        <Route path="/cases/:id/queue" element={<ResearchQueue />} />
        <Route path="/findings/:id" element={<FindingDetail />} />
        <Route path="*" element={<NotFound />} />
      </Routes>
    </div>
  )
}

function NotFound() {
  return (
    <>
      <h1>Not found</h1>
      <p className="muted">
        Client routes are <code>/cases/:id</code>, <code>/cases/:id/queue</code> and{' '}
        <code>/findings/:id</code>. The tumour-board brief is server-rendered at{' '}
        <code>/cases/:id/brief</code>.
      </p>
    </>
  )
}
