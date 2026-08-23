from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


CONTRACT_VERSION = "v1"
I4S_RULE_VERSION = "structural-disclosure-v50"
I4S_PROMPT_VERSION = "i4-s-structural-v27"
I4I_RULE_VERSION = "inventive-step-three-step-v1"
I4I_PROMPT_VERSION = "i4-i-three-step-v1"


class InvestigationStatus(StrEnum):
    CREATED = "created"
    DRAFT = "draft"
    QUEUED = "queued"
    RUNNING = "running"
    NEEDS_HUMAN_REVIEW = "needs_human_review"
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"
    PAUSED = "paused"
    CANCELLED = "cancelled"


class ClaimStatus(StrEnum):
    QUEUED = "queued"
    CRITICAL_DATE_REVIEW = "critical_date_review"
    INITIAL_SEARCH = "initial_search"
    SINGLE_REFERENCE_REVIEW = "single_reference_review"
    CLOSEST_PRIOR_ART_SELECTED = "closest_prior_art_selected"
    OBVIOUSNESS_PRECHECK = "obviousness_precheck"
    COMBINATION_REVIEW = "combination_review"
    GAP_SEARCH = "gap_search"
    NOVELTY_EVIDENCE_COMPLETE = "novelty_evidence_complete"
    INVENTIVE_STEP_EVIDENCE_COMPLETE = "inventive_step_evidence_complete"
    SEARCH_BUDGET_EXHAUSTED = "search_budget_exhausted"
    EXHAUSTED = "search_budget_exhausted"
    LEGACY_EXHAUSTED = "exhausted"
    NEEDS_HUMAN_REVIEW = "needs_human_review"
    PARTIAL = "partial"
    FAILED = "failed"
    CANCELLED = "cancelled"


class EvidenceStage(StrEnum):
    LEAD = "lead"
    RETRIEVED_DOCUMENT = "retrieved_document"
    QUALIFIED_EVIDENCE = "qualified_evidence"


class DisclosureStatus(StrEnum):
    EXPLICIT = "explicit"
    DIRECT_AND_UNAMBIGUOUS = "direct_and_unambiguous"
    NECESSARILY_IMPLICIT = "necessarily_implicit"
    NOT_DISCLOSED = "not_disclosed"
    UNCERTAIN = "uncertain"
    ANALYSIS_FAILED = "analysis_failed"


class SearchObjective(StrEnum):
    """Search purpose; gap rounds now emit GAP_OR_COMBINATION only."""

    FULL_CLAIM_SINGLE_REFERENCE = "full_claim_single_reference"
    GAP_OR_COMBINATION = "gap_or_combination"


class DateChannel(StrEnum):
    """Provider discovery lanes; local date qualification remains authoritative."""

    ORDINARY_PRIOR_ART = "ordinary_prior_art"
    CN_CONFLICTING_APPLICATION = "cn_conflicting_application"


class GapType(StrEnum):
    FEATURE = "feature_gap"
    EVIDENCE = "evidence_gap"
    DATE = "date_gap"
    COMBINATION = "combination_gap"


class QueryRole(StrEnum):
    """Why one query exists inside the first-round search portfolio."""

    INVENTIVE_POINT_PRECISION = "inventive_point_precision"
    CLAIM_CONTEXT_RECALL = "claim_context_recall"
    TITLE_ABSTRACT_CONCEPT = "title_abstract_concept"
    GAP_FOLLOWUP = "gap_followup"


