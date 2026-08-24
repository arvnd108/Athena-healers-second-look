// A human published this, with a citation. The citation is always shown and
// always clickable (ARCHITECTURE.md §5) — there is no branch that renders a
// documented card without one, mirroring DocumentedSource.__post_init__.
export default function DocumentedCard({ claim, sourceName, citationUrl, citationId, evidenceLevel, caveats = [] }) {
  if (!citationUrl) {
    // Loud, not silent. A documented claim whose citation went missing is a
    // data bug; rendering it citation-less would launder it into an
    // uncheckable assertion.
    throw new Error('DocumentedCard requires citationUrl — a documented claim without a citation is not renderable')
  }
  return (
    <article className="card card-documented">
      <div className="badge badge-documented">Documented</div>
      <p className="claim">{claim}</p>
      <p className="small">
        {sourceName}
        {evidenceLevel ? <span className="small muted"> Level {evidenceLevel}</span> : null}
        {' · '}
        <a href={citationUrl} rel="noreferrer noopener" target="_blank">{citationId || sourceName || citationUrl}</a>
      </p>
      <Caveats items={caveats} />
    </article>
  )
}

export function Caveats({ items = [] }) {
  if (!items.length) return null
  // Every caveat, always. No "show more" — per ARCHITECTURE.md §8 a caveat
  // that exists in the data and not on screen is a silent gap. This matters
  // most for computed trial-eligibility signals, whose caveats name exactly
  // which criteria could not be checked; that naming is the actionable part
  // for a clinician and must not become a tooltip.
  return (
    <ul className="caveats">
      {items.map((c, i) => <li key={i}>{c}</li>)}
    </ul>
  )
}
