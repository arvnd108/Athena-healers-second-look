"""Confirm the FalkorDB Python client and vector index work in this environment.

No CIViC or domain logic — dummy nodes, dummy vectors, then cleanup.

Usage:
    python src/secondlook/tier1/verify_falkordb.py

Environment:
    FALKORDB_HOST        default: localhost
    FALKORDB_PORT        default: 6379
    FALKORDB_GRAPH_NAME  default: tier1_verify
    FALKORDB_PASSWORD    optional (only if the server requires auth)

Requires a running FalkorDB instance (see docker-compose.yml at the repo root).
"""

from __future__ import annotations

import os
import sys
import traceback

# Dummy 4-d vectors so this script does not need sentence-transformers.
VECTOR_DIM = 4
LABEL = "DummyNode"
VECTOR_PROP = "embedding"
REL_TYPE = "RELATED_TO"
NODE_A = "alpha"
NODE_B = "beta"
VEC_A = [1.0, 0.0, 0.0, 0.0]
VEC_B = [0.9, 0.1, 0.0, 0.0]
QUERY_VEC = [1.0, 0.0, 0.0, 0.0]


def _env() -> dict[str, object]:
    return {
        "host": os.environ.get("FALKORDB_HOST", "localhost"),
        "port": int(os.environ.get("FALKORDB_PORT", "6379")),
        "graph_name": os.environ.get("FALKORDB_GRAPH_NAME", "tier1_verify"),
        "password": os.environ.get("FALKORDB_PASSWORD") or None,
    }


def _print_result(title: str, result) -> None:
    print(f"  {title} header: {getattr(result, 'header', None)}")
    rows = getattr(result, "result_set", result)
    if not rows:
        print(f"  {title}: (empty)")
        return
    for i, row in enumerate(rows):
        print(f"  {title}[{i}]: {row}")


def main() -> int:
    cfg = _env()
    print("=== FalkorDB client + vector-index smoke test ===")
    print(
        f"[1] config host={cfg['host']!r} port={cfg['port']} "
        f"graph={cfg['graph_name']!r} password_set={cfg['password'] is not None}"
    )

    graph = None
    try:
        print("[2] import falkordb.FalkorDB ...")
        from falkordb import FalkorDB

        print("[2] import ok")

        print("[3] connect ...")
        connect_kwargs: dict[str, object] = {
            "host": cfg["host"],
            "port": cfg["port"],
        }
        if cfg["password"] is not None:
            connect_kwargs["password"] = cfg["password"]
        db = FalkorDB(**connect_kwargs)
        graphs = db.list_graphs()
        print(f"[3] connected. existing graphs: {graphs}")

        print(f"[4] select_graph({cfg['graph_name']!r}) ...")
        graph = db.select_graph(str(cfg["graph_name"]))
        if str(cfg["graph_name"]) in graphs:
            print("[4] leftover test graph found; deleting it first ...")
            graph.delete()
            print("[4] leftover graph deleted")
        print("[4] graph selected")

        print("[5] CREATE 2 DummyNode nodes + 1 RELATED_TO edge (vecf32 embeddings) ...")
        create_result = graph.query(
            f"""
            CREATE (a:{LABEL} {{name: $name_a, embedding: vecf32($vec_a)}})
            CREATE (b:{LABEL} {{name: $name_b, embedding: vecf32($vec_b)}})
            CREATE (a)-[r:{REL_TYPE}]->(b)
            RETURN a.name, b.name, type(r)
            """,
            params={
                "name_a": NODE_A,
                "name_b": NODE_B,
                "vec_a": VEC_A,
                "vec_b": VEC_B,
            },
        )
        _print_result("create", create_result)
        print("[5] graph data created")

        # Confirmed against installed falkordb 1.7.1 Graph.create_node_vector_index:
        # it emits
        #   CREATE VECTOR INDEX FOR (e:Label) ON (e.prop)
        #   OPTIONS {dimension:<dim>, similarityFunction:'<fn>'}
        print(
            f"[6] create_node_vector_index({LABEL!r}, {VECTOR_PROP!r}, "
            f"dim={VECTOR_DIM}, similarity_function='cosine') ..."
        )
        index_result = graph.create_node_vector_index(
            LABEL,
            VECTOR_PROP,
            dim=VECTOR_DIM,
            similarity_function="cosine",
        )
        _print_result("create_index", index_result)
        print("[6] vector index created")

        print("[7] list_indices() ...")
        indices = graph.list_indices()
        _print_result("indices", indices)
        print("[7] index listing done")

        print("[8] hybrid query — Cypher MATCH ...")
        match_result = graph.query(f"""
            MATCH (a:{LABEL})-[r:{REL_TYPE}]->(b:{LABEL})
            RETURN a.name AS source, type(r) AS rel, b.name AS target
            """)
        _print_result("MATCH", match_result)
        print("[8] MATCH ok")

        print("[8] hybrid query — vector similarity (db.idx.vector.queryNodes) ...")
        # Procedure label/attribute/k are Cypher literals in the current client
        # docs; only the query vector is parameterized and wrapped in vecf32().
        vector_result = graph.query(
            f"""
            CALL db.idx.vector.queryNodes('{LABEL}', '{VECTOR_PROP}', 2, vecf32($query_vec))
            YIELD node, score
            RETURN node.name AS name, score
            ORDER BY score
            """,
            params={"query_vec": QUERY_VEC},
        )
        _print_result("VECTOR", vector_result)
        print("[8] vector search ok")

        print("[8] hybrid query — MATCH after vector CALL ...")
        hybrid_result = graph.query(
            f"""
            CALL db.idx.vector.queryNodes('{LABEL}', '{VECTOR_PROP}', 2, vecf32($query_vec))
            YIELD node, score
            MATCH (node)-[r:{REL_TYPE}]->(other)
            RETURN node.name AS source, other.name AS target, type(r) AS rel, score
            """,
            params={"query_vec": QUERY_VEC},
        )
        _print_result("HYBRID", hybrid_result)
        print("[8] hybrid query ok")

        print("[9] all steps succeeded")
        return 0

    # This is a one-off environment smoke test, not a call site inside a real
    # Tier 1 module: its whole job is to catch *anything* the client raises and
    # report it, since the exact exception types the client can raise are one
    # of the things this script exists to discover. Real modules (civic_loader,
    # retrieval, literature_rag) must not do this — see rarecure-build-reference.md.
    except Exception as exc:  # noqa: BLE001
        print(f"[FAIL] {type(exc).__name__}: {exc}")
        traceback.print_exc()
        return 1

    finally:
        if graph is not None:
            print(f"[10] cleanup: delete graph {cfg['graph_name']!r} ...")
            try:
                graph.delete()
                print("[10] graph deleted")
            except Exception as exc:  # noqa: BLE001 -- best-effort cleanup, see above
                print(f"[10] cleanup failed: {type(exc).__name__}: {exc}")


if __name__ == "__main__":
    sys.exit(main())
