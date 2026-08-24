import DocumentedCard from './DocumentedCard.jsx'
import ComputedCard from './ComputedCard.jsx'
import RegulatoryCard from './RegulatoryCard.jsx'
import ContextualCard from './ContextualCard.jsx'

// Dispatch on evidence_class, unpacking only the fields that class carries.
//
// Note what does NOT happen here: the finding's `source` object is never
// spread wholesale into a card. Each branch names the props it passes, so a
// citation_url sitting on a computed source — however it got there — has no
// route to the screen.
//
// An unknown class throws rather than falling back to a generic box. A fifth
// evidence class arriving unnoticed and rendering as a neutral card is
// exactly the flattening ARCHITECTURE.md §5 forbids.
export default function EvidenceCard({ finding }) {
  const source = finding.source || {}
  const common = { claim: finding.claim, caveats: finding.caveats || [] }

  switch (finding.evidence_class) {
    case 'documented':
      return (
        <DocumentedCard
          {...common}
          sourceName={source.name}
          citationUrl={source.citation_url}
          citationId={source.citation_id}
          evidenceLevel={finding.evidence_level}
        />
      )
    case 'computed':
      return <ComputedCard {...common} method={source.method} version={source.version} />
    case 'regulatory':
      return <RegulatoryCard {...common} instrument={source.instrument} citationUrl={source.citation_url} />
    case 'contextual':
      return <ContextualCard {...common} sourceName={source.name} citationUrl={source.citation_url} />
    default:
      throw new Error(
        `unknown evidence class ${finding.evidence_class} — the four classes in ` +
        'ARCHITECTURE.md §5 each have their own component, and there is no generic fallback on purpose'
      )
  }
}

export { DocumentedCard, ComputedCard, RegulatoryCard, ContextualCard }
