"""Unit tests for pubmed_loader.py -- no network, no live FalkorDB, no real
model load (embed_text is monkeypatched).

Mocks httpx at the transport level (httpx.MockTransport), routed on URL
PATH (esearch.fcgi vs efetch.fcgi), the actual boundary requests cross --
same discipline as test_chembl_enrich.py, whose mock-routing-by-substring
bug (a query string containing another endpoint's name) is exactly why
path-suffix routing is used here from the start rather than `in url`.
"""

from __future__ import annotations

import json

import httpx
import pytest

from secondlook.tier1 import pubmed_loader
from secondlook.tier1.pubmed_loader import (
    LoadSummary,
    PubmedApiError,
    fetch_pubmed_abstracts,
    load_publication,
    load_publications_for_query,
)

GOOD_ARTICLE_XML = b"""<?xml version="1.0" ?>
<PubmedArticleSet>
<PubmedArticle><MedlineCitation><PMID Version="1">12345678</PMID>
<Article><Journal><Title>Cancers</Title><JournalIssue><PubDate><Year>2024</Year>
</PubDate></JournalIssue></Journal>
<ArticleTitle>NTRK fusion review.</ArticleTitle>
<Abstract><AbstractText Label="BACKGROUND">Background text.</AbstractText>
<AbstractText Label="RESULTS">Results text.</AbstractText></Abstract>
<PublicationTypeList><PublicationType>Journal Article</PublicationType></PublicationTypeList>
</Article>
<MeshHeadingList><MeshHeading><DescriptorName>Humans</DescriptorName></MeshHeading>
<MeshHeading><DescriptorName>Neoplasms</DescriptorName></MeshHeading></MeshHeadingList>
</MedlineCitation></PubmedArticle>
</PubmedArticleSet>
"""

MARKUP_ARTICLE_XML = b"""<?xml version="1.0" ?>
<PubmedArticleSet>
<PubmedArticle><MedlineCitation><PMID Version="1">99999999</PMID>
<Article><Journal><Title>JCO</Title><JournalIssue><PubDate><MedlineDate>2020 Jan-Feb</MedlineDate>
</PubDate></JournalIssue></Journal>
<ArticleTitle>Adult Sarcomas with <i>NTRK</i> Fusions.</ArticleTitle>
<Abstract><AbstractText><i>NTRK1</i> fusions are oncogenic drivers.</AbstractText></Abstract>
</Article>
</MedlineCitation></PubmedArticle>
</PubmedArticleSet>
"""

NO_ABSTRACT_ARTICLE_XML = b"""<?xml version="1.0" ?>
<PubmedArticleSet>
<PubmedArticle><MedlineCitation><PMID Version="1">11111111</PMID>
<Article><Journal><Title>Nat Genet</Title><JournalIssue><PubDate><Year>1998</Year>
</PubDate></JournalIssue></Journal>
<ArticleTitle>A novel gene fusion in congenital fibrosarcoma.</ArticleTitle>
</Article>
</MedlineCitation></PubmedArticle>
</PubmedArticleSet>
"""

EMPTY_ARTICLE_SET_XML = b'<?xml version="1.0" ?>\n<PubmedArticleSet></PubmedArticleSet>\n'


def _esearch_body(idlist: list[str]) -> dict:
    return {
        "header": {"type": "esearch"},
        "esearchresult": {"count": str(len(idlist)), "idlist": idlist},
    }


