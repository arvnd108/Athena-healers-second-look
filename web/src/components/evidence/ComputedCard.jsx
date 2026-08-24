import { Caveats } from './DocumentedCard.jsx'

// A model or measurement produced this.
//
// IMPLEMENTATION_PLAN.md §9.2: "The `computed` card must have **no place**
// for a citation — not an empty one." So this component destructures only
// the props a computed signal carries. There is no citationUrl prop, no
// conditional citation render, and deliberately no shared <Card> parent that
// could grow one. If you find yourself adding `citationUrl` here because
// "this particular computed signal does have a URL", the signal is
// misclassified — fix it in the generator, where signals/types.py enforces
// the same rule structurally.
//
// `secondlook/web/render.py::_computed_card` is the server-rendered twin of
// this component and holds the identical constraint; tests/web/test_render.py
// asserts it on that side by inspecting the function signature.
export default function ComputedCard({ claim, method, version, caveats = [] }) {
  return (
    <article className="card card-computed">
      <div className="badge badge-computed">Computed</div>
      <p className="claim">{claim}</p>
      <p className="method">{method} · {version}</p>
      <Caveats items={caveats} />
    </article>
  )
}