class QueryVariant(StrEnum):
    """The concrete concept combination executed inside one query role."""

    MAXIMAL_SIMILARITY_PRECISION = "maximal_similarity_precision"
    OBJECT_PLUS_INVENTIVE_POINT = "object_plus_inventive_point"
    SUBJECT_CLASSIFICATION_PLUS_INVENTIVE_POINT = (
        "subject_classification_plus_inventive_point"
    )
    OBJECT_PLUS_INVENTIVE_CLASSIFICATION = (
        "object_plus_inventive_classification"
    )
    OBJECT_PLUS_TWO_INVENTIVE_POINTS = "object_plus_two_inventive_points"
    OBJECT_PLUS_COMPONENT_PLUS_EFFECT = "object_plus_component_plus_effect"
    SYSTEM_ARCHITECTURE_RECALL = "system_architecture_recall"
    TARGET_CITATION_LOOKUP = "target_citation_lookup"
    TARGET_EFFECT_RECALL = "target_effect_recall"
    CLASSIFICATION_ACTION_RECALL = "classification_action_recall"
    TITLE_ABSTRACT_CONCEPT = "title_abstract_concept"
    GAP_FOLLOWUP = "gap_followup"
    # Fixed first-round lane set (charter 2.15 / SPEC 3.20).
    APPLICANT_PLUS_OBJECT = "applicant_plus_object"
    TITLE_OBJECT_PLUS_DESC_INVENTIVE = "title_object_plus_desc_inventive"
    CLASSIFICATION_PLUS_DESC_INVENTIVE = "classification_plus_desc_inventive"
    DESC_OBJECT_PLUS_DESC_INVENTIVE_PLUS_EFFECT = (
        "desc_object_plus_desc_inventive_plus_effect"
    )
    TITLE_KEYWORD_OBJECT_PLUS_DESC_FUNCTION = (
        "title_keyword_object_plus_desc_function"
    )


class SearchScope(StrEnum):
    """The patent field in which the selected concepts are most likely to occur."""

    TITLE_ABSTRACT = "title_abstract"
    CLAIMS = "claims"
    FULL_TEXT = "full_text"
    CLASSIFICATION = "classification"


class CreateInvestigationRequest(BaseModel):
    contract_version: Literal["v1"] = CONTRACT_VERSION
    analysis_session_id: str = Field(min_length=1, max_length=200)
    source_path: str | None = None
    source_url: str | None = None
    patent_record_id: int | None = Field(default=None, gt=0)
    patent_number: str | None = None
    declared_critical_date: date | None = None
    # Compatibility name: this now means post-initial gap-search iterations.
    # The fixed five-query initial search is iteration 0 for this budget.
    max_rounds: int = Field(default=5, ge=1, le=5)
    provider_mode: Literal["fixture", "manual", "live"] = "live"
    idempotency_key: str = Field(min_length=8, max_length=200)

    @model_validator(mode="after")
    def exactly_one_source(self) -> "CreateInvestigationRequest":
        sources = [self.source_path, self.source_url, self.patent_record_id]
        if sum(value is not None and value != "" for value in sources) != 1:
            raise ValueError("source_path、source_url、patent_record_id 必须且只能提供一个")
        return self


class ModuleRunRequest(BaseModel):
    contract_version: Literal["v1"] = CONTRACT_VERSION
    module_code: Literal[
        "I1_TARGET_SNAPSHOT",
        "I1_5_CLAIM_DATES",
        "I2_INVENTIVE_PROFILE",
        "I2_QUERY_PLAN",
        "I2_GAP_QUERY_PLAN",
        "I3_PATENT_SEARCH",
        "I3_NPL_SEARCH",
        "I3_CANDIDATE_FILTER",
        "I3_FETCH",
        "I3_QUALIFY",
        "I4_S_SINGLE_REFERENCE",
        "I4_C_CLOSEST_PRIOR_ART",
        "I4_O_OBVIOUSNESS_PRECHECK",
        "I4_I_INVENTIVE_STEP",
        "I5_REPORT",
    ]
    input_mode: Literal["fixture", "manual", "live"]
    investigation_id: str | None = None
    claim_investigation_id: str | None = None
    iteration_number: int | None = Field(default=None, ge=1)
    force_recompute: bool = True
    input: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str = Field(min_length=8, max_length=200)


class ClaimSnapshot(BaseModel):
    claim_id: str
    claim_type: Literal["INDEPENDENT", "DEPENDENT"]
    claim_text: str
    parent_claim_ids: list[str] = Field(default_factory=list)
    dependency_uncertain: bool = False
    sentence_units: list[str] = Field(default_factory=list)
    expanded_claim_text: str | None = None


