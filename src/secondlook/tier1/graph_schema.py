"""Tier 1 knowledge-graph schema (V1) -- node/edge types, provenance, and
bounded-value validation for the CIViC/PubMed evidence graph in FalkorDB.

This module sits at the bottom of the Tier 1 import graph: it has zero
imports from any other `secondlook.tier1` module, and every other Tier 1
module (civic_loader, retrieval, literature_rag, ...) imports from it, never
the reverse. That keeps the project-wide import graph acyclic, matching the
config -> models -> modules -> pipeline pattern already used for Tier 2.

Not an ORM -- these are constants (node/edge shapes as data) plus a few
small validation helpers. Cypher query construction and the FalkorDB
connection itself live in the modules that actually talk to the graph.

Source: docs/tier1-implementation-spec.md SS3 (node/edge schema) and
docs/rarecure-build-reference.md (specific failure modes this schema is
designed against -- referenced inline below at the points they apply).
"""

from __future__ import annotations

from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Allowed-value sets
#
# RareCure rule (rarecure-build-reference.md SS4, "a metric that is claimed
# to be bounded but isn't"): any value presented as bounded/enum-constrained
# must be asserted in code against the real allowed set, not just covered by
# a loose test threshold. RareCure's own clamp_weights() claimed a bound its
# renormalization step silently violated, and the test threshold was set to
# accommodate the escape rather than catch it. assert_valid() below is the
# code-level check every write site for one of these fields must call.
# ---------------------------------------------------------------------------

EVIDENCE_LEVELS: frozenset[str] = frozenset("ABCDE")

# EVIDENCE_LEVELS above validates the EvidenceItem.evidence_level *graph
# property* -- CIViC's own A-E grading scale -- and is what civic_loader.py
# and civic_verify.py check the live CIViC API shape against. Do not add
# "literature" to it: CIViC will never emit that value, and doing so would
# let a future API-shape check silently accept a value that doesn't belong
# to CIViC's scale.
#
# RESULT_EVIDENCE_LEVELS below is scoped only to the Tier 1 *output item*
# shape defined in api-contracts.md, which explicitly allows evidence_level
# to be "CIViC A-E, or 'literature'". It validates what retrieval functions
# return to callers (e.g. a PubMed-sourced semantic hit with no CIViC
# grade), never what gets written into the graph -- retrieval.py's Mode
# 1/Mode 2 code paths, which read real EvidenceItem.evidence_level values
# off the graph, must keep validating against EVIDENCE_LEVELS, not this.
RESULT_EVIDENCE_LEVELS: frozenset[str] = EVIDENCE_LEVELS | {"literature"}

VARIANT_TYPES: frozenset[str] = frozenset({"missense", "indel", "fusion", "splice"})
RETRIEVAL_MODES: frozenset[str] = frozenset({"exact", "relaxed", "semantic"})
RESPONSE_DIRECTIONS: frozenset[str] = frozenset({"sensitive", "resistant"})

# Tier-2-facing extension (schema only -- see STRUCTURAL_SIGNAL below).
STRUCTURAL_SIGNAL_METHODS: frozenset[str] = frozenset({"mCSM-lig", "docking"})


def assert_valid(value: str, allowed: frozenset[str], field_name: str) -> None:
    """Raise ValueError if `value` is not in `allowed`; otherwise return None.

    Call this at every site that sets one of the bounded fields above
    (evidence_level, variant_type, retrieval_mode, direction, ...) rather
    than trusting the caller or an upstream API response to already be
    well-formed.
    """
    if value not in allowed:
        raise ValueError(
            f"{field_name}={value!r} is not one of the allowed values: {sorted(allowed)}"
        )


# ---------------------------------------------------------------------------
# Node types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class NodeType:
    """A graph node label plus its declared, type-specific property names.

    Deliberately not an ORM: this only documents the shape loaders and
    queries must agree on. Provenance properties (source/source_version/
    retrieved_at) are NOT included here -- they are added uniformly by
    with_provenance() at write time, on top of whichever NodeType's
    properties a given node carries.
    """

    label: str
    properties: tuple[str, ...]


GENE = NodeType("Gene", ("symbol", "ensembl_id", "uniprot_accession", "hgnc_id"))

VARIANT = NodeType(
    "Variant",
    (
        "hgvs_p",
        "hgvs_c",
        "protein_position",
        "ref_aa",
        "alt_aa",
        "variant_type",
        "civic_variant_id",
    ),
)

