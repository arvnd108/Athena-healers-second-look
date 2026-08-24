import { Caveats } from './DocumentedCard.jsx'

// Cohort/background, not about this patient. Visually subordinate by
// construction — dotted, grey, smaller — because ARCHITECTURE.md §5 says it
// can never drive an option, and it must not look like it could.
export default function ContextualCard({ claim, sourceName, citationUrl, caveats = [] }) {
  return (
    <article className="card card-contextual">
      <div className="badge badge-contextual">Contextual</div>
      <p className="claim">{claim}</p>
      <p className="small">
        Background, not a finding about this patient.
        {citationUrl ? (
          <>{' '}<a href={citationUrl} rel="noreferrer noopener" target="_blank">{sourceName || 'source'}</a></>
        ) : null}
      </p>
      <Caveats items={caveats} />
    </article>
  )
}