class PatentSnapshot(BaseModel):
    source_sha256: str
    source_uri: str
    source_format: str | None = None
    source_byte_size: int | None = Field(default=None, ge=0)
    page_count: int | None = Field(default=None, ge=0)
    used_ocr: bool = False
    parser_version: str | None = None
    patent_record_id: int | None = None
    parser_task_id: str | None = None
    patent_number: str | None = None
    application_number: str | None = None
    title: str | None = None
    holder: str | None = None
    abstract: str | None = None
    application_date: str | None = None
    priority_date: str | None = None
    publication_date: str | None = None
    grant_date: str | None = None
    bibliographic_data: dict[str, Any] = Field(default_factory=dict)
    claims_section_text: str | None = None
    page_texts: list[dict[str, Any]] = Field(default_factory=list)
    specification: dict[str, str] = Field(default_factory=dict)
    claims: list[ClaimSnapshot]
    figures: list[dict[str, Any]] = Field(default_factory=list)
    parser_errors: list[dict[str, Any]] = Field(default_factory=list)
    snapshot_created_at: datetime = Field(default_factory=datetime.utcnow)


class Limitation(BaseModel):
    feature_id: str
    claim_id: str
    sequence: int = Field(ge=1)
    text: str
    mandatory: bool = True
    inherited_from_claim_id: str | None = None
    technical_subject: str
    synonyms_zh: list[str] = Field(default_factory=list)
    synonyms_en: list[str] = Field(default_factory=list)
    visual_relevance: str | None = None


class ProfileTermGroup(BaseModel):
    """One frozen rewrite term group attached to an inventive search concept.

    The inventive-terminology rewrite rule rewrites a self-coined claim term
    into a generic component word (``generic_component``) plus a function or
    effect phrase (``function_effect``).  Both keep the same traceability
    shape as a concept: a head term, bilingual synonyms and a target-only
    source reference.  Every field is optional so older cached profiles
    without the rewrite fields keep parsing unchanged.
    """

    text: str = ""
    synonyms_zh: list[str] = Field(default_factory=list)
    synonyms_en: list[str] = Field(default_factory=list)
    core_meaning_terms_zh: list[str] = Field(default_factory=list)
    core_meaning_terms_en: list[str] = Field(default_factory=list)
    source_reference: str = ""
    rationale: str = ""


class SearchConcept(BaseModel):
    concept_id: str
    text: str
    feature_ids: list[str] = Field(min_length=1)
    synonyms_zh: list[str] = Field(default_factory=list)
    synonyms_en: list[str] = Field(default_factory=list)
    core_meaning_terms_zh: list[str] = Field(default_factory=list)
    core_meaning_terms_en: list[str] = Field(default_factory=list)
    source_reference: str
    rationale: str
    preferred_scope: SearchScope
    generic_component: ProfileTermGroup | None = None
    function_effect: ProfileTermGroup | None = None


class MechanismElement(BaseModel):
    """One claim-traceable unit in the invention mechanism model.

    The unit is intentionally provider-neutral.  It describes what a component
    does in the whole structure and how it relates to other components, without
    requiring a prior-art document to use the target patent's exact noun.
    """

    element_id: str
    kind: Literal[
        "component_role",
        "topology_relation",
        "motion_state_relation",
        "control_relation",
        "technical_role",
    ]
    text: str
    feature_ids: list[str] = Field(min_length=1)
    source_reference: str
    rationale: str
    original_terms: list[str] = Field(default_factory=list)
    role_equivalent_terms: list[str] = Field(default_factory=list)
    operation_terms: list[str] = Field(default_factory=list)
    preferred_scope: SearchScope = SearchScope.FULL_TEXT


class MechanismModel(BaseModel):
    """Generic whole-invention model used by search and disclosure analysis."""

    summary: str
    elements: list[MechanismElement] = Field(default_factory=list)


class InventionSearchProfile(BaseModel):
    protected_subject: str
    subject_synonyms_zh: list[str] = Field(default_factory=list)
    subject_synonyms_en: list[str] = Field(default_factory=list)
    subject_core_terms_zh: list[str] = Field(default_factory=list)
    subject_core_terms_en: list[str] = Field(default_factory=list)
    classification_anchors: list[str] = Field(default_factory=list)
    classification_anchor_sources: dict[
        str, Literal["parsed_fact", "model_suggested"]
    ] = Field(default_factory=dict)
    classification_anchor_roles: dict[
        str, Literal["subject", "inventive_point", "general"]
    ] = Field(default_factory=dict)
    common_context_features: list[SearchConcept] = Field(default_factory=list)
    inventive_point_features: list[SearchConcept] = Field(default_factory=list)
    mechanism_model: MechanismModel | None = None
    invention_summary: str


