import { Link, useParams } from 'react-router-dom'
import useAsync from '../api/useAsync.js'
import { getQueue } from '../api/client.js'
import { Failure } from './CaseDashboard.jsx'

// §9 — Research Queue. Open questions by priority, and "N suppressed as
// already answered" as a VISIBLE count.
//
// The suppressed count is a deliverable, not a detail: IMPLEMENTATION_PLAN.md
// §4.1 says suppressed questions are returned and never dropped silently, and
// the count is what shows the case has memory — it is the second-order wow
// moment. So it renders as a block, and every suppressed question can be
// expanded to show the prior question it matched. A count nobody can
// interrogate is a number the user has to take on trust.
export default function ResearchQueue() {
  const { id } = useParams()
  const queue = useAsync(() => getQueue(id), [id])

  if (queue.loading) return <p className="muted">Loading queue…</p>
  if (queue.error) return <Failure what="research queue" error={queue.error} />

  const { counts = {}, open = [], suppressed = [] } = queue.data
  const byPriority = [...open].sort((a, b) => b.priority - a.priority || a.id.localeCompare(b.id))

  return (
    <>
      <p className="small muted"><Link to={`/cases/${id}`}>← Case dashboard</Link></p>
      <h1>Research queue</h1>
      <p className="small muted">
        {counts.open ?? open.length} open · {counts.answered ?? 0} answered ·{' '}
        {counts.suppressed ?? suppressed.length} suppressed
      </p>

      <h2>Open questions</h2>
      {byPriority.length === 0 ? (
        <p className="muted">No open questions. Every question raised by the last change set has been answered.</p>
      ) : (
        byPriority.map((q) => (
          <div className="q" key={q.id}>
            <div className="q-priority">Priority {q.priority} · {String(q.kind).replace(/_/g, ' ')}</div>
            <div>{q.text}</div>
            {q.triggered_by?.summary ? (
              <div className="small muted">Triggered by: {q.triggered_by.summary}</div>
            ) : null}
            {q.finding_ids?.length ? (
              <div className="small">
                {q.finding_ids.map((fid, i) => (
                  <span key={fid}>
                    {i > 0 ? ' · ' : ''}
                    <Link to={`/findings/${fid}`}>Finding {fid.slice(-4)}</Link>
                  </span>
                ))}
              </div>
            ) : (
              <div className="small muted">No findings recorded against this question yet.</div>
            )}
          </div>
        ))
      )}

      <SuppressedBlock items={suppressed} count={counts.suppressed ?? suppressed.length} />
    </>
  )
}

function SuppressedBlock({ items, count }) {
  if (!count) return null
  return (
    <div className="suppressed-count">
      <strong>{count} suppressed as already answered</strong>
      <p className="small muted">
        These were raised again by the latest change set and matched a question this
        case has already answered. They are kept, not discarded.
      </p>
      {items.map((q) => (
        <details key={q.id}>
          <summary className="small">{q.text}</summary>
          <div className="small muted">
            Matched: “{q.matched_prior_question?.text}”
            {q.matched_prior_question?.answered_at
              ? ` · answered ${q.matched_prior_question.answered_at.slice(0, 10)}`
              : null}
            {typeof q.similarity === 'number' ? ` · similarity ${q.similarity.toFixed(2)}` : null}
          </div>
        </details>
      ))}
    </div>
  )
}
