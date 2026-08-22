"""Unit tests for chembl_enrich.py -- no network, no live FalkorDB.

Mocks httpx at the transport level (httpx.MockTransport), which is the
actual boundary requests cross -- confirms the mock genuinely intercepts
the call rather than something upstream of it (RareCure rule #9).
"""

from __future__ import annotations

import json

import httpx
import pytest

from secondlook.tier1.chembl_enrich import (
    ChemblApiError,
    chembl_db_version,
    enrich_drug_classes,
    resolve_drug_class,
)


class FakeGraph:
    def __init__(self, unresolved_names: list[str], enriched_count: int = 0):
        self._unresolved_names = unresolved_names
        self._enriched_count = enriched_count
        self.writes: list[tuple[str, dict]] = []

    def query(self, cypher: str, params: dict | None = None):
        params = params or {}
        if "WHERE d.drug_class IS NULL RETURN d.name" in cypher:
            return _Result([(name,) for name in self._unresolved_names])
        if "WHERE d.drug_class IS NOT NULL RETURN count(d)" in cypher:
            return _Result([(self._enriched_count,)])
        if cypher.strip().startswith("MATCH (d:Drug {name:"):
            self.writes.append((cypher, params))
            return _Result([])
        raise AssertionError(f"unexpected query: {cypher}")


class _Result:
    def __init__(self, rows):
        self.result_set = rows


def _molecule_response(molecule_chembl_id: str | None) -> dict:
    if molecule_chembl_id is None:
        return {"molecules": []}
    return {"molecules": [{"molecule_chembl_id": molecule_chembl_id}]}


def _mechanism_response(moa: str | None) -> dict:
    if moa is None:
        return {"mechanisms": []}
    return {"mechanisms": [{"mechanism_of_action": moa}]}


def _status_response(version: str | None = "ChEMBL_37") -> dict:
    return {"chembl_db_version": version, "status": "UP"}


