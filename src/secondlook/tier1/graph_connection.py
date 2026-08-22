"""Shared FalkorDB connection helper.

Every module that talks to the graph (civic_loader, retrieval, ...) connects
the same way -- one place to change the connection logic rather than
duplicating it per module.
"""

from __future__ import annotations

import os


def connect_graph(graph_name: str | None = None):
    """Open the configured FalkorDB graph.

    Imported lazily inside the function (not at module scope) so importing
    this module never requires the `falkordb` package to be installed --
    unit tests that inject a fake graph object never need a live server or
    even the client library present.
    """
    from falkordb import FalkorDB

    db = FalkorDB(
        host=os.environ.get("FALKORDB_HOST", "localhost"),
        port=int(os.environ.get("FALKORDB_PORT", "6379")),
    )
    return db.select_graph(graph_name or os.environ.get("FALKORDB_GRAPH_NAME", "secondlook_tier1"))