GapSearchStrategy = Literal[
    "adjacent_object_direct_structure",
    "broader_object_structural_family",
    "same_function_object_action_role",
    "subsystem_component_relation_path",
    "analogous_domain_principle_effect",
]


class SearchQuery(BaseModel):
    query_id: str
    provider_kind: Literal["patent", "npl"]
    purpose: Literal[
        "initial",
        "feature_uncovered",
        "disclosure_uncertain",
        "combination_motivation",
        "common_knowledge_evidence",
        "technical_effect",
        "citation_followup",
    ]
    technical_subject: str
    feature_ids: list[str]
    expression: str
    language: str
    query_role: QueryRole = QueryRole.CLAIM_CONTEXT_RECALL
    query_variant: QueryVariant = QueryVariant.SYSTEM_ARCHITECTURE_RECALL
    search_scope: SearchScope = SearchScope.FULL_TEXT
    scope_reason: str = ""
    subject_terms: list[str] = Field(default_factory=list)
    feature_terms: list[str] = Field(default_factory=list)
    feature_term_groups: list[list[str]] = Field(default_factory=list)
    concept_ids: list[str] = Field(default_factory=list)
    classification_anchors: list[str] = Field(default_factory=list)
    classification_anchor_sources: dict[
        str, Literal["parsed_fact", "model_suggested"]
    ] = Field(default_factory=dict)
    classification_anchor_roles: dict[
        str, Literal["subject", "inventive_point", "general"]
    ] = Field(default_factory=dict)
    allow_zero_results: bool = False
    compact_fallback_allowed: bool = True
    provider_expression: str | None = None
    target_citation: str | None = None
    citation_source_excerpt: str = ""
    target_effect_terms: list[str] = Field(default_factory=list)
    target_context_terms: list[str] = Field(default_factory=list)
    effect_source_excerpt: str = ""
    applicant_terms: list[str] = Field(default_factory=list)
    excluded_publication: str | None = None
    parent_query_id: str | None = None
    rationale: str
    search_objective: SearchObjective = SearchObjective.FULL_CLAIM_SINGLE_REFERENCE
    date_channel: DateChannel = DateChannel.ORDINARY_PRIOR_ART
    target_gap_type: GapType | None = None
    gap_feature_ids: list[str] = Field(default_factory=list)
    anchor_document_id: str | None = None
    gap_search_iteration: int = Field(default=0, ge=0, le=5)
    gap_search_strategy: GapSearchStrategy | None = None
    gap_search_strategy_label: str = ""
    covered_difference_feature_ids: list[str] = Field(default_factory=list)
    uncovered_difference_feature_ids: list[str] = Field(default_factory=list)
    existing_corpus_reuse: list[dict[str, Any]] = Field(default_factory=list)
    previous_iteration_failure_reason: str = ""

    @model_validator(mode="after")
    def validate_search_lane(self) -> "SearchQuery":
        if (
            self.provider_kind == "npl"
            and self.date_channel is DateChannel.CN_CONFLICTING_APPLICATION
        ):
            raise ValueError("非专利文献不得进入中国抵触申请检索通道")
        if any(item not in self.feature_ids for item in self.gap_feature_ids):
            raise ValueError("gap_feature_ids 必须是 feature_ids 的子集")
        if (
            self.search_objective is SearchObjective.GAP_OR_COMBINATION
            and self.target_gap_type is None
        ):
            raise ValueError("gap_or_combination 查询必须声明 target_gap_type")
        if (
            self.search_objective is SearchObjective.FULL_CLAIM_SINGLE_REFERENCE
            and self.target_gap_type is not None
        ):
            raise ValueError("完整权利要求单文献检索线不得伪装为 gap 查询")
        return self


class FeatureDisclosure(BaseModel):
    feature_id: str
    feature_text: str = ""
    status: DisclosureStatus
    evidence_quote: str = ""
    evidence_location: str = ""
    reasoning: str
    confidence: float = Field(ge=0, le=1)
    target_structural_role: str = ""
    reference_structure_mapping: str = ""
    mapping_basis: Literal[
        "literal",
        "role_and_relation",
        "structural_equivalent",
        "necessarily_implicit_from_operation",
        "function_only",
        "none",
        "uncertain",
    ] = "uncertain"
    structural_evidence: list[str] = Field(default_factory=list)
    integrated_structure_mapping: bool = False
    structural_search_summary: str = ""
    necessity_chain: list[str] = Field(default_factory=list)
    reasonable_alternatives_excluded: bool = False
    alternative_path_analysis: str = ""
    guard_original_status: str = ""
    guard_note: str = ""