DISEASE = NodeType("Disease", ("name", "doid", "is_in_scope_cancer_type"))

DRUG = NodeType(
    "Drug",
    (
        "name",
        "chembl_id",
        "approval_status",
        "smiles",
        "india_availability",
        # Populated by a separate enrichment step (chembl_enrich.py), not by
        # civic_loader.py, from ChEMBL's `mechanism_of_action` (e.g.
        # "Neurotrophic tyrosine kinase receptor inhibitor") -- confirmed
        # live this resolves for only 58% of CIViC's therapy names (many are
        # CIViC's own generic placeholders like "BET Inhibitor" with no
        # ChEMBL entry at all), so this field is legitimately absent on a
        # real, correctly-loaded Drug node. Feeds retrieval.py Mode 2 path
        # (b), same-disease-same-drug-class relaxation.
        "drug_class",
    ),
)

EVIDENCE_ITEM = NodeType(
    "EvidenceItem",
    (
        "civic_id",
        "evidence_level",
        "evidence_type",
        "clinical_significance",
        "direction",
        "summary",
        "citation_url",
        # Populated by a separate backfill pass (semantic_retrieval.py
        # backfill_evidence_embeddings), not by civic_loader.py -- same
        # separation-of-passes reasoning as Drug.drug_class. 384-dim,
        # sentence-transformers/all-MiniLM-L6-v2, confirmed live.
        "summary_embedding",
    ),
)

#: ClinicalTrials.gov's `overallStatus` vocabulary. Bounded because
#: `recruiting` vs `active, not recruiting` decides whether a patient can be
#: referred at all -- a typo or an unmapped upstream value must fail loudly
#: rather than quietly render as "not recruiting".
TRIAL_STATUSES: frozenset[str] = frozenset(
    {
        "NOT_YET_RECRUITING",
        "RECRUITING",
        "ENROLLING_BY_INVITATION",
        "ACTIVE_NOT_RECRUITING",
        "SUSPENDED",
        "TERMINATED",
        "COMPLETED",
        "WITHDRAWN",
        "AVAILABLE",
        "NO_LONGER_AVAILABLE",
        "TEMPORARILY_NOT_AVAILABLE",
        "APPROVED_FOR_MARKETING",
        "UNKNOWN",
    }
)

TRIAL = NodeType(
    "Trial",
    (
        "registry_id",
        "registry",
        "status",
        "phase",
        "locations",
        "country_codes",
        "eligibility_url",
        # Added for the Trial Intelligence & Eligibility Matcher (Subsystem F).
        # The raw criteria text is stored because extraction is a separate,
        # cached, one-time step -- re-fetching the source text every time the
        # extractor is re-run would make the extraction unreproducible against
        # a registry that edits its own records.
        "brief_title",
        "conditions",
        "eligibility_criteria",
        "minimum_age",
        "maximum_age",
        "sex",
        "study_type",
        # Cross-referenced by the Access Pathway Registry (Subsystem J):
        # a trial with an expanded-access programme IS an access route, and is
        # materially different from one that merely exists.
        "has_expanded_access",
        "last_update_posted",
    ),
)

PUBLICATION = NodeType(
    "Publication",
    (
        "pmid",
        "title",
        "journal",
        "year",
        "pub_type",
        "mesh_terms",
        "abstract",
        "abstract_embedding",
    ),
)

# Tier-2-facing extension node (schema only, no logic here -- Tier 2 is the
# writer; see tier1-implementation-spec.md SS3.4 and tier2-implementation-spec.md
# SS4 for the disclaimer-text contract that accompanies every value Tier 2
# attaches via this node).
# Tier-2-owned node. The property list below must stay equal to what
# `secondlook.graph.StructuralSignal.node_properties()` actually returns --
# Tier 2 is the writer, this is only the reader-side declaration. A field
# emitted there but missing here is not a crash (NodeType is documentation,
# not an ORM); it is worse, because a query written from this list silently
# never reads it. Verified equal on 2026-08-22.
STRUCTURAL_SIGNAL = NodeType(
    "StructuralSignal",
    (
        "alphamissense_score",
        "alphamissense_class",
        "structure_source",
        "structure_id",
        "plddt_at_residue",
        "reliability_flag",
        "method",
        # Added 2026-08-22 during Tier 1 <-> Tier 2 integration. Tier 2 emitted
        # all six already; only this declaration was behind.
        #
        # binding_site_distance_angstrom is the load-bearing one: Tier 2's
        # gold-standard run currently produces proximity-only results (no
        # scored binding delta), so for every case in that run this field IS
        # the finding. A UI reading only the declared list would render
        # nothing at all.
        "binding_site_distance_angstrom",
        "confidence",
        # calibration_status rides on every signal and is currently
        # "provisional". It must survive into the graph: a label whose
        # calibration state is lost on write becomes indistinguishable from a
        # validated one on read.
        "calibration_status",
        "computed_at",
        "pipeline_version",
        "labeling_version",
    ),
)

