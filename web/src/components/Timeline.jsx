// Timeline of events, ordered by CLINICAL date (occurred_at), not the date we
// learned about them (recorded_at). Both are shown where they differ: a
// result from 14 Feb recorded on 16 Feb is two different facts, and a
// timeline that silently collapses them misrepresents when the clinician
// could first have acted.
export default function Timeline({ events = [], highlightSince }) {
  if (!events.length) return <p className="muted">No events recorded for this case.</p>
  const ordered = [...events].sort((a, b) => String(a.occurred_at).localeCompare(String(b.occurred_at)))
  return (
    <ol className="timeline">
      {ordered.map((event) => {
        const isNew = highlightSince && String(event.occurred_at) > String(highlightSince)
        return (
          <li key={event.id} className={isNew ? 'is-new' : undefined}>
            <div className="event-type">{event.event_type.replace(/_/g, ' ')}</div>
            <div>{describe(event)}</div>
            <div className="small muted">
              {event.occurred_at}
              {event.recorded_at && event.recorded_at !== event.occurred_at
                ? ` · recorded ${event.recorded_at}`
                : null}
              {event.source_document ? ` · ${event.source_document}` : null}
            </div>
          </li>
        )
      })}
    </ol>
  )
}

// One phrasing per event type. Deterministic string building, no cleverness —
// this mirrors case/questions.py's template approach rather than inventing a
// second, looser way to describe the same five event types.
function describe(event) {
  const p = event.payload || {}
  switch (event.event_type) {
    case 'ALTERATION_OBSERVED':
      return `${p.gene} ${p.variant}${p.assay ? ` (${p.assay})` : ''}`
    case 'BIOMARKER_MEASURED':
      return `${p.name} ${p.value}${p.unit ? ` ${p.unit}` : ''}`
    case 'TREATMENT_LINE':
      return `${p.regimen} ${p.action}${p.line ? `, line ${p.line}` : ''}${p.reason ? ` — ${p.reason}` : ''}`
    case 'DISEASE_ASSESSMENT':
      return `${p.status}${p.sites?.length ? ` — ${p.sites.join(', ')}` : ''}`
    case 'CLINICAL_QUESTION':
      return p.text || 'clinical question recorded'
    default:
      // The taxonomy is five types (case/models.py EVENT_TYPES). A sixth
      // showing up should be visible, not silently blank.
      return `unrecognised event type: ${event.event_type}`
  }
}