class DocumentComparison(BaseModel):
    document_id: str
    document_title: str = ""
    publication_number: str = ""
    disclosures: list[FeatureDisclosure]
    field_alignment: float = Field(ge=0, le=1)
    purpose_alignment: float = Field(ge=0, le=1)
    effect_alignment: float = Field(ge=0, le=1)
    evidence_completeness: float = Field(ge=0, le=1)
    model: str
    used_target_images: int = 0
    used_document_images: int = 0
    target_mechanism_summary: str = ""
    reference_mechanism_summary: str = ""
    structural_review_attempted: bool = False
    structural_review_performed: bool = False
    structural_review_status: Literal[
        "not_needed", "completed", "model_error"
    ] = "not_needed"
    structural_review_error_code: str = ""
    structural_review_used_target_images: int = Field(default=0, ge=0, le=2)
    structural_review_used_document_images: int = Field(default=0, ge=0, le=4)
    analysis_pass_count: int = Field(default=1, ge=1, le=2)
    analysis_rule_version: str = "legacy-unversioned"
    negation_guard_attempted: bool = False
    negation_guard_performed: bool = False
    negation_guard_downgraded: list[str] = Field(default_factory=list)
    negation_guard_error: str = ""
    candidate_discovery_attempted: bool = False
    candidate_discovery_performed: bool = False
    candidate_discovery_error: str = ""
    candidate_discovery_attempts: int = Field(default=0, ge=0)
    candidate_inventory: list[dict[str, Any]] = Field(default_factory=list)
    candidate_discovery_dropped: list[dict[str, Any]] = Field(
        default_factory=list
    )
    consistency_guard_attempted: bool = False
    consistency_guard_performed: bool = False
    consistency_guard_clusters: int = Field(default=0, ge=0)
    consistency_guard_downgraded: list[str] = Field(default_factory=list)
    consistency_guard_error: str = ""
    guard_warnings: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_structural_review_audit(self) -> "DocumentComparison":
        if self.structural_review_status == "not_needed":
            if self.structural_review_attempted or self.structural_review_performed:
                raise ValueError("未触发结构复核时 attempted/performed 必须为 false")
            if self.analysis_pass_count != 1:
                raise ValueError("未触发结构复核时 analysis_pass_count 必须为 1")
        elif self.structural_review_status == "completed":
            if not self.structural_review_attempted or not self.structural_review_performed:
                raise ValueError("完成结构复核时 attempted/performed 必须为 true")
            if self.analysis_pass_count != 2:
                raise ValueError("完成结构复核时 analysis_pass_count 必须为 2")
            if self.structural_review_error_code:
                raise ValueError("完成结构复核时不得保留 error_code")
        else:
            if not self.structural_review_attempted or self.structural_review_performed:
                raise ValueError("结构复核失败时必须 attempted=true/performed=false")
            if self.analysis_pass_count != 1:
                raise ValueError("结构复核失败时 analysis_pass_count 必须为 1")
            if not self.structural_review_error_code.strip():
                raise ValueError("结构复核失败时必须保存安全 error_code")
        return self

    def fully_discloses(self, mandatory_feature_ids: set[str]) -> bool:
        accepted = {
            DisclosureStatus.EXPLICIT,
            DisclosureStatus.DIRECT_AND_UNAMBIGUOUS,
            DisclosureStatus.NECESSARILY_IMPLICIT,
        }
        covered = {
            item.feature_id
            for item in self.disclosures
            if item.status in accepted and item.confidence >= 0.7
        }
        return mandatory_feature_ids.issubset(covered)


class InvestigationCreated(BaseModel):
    contract_version: Literal["v1"] = CONTRACT_VERSION
    investigation_id: str
    status: InvestigationStatus
    environment: Literal["test", "prod"]
    idempotent_replay: bool = False


class ErrorResponse(BaseModel):
    error: str
    code: str
    details: dict[str, Any] = Field(default_factory=dict)