#: How a drug can legitimately be obtained for a patient. Bounded, because the
#: difference between "approved for this indication" and "compassionate use
#: application required" is the entire content of the answer.
PATHWAY_TYPES: frozenset[str] = frozenset(
    {
        "on_label",
        "off_label",
        "clinical_trial",
        "expanded_access_individual",
        "expanded_access_protocol",
        "compassionate_use",
        "named_patient_import",
        "manufacturer_programme",
    }
)

#: The distinction the Access Pathway Registry issue calls out explicitly:
#: "expanded access has been granted for this agent before" is a materially
#: different claim from "a pathway theoretically exists", and the two must
#: never render identically. Bounded so a loader cannot invent a third state
#: that the UI has no rule for.
PRECEDENT_STRENGTHS: frozenset[str] = frozenset({"theoretical", "granted_before"})

ACCESS_PATHWAY = NodeType(
    "AccessPathway",
    (
        "pathway_id",
        "pathway_type",
        "country",
        "regulator",
        # The legal instrument the route rests on, cited verbatim -- e.g. a rule
        # number or form number. Never paraphrased: a clinician following this
        # needs the citable text, and an approximate citation is worse than none.
        "instrument",
        "description",
        "source_url",
        "precedent_strength",
        "precedent_examples",
        "config_version",
    ),
)

ALL_NODE_TYPES: tuple[NodeType, ...] = (
    GENE,
    VARIANT,
    DISEASE,
    DRUG,
    EVIDENCE_ITEM,
    TRIAL,
    PUBLICATION,
    STRUCTURAL_SIGNAL,
    ACCESS_PATHWAY,
)

# Every node carries these in addition to its own NodeType.properties.
#
# RareCure rule (rarecure-build-reference.md SS3.1): RareCure's outputs
# recorded no retrieval timestamp or data version anywhere, so its published
# metrics could not be reconstructed from its own committed artifacts. This
# is why provenance is non-negotiable and enforced through a single shared
# helper (with_provenance) rather than left to each loader to set ad hoc.
PROVENANCE_PROPERTIES: tuple[str, ...] = ("source", "source_version", "retrieved_at")


def with_provenance(
    properties: dict,
    source: str,
    retrieved_at: str,
    source_version: str | None = None,
) -> dict:
    """Return a NEW dict: `properties` plus source/source_version/retrieved_at.

    Every node-creation call site in every loader must route its properties
    through this helper instead of setting provenance fields ad hoc, so a
    node can never be written without a record of where it came from and
    when. Does not mutate the input `properties` dict.

    `source_version` covers whatever an upstream source calls its own
    versioning field (CIViC "release", PubMed baseline year, etc.) -- pass
    None when the source doesn't expose one.
    """
    return {
        **properties,
        "source": source,
        "source_version": source_version,
        "retrieved_at": retrieved_at,
    }


def with_enrichment_provenance(
    properties: dict,
    source: str,
    retrieved_at: str,
    source_version: str | None = None,
) -> dict:
    """Provenance for a SECOND (or later) writer merging new properties onto
    a node another source already created.

    Do not use with_provenance() for this case: a Cypher `SET n += $props`
    only overwrites the keys present in `props`, and with_provenance()
    always includes `source`/`retrieved_at`/`source_version` -- so an
    enrichment write blanket-overwrites the node-level provenance the
    original creator set, collapsing "name from CIViC, drug_class from
    ChEMBL" down to "last touched by ChEMBL". Confirmed live: a Drug node
    civic_loader.py created with source="CIViC" read back as source="ChEMBL"
    after chembl_enrich.py enriched it, with no trace CIViC ever touched it.

    Instead, this sets `<property>_source` / `<property>_retrieved_at` /
    `<property>_source_version` for each key in `properties`, leaving the
    node-level source/retrieved_at/source_version -- and any other
    property's own per-property provenance -- untouched. Multiple sources'
    provenance then coexists on one node instead of one overwriting another.

    This is the reusable shape for any future module that enriches a node
    another module created (e.g. a second source ever touching a
    Publication node written by the RAG loader) -- route through this
    helper rather than reintroducing the same overwrite bug.
    """
    out = dict(properties)
    for key in properties:
        out[f"{key}_source"] = source
        out[f"{key}_retrieved_at"] = retrieved_at
        out[f"{key}_source_version"] = source_version
    return out