def make_client(
    idlist: list[str],
    efetch_xml: bytes,
    *,
    esearch_status: int = 200,
    esearch_content_type: str = "application/json",
    efetch_status: int = 200,
    efetch_content_type: str = "text/xml; charset=UTF-8",
    esearch_body: dict | None = None,
) -> httpx.Client:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if request.url.path.endswith("/esearch.fcgi"):
            body = esearch_body if esearch_body is not None else _esearch_body(idlist)
            return httpx.Response(
                esearch_status,
                headers={"content-type": esearch_content_type},
                content=json.dumps(body).encode(),
            )
        return httpx.Response(
            efetch_status, headers={"content-type": efetch_content_type}, content=efetch_xml
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    client._test_calls = calls  # type: ignore[attr-defined]
    return client


class FakeGraph:
    def __init__(self):
        self.writes: list[tuple[str, dict]] = []

    def query(self, cypher: str, params: dict | None = None):
        params = params or {}
        if cypher.strip().startswith("MERGE (p:Publication"):
            self.writes.append((cypher, params))
            return _Result([])
        raise AssertionError(f"unexpected query: {cypher}")


class _Result:
    def __init__(self, rows):
        self.result_set = rows


@pytest.fixture(autouse=True)
def reset_query_cache():
    """The query cache is deliberately module-level, persistent state
    (RareCure rule #2 -- cached from day one). That makes it cross-test
    pollution risk unless reset before every test."""
    pubmed_loader._QUERY_CACHE.clear()
    yield
    pubmed_loader._QUERY_CACHE.clear()


@pytest.fixture(autouse=True)
def stub_embed_text(monkeypatch):
    monkeypatch.setattr(pubmed_loader, "embed_text", lambda text: [0.0] * 384)


class TestFetchPubmedAbstracts:
    def test_parses_full_shape_correctly(self):
        client = make_client(["12345678"], GOOD_ARTICLE_XML)
        results = fetch_pubmed_abstracts("NTRK1 fusion sarcoma", client=client)

        assert len(results) == 1
        pub = results[0]
        assert pub["pmid"] == "12345678"
        assert pub["title"] == "NTRK fusion review."
        assert pub["journal"] == "Cancers"
        assert pub["year"] == 2024
        assert pub["pub_type"] == ["Journal Article"]
        assert pub["mesh_terms"] == ["Humans", "Neoplasms"]
        assert pub["abstract"] == "Background text. Results text."

    def test_nested_markup_in_title_and_abstract_is_stripped_not_dropped(self):
        """.text alone would silently drop everything after the first
        nested <i> tag -- itertext() must be used instead."""
        client = make_client(["99999999"], MARKUP_ARTICLE_XML)
        results = fetch_pubmed_abstracts("query", client=client)

        assert results[0]["title"] == "Adult Sarcomas with NTRK Fusions."
        assert results[0]["abstract"] == "NTRK1 fusions are oncogenic drivers."

    def test_medline_date_fallback_extracts_year(self):
        client = make_client(["99999999"], MARKUP_ARTICLE_XML)
        results = fetch_pubmed_abstracts("query", client=client)
        assert results[0]["year"] == 2020

    def test_article_with_no_abstract_has_abstract_none_not_dropped(self):
        """No abstract is a legitimate PubMed record (e.g. a comment or
        letter) -- fetch_pubmed_abstracts must still return it, honestly
        shaped, rather than silently omitting it; load_publication is
        where the "nothing to chunk" decision is made."""
        client = make_client(["11111111"], NO_ABSTRACT_ARTICLE_XML)
        results = fetch_pubmed_abstracts("query", client=client)

        assert len(results) == 1
        assert results[0]["pmid"] == "11111111"
        assert results[0]["abstract"] is None

    def test_no_results_returns_empty_list_not_an_error(self):
        client = make_client([], b"")
        results = fetch_pubmed_abstracts("zzzznonexistentqueryxyz123", client=client)
        assert results == []

    def test_esearch_error_key_raises_even_on_http_200(self):
        """Confirmed live against db=pubmedBAD: an invalid request returns
        HTTP 200 with an ERROR key inside esearchresult, not a non-200
        status -- status-code-only validation would silently pass this."""
        client = make_client(
            [], b"", esearch_body={"esearchresult": {"ERROR": "Invalid db name specified"}}
        )
        with pytest.raises(PubmedApiError, match="Invalid db name"):
            fetch_pubmed_abstracts("query", client=client)

    def test_esearch_non_200_raises(self):
        client = make_client([], b"", esearch_status=500)
        with pytest.raises(PubmedApiError):
            fetch_pubmed_abstracts("query", client=client)

    def test_esearch_wrong_content_type_raises_even_on_200(self):
        client = make_client([], b"", esearch_content_type="text/html")
        with pytest.raises(PubmedApiError, match="content-type"):
            fetch_pubmed_abstracts("query", client=client)

    def test_esearch_malformed_json_raises(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200, headers={"content-type": "application/json"}, content=b"not json"
            )

        client = httpx.Client(transport=httpx.MockTransport(handler))
        with pytest.raises(PubmedApiError):
            fetch_pubmed_abstracts("query", client=client)

    def test_esearch_missing_idlist_raises(self):
        client = make_client([], b"", esearch_body={"esearchresult": {}})
        with pytest.raises(PubmedApiError, match="idlist"):
            fetch_pubmed_abstracts("query", client=client)

    def test_efetch_non_200_raises(self):
        client = make_client(["1"], b"", efetch_status=500)
        with pytest.raises(PubmedApiError):
            fetch_pubmed_abstracts("query", client=client)

    def test_efetch_wrong_content_type_raises_even_on_200(self):
        client = make_client(["1"], GOOD_ARTICLE_XML, efetch_content_type="text/html")
        with pytest.raises(PubmedApiError, match="content-type"):
            fetch_pubmed_abstracts("query", client=client)

    def test_efetch_malformed_xml_raises(self):
        client = make_client(["1"], b"<not><valid")
        with pytest.raises(PubmedApiError):
            fetch_pubmed_abstracts("query", client=client)

    def test_pmid_not_found_returns_fewer_articles_than_requested(self):
        """Confirmed live: efetch on a nonexistent PMID returns a
        well-formed but empty <PubmedArticleSet/>, not an error."""
        client = make_client(["999999999999"], EMPTY_ARTICLE_SET_XML)
        results = fetch_pubmed_abstracts("query", client=client)
        assert results == []

    def test_identical_query_hits_the_mock_transport_only_once(self):
        """Proves caching actually short-circuits the HTTP call, not just
        that a cache dict exists somewhere."""
        client = make_client(["12345678"], GOOD_ARTICLE_XML)

        first = fetch_pubmed_abstracts("NTRK1 fusion sarcoma", client=client)
        calls_after_first = len(client._test_calls)
        second = fetch_pubmed_abstracts("NTRK1 fusion sarcoma", client=client)

        assert second == first
        assert len(client._test_calls) == calls_after_first, "second call must not hit the client"

    def test_different_max_results_are_not_conflated_by_the_cache(self):
        """The cache key includes max_results -- a query-only key would
        silently return a truncated cached result for a later call asking
        for more results on the same query string."""
        client = make_client(["12345678"], GOOD_ARTICLE_XML)

        fetch_pubmed_abstracts("query", max_results=5, client=client)
        calls_after_first = len(client._test_calls)
        fetch_pubmed_abstracts("query", max_results=10, client=client)

        assert len(client._test_calls) > calls_after_first, "different max_results must re-fetch"

    def test_api_key_included_when_env_var_set(self, monkeypatch):
        monkeypatch.setenv("PUBMED_API_KEY", "test-key-123")
        seen_urls: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen_urls.append(str(request.url))
            if request.url.path.endswith("/esearch.fcgi"):
                return httpx.Response(
                    200,
                    headers={"content-type": "application/json"},
                    content=json.dumps(_esearch_body(["12345678"])).encode(),
                )
            return httpx.Response(
                200, headers={"content-type": "text/xml"}, content=GOOD_ARTICLE_XML
            )

        client = httpx.Client(transport=httpx.MockTransport(handler))
        fetch_pubmed_abstracts("query", client=client)

        assert all("api_key=test-key-123" in url for url in seen_urls)


class TestLoadPublication:
    GOOD_PUB = {
        "pmid": "12345678",
        "title": "NTRK fusion review.",
        "journal": "Cancers",
        "year": 2024,
        "pub_type": ["Journal Article"],
        "mesh_terms": ["Humans"],
        "abstract": "Background text. Results text.",
    }

    def test_writes_publication_node_with_embedding_and_provenance(self):
        graph = FakeGraph()
        written = load_publication(self.GOOD_PUB, graph=graph)

        assert written is True
        assert len(graph.writes) == 1
        cypher, params = graph.writes[0]
        assert params["pmid"] == "12345678"
        assert params["vec"] == [0.0] * 384
        props = params["props"]
        assert props["abstract"] == "Background text. Results text."
        assert props["title"] == "NTRK fusion review."
        assert props["source"] == "PubMed"
        assert "retrieved_at" in props
        assert "pmid" not in props, "MERGE key must not be redundantly duplicated into SET props"

    def test_uses_merge_not_create(self):
        graph = FakeGraph()
        load_publication(self.GOOD_PUB, graph=graph)
        cypher, _params = graph.writes[0]
        assert cypher.strip().startswith("MERGE (p:Publication")
        assert "CREATE" not in cypher

    def test_idempotent_on_repeated_calls_with_same_pmid(self):
        graph = FakeGraph()
        load_publication(self.GOOD_PUB, graph=graph)
        load_publication(self.GOOD_PUB, graph=graph)

        assert len(graph.writes) == 2, "both calls issue a MERGE (idempotent on the graph side)"
        for cypher, _params in graph.writes:
            assert cypher.strip().startswith("MERGE (p:Publication")

    def test_missing_abstract_is_skipped_and_returns_false(self):
        graph = FakeGraph()
        pub = {**self.GOOD_PUB, "abstract": None}
        written = load_publication(pub, graph=graph)

        assert written is False
        assert graph.writes == []

    def test_missing_pmid_is_skipped_and_returns_false(self):
        graph = FakeGraph()
        pub = {**self.GOOD_PUB, "pmid": None}
        written = load_publication(pub, graph=graph)

        assert written is False
        assert graph.writes == []


class TestLoadPublicationsForQuery:
    def test_summary_counts_fetched_written_and_skipped(self, monkeypatch):
        graph = FakeGraph()

        def fake_fetch(query, max_results=20, *, client=None):
            return [
                {
                    "pmid": "1",
                    "title": "T1",
                    "journal": "J",
                    "year": 2024,
                    "pub_type": [],
                    "mesh_terms": [],
                    "abstract": "Real abstract text.",
                },
                {
                    "pmid": "2",
                    "title": "T2",
                    "journal": "J",
                    "year": 2024,
                    "pub_type": [],
                    "mesh_terms": [],
                    "abstract": None,
                },
            ]

        monkeypatch.setattr(pubmed_loader, "fetch_pubmed_abstracts", fake_fetch)
        summary = load_publications_for_query("query", graph=graph)

        assert isinstance(summary, LoadSummary)
        assert summary.fetched == 2
        assert summary.written == 1
        assert summary.skipped_no_abstract == 1
        assert len(graph.writes) == 1

    def test_dry_run_does_not_write(self, monkeypatch):
        graph = FakeGraph()

        def fake_fetch(query, max_results=20, *, client=None):
            return [
                {
                    "pmid": "1",
                    "title": "T",
                    "journal": "J",
                    "year": 2024,
                    "pub_type": [],
                    "mesh_terms": [],
                    "abstract": "Real abstract text.",
                }
            ]

        monkeypatch.setattr(pubmed_loader, "fetch_pubmed_abstracts", fake_fetch)
        summary = load_publications_for_query("query", graph=graph, dry_run=True)

        assert summary.fetched == 1
        assert graph.writes == [], "dry-run must not write to the graph"

    def test_summary_to_dict_is_json_serializable(self):
        json.dumps(LoadSummary(query="q", fetched=1, written=1).to_dict())
