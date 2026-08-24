import { Caveats } from './DocumentedCard.jsx'

// A pathway defined by an instrument. The instrument is cited; precedent of
// use is a separate claim and is never implied by availability
// (ARCHITECTURE.md §5). This component states the instrument and nothing
// about whether anyone has actually travelled the pathway.
export default function RegulatoryCard({ claim, instrument, citationUrl, caveats = [] }) {
  return (
    <article className="card card-regulatory">
      <div className="badge badge-regulatory">Regulatory</div>
      <p className="claim">{claim}</p>
      <p className="small">
        {instrument}
        {citationUrl ? (
          <>{' · '}<a href={citationUrl} rel="noreferrer noopener" target="_blank">instrument</a></>
        ) : null}
      </p>
      <Caveats items={caveats} />
    </article>
  )
}