# ---------------------------------------------------------------------------
# Edge types
#
# Written as string constants (relationship type names) rather than full
# Cypher templates: the modules that actually query/write the graph
# (civic_loader.py, retrieval.py, ...) compose these into Cypher themselves,
# using parameterized queries -- this module has no FalkorDB connection
# logic and builds no query strings beyond the one validated property-dict
# helper below.
# ---------------------------------------------------------------------------

# (Gene)-[:HAS_VARIANT]->(Variant)
HAS_VARIANT = "HAS_VARIANT"
# (Variant)-[:OBSERVED_IN]->(Disease)
OBSERVED_IN = "OBSERVED_IN"
# (Variant)-[:PREDICTS_RESPONSE_TO {direction}]->(Drug)
PREDICTS_RESPONSE_TO = "PREDICTS_RESPONSE_TO"
# (EvidenceItem)-[:SUPPORTS]->(Variant|Drug|Disease)
SUPPORTS = "SUPPORTS"
# (EvidenceItem)-[:CITES]->(Publication)
CITES = "CITES"
# (Trial)-[:RECRUITS_FOR]->(Disease)
RECRUITS_FOR = "RECRUITS_FOR"
# (Trial)-[:TARGETS_BIOMARKER]->(Variant|Gene)
TARGETS_BIOMARKER = "TARGETS_BIOMARKER"
# (Trial)-[:INVESTIGATES]->(Drug)
INVESTIGATES = "INVESTIGATES"
# (Publication)-[:MENTIONS]->(Gene|Variant|Drug)
MENTIONS = "MENTIONS"

# Tier-2-facing extension edges (schema only, no logic here).
# (Variant)-[:HAS_COMPUTATIONAL_SIGNAL]->(StructuralSignal)
HAS_COMPUTATIONAL_SIGNAL = "HAS_COMPUTATIONAL_SIGNAL"
# (StructuralSignal)-[:PREDICTS_BINDING_CHANGE {delta,label,method}]->(Drug)
PREDICTS_BINDING_CHANGE = "PREDICTS_BINDING_CHANGE"

ALL_EDGE_TYPES: tuple[str, ...] = (
    HAS_VARIANT,
    OBSERVED_IN,
    PREDICTS_RESPONSE_TO,
    SUPPORTS,
    CITES,
    RECRUITS_FOR,
    TARGETS_BIOMARKER,
    INVESTIGATES,
    MENTIONS,
    HAS_COMPUTATIONAL_SIGNAL,
    PREDICTS_BINDING_CHANGE,
)


def predicts_response_to_properties(direction: str) -> dict:
    """Build the property dict for a (Variant)-[:PREDICTS_RESPONSE_TO]->(Drug) edge.

    `direction` is REQUIRED (no default parameter -- omitting it is a
    TypeError, not a silent fallback) and validated against
    RESPONSE_DIRECTIONS (an out-of-set value is a ValueError). "This drug is
    associated with this variant" is clinically useless without knowing
    whether the association is sensitivity or resistance, so there is no
    code path that can construct this edge's properties without a valid
    direction.

    This is a direct response to RareCure's own dedup defect
    (rarecure-build-reference.md SS5): it collapsed drugs by name alone and
    lost gene attribution entirely, producing output like
    `"Pazopanib" | gene: "TP53"`. Loaders must MERGE this edge on the full
    (variant, drug, direction) tuple -- never on drug name alone -- and
    having `direction` be impossible to omit or fabricate here is the first
    line of defense for that rule.
    """
    if direction not in RESPONSE_DIRECTIONS:
        raise ValueError(
            f"direction={direction!r} is required and must be one of "
            f"{sorted(RESPONSE_DIRECTIONS)} -- refusing to build a "
            "PREDICTS_RESPONSE_TO edge without a valid direction."
        )
    return {"direction": direction}
