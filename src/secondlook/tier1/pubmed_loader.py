"""Fetch PubMed abstracts (E-utilities) and load them as Publication nodes,
per tier1-implementation-spec.md Deliverable 4's ingestion requirements.

Scope for this phase, deliberately narrower than the full Literature RAG
deliverable: PubMed ingestion + wiring literature hits into the EXISTING
retrieve_semantic() vector search from Phase 4 (_publication_to_item in
semantic_retrieval.py). Hybrid BM25+dense fusion, reranking, and
publication-type/recency weighting are explicitly deferred to a later
phase, after Phase 6's eval set exists to tune them against.

Two E-utilities calls, both shape-checked before use (RareCure rule #1 --
a 200 status is not success):
  esearch.fcgi -- query string -> list of PMIDs (JSON)
  efetch.fcgi  -- PMIDs -> full records, abstract included (XML)

Confirmed live before writing the parser (not assumed):
  - esearch errors (e.g. a bad `db` value) return HTTP 200 with an "ERROR"
    key inside `esearchresult` rather than a non-200 status or an empty
    idlist -- checked explicitly below.
  - efetch on a nonexistent PMID returns HTTP 200 with a valid but empty
    <PubmedArticleSet></PubmedArticleSet> -- handled as "no article", not
    an error.
  - Article/abstract text can contain nested markup (e.g. <i>NTRK1</i>
    inside a title or abstract sentence) -- extracted via itertext(), not
    .text, or embedded tag content would be silently dropped.
  - Abstracts are frequently split across multiple labelled
    <AbstractText Label="BACKGROUND"|"METHODS"|...> sections -- joined into
    one string per RareCure "chunk by abstract" requirement (one PubMed
    abstract = one Publication node, never split further).
  - JournalIssue/PubDate/Year is sometimes absent in favor of a freeform
    MedlineDate string (e.g. "2020 Jan-Feb") -- a 4-digit year is pulled
    out of that as a fallback rather than left unset.
  - MeSH heading indexing lags for newly published articles -- an empty
    mesh_terms list is a legitimate result, not a parse failure.

Run:
    python -m secondlook.tier1.pubmed_loader "NTRK1 fusion sarcoma"
    python -m secondlook.tier1.pubmed_loader "NTRK1 fusion sarcoma" --dry-run
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx

from secondlook.tier1.graph_connection import connect_graph
from secondlook.tier1.graph_schema import with_provenance
from secondlook.tier1.semantic_retrieval import embed_text

logger = logging.getLogger(__name__)

ESEARCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
EFETCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
REQUEST_TIMEOUT_SECONDS = 30.0
SOURCE_NAME = "PubMed"


class PubmedApiError(RuntimeError):
    """Raised on transport failure, non-200, wrong content-type, an
    esearchresult.ERROR payload, or a shape E-utilities' own docs don't
    promise. Caught at the call site -- never a bare except."""


# Cached on the exact (query, max_results) string+int from the start, not
# added later as an optimization (RareCure rule #2 -- civic_loader.py's own
# sibling bug class: ~5,700 near-duplicate calls across a cohort with no
# caching). Keyed on max_results too, not just the query string: a plain
# query-only key would silently return a truncated cached result for a
# later call asking for more results on the same query.
_QUERY_CACHE: dict[tuple[str, int], list[dict]] = {}


def _api_key_param() -> dict[str, str]:
    api_key = os.environ.get("PUBMED_API_KEY")
    return {"api_key": api_key} if api_key else {}


def _esearch_pmids(client: httpx.Client, query: str, max_results: int) -> list[str]:
    """Query string -> list of PMIDs. Shape-checked: an esearch "error" is a
    200 with an ERROR key inside esearchresult, confirmed live against
    db=pubmedBAD -- not a non-200 status, so status-code-only validation
    would silently pass it through as success."""
    params = {
        "db": "pubmed",
        "term": query,
        "retmax": str(max_results),
        "retmode": "json",
        **_api_key_param(),
    }
    try:
        response = client.get(ESEARCH_URL, params=params)
    except httpx.RequestError as exc:
        raise PubmedApiError(f"PubMed esearch request failed: {type(exc).__name__}: {exc}") from exc

    if response.status_code != 200:
        raise PubmedApiError(f"PubMed esearch returned HTTP {response.status_code}")
    if "application/json" not in response.headers.get("content-type", ""):
        raise PubmedApiError(
            f"PubMed esearch returned content-type "
            f"{response.headers.get('content-type')!r}, expected application/json "
            f"(body starts: {response.text[:120]!r})"
        )
    try:
        body = response.json()
    except json.JSONDecodeError as exc:
        raise PubmedApiError(f"PubMed esearch returned undecodable JSON: {exc}") from exc

    result = body.get("esearchresult")
    if not isinstance(result, dict):
        raise PubmedApiError(f"PubMed esearch response missing 'esearchresult': {body!r}")
    if "ERROR" in result:
        raise PubmedApiError(f"PubMed esearch error: {result['ERROR']}")
    idlist = result.get("idlist")
    if not isinstance(idlist, list):
        raise PubmedApiError(f"PubMed esearch response missing 'idlist' list: {result!r}")
    return idlist


def _efetch_articles(client: httpx.Client, pmids: list[str]) -> list[ET.Element]:
    """PMIDs -> parsed <PubmedArticle> elements. A nonexistent PMID is
    simply absent from the result (confirmed live: efetch returns an empty
    but well-formed <PubmedArticleSet/>, not an error) -- callers see fewer
    articles than PMIDs requested, never a fabricated placeholder."""
    params = {
        "db": "pubmed",
        "id": ",".join(pmids),
        "rettype": "abstract",
        "retmode": "xml",
        **_api_key_param(),
    }
    try:
        response = client.get(EFETCH_URL, params=params)
    except httpx.RequestError as exc:
        raise PubmedApiError(f"PubMed efetch request failed: {type(exc).__name__}: {exc}") from exc

    if response.status_code != 200:
        raise PubmedApiError(f"PubMed efetch returned HTTP {response.status_code}")
    if "xml" not in response.headers.get("content-type", ""):
        raise PubmedApiError(
            f"PubMed efetch returned content-type {response.headers.get('content-type')!r}, "
            f"expected xml (body starts: {response.text[:120]!r})"
        )
    try:
        root = ET.fromstring(response.content)
    except ET.ParseError as exc:
        raise PubmedApiError(f"PubMed efetch returned unparseable XML: {exc}") from exc

    return root.findall(".//PubmedArticle")


def _text_of(elem: ET.Element | None) -> str | None:
    """itertext(), not .text -- a title or abstract sentence can contain
    nested markup (e.g. "<i>NTRK1</i> fusions"), and .text alone silently
    drops everything after the first nested tag."""
    if elem is None:
        return None
    text = "".join(elem.itertext()).strip()
    return text or None


_YEAR_RE = re.compile(r"(1[89]\d{2}|20\d{2})")


def _extract_year(article: ET.Element) -> int | None:
    year_text = article.findtext(".//JournalIssue/PubDate/Year")
    if year_text and year_text.strip().isdigit():
        return int(year_text.strip())
    medline_date = article.findtext(".//JournalIssue/PubDate/MedlineDate")
    if medline_date:
        match = _YEAR_RE.search(medline_date)
        if match:
            return int(match.group(1))
    return None


def _parse_article(article: ET.Element) -> dict | None:
    """One <PubmedArticle> -> one plain dict, or None if it has no PMID at
    all (defensive -- the DTD requires one, but never trust an upstream
    payload to match its own spec)."""
    pmid = article.findtext(".//PMID")
    if not pmid or not pmid.strip():
        return None

    abstract_parts = article.findall(".//Abstract/AbstractText")
    abstract = " ".join(filter(None, (_text_of(part) for part in abstract_parts))) or None

    return {
        "pmid": pmid.strip(),
        "title": _text_of(article.find(".//ArticleTitle")),
        "journal": article.findtext(".//Journal/Title"),
        "year": _extract_year(article),
        "pub_type": [
            t.text for t in article.findall(".//PublicationTypeList/PublicationType") if t.text
        ],
        "mesh_terms": [
            d.text
            for d in article.findall(".//MeshHeadingList/MeshHeading/DescriptorName")
            if d.text
        ],
        "abstract": abstract,
    }


def fetch_pubmed_abstracts(
    query: str, max_results: int = 20, *, client: httpx.Client | None = None
) -> list[dict]:
    """Search PubMed and fetch full records for the matches. Returns one
    dict per article (pmid/title/journal/year/pub_type/mesh_terms/abstract)
    -- including articles with abstract=None (e.g. letters, comments); the
    caller decides whether a missing abstract means "nothing to load"
    rather than that being hidden here.
    """
    cache_key = (query, max_results)
    if cache_key in _QUERY_CACHE:
        return _QUERY_CACHE[cache_key]

    owns_client = client is None
    client = client or httpx.Client(
        timeout=REQUEST_TIMEOUT_SECONDS, headers={"User-Agent": "SecondLook-Tier1/1.0"}
    )
    try:
        pmids = _esearch_pmids(client, query, max_results)
        if not pmids:
            results: list[dict] = []
        else:
            articles = _efetch_articles(client, pmids)
            results = [
                parsed for article in articles if (parsed := _parse_article(article)) is not None
            ]
    finally:
        if owns_client:
            client.close()

    _QUERY_CACHE[cache_key] = results
    return results


def load_publication(pub: dict, graph=None) -> bool:
    """MERGE exactly one Publication node for a fetched abstract. Returns
    False (writes nothing) if there's no abstract text -- "one PubMed
    abstract = one Publication node" per Deliverable 4 means an
    abstract-less record has nothing to chunk into a citable node.

    Single-source write: with_provenance(), not with_enrichment_provenance()
    -- no other module writes to a Publication node yet, so there is no
    second source's provenance to preserve alongside this one (see
    with_enrichment_provenance()'s docstring for when that changes).

    abstract_embedding is computed and written here directly (not via a
    separate backfill pass like backfill_evidence_embeddings) because,
    unlike CIViC's EvidenceItem nodes, a Publication node created by this
    loader always already has its abstract text in hand at write time --
    there's no readiness gap to bridge later. Set via vecf32() in the same
    query as the rest of the properties, mirroring
    backfill_evidence_embeddings()'s placeholder-drop pattern: the vector
    needs an explicit cast, so it must never pass through a plain
    `SET p += $props` merge.
    """
    graph = graph if graph is not None else connect_graph()
    pmid = pub.get("pmid")
    abstract = pub.get("abstract")
    if not pmid or not abstract:
        logger.warning("load_publication: skipping pmid=%r -- no abstract text", pmid)
        return False

    vector = embed_text(abstract)
    props = with_provenance(
        {
            "title": pub.get("title"),
            "journal": pub.get("journal"),
            "year": pub.get("year"),
            "pub_type": pub.get("pub_type") or [],
            "mesh_terms": pub.get("mesh_terms") or [],
            "abstract": abstract,
        },
        source=SOURCE_NAME,
        retrieved_at=datetime.now(UTC).isoformat(),
    )
    graph.query(
        "MERGE (p:Publication {pmid: $pmid}) SET p.abstract_embedding = vecf32($vec), p += $props",
        params={"pmid": pmid, "vec": vector, "props": props},
    )
    return True


@dataclass
class LoadSummary:
    query: str
    fetched: int = 0
    written: int = 0
    skipped_no_abstract: int = 0

    def to_dict(self) -> dict:
        return {
            "query": self.query,
            "fetched": self.fetched,
            "written": self.written,
            "skipped_no_abstract": self.skipped_no_abstract,
        }


def load_publications_for_query(
    query: str,
    max_results: int = 20,
    *,
    graph=None,
    client: httpx.Client | None = None,
    dry_run: bool = False,
) -> LoadSummary:
    """Fetch + load in one call: the operational entry point every other
    tier1 loader module exposes (civic_loader.run_load,
    chembl_enrich.enrich_drug_classes). Every abstract-less record is
    counted, never just dropped with a log line (RareCure rule #4)."""
    pubs = fetch_pubmed_abstracts(query, max_results, client=client)
    summary = LoadSummary(query=query, fetched=len(pubs))

    if dry_run:
        summary.skipped_no_abstract = sum(1 for pub in pubs if not pub.get("abstract"))
        return summary

    graph = graph if graph is not None else connect_graph()
    for pub in pubs:
        if load_publication(pub, graph=graph):
            summary.written += 1
        else:
            summary.skipped_no_abstract += 1

    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Fetch PubMed abstracts and load them as Publication nodes"
    )
    parser.add_argument("query", help="PubMed search query, e.g. 'NTRK1 fusion sarcoma'")
    parser.add_argument("--max-results", type=int, default=20)
    parser.add_argument("--dry-run", action="store_true", help="Fetch and report, write nothing")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )

    try:
        summary = load_publications_for_query(args.query, args.max_results, dry_run=args.dry_run)
    except PubmedApiError as exc:
        print(f"[FAIL] {exc}")
        return 1

    print(json.dumps(summary.to_dict(), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