def make_client(
    molecule_body: dict,
    mechanism_body: dict,
    *,
    status_body: dict | None = None,
    status: int = 200,
    content_type: str = "application/json",
) -> httpx.Client:
    """Routes on URL PATH (not the full URL) across /status, /molecule, and
    /mechanism -- the mechanism endpoint's query string contains
    "molecule_chembl_id", which would false-match a substring check against
    the whole URL, so path-suffix routing is required, not `in`."""
    status_body = status_body if status_body is not None else _status_response()
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        path = request.url.path
        if path.endswith("/status"):
            body = status_body
        elif path.endswith("/molecule"):
            body = molecule_body
        else:
            body = mechanism_body
        return httpx.Response(
            status,
            headers={"content-type": content_type},
            content=json.dumps(body).encode(),
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    client._test_calls = calls  # type: ignore[attr-defined]
    return client


class TestResolveDrugClass:
    def test_resolves_mechanism_of_action(self):
        client = make_client(
            _molecule_response("CHEMBL3889654"),
            _mechanism_response("Neurotrophic tyrosine kinase receptor inhibitor"),
        )
        result = resolve_drug_class("Larotrectinib", client=client)

        assert result == "Neurotrophic tyrosine kinase receptor inhibitor"
        assert len(client._test_calls) == 2, "mock must actually be hit for both calls"

    def test_no_molecule_match_returns_none(self):
        client = make_client(_molecule_response(None), {})
        assert resolve_drug_class("BET Inhibitor", client=client) is None

    def test_molecule_found_but_no_mechanism_returns_none(self):
        client = make_client(_molecule_response("CHEMBL1"), _mechanism_response(None))
        assert resolve_drug_class("Doxorubicin", client=client) is None

    def test_non_200_raises_chembl_api_error(self):
        client = make_client(_molecule_response("CHEMBL1"), {}, status=500)
        with pytest.raises(ChemblApiError):
            resolve_drug_class("X", client=client)

    def test_wrong_content_type_raises_even_on_200(self):
        """The exact RareCure DGIdb failure shape: 200 status, wrong body."""
        client = make_client(
            _molecule_response("CHEMBL1"), {}, status=200, content_type="text/html"
        )
        with pytest.raises(ChemblApiError, match="content-type"):
            resolve_drug_class("X", client=client)

    def test_malformed_json_raises(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200, headers={"content-type": "application/json"}, content=b"not json"
            )

        client = httpx.Client(transport=httpx.MockTransport(handler))
        with pytest.raises(ChemblApiError):
            resolve_drug_class("X", client=client)

    def test_missing_molecules_key_raises(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, headers={"content-type": "application/json"}, content=b"{}")

        client = httpx.Client(transport=httpx.MockTransport(handler))
        with pytest.raises(ChemblApiError, match="molecules"):
            resolve_drug_class("X", client=client)


class TestChemblDbVersion:
    def test_resolves_the_real_field_name(self):
        client = make_client({}, {}, status_body=_status_response("ChEMBL_37"))
        assert chembl_db_version(client=client) == "ChEMBL_37"

    def test_missing_field_returns_none_rather_than_raising(self):
        client = make_client({}, {}, status_body={"status": "UP"})
        assert chembl_db_version(client=client) is None


class TestEnrichDrugClasses:
    def test_resolved_drugs_are_written_and_counted(self):
        graph = FakeGraph(unresolved_names=["Larotrectinib"])
        client = make_client(
            _molecule_response("CHEMBL3889654"),
            _mechanism_response("Neurotrophic tyrosine kinase receptor inhibitor"),
        )

        summary = enrich_drug_classes(graph, client=client)

        assert summary.drugs_considered == 1
        assert summary.resolved == {
            "Larotrectinib": "Neurotrophic tyrosine kinase receptor inhibitor"
        }
        assert summary.unresolved == []
        assert len(graph.writes) == 1
        _cypher, params = graph.writes[0]
        assert params["name"] == "Larotrectinib"
        props = params["props"]
        assert props["drug_class"] == "Neurotrophic tyrosine kinase receptor inhibitor"
        # Per-property provenance ONLY -- see TestWithEnrichmentProvenance in
        # test_graph_schema.py for why bare source/retrieved_at must be
        # absent: those keys are what let `SET d += $props` overwrite the
        # Drug node's original CIViC provenance.
        assert props["drug_class_source"] == "ChEMBL"
        assert props["drug_class_source_version"] == "ChEMBL_37"
        assert "drug_class_retrieved_at" in props
        assert "source" not in props
        assert "retrieved_at" not in props
        assert "source_version" not in props

    def test_unresolved_drug_is_counted_not_written(self):
        graph = FakeGraph(unresolved_names=["BET Inhibitor"])
        client = make_client(_molecule_response(None), {})

        summary = enrich_drug_classes(graph, client=client)

        assert summary.unresolved == ["BET Inhibitor"]
        assert summary.resolved == {}
        assert graph.writes == []

    def test_dry_run_resolves_but_writes_nothing(self):
        graph = FakeGraph(unresolved_names=["Larotrectinib"])
        client = make_client(
            _molecule_response("CHEMBL3889654"), _mechanism_response("Kinase inhibitor")
        )

        summary = enrich_drug_classes(graph, client=client, dry_run=True)

        assert summary.resolved == {"Larotrectinib": "Kinase inhibitor"}
        assert graph.writes == [], "dry-run must not write to the graph"

    def test_already_enriched_count_is_reported(self):
        graph = FakeGraph(unresolved_names=[], enriched_count=5)
        client = make_client({}, {})

        summary = enrich_drug_classes(graph, client=client)

        assert summary.already_enriched == 5
        assert summary.drugs_considered == 0

    def test_error_for_one_drug_does_not_abort_the_rest(self):
        graph = FakeGraph(unresolved_names=["Bad", "Good"])

        def handler(request: httpx.Request) -> httpx.Response:
            path = request.url.path
            if path.endswith("/status"):
                body, status = _status_response(), 200
            elif "pref_name__iexact=Bad" in str(request.url):
                body, status = {}, 500
            elif path.endswith("/molecule"):
                body, status = _molecule_response("CHEMBL1"), 200
            else:
                body, status = _mechanism_response("Inhibitor"), 200
            return httpx.Response(
                status,
                headers={"content-type": "application/json"},
                content=json.dumps(body).encode(),
            )

        client = httpx.Client(transport=httpx.MockTransport(handler))
        summary = enrich_drug_classes(graph, client=client)

        assert "Bad" in summary.errors
        assert summary.resolved == {"Good": "Inhibitor"}

    def test_status_endpoint_failure_does_not_abort_the_whole_run(self):
        """The db version is a nice-to-have stamp, not load-bearing -- a
        /status outage must not prevent resolving and writing drug_class."""
        graph = FakeGraph(unresolved_names=["Larotrectinib"])

        def handler(request: httpx.Request) -> httpx.Response:
            path = request.url.path
            if path.endswith("/status"):
                return httpx.Response(
                    500, headers={"content-type": "application/json"}, content=b"{}"
                )
            if path.endswith("/molecule"):
                body = _molecule_response("CHEMBL3889654")
            else:
                body = _mechanism_response("Neurotrophic tyrosine kinase receptor inhibitor")
            return httpx.Response(
                200, headers={"content-type": "application/json"}, content=json.dumps(body).encode()
            )

        client = httpx.Client(transport=httpx.MockTransport(handler))
        summary = enrich_drug_classes(graph, client=client)

        assert summary.resolved == {
            "Larotrectinib": "Neurotrophic tyrosine kinase receptor inhibitor"
        }
        _cypher, params = graph.writes[0]
        assert params["props"]["drug_class_source_version"] is None
        assert summary.db_version_fetch_failed is True, (
            "a null drug_class_source_version is ambiguous on its own -- this flag is what "
            "lets a caller distinguish '/status errored' from 'version just wasn't available'"
        )

    def test_db_version_fetch_failed_is_false_when_status_succeeds(self):
        graph = FakeGraph(unresolved_names=["Larotrectinib"])
        client = make_client(
            _molecule_response("CHEMBL3889654"),
            _mechanism_response("Neurotrophic tyrosine kinase receptor inhibitor"),
        )

        summary = enrich_drug_classes(graph, client=client)

        assert summary.db_version_fetch_failed is False

    def test_summary_to_dict_is_json_serializable(self):
        graph = FakeGraph(unresolved_names=[])
        client = make_client({}, {})
        summary = enrich_drug_classes(graph, client=client)

        json.dumps(summary.to_dict())  # must not raise
