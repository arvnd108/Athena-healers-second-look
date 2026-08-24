import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import EvidenceCard, { ComputedCard, DocumentedCard } from './index.jsx'
import findings from '../../../fixtures/findings.json'

const TRIAL_EXISTS = 'f0000000-0000-4000-8000-000000000031'
const TRIAL_ELIGIBILITY = 'f0000000-0000-4000-8000-000000000032'
const REGULATORY = 'f0000000-0000-4000-8000-000000000051'
const CONTEXTUAL = 'f0000000-0000-4000-8000-000000000061'
const DOCUMENTED = 'f0000000-0000-4000-8000-000000000021'

const TRIAL_CITATION = 'https://clinicaltrials.gov/study/NCT03778229'

// The server-rendered half asserts this by inspect.signature, which JavaScript
// has no equivalent of — a component's props are destructured, not declared.
// Reading the source is the closest true analogue, and it catches the same
// regression: someone adding a citation prop to "support the case where we do
// have a URL".
describe('§9.2 — the computed card has no place for a citation', () => {
  it('never mentions a citation anywhere in its source', () => {
    // Resolved from the vitest root (web/) rather than import.meta.url:
    // the transform rewrites module URLs, so import.meta.url is not a
    // file: URL by the time this runs.
    const source = readFileSync(
      resolve(process.cwd(), 'src/components/evidence/ComputedCard.jsx'),
      'utf8',
    )
    const code = source
      .split('\n')
      .filter((line) => !line.trim().startsWith('//') && !line.trim().startsWith('*'))
      .join('\n')
    expect(code).not.toMatch(/citation/i)
    expect(code).not.toMatch(/href/i)
  })

  it('renders nothing when a citation is passed anyway', () => {
    const { container } = render(
      <ComputedCard
        claim="a computed claim"
        method="m"
        version="v"
        citationUrl={TRIAL_CITATION}
      />,
    )
    expect(container.querySelector('a')).toBeNull()
    expect(container.innerHTML).not.toContain(TRIAL_CITATION)
  })

  it('does not let a citation smuggled onto the source reach the DOM', () => {
    // Defence in depth: even if a generator emitted a malformed computed
    // signal, the dispatcher names each prop rather than spreading `source`,
    // so there is no route to the screen.
    const finding = {
      ...findings[TRIAL_ELIGIBILITY],
      source: { ...findings[TRIAL_ELIGIBILITY].source, citation_url: TRIAL_CITATION },
    }
    const { container } = render(<EvidenceCard finding={finding} />)
    expect(container.querySelector('a')).toBeNull()
    expect(container.innerHTML).not.toContain(TRIAL_CITATION)
  })

  it('states method and version instead', () => {
    render(<EvidenceCard finding={findings[TRIAL_ELIGIBILITY]} />)
    expect(screen.getByText(/signals\.trial_matching\.match_trial/)).toBeInTheDocument()
    expect(screen.getByText(/signals\.trial_matching\/1/)).toBeInTheDocument()
  })
})

describe('the documented card refuses to render without a citation', () => {
  it('throws rather than rendering an uncheckable claim', () => {
    const spy = vi.spyOn(console, 'error').mockImplementation(() => {})
    expect(() => render(<DocumentedCard claim="x" sourceName="CIViC" />)).toThrow(
      /requires citationUrl/,
    )
    spy.mockRestore()
  })

  it('renders the citation as a real link', () => {
    const { container } = render(<EvidenceCard finding={findings[TRIAL_EXISTS]} />)
    const link = container.querySelector('a')
    expect(link).not.toBeNull()
    expect(link.getAttribute('href')).toBe(TRIAL_CITATION)
  })
})

describe('the four classes stay four classes', () => {
  it.each([
    [DOCUMENTED, 'card-documented'],
    [TRIAL_ELIGIBILITY, 'card-computed'],
    [REGULATORY, 'card-regulatory'],
    [CONTEXTUAL, 'card-contextual'],
  ])('%s renders as %s', (id, className) => {
    const { container } = render(<EvidenceCard finding={findings[id]} />)
    expect(container.querySelector(`.${className}`)).not.toBeNull()
  })

  it('throws on an unknown class rather than falling back to a generic box', () => {
    const spy = vi.spyOn(console, 'error').mockImplementation(() => {})
    expect(() =>
      render(<EvidenceCard finding={{ evidence_class: 'vibes', claim: 'x', source: {} }} />),
    ).toThrow(/unknown evidence class/)
    spy.mockRestore()
  })
})

describe('the #46 trial pair renders as two cards, not one', () => {
  it('gives the two halves different treatments and only one a link', () => {
    const { container: exists } = render(<EvidenceCard finding={findings[TRIAL_EXISTS]} />)
    const { container: bucket } = render(<EvidenceCard finding={findings[TRIAL_ELIGIBILITY]} />)

    expect(exists.querySelector('.card-documented')).not.toBeNull()
    expect(exists.querySelector('.card-computed')).toBeNull()
    expect(bucket.querySelector('.card-computed')).not.toBeNull()
    expect(bucket.querySelector('.card-documented')).toBeNull()

    // The registry's claim is citable; our verdict is not.
    expect(exists.querySelector('a')).not.toBeNull()
    expect(bucket.querySelector('a')).toBeNull()
  })
})

describe('caveats are never truncated', () => {
  it('renders every caveat in the data, with no show-more control', () => {
    const finding = findings[TRIAL_ELIGIBILITY]
    const { container } = render(<EvidenceCard finding={finding} />)
    const rendered = container.querySelectorAll('.caveats li')
    expect(rendered).toHaveLength(finding.caveats.length)
    finding.caveats.forEach((caveat, i) => {
      expect(rendered[i].textContent).toBe(caveat)
    })
    expect(container.querySelector('details')).toBeNull()
    expect(container.innerHTML).not.toMatch(/show more|\.\.\. more|…/i)
  })

  it('names which criteria could not be checked — the actionable part', () => {
    render(<EvidenceCard finding={findings[TRIAL_ELIGIBILITY]} />)
    expect(screen.getByText(/ECOG performance status 0-1/)).toBeInTheDocument()
    expect(screen.getByText(/DISEASE_STAGE_REQUIRES/)).toBeInTheDocument()
  })
})
