import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import ChangeBanner from './ChangeBanner.jsx'
import changes from '../../fixtures/changes.json'

describe('§9.1 — the change banner', () => {
  it('renders every change, not a truncated sample', () => {
    const { container } = render(<ChangeBanner changeSet={changes} />)
    expect(container.querySelectorAll('.change-line')).toHaveLength(changes.changes.length)
    changes.changes.forEach((change) => {
      expect(screen.getByText(change.summary)).toBeInTheDocument()
    })
  })

  it('counts the changes in its heading', () => {
    render(<ChangeBanner changeSet={changes} />)
    expect(screen.getByText(/3 changes since 2026-01-02/)).toBeInTheDocument()
  })

  it('marks superseded findings with the class that carries the strikethrough', () => {
    const { container } = render(<ChangeBanner changeSet={changes} />)
    const struck = container.querySelectorAll('.superseded-claim')
    expect(struck).toHaveLength(changes.supersessions.length)
  })

  it('uses a literal text-decoration: line-through, not opacity or colour', () => {
    // §9.1: "visible from the back of the room". Asserted against the
    // stylesheet, because the component only carries the class name — the
    // rule that makes it a strikethrough lives in CSS and is the half that
    // would silently become `opacity: .5` in a redesign.
    const css = readFileSync(resolve(process.cwd(), 'src/styles/app.css'), 'utf8')
    expect(css).toMatch(/\.superseded-claim\s*\{[^}]*text-decoration:\s*line-through/)
  })

  it('shows the broken assumption and what triggered it', () => {
    render(<ChangeBanner changeSet={changes} />)
    expect(
      screen.getByText(/This finding assumed no known alteration in EGFR\./),
    ).toBeInTheDocument()
    expect(screen.getByText(/14 Feb sequencing/)).toBeInTheDocument()
  })

  it('spells out each change kind for screen readers, not just a glyph', () => {
    const { container } = render(<ChangeBanner changeSet={changes} />)
    const spoken = [...container.querySelectorAll('.change-kind')].map((n) => n.textContent)
    expect(spoken).toContain('newly observed:')
    expect(spoken).toContain('threshold crossed:')
    // The glyphs themselves are hidden from assistive tech.
    container.querySelectorAll('.change-mark').forEach((mark) => {
      expect(mark.getAttribute('aria-hidden')).toBe('true')
    })
  })

  it('states its reason when nothing changed rather than rendering nothing', () => {
    render(
      <ChangeBanner
        changeSet={{ changes: [], supersessions: [], unchanged_reason: 'No tracked field changed.' }}
      />,
    )
    expect(screen.getByText('No tracked field changed.')).toBeInTheDocument()
  })

  it('never renders an empty banner with no explanation', () => {
    const { container } = render(<ChangeBanner changeSet={{ changes: [], supersessions: [] }} />)
    expect(container.textContent.trim().length).toBeGreaterThan(0)
  })
})
