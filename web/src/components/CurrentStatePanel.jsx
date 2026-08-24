// The derived current state — what CaseState folds to right now.
//
// Every row renders even when empty, with an explicit "none recorded".
// An absent row and a row reading "none recorded" mean different things to a
// clinician, and only one of them is honest: not testing for a marker is not
// the same as testing negative.
export default function CurrentStatePanel({ state = {}, cancerType, stage, ageYears }) {
  const alterations = (state.alterations || []).map((a) => `${a.gene} ${a.variant}`).join(', ')
  const biomarkers = Object.values(state.biomarkers || {})
    .map((b) => `${b.name} ${b.value}${b.unit || ''}`)
    .join(', ')
  const treatments = (state.treatments || [])
    .map((t) => `${t.regimen} (${t.action}${t.line ? `, line ${t.line}` : ''})`)
    .join(', ')
  const assessments = state.assessments || []
  const latest = assessments.length ? assessments[assessments.length - 1].status : null

  return (
    <div className="panel">
      <dl>
        <dt>Cancer type</dt><dd>{cancerType || 'not recorded'}</dd>
        <dt>Stage</dt><dd>{stage || 'not recorded'}</dd>
        <dt>Age</dt><dd>{ageYears ?? 'not recorded'}</dd>
        <dt>Alterations</dt><dd>{alterations || 'none recorded'}</dd>
        <dt>Biomarkers</dt><dd>{biomarkers || 'none recorded'}</dd>
        <dt>Treatments</dt><dd>{treatments || 'none recorded'}</dd>
        <dt>Latest assessment</dt><dd>{latest || 'never assessed'}</dd>
      </dl>
    </div>
  )
}
