# Subsystem M — Frontend / Low-Bandwidth Client Layer

Issue #14. `IMPLEMENTATION_PLAN.md` §9, plus the low-resource extension the
issue adds on top of it.

Two halves, on purpose:

| Half | Lives in | Serves | Ships |
|---|---|---|---|
| React client | `web/` | Case Dashboard, Research Queue, Finding Detail | ~62 KB gzipped JS |
| Server-rendered fallback | `src/secondlook/web/` | Finding Detail, Brief | **0 bytes of JS** |

The fallback is not a degraded copy. Issue #14 calls out Finding Detail and
Brief as "read-mostly and benefit least from a heavy client bundle", so those
two get a no-JavaScript path that costs one request and ~3.7–5.2 KB gzipped
on a cold connection. Review buttons on the no-JS Finding Detail are a plain
`<form method="post">`: a clinician on a 3G handset can still record a
decision, which is the point of the fallback existing at all.

## Run it

```bash
# Client (Case Dashboard, Research Queue, Finding Detail)
cd web && npm install && npm run dev

# Server-rendered fallback (Finding Detail, Brief) — no Node involved
python -m secondlook.web.server        # http://127.0.0.1:8014
```

Both read the **same** fixtures from `web/fixtures/`, so the two halves
cannot show different things.

## Why fixtures

Subsystem L (the REST API, issue #13) does not exist yet — there is no
`src/secondlook/api/` on disk. Rather than block, both halves read committed
JSON whose shapes mirror `IMPLEMENTATION_PLAN.md` §5's routes verbatim:

```
web/fixtures/case.json     ->  GET /api/cases/{id}
web/fixtures/changes.json  ->  GET /api/cases/{id}/changes
web/fixtures/queue.json    ->  GET /api/cases/{id}/queue
web/fixtures/findings.json ->  GET /api/findings/{id}   (keyed by id)
```

Wiring the real API in is a base-URL swap — set `VITE_API_BASE` — with no
call sites changed. The client says so on screen while it is fixture-backed,
per `docs/ui-flow.md`'s rule against a demo that quietly looks live.

The fixture case is **synthetic**. No real patient data, per `POLICY.md` §5.

## The one invariant this subsystem exists to hold

`IMPLEMENTATION_PLAN.md` §9.2: *"The `computed` card must have **no place**
for a citation — not an empty one."*

There is no shared `Card` component with an optional citation prop. There are
four components with four different prop sets, and `ComputedCard` does not
take a citation. The server-rendered twin holds the same line by function
signature — `render._computed_card()` cannot be passed a citation — and
`tests/web/test_render.py` asserts that signature, so a future refactor into
a "nice" shared card fails the suite instead of quietly reintroducing the
ambiguity `signals/types.py` enforces in the data model.

This matters more since issue #46: a matched trial now emits **two** signals —
a `documented` one citing the registry ("this trial is recruiting") and a
`computed` one carrying the eligibility verdict ("this patient probably does
not qualify"). Same trial, different warrants. Per `signals/types.py` the
generators deliberately do not reconcile them; rendering them side by side is
this subsystem's job. Finding Detail shows both, in their two treatments.

`needs_verification` is not a rare third bucket. It is where everything the
extractor refuses to guess at lands, and PR #63 deliberately moved a line
into it. It always carries caveats naming *which* criteria could not be
checked, and those names are the actionable part for a clinician — so caveats
render in full, on both halves, with no truncation and no tooltip.

## Budgets

Measured, enforced, and failing loudly rather than aging into prose:

```bash
cd web && npm run build && npm run budget   # JS/CSS budget vs a real dist/
pytest tests/web                            # SSR page-weight budget
```

See `docs/performance-budget.md` for the targets and the measurement method.

## What is deliberately not here

A dedicated SMS/USSD interface. Named as a considered omission for the
lowest-resource settings (no smartphone, intermittent connectivity), not an
unconsidered gap — out of scope until the core product is validated.
