// §9.1 — the change banner. "This is the wow moment; everything else can be ugly."
//
// Two rules from the spec are load-bearing and easy to lose in a refactor:
//
// 1. The strikethrough is a literal `text-decoration: line-through` (see
//    .superseded-claim in styles/app.css), "visible from the back of the
//    room". Not opacity, not grey text — a struck claim must still read as
//    struck in a photograph of the screen or a monochrome printout.
// 2. Every change is rendered. §9.1's ASCII mock shows two lines because it
//    is a sketch, not a cap; truncating to a "top N" would be the silent gap
//    ARCHITECTURE.md §8 forbids.

const MARKS = {
  new_alteration: ['+', 'newly observed'],
  biomarker_shift: ['↑', 'threshold crossed'],
  treatment_line_change: ['→', 'treatment line changed'],
  disease_progression: ['!', 'disease assessment changed'],
}

export default function ChangeBanner({ changeSet }) {
  const changes = changeSet?.changes || []
  const supersessions = changeSet?.supersessions || []

  if (!changes.length && !supersessions.length) {
    // Never an empty banner and never a hidden one: say why there is nothing.
    return (
      <div className="panel">
        <p className="muted">{changeSet?.unchanged_reason || 'No tracked field changed.'}</p>
      </div>
    )
  }

  return (
    <section className="banner" role="alert" aria-label="Changes since last review">
      <div className="banner-head">
        ⚠ {changes.length} change{changes.length === 1 ? '' : 's'}
        {changeSet.since ? ` since ${changeSet.since}` : null}
      </div>

      {changes.map((change) => {
        const [mark, spoken] = MARKS[change.kind] || ['•', 'changed']
        return (
          <div className="change-line" key={change.triggering_event_id + change.summary}>
            <span className="change-mark" aria-hidden="true">{mark}</span>
            {/* The glyph is decorative; the kind is spelled out for screen readers. */}
            <span className="change-kind">{spoken}:</span>
            <span>{change.summary}</span>
            {change.observed_on ? <span className="change-when">{change.observed_on}</span> : null}
          </div>
        )
      })}

      {supersessions.length ? (
        <>
          <div className="supersession-head">
            ⊘ {supersessions.length} prior finding{supersessions.length === 1 ? '' : 's'} superseded
          </div>
          {supersessions.map((s) => (
            <div className="supersession" key={s.finding_id}>
              <div className="superseded-claim">
                {s.finding_label || s.finding_id}: {s.finding_claim}
              </div>
              <div className="supersession-why">{s.note}</div>
              <div className="supersession-trigger">
                → {s.triggering_event_label || s.triggering_event_id}
              </div>
            </div>
          ))}
        </>
      ) : null}
    </section>
  )
}
