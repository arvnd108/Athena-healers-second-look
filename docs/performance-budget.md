# Performance budget — Subsystem M

A deliverable of issue #14, not an aspiration:

> A documented performance budget — target: usable on a low-end Android device
> over an intermittent 3G connection. **This is a real constraint in many of the
> settings this concept targets, not a nice-to-have.**

Every number here is measured, and every budget is asserted by a test that fails
loudly rather than ageing into prose.

## The target

| | |
|---|---|
| Device | low-end Android, ~4 years old, 2 GB RAM |
| Link | **400 kbps down, 400 ms RTT**, intermittent |
| Compression | **gzip, not brotli** |

Two of those need justifying.

**gzip, not brotli.** Brotli is smaller and every number below would look better
in it. It is also the number we would not be serving: brotli negotiation depends
on the proxy chain, and the deployment settings this concept targets are exactly
the ones with old middleboxes in the path. gzip is what is *guaranteed* to be
negotiated, so the budget is stated against the worst case actually served.

**400 ms RTT is the reason the round-trip count matters more than the byte
count.** On this link a second round trip costs more than 15 KB of payload. That
is why the server-rendered pages carry their CSS inline instead of linking a
stylesheet: the file would be smaller, and the page would be slower.

## Server-rendered pages — Finding Detail and Brief

These are the pages issue #14 singles out, because they are read-mostly and
"benefit least from a heavy client bundle". They ship **no JavaScript at all**.

| Page | Raw | Gzipped |
|---|---:|---:|
| Finding Detail (worst of 8 fixtures) | 9,897 B | **3,896 B** |
| Finding Detail (median) | ~9,300 B | ~3,640 B |
| Brief (8 findings) | 14,631 B | **5,223 B** |

**Budget: 14,000 B gzipped per page.** Both pages are at 28–37% of it.

That number is not arbitrary. ~14.6 KB is the initial TCP congestion window: a
response that fits inside it arrives in **one round trip**, with no wait for the
server to receive an ACK before sending more. At 400 ms RTT, crossing that line
is a visible stall, not a rounding error.

Also asserted, not merely intended:

- **zero** `<script>`, `<link>`, `<img>`, `<iframe>` or `@import`
- **zero** webfonts — system font stacks only
- **zero** inline event handlers
- one request renders the page completely

On an intermittent link every additional subresource is another chance to hang
half-loaded. A page that renders from a single response either arrives or
doesn't; it cannot arrive broken.

### Where it is enforced

`tests/web/test_render.py::TestPerformanceBudget` gzips each rendered page and
asserts it stays under the budget. It runs in the ordinary offline suite —
the **Offline test suite (Python 3.11)** CI job — so a change that doubles the
page fails the pull request rather than being discovered on a phone.

There is also a guard on the guard: a test asserts the budget is measured
against a **non-empty** page, so the check cannot start passing because the
renderer began producing nothing.

### What is doing the serving

The numbers above are the *rendered output*. The pages are produced by plain
functions, so anything can serve them — the API layer once Subsystem L exists,
a static export, or the small harness bundled for development.

That harness is **development and demo only**: no authentication, no TLS,
loopback by default. It is not a deployment target, and nothing in this budget
should be read as clearing it for one. Issue #60 tracks the access control that
would be required first, and it exists because someone will eventually point one
of these at a non-loopback interface.

## Client bundle — Dashboard and Research Queue

These two are interactive and stay a client app, per `IMPLEMENTATION_PLAN.md`
§9. Measured from a real `vite build`, target es2018:

| Asset | Raw | Gzipped |
|---|---:|---:|
| vendor (react, react-dom, react-router-dom) | 163,258 B | 52,971 B |
| app code | 33,289 B | 9,140 B |
| CSS | 4,256 B | 1,478 B |
| index.html | 1,124 B | 582 B |
| **JS total** | **196,547 B** | **62,111 B** |

| Budget | Used | |
|---|---|---|
| JS ≤ 100 KB gzipped | 62,111 B | **62%** |
| CSS ≤ 20 KB gzipped | 1,478 B | **7%** |

Enforced by `web/scripts/check-budget.mjs` (`npm run budget`) against a real
`dist/`, so it measures what ships rather than what was intended — and it
runs in CI, in the **Subsystem M client (Node 20)** job, not only when
someone remembers to run it locally. A change that inflates the bundle fails
the pull request.

**What this costs on the target link.** 62 KB of gzipped JS is roughly 1.3 s of
transfer at 400 kbps, before parse and execute on a slow CPU. That is the honest
reason the Brief and Finding Detail do not use it, and why the dashboard links
to the brief as a **plain anchor rather than a client route** — following it
gets the no-JS page instead of the bundle.

The vendor chunk is 85% of the JS budget and none of it is ours. If this budget
is ever exceeded, the first question is whether the two interactive screens still
need a router and a virtual DOM — not which feature to cut.

## Explicitly deferred: SMS / USSD

Issue #14 names this, and it is recorded here rather than left silent:

> A dedicated SMS/USSD interface. Worth naming as a real future direction for
> the lowest-resource settings (no smartphone, intermittent connectivity) but
> out of scope until the core product is validated — flagged here as a known,
> **considered omission rather than an unconsidered gap**.

Everything above assumes a browser. In the lowest-resource settings that
assumption fails outright: no smartphone, and connectivity too intermittent for
even a 4 KB page. SMS and USSD reach those users and nothing here does.

It is deferred because the failure mode is asymmetric. This system's core
discipline is that every claim carries its provenance — a citation, or a method
and version, and the disclaimer that a computational signal is not clinical
evidence. A 160-character channel cannot carry that. An SMS interface built
before the product is validated would either drop the provenance, which breaks
the one rule the whole system rests on, or truncate it, which is worse: a
clinical claim arriving with **part** of its evidence looks better sourced than
one arriving with none.

So the gap is real and is not being papered over. The prerequisite is a
validated core and a designed answer to how provenance survives 160 characters —
not more engineering on the client.

## Re-measuring

```bash
# Server-rendered pages
python -m pytest tests/web/test_render.py -k PerformanceBudget

# Client bundle, against a real build
cd web && npm run build && npm run budget
```

Both numbers move with the fixtures and with the dependency tree. Re-measure
before quoting them; do not copy figures out of this document into a slide.
