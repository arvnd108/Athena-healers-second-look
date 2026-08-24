"""Subsystem M's server-rendered half.

`web/` (repo root) is the React client. This package is the no-JavaScript
fallback for the two read-mostly views -- Finding Detail and Brief -- that
issue #14 calls out as benefiting least from a client bundle.

It is deliberately *not* `secondlook.api` (Subsystem L, issue #13). It owns
no routes, no database session and no serialisation contract; it turns
already-assembled dictionaries into HTML. When Subsystem L lands, its
handlers call `render_finding_detail()` / `render_brief()` and pass real
rows instead of fixtures -- nothing here has to change.
"""

from secondlook.web.render import render_brief, render_finding_detail

__all__ = ["render_brief", "render_finding_detail"]
