from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Protocol
import uuid

from pydantic import BaseModel, Field

from invalidity.artifacts import ArtifactStore
from invalidity.candidate_filter import (
    FILTER_CONTRACT_VERSION,
    IDENTITY_RULE_VERSION,
    SEMANTIC_RULE_VERSION,
    apply_filter_decisions,
    build_raw_candidates,
    group_candidates,
    invoke_semantic_candidate_filter,
    matched_positive_anchors,
    matched_technical_terms,
    metadata_is_sufficient,
    stable_json_sha256,
    target_filter_context,
)
from invalidity.analysis import (
    AnalysisGap,
    AnalysisValidationError,
    ClosestPriorArtSelection,
    GapSearchDecision,
    InventiveStepAssessment,
    InvalidityAnalysisEngine,
    I2_PROMPT_VERSION,
    I2_RULE_VERSION,
    ObviousnessPrecheckAssessment,
    QueryPlan,
    apply_obviousness_routes_to_gap_decision,
    bind_gap_search_strategy,
    generate_feature_gaps,
    select_closest_prior_art,
    build_distinguishing_features,
    evaluate_existing_corpus_gap_coverage,
    gap_search_strategy_metadata,
    select_large_claim_chart_documents,
    single_reference_fully_discloses,
    target_patent_text_for_analysis,
)
from invalidity.contracts import (
    ClaimSnapshot,
    ClaimStatus,
    DateChannel,
    DocumentComparison,
    I4I_PROMPT_VERSION,
    I4I_RULE_VERSION,
    I4S_PROMPT_VERSION,
    I4S_RULE_VERSION,
    InvestigationStatus,
    Limitation,
    PatentSnapshot,
    SearchQuery,
)
from invalidity.date_rules import (
    CriticalDateResult,
    classify_date_eligibility,
    parse_date,
    resolve_critical_date,
)
from invalidity.llm import VisionLLMClient
from invalidity.providers import (
    ArxivProvider,
    CompositeNplProvider,
    CrossrefProvider,
    EpoOpsProvider,
    EvidenceRecord,
    EvidenceStage,
    GooglePatentsProvider,
    OpenAlexProvider,
    ProviderSearchBatch,
    ProviderError,
    ProviderContentError,
    RetrievedArtifact,
    WebEvidenceProvider,
    qualify_evidence,
)
from invalidity.patsnap import PatsnapProvider
from invalidity.patent_retrieval import EpoFirstPatsnapPdfRetrievalProvider
from invalidity.readable_patent import (
    NORMALIZATION_VERSION as READABLE_PATENT_VERSION,
    load_cached_readable_document,
    normalize_epo_bundle,
    normalize_pdf,
    persist_readable_document,
    verify_frozen_patent_pdf_front_page_date,
)
from invalidity.state_machine import RoundProgress, next_round_or_stop
from invalidity.worker import JobCancellationRequested


WORKFLOW_VERSION = "invalidity-workflow-v1"
MAX_AUTOMATIC_GAP_SEARCH_ITERATIONS = 5
# Absolute persisted iteration count: one initial iteration plus five gap iterations.
MAX_AUTOMATIC_ROUNDS = 1 + MAX_AUTOMATIC_GAP_SEARCH_ITERATIONS
MAX_CONTINUATION_ROUNDS = 3
_NON_LIVE_EVIDENCE_PROVIDERS = frozenset({"fixture", "fixture_simulation", "manual"})
_CHECKPOINT_KEY = "invalidity_workflow"
_TERMINAL_CLAIM_VALUES = {
    ClaimStatus.NOVELTY_EVIDENCE_COMPLETE.value,
    ClaimStatus.INVENTIVE_STEP_EVIDENCE_COMPLETE.value,
    ClaimStatus.EXHAUSTED.value,
    ClaimStatus.LEGACY_EXHAUSTED.value,
    ClaimStatus.NEEDS_HUMAN_REVIEW.value,
    ClaimStatus.PARTIAL.value,
    ClaimStatus.FAILED.value,
    ClaimStatus.CANCELLED.value,
}
_SUCCESSFUL_CLAIM_VALUES = {
    ClaimStatus.NOVELTY_EVIDENCE_COMPLETE.value,
    ClaimStatus.INVENTIVE_STEP_EVIDENCE_COMPLETE.value,
}
_CONTINUABLE_CLAIM_VALUES = {
    ClaimStatus.EXHAUSTED.value,
    ClaimStatus.LEGACY_EXHAUSTED.value,
    ClaimStatus.NEEDS_HUMAN_REVIEW.value,
    ClaimStatus.PARTIAL.value,
    ClaimStatus.FAILED.value,
}
_TERMINAL_CONTINUATION_BATCH_VALUES = {
    "succeeded",
    "partial",
    "failed",
    "cancelled",
}


class WorkflowError(RuntimeError):
    """Base error surfaced to the durable worker without hiding its cause."""

    retryable = False


class WorkflowInputError(WorkflowError):
    """The persisted input cannot be safely executed."""


class WorkflowExecutionError(WorkflowError):
    """A retryable infrastructure failure outside a single claim."""

    retryable = True


def _target_claims(patent: PatentSnapshot) -> list[ClaimSnapshot]:
    """Return the only claims that belong to the current automated scope."""

    claims = [
        claim for claim in patent.claims if claim.claim_type == "INDEPENDENT"
    ]
    if not claims:
        raise WorkflowInputError(
            "目标专利未识别到独立权利要求；当前自动无效检索不分析从属权利要求"
        )
    return claims


class WorkflowRepository(Protocol):
    """Only public Repository operations used by I0 are listed here."""

    def get_investigation(self, investigation_id: uuid.UUID | str) -> dict[str, Any] | None: ...

    def update_investigation(self, investigation_id: uuid.UUID | str, **kwargs: Any) -> dict[str, Any]: ...

    def merge_investigation_workflow_state(
        self, investigation_id: uuid.UUID | str, patch: Mapping[str, Any], **kwargs: Any
    ) -> dict[str, Any]: ...

    def create_claim_investigation(self, **kwargs: Any) -> dict[str, Any]: ...

    def get_latest_critical_date_confirmation(
        self, claim_investigation_id: uuid.UUID | str
    ) -> dict[str, Any] | None: ...

    def update_claim_status(
        self, claim_investigation_id: uuid.UUID | str, status: str, **kwargs: Any
    ) -> dict[str, Any]: ...

    def mark_claim_terminal(self, claim_investigation_id: uuid.UUID | str, **kwargs: Any) -> dict[str, Any]: ...

    def insert_claim_limitations(
        self, claim_investigation_id: uuid.UUID | str, limitations: Sequence[Mapping[str, Any]], **kwargs: Any
    ) -> list[dict[str, Any]]: ...

    def list_claim_limitations(self, claim_investigation_id: uuid.UUID | str) -> list[dict[str, Any]]: ...

    def create_iteration(self, **kwargs: Any) -> dict[str, Any]: ...

    def update_iteration(self, iteration_id: uuid.UUID | str, **kwargs: Any) -> dict[str, Any]: ...

    def upsert_query(self, **kwargs: Any) -> dict[str, Any]: ...

    def update_query_execution(
        self, query_id: uuid.UUID | str, **kwargs: Any
    ) -> dict[str, Any]: ...

    def upsert_document(self, **kwargs: Any) -> dict[str, Any]: ...

    def upsert_document_source(self, **kwargs: Any) -> dict[str, Any]: ...

    def upsert_document_qualification(self, **kwargs: Any) -> dict[str, Any]: ...

    def insert_artifact(self, **kwargs: Any) -> dict[str, Any]: ...

    def create_module_run(self, **kwargs: Any) -> dict[str, Any]: ...

    def finish_module_run(
        self, module_run_id: uuid.UUID | str, **kwargs: Any
    ) -> dict[str, Any]: ...

    def list_module_runs(self, **kwargs: Any) -> list[dict[str, Any]]: ...

    def insert_feature_disclosures(self, **kwargs: Any) -> list[dict[str, Any]]: ...

    def insert_closest_prior_art_version(self, **kwargs: Any) -> dict[str, Any]: ...

    def insert_gaps(self, **kwargs: Any) -> list[dict[str, Any]]: ...

    def reconcile_gap_frontier(self, **kwargs: Any) -> list[dict[str, Any]]: ...

    def insert_combination(self, **kwargs: Any) -> dict[str, Any]: ...

    def get_continuation_batch(
        self, continuation_batch_id: uuid.UUID | str
    ) -> dict[str, Any] | None: ...

    def update_continuation_batch(
        self, continuation_batch_id: uuid.UUID | str, **kwargs: Any
    ) -> dict[str, Any]: ...

    def get_human_review_apply_context(
        self, review_action_id: uuid.UUID | str
    ) -> dict[str, Any] | None: ...

    def complete_human_review_apply(self, **kwargs: Any) -> dict[str, Any]: ...


@dataclass(frozen=True, slots=True)
class SearchBatch:
    """One provider response; ``complete=False`` prevents a success/ exhaustion result."""

    provider: str
    records: tuple[EvidenceRecord, ...] = ()
    complete: bool = True
    errors: tuple[str, ...] = ()
    network_used: bool = False
    artifacts: tuple[Mapping[str, Any], ...] = ()


class EvidenceGateway(Protocol):
    def search(
        self,
        query: SearchQuery,
        *,
        investigation_id: str,
        claim_id: str,
        iteration_number: int,
        critical_date: date,
        target_publication_date: date | None,
        target_publication_date_verified: bool,
    ) -> SearchBatch | Sequence[EvidenceRecord]: ...


@dataclass(slots=True)
class UnavailableEvidenceGateway:
    reason: str

    def search(self, query: SearchQuery, **_: Any) -> SearchBatch:
        return SearchBatch(
            provider="unavailable",
            complete=False,
            errors=(self.reason,),
        )


class ConfiguredEvidenceGateway:
    """Small live adapter composition; it returns only actually fetched content.

    Provider-side date filters remain discovery hints.  Every returned document
    is locally qualified with the deterministic date engine, and images/PDF
    pages are materialized under the environment-scoped artifact root before
    I4-S is allowed to run.
    """

    def __init__(
        self,
        *,
        patent_provider: Any,
        patent_retrieval_provider: Any | None = None,
        npl_provider: Any,
        artifact_store: ArtifactStore,
        max_candidates_per_query: int,
    ) -> str:
        self.patent_provider = patent_provider
        self.patent_retrieval_provider = (
            patent_retrieval_provider
            if patent_retrieval_provider is not None
            else patent_provider
        )
        self.npl_provider = npl_provider
        self.artifact_store = artifact_store
        self.max_candidates_per_query = max(1, int(max_candidates_per_query))

    def close(self) -> None:
        self.patent_provider.close()
        if self.patent_retrieval_provider is not self.patent_provider:
            self.patent_retrieval_provider.close()
        self.npl_provider.close()

    def search(
        self,
        query: SearchQuery,
        *,
        investigation_id: str,
        claim_id: str,
        iteration_number: int,
        critical_date: date,
        target_publication_date: date | None,
        target_publication_date_verified: bool,
    ) -> SearchBatch:
        discovery = self.discover(
            query,
            investigation_id=investigation_id,
            claim_id=claim_id,
            iteration_number=iteration_number,
            critical_date=critical_date,
            target_publication_date=target_publication_date,
            target_publication_date_verified=target_publication_date_verified,
        )
        retrieval = self.retrieve(
            query,
            discovery.records,
            investigation_id=investigation_id,
            claim_id=claim_id,
            iteration_number=iteration_number,
            critical_date=critical_date,
            target_publication_date=target_publication_date,
            target_publication_date_verified=target_publication_date_verified,
        )
        return SearchBatch(
            provider=retrieval.provider or discovery.provider,
            records=retrieval.records,
            complete=discovery.complete and retrieval.complete,
            errors=(*discovery.errors, *retrieval.errors),
            network_used=discovery.network_used or retrieval.network_used,
            artifacts=(*discovery.artifacts, *retrieval.artifacts),
        )

    def discover(
        self,
        query: SearchQuery,
        *,
        investigation_id: str,
        claim_id: str,
        iteration_number: int,
        critical_date: date,
        target_publication_date: date | None,
        target_publication_date_verified: bool,
    ) -> SearchBatch:
        """Freeze provider-ranked leads without fetching candidate documents."""

        del claim_id, iteration_number
        if query.provider_kind == "patent":
            return self._discover_patents(
                query=query,
                investigation_id=investigation_id,
                critical_date=critical_date,
                target_publication_date=target_publication_date,
                target_publication_date_verified=target_publication_date_verified,
            )
        if query.provider_kind == "npl":
            return self._discover_npl(query=query)
        return SearchBatch(
            provider=query.provider_kind,
            complete=False,
            errors=(f"不支持 provider_kind={query.provider_kind}",),
        )

    def retrieve(
        self,
        query: SearchQuery,
        records: Sequence[EvidenceRecord],
        *,
        investigation_id: str,
        claim_id: str,
        iteration_number: int,
        critical_date: date,
        target_publication_date: date | None,
        target_publication_date_verified: bool,
        cancel_check: Callable[[], None] | None = None,
    ) -> SearchBatch:
        """Fetch only representatives retained by the persisted I3-F run."""

        del claim_id, iteration_number
        if query.provider_kind == "patent":
            return self._retrieve_patents(
                query=query,
                leads=records,
                investigation_id=investigation_id,
                critical_date=critical_date,
                target_publication_date=target_publication_date,
                target_publication_date_verified=target_publication_date_verified,
                cancel_check=cancel_check,
            )
        if query.provider_kind == "npl":
            return self._retrieve_npl(
                leads=records,
                investigation_id=investigation_id,
                critical_date=critical_date,
                cancel_check=cancel_check,
            )
        return SearchBatch(
            provider=query.provider_kind,
            complete=False,
            errors=(f"不支持 provider_kind={query.provider_kind}",),
        )

    def _discover_patents(
        self,
        *,
        query: SearchQuery,
        investigation_id: str,
        critical_date: date,
        target_publication_date: date | None,
        target_publication_date_verified: bool,
    ) -> SearchBatch:
        provider_name = str(
            getattr(self.patent_provider, "provider_name", "patent")
        )
        if query.date_channel is DateChannel.CN_CONFLICTING_APPLICATION and (
            not target_publication_date_verified or target_publication_date is None
        ):
            return SearchBatch(
                provider=provider_name,
                complete=False,
                errors=("抵触申请通道缺少已确认的目标公开日，已 fail-closed 跳过",),
            )
        try:
            search_options: dict[str, Any] = {
                "max_results": self.max_candidates_per_query,
            }
            if query.date_channel is DateChannel.CN_CONFLICTING_APPLICATION:
                search_options.update(
                    {
                        "country": "CN",
                        "server_after": critical_date,
                        "server_before": target_publication_date,
                        "filing_before": critical_date,
                    }
                )
            else:
                search_options["server_before"] = critical_date
            leads = list(
                self.patent_provider.search(
                    query.provider_expression or query.expression,
                    **search_options,
                )
            )
        except Exception as exc:
            failure = _provider_failure(
                provider_name,
                "search",
                exc,
                network_used=True,
            )
            raw_search_artifact = getattr(
                self.patent_provider, "last_search_artifact", None
            )
            if not isinstance(raw_search_artifact, RetrievedArtifact):
                return failure
            try:
                stored_search = self._materialize_provider_search_response(
                    raw_search_artifact,
                    investigation_id=investigation_id,
                )
            except Exception as freeze_exc:
                return replace(
                    failure,
                    errors=(
                        *failure.errors,
                        "provider 失败响应无法冻结: "
                        f"{type(freeze_exc).__name__}: {_safe_error(freeze_exc)}",
                    ),
                )
            return replace(failure, artifacts=(stored_search,))

        errors: list[str] = []
        search_artifacts: list[dict[str, Any]] = []
        raw_search_artifact = getattr(
            self.patent_provider, "last_search_artifact", None
        )
        if isinstance(raw_search_artifact, RetrievedArtifact):
            try:
                stored_search = self._materialize_provider_search_response(
                    raw_search_artifact,
                    investigation_id=investigation_id,
                )
            except Exception as exc:
                errors.append(
                    "provider 原始检索响应无法冻结: "
                    f"{type(exc).__name__}: {_safe_error(exc)}"
                )
            else:
                search_artifacts.append(stored_search)
                leads = [
                    replace(
                        lead,
                        artifacts=(*lead.artifacts, stored_search),
                        provenance={
                            **dict(lead.provenance),
                            "search_snapshot_sha256": stored_search["sha256"],
                            "search_snapshot_uri": stored_search["uri"],
                            "search_snapshot_retrieved_at": stored_search[
                                "retrieved_at"
                            ],
                        },
                    )
                    for lead in leads
                ]
        elif provider_name in {"epo_ops", "patsnap"}:
            errors.append("live patent provider 未提供可冻结的原始检索响应")

        fallback_discovery = any(
            str(lead.provenance.get("discovery_provider") or "").endswith(
                "_web_fallback"
            )
            for lead in leads
        )
        if fallback_discovery:
            errors.append(
                "Google Patents API 不可用，本批次仅由公开网页备用源发现候选；"
                "即使后续取得真实文献，也不得把该批次标记为完整检索"
            )
        return SearchBatch(
            provider=(
                f"{provider_name}+yahoo_web_fallback"
                if fallback_discovery
                else provider_name
            ),
            records=tuple(leads),
            complete=not errors,
            errors=tuple(errors),
            network_used=True,
            artifacts=tuple(search_artifacts),
        )

    def _retrieve_patents(
        self,
        *,
        query: SearchQuery,
        leads: Sequence[EvidenceRecord],
        investigation_id: str,
        critical_date: date,
        target_publication_date: date | None,
        target_publication_date_verified: bool,
        cancel_check: Callable[[], None] | None = None,
    ) -> SearchBatch:
        errors: list[str] = []
        artifacts: list[dict[str, Any]] = []
        records: list[EvidenceRecord] = []
        for lead in leads:
            # 逐候选协作式停止：EPO/P020 逐篇下载可能很慢，取消不得等待整批。
            if cancel_check:
                cancel_check()
            try:
                retrieved = self.patent_retrieval_provider.retrieve(lead)
                retrieved, retrieval_trace_artifacts = (
                    self._materialize_retrieval_trace_artifacts(
                        retrieved,
                        provider=self.patent_retrieval_provider,
                        investigation_id=investigation_id,
                        relative_stem=_safe_stem(lead.external_id),
                    )
                )
                artifacts.extend(retrieval_trace_artifacts)
                if retrieved.stage is EvidenceStage.LEAD:
                    for tracked in tuple(
                        getattr(
                            self.patent_retrieval_provider,
                            "last_retrieval_artifacts",
                            (),
                        )
                        or ()
                    ):
                        if not isinstance(tracked, RetrievedArtifact):
                            continue
                        try:
                            frozen = self._materialize_provider_response_artifact(
                                tracked,
                                investigation_id=investigation_id,
                                group="retrieval-incomplete",
                                relative_stem=_safe_stem(lead.external_id),
                            )
                        except Exception as freeze_exc:
                            errors.append(
                                f"{lead.external_id}: provider 不完整详情响应无法冻结: "
                                f"{type(freeze_exc).__name__}: "
                                f"{_safe_error(freeze_exc)}"
                            )
                        else:
                            artifacts.append(frozen)
                    records.append(retrieved)
                    errors.append(f"{lead.external_id}: 详情页未取得真实全文")
                    continue
                materialized = self._materialize_google(
                    retrieved,
                    investigation_id=investigation_id,
                )
                public_date_evidence = str(
                    materialized.provenance.get("public_date_evidence") or ""
                ).strip()
                reviewed = self.patent_retrieval_provider.qualify(
                    materialized,
                    critical_date=critical_date,
                    content_relevance_verified=False,
                    source_chain_verified=_has_verified_primary_snapshot(
                        materialized,
                        allowed_root=self.artifact_store.root,
                    ),
                    public_date_verified=bool(
                        materialized.provenance.get("publication_date_verified")
                    ),
                    candidate_filing_date_verified=bool(
                        materialized.provenance.get("filing_date_verified")
                    ),
                    candidate_priority_date_verified=bool(
                        materialized.provenance.get("priority_date_verified")
                    ),
                    public_date_evidence=public_date_evidence or None,
                    target_publication_date=target_publication_date,
                    target_publication_date_verified=target_publication_date_verified,
                    date_channel=query.date_channel.value,
                )
                records.append(reviewed)
                if not _record_images(materialized):
                    errors.append(f"{lead.external_id}: 未取得可核验附图/PDF页面")
            except Exception as exc:
                for tracked in tuple(
                    getattr(
                        self.patent_retrieval_provider,
                        "last_retrieval_artifacts",
                        (),
                    )
                    or ()
                ):
                    if not isinstance(tracked, RetrievedArtifact):
                        continue
                    try:
                        frozen = self._materialize_provider_response_artifact(
                            tracked,
                            investigation_id=investigation_id,
                            group="retrieval-failures",
                            relative_stem=_safe_stem(lead.external_id),
                        )
                    except Exception as freeze_exc:
                        errors.append(
                            f"{lead.external_id}: provider 详情失败响应无法冻结: "
                            f"{type(freeze_exc).__name__}: {_safe_error(freeze_exc)}"
                        )
                    else:
                        artifacts.append(frozen)
                errors.append(
                    f"{lead.external_id}: {type(exc).__name__}: {_safe_error(exc)}"
                )
        discovery_name = str(
            getattr(self.patent_provider, "provider_name", "patent")
        )
        retrieval_name = str(
            getattr(self.patent_retrieval_provider, "provider_name", "patent")
        )
        return SearchBatch(
            provider=(
                f"{discovery_name}+{retrieval_name}_retrieval"
                if self.patent_retrieval_provider is not self.patent_provider
                else discovery_name
            ),
            records=tuple(records),
            complete=not errors,
            errors=tuple(errors),
            network_used=bool(leads),
            artifacts=tuple(artifacts),
        )

    def _discover_npl(self, *, query: SearchQuery) -> SearchBatch:
        try:
            provider_query: str | Mapping[str, Any] = query.expression
            if query.subject_terms and query.feature_terms:
                provider_query = {
                    "text": query.expression,
                    "subject_terms": list(query.subject_terms),
                    "feature_terms": list(query.feature_terms),
                }
            search_result = self.npl_provider.search(
                provider_query,
                max_results=self.max_candidates_per_query,
            )
        except Exception as exc:
            return _provider_failure(
                str(getattr(self.npl_provider, "provider_name", "npl")),
                "search",
                exc,
                network_used=True,
            )
        if isinstance(search_result, ProviderSearchBatch):
            leads = list(search_result.records)
            provider_name = "composite_npl"
            errors = list(search_result.errors)
        else:
            leads = list(search_result)
            provider_name = str(
                getattr(self.npl_provider, "provider_name", "arxiv")
            )
            errors = []
        return SearchBatch(
            provider=provider_name,
            records=tuple(leads),
            complete=not errors,
            errors=tuple(errors),
            network_used=True,
        )

    def _retrieve_npl(
        self,
        *,
        leads: Sequence[EvidenceRecord],
        investigation_id: str,
        critical_date: date,
        cancel_check: Callable[[], None] | None = None,
    ) -> SearchBatch:
        errors: list[str] = []
        records: list[EvidenceRecord] = []
        for lead in leads:
            # 逐候选协作式停止，与专利取文路径一致。
            if cancel_check:
                cancel_check()
            if isinstance(self.npl_provider, CompositeNplProvider):
                adapter, retrieval_lead = self.npl_provider.retrieval_plan_for(lead)
            else:
                adapter, retrieval_lead = self.npl_provider, lead
            if adapter is None:
                records.append(lead)
                errors.append(f"{lead.external_id}: 找不到对应的 NPL 取文 adapter")
                continue
            try:
                if isinstance(adapter, ArxivProvider):
                    artifact = adapter.download_pdf(lead)
                    retrieved = lead.with_artifact(artifact)
                    materialized = self._materialize_pdf(
                        retrieved,
                        investigation_id=investigation_id,
                        pdf_content=artifact.content,
                        relative_stem=_safe_stem(lead.external_id),
                    )
                    public_date_evidence = str(
                        materialized.provenance.get("public_date_evidence") or ""
                    ).strip()
                    reviewed = adapter.qualify(
                        materialized,
                        critical_date=critical_date,
                        content_relevance_verified=False,
                        source_chain_verified=_has_verified_primary_snapshot(
                            materialized,
                            allowed_root=self.artifact_store.root,
                        ),
                        public_date_verified=bool(public_date_evidence),
                        public_date_evidence=public_date_evidence or None,
                    )
                    records.append(reviewed)
                    if not _record_images(materialized):
                        errors.append(
                            f"{lead.external_id}: PDF 未渲染出可核验页面"
                        )
                    continue
                if isinstance(adapter, WebEvidenceProvider):
                    retrieved = adapter.retrieve(retrieval_lead)
                    if (
                        retrieved.primary_artifact
                        and retrieved.primary_artifact.kind == "pdf"
                    ):
                        materialized = self._materialize_pdf(
                            retrieved,
                            investigation_id=investigation_id,
                            pdf_content=retrieved.primary_artifact.content,
                            relative_stem=_safe_stem(lead.external_id),
                        )
                    else:
                        materialized = self._materialize_primary_source(
                            retrieved,
                            investigation_id=investigation_id,
                            relative_stem=_safe_stem(lead.external_id),
                        )
                    public_date_evidence = str(
                        materialized.provenance.get("public_date_evidence") or ""
                    ).strip()
                    reviewed = adapter.qualify(
                        materialized,
                        critical_date=critical_date,
                        content_relevance_verified=False,
                        source_chain_verified=_has_verified_primary_snapshot(
                            materialized,
                            allowed_root=self.artifact_store.root,
                        ),
                        public_date_verified=False,
                        public_date_evidence=public_date_evidence or None,
                    )
                    records.append(reviewed)
                    errors.append(
                        f"{lead.external_id}: 网页已取回但公开日期/来源链待人工核验"
                    )
                    continue
                records.append(lead)
                errors.append(
                    f"{lead.external_id}: {lead.provider} 仅返回题录 lead，尚未取得真实文件"
                )
            except Exception as exc:
                errors.append(
                    f"{lead.external_id}: {type(exc).__name__}: {_safe_error(exc)}"
                )
        provider_name = (
            "composite_npl"
            if isinstance(self.npl_provider, CompositeNplProvider)
            else str(getattr(self.npl_provider, "provider_name", "arxiv"))
        )
        return SearchBatch(
            provider=provider_name,
            records=tuple(records),
            complete=not errors,
            errors=tuple(errors),
            network_used=bool(leads),
        )

    def _search_patents(
        self,
        *,
        query: SearchQuery,
        investigation_id: str,
        critical_date: date,
        target_publication_date: date | None,
        target_publication_date_verified: bool,
    ) -> SearchBatch:
        if query.date_channel is DateChannel.CN_CONFLICTING_APPLICATION and (
            not target_publication_date_verified or target_publication_date is None
        ):
            return SearchBatch(
                provider=str(
                    getattr(self.patent_provider, "provider_name", "patent")
                ),
                complete=False,
                errors=("抵触申请通道缺少已确认的目标公开日，已 fail-closed 跳过",),
            )
        try:
            search_options: dict[str, Any] = {
                "max_results": self.max_candidates_per_query,
            }
            if query.date_channel is DateChannel.CN_CONFLICTING_APPLICATION:
                search_options.update(
                    {
                        "country": "CN",
                        "server_after": critical_date,
                        "server_before": target_publication_date,
                        "filing_before": critical_date,
                    }
                )
            else:
                search_options["server_before"] = critical_date
            leads = list(
                self.patent_provider.search(
                    query.provider_expression or query.expression,
                    **search_options,
                )
            )
        except Exception as exc:
            failure = _provider_failure(
                str(getattr(self.patent_provider, "provider_name", "patent")),
                "search",
                exc,
                network_used=True,
            )
            raw_search_artifact = getattr(
                self.patent_provider,
                "last_search_artifact",
                None,
            )
            if not isinstance(raw_search_artifact, RetrievedArtifact):
                return failure
            try:
                stored_search = self._materialize_provider_search_response(
                    raw_search_artifact,
                    investigation_id=investigation_id,
                )
            except Exception as freeze_exc:
                return replace(
                    failure,
                    errors=(
                        *failure.errors,
                        "provider 失败响应无法冻结: "
                        f"{type(freeze_exc).__name__}: {_safe_error(freeze_exc)}",
                    ),
                )
            return replace(failure, artifacts=(stored_search,))
        errors: list[str] = []
        search_artifacts: list[dict[str, Any]] = []
        raw_search_artifact = getattr(
            self.patent_provider,
            "last_search_artifact",
            None,
        )
        if isinstance(raw_search_artifact, RetrievedArtifact):
            try:
                stored_search = self._materialize_provider_search_response(
                    raw_search_artifact,
                    investigation_id=investigation_id,
                )
            except Exception as exc:
                errors.append(
                    "provider 原始检索响应无法冻结: "
                    f"{type(exc).__name__}: {_safe_error(exc)}"
                )
            else:
                search_artifacts.append(stored_search)
                leads = [
                    replace(
                        lead,
                        artifacts=(*lead.artifacts, stored_search),
                        provenance={
                            **dict(lead.provenance),
                            "search_snapshot_sha256": stored_search["sha256"],
                            "search_snapshot_uri": stored_search["uri"],
                            "search_snapshot_retrieved_at": stored_search[
                                "retrieved_at"
                            ],
                        },
                    )
                    for lead in leads
                ]
        elif str(
            getattr(self.patent_provider, "provider_name", "")
        ) in {"epo_ops", "patsnap"}:
            errors.append("live patent provider 未提供可冻结的原始检索响应")

        fallback_discovery = any(
            str(lead.provenance.get("discovery_provider") or "").endswith(
                "_web_fallback"
            )
            for lead in leads
        )
        records: list[EvidenceRecord] = []
        if fallback_discovery:
            errors.append(
                "Google Patents API 不可用，本批次仅由公开网页备用源发现候选；"
                "即使后续取得真实文献，也不得把该批次标记为完整检索"
            )
        for lead in leads:
            try:
                retrieved = self.patent_retrieval_provider.retrieve(lead)
                retrieved, retrieval_trace_artifacts = (
                    self._materialize_retrieval_trace_artifacts(
                        retrieved,
                        provider=self.patent_retrieval_provider,
                        investigation_id=investigation_id,
                        relative_stem=_safe_stem(lead.external_id),
                    )
                )
                search_artifacts.extend(retrieval_trace_artifacts)
                if retrieved.stage is EvidenceStage.LEAD:
                    for tracked in tuple(
                        getattr(
                            self.patent_retrieval_provider,
                            "last_retrieval_artifacts",
                            (),
                        )
                        or ()
                    ):
                        if not isinstance(tracked, RetrievedArtifact):
                            continue
                        try:
                            frozen = self._materialize_provider_response_artifact(
                                tracked,
                                investigation_id=investigation_id,
                                group="retrieval-incomplete",
                                relative_stem=_safe_stem(lead.external_id),
                            )
                        except Exception as freeze_exc:
                            errors.append(
                                f"{lead.external_id}: provider 不完整详情响应无法冻结: "
                                f"{type(freeze_exc).__name__}: "
                                f"{_safe_error(freeze_exc)}"
                            )
                        else:
                            search_artifacts.append(frozen)
                    records.append(retrieved)
                    errors.append(f"{lead.external_id}: 详情页未取得真实全文")
                    continue
                materialized = self._materialize_google(
                    retrieved,
                    investigation_id=investigation_id,
                )
                public_date_evidence = str(
                    materialized.provenance.get("public_date_evidence") or ""
                ).strip()
                public_date_verified = bool(
                    materialized.provenance.get("publication_date_verified")
                )
                filing_date_verified = bool(
                    materialized.provenance.get("filing_date_verified")
                )
                priority_date_verified = bool(
                    materialized.provenance.get("priority_date_verified")
                )
                reviewed = self.patent_retrieval_provider.qualify(
                    materialized,
                    critical_date=critical_date,
                    # Query overlap is a recall signal, never technical
                    # disclosure verification.  I4-S is the only component
                    # allowed to promote this after an evidence matrix exists.
                    content_relevance_verified=False,
                    source_chain_verified=_has_verified_primary_snapshot(
                        materialized,
                        allowed_root=self.artifact_store.root,
                    ),
                    # Provider-side dates become verified only when the
                    # adapter explicitly ties them to a frozen official
                    # response. Google detail metadata intentionally leaves
                    # these flags false; EPO OPS biblio may set them true.
                    public_date_verified=public_date_verified,
                    candidate_filing_date_verified=filing_date_verified,
                    candidate_priority_date_verified=priority_date_verified,
                    public_date_evidence=public_date_evidence or None,
                    target_publication_date=target_publication_date,
                    target_publication_date_verified=target_publication_date_verified,
                    date_channel=query.date_channel.value,
                )
                records.append(reviewed)
                if not _record_images(materialized):
                    errors.append(f"{lead.external_id}: 未取得可核验附图/PDF页面")
            except Exception as exc:
                for tracked in tuple(
                    getattr(
                        self.patent_retrieval_provider,
                        "last_retrieval_artifacts",
                        (),
                    )
                    or ()
                ):
                    if not isinstance(tracked, RetrievedArtifact):
                        continue
                    try:
                        frozen = self._materialize_provider_response_artifact(
                            tracked,
                            investigation_id=investigation_id,
                            group="retrieval-failures",
                            relative_stem=_safe_stem(lead.external_id),
                        )
                    except Exception as freeze_exc:
                        errors.append(
                            f"{lead.external_id}: provider 详情失败响应无法冻结: "
                            f"{type(freeze_exc).__name__}: {_safe_error(freeze_exc)}"
                        )
                    else:
                        search_artifacts.append(frozen)
                errors.append(
                    f"{lead.external_id}: {type(exc).__name__}: {_safe_error(exc)}"
                )
        return SearchBatch(
            provider=(
                f"{getattr(self.patent_provider, 'provider_name', 'patent')}"
                "+yahoo_web_fallback"
                if fallback_discovery
                else (
                    f"{getattr(self.patent_provider, 'provider_name', 'patent')}"
                    f"+{getattr(self.patent_retrieval_provider, 'provider_name', 'patent')}"
                    "_retrieval"
                    if self.patent_retrieval_provider is not self.patent_provider
                    else str(
                        getattr(self.patent_provider, "provider_name", "patent")
                    )
                )
            ),
            records=tuple(records),
            complete=not errors,
            errors=tuple(errors),
            network_used=True,
            artifacts=tuple(search_artifacts),
        )

    def _search_npl(
        self,
        *,
        query: SearchQuery,
        investigation_id: str,
        critical_date: date,
    ) -> SearchBatch:
        try:
            provider_query: str | Mapping[str, Any] = query.expression
            if query.subject_terms and query.feature_terms:
                provider_query = {
                    "text": query.expression,
                    "subject_terms": list(query.subject_terms),
                    "feature_terms": list(query.feature_terms),
                }
            search_result = self.npl_provider.search(
                provider_query,
                max_results=self.max_candidates_per_query,
            )
        except Exception as exc:
            return _provider_failure(
                str(getattr(self.npl_provider, "provider_name", "npl")),
                "search",
                exc,
                network_used=True,
            )
        if isinstance(search_result, ProviderSearchBatch):
            leads = list(search_result.records)
            provider_name = "composite_npl"
            errors = list(search_result.errors)
        else:
            leads = list(search_result)
            provider_name = str(
                getattr(self.npl_provider, "provider_name", "arxiv")
            )
            errors = []
        records: list[EvidenceRecord] = []
        for lead in leads:
            if isinstance(self.npl_provider, CompositeNplProvider):
                adapter, retrieval_lead = self.npl_provider.retrieval_plan_for(lead)
            else:
                adapter, retrieval_lead = self.npl_provider, lead
            if adapter is None:
                records.append(lead)
                errors.append(f"{lead.external_id}: 找不到对应的 NPL 取文 adapter")
                continue
            try:
                if isinstance(adapter, ArxivProvider):
                    artifact = adapter.download_pdf(lead)
                    retrieved = lead.with_artifact(artifact)
                    materialized = self._materialize_pdf(
                        retrieved,
                        investigation_id=investigation_id,
                        pdf_content=artifact.content,
                        relative_stem=_safe_stem(lead.external_id),
                    )
                    public_date_evidence = str(
                        materialized.provenance.get("public_date_evidence") or ""
                    ).strip()
                    reviewed = adapter.qualify(
                        materialized,
                        critical_date=critical_date,
                        content_relevance_verified=False,
                        source_chain_verified=_has_verified_primary_snapshot(
                            materialized,
                            allowed_root=self.artifact_store.root,
                        ),
                        public_date_verified=bool(public_date_evidence),
                        public_date_evidence=public_date_evidence or None,
                    )
                    records.append(reviewed)
                    if not _record_images(materialized):
                        errors.append(f"{lead.external_id}: PDF 未渲染出可核验页面")
                    continue
                if isinstance(adapter, WebEvidenceProvider):
                    retrieved = adapter.retrieve(retrieval_lead)
                    if (
                        retrieved.primary_artifact
                        and retrieved.primary_artifact.kind == "pdf"
                    ):
                        materialized = self._materialize_pdf(
                            retrieved,
                            investigation_id=investigation_id,
                            pdf_content=retrieved.primary_artifact.content,
                            relative_stem=_safe_stem(lead.external_id),
                        )
                    else:
                        materialized = self._materialize_primary_source(
                            retrieved,
                            investigation_id=investigation_id,
                            relative_stem=_safe_stem(lead.external_id),
                        )
                    public_date_evidence = str(
                        materialized.provenance.get("public_date_evidence") or ""
                    ).strip()
                    reviewed = adapter.qualify(
                        materialized,
                        critical_date=critical_date,
                        content_relevance_verified=False,
                        # An embedded page date is not, by itself, a verified
                        # public-availability/source chain.  Keep it retrieved
                        # until explicit evidence review supplies both facts.
                        source_chain_verified=_has_verified_primary_snapshot(
                            materialized,
                            allowed_root=self.artifact_store.root,
                        ),
                        public_date_verified=False,
                        public_date_evidence=public_date_evidence or None,
                    )
                    records.append(reviewed)
                    errors.append(
                        f"{lead.external_id}: 网页已取回但公开日期/来源链待人工核验"
                    )
                    continue

                # OpenAlex/Crossref are discovery-only adapters.  A DOI or PDF
                # URL is not a fetched document, so retain the lead and make
                # the missing retrieval path visible to the investigation.
                records.append(lead)
                errors.append(
                    f"{lead.external_id}: {lead.provider} 仅返回题录 lead，尚未取得真实文件"
                )
            except Exception as exc:
                errors.append(
                    f"{lead.external_id}: {type(exc).__name__}: {_safe_error(exc)}"
                )
        return SearchBatch(
            provider=provider_name,
            records=tuple(records),
            complete=not errors,
            errors=tuple(errors),
            network_used=True,
        )

    def _materialize_provider_search_response(
        self,
        artifact: RetrievedArtifact,
        *,
        investigation_id: str,
    ) -> dict[str, Any]:
        if artifact.kind not in {
            "provider_search_response",
            "provider_error_response",
        } or (not artifact.content and artifact.kind != "provider_error_response"):
            raise ProviderContentError("provider 检索响应工件类型无效")
        return self._materialize_provider_response_artifact(
            artifact,
            investigation_id=investigation_id,
            group="search",
            relative_stem="response",
        )

    def _materialize_provider_response_artifact(
        self,
        artifact: RetrievedArtifact,
        *,
        investigation_id: str,
        group: str,
        relative_stem: str,
    ) -> dict[str, Any]:
        if not artifact.kind.startswith("provider_") or (
            not artifact.content and artifact.kind != "provider_error_response"
        ):
            raise ProviderContentError("provider 原始响应工件类型无效")
        suffix = _source_suffix(artifact.media_type, artifact.kind)
        stored = self.artifact_store.write_bytes(
            investigation_id,
            (
                f"provider-responses/{_safe_stem(artifact.provider)}/"
                f"{_safe_stem(group)}/{_safe_stem(relative_stem)}/"
                f"{_safe_stem(artifact.kind)}-{artifact.content_sha256}{suffix}"
            ),
            artifact.content,
            artifact_type=artifact.kind,
            mime_type=artifact.media_type,
        )
        if stored.sha256 != artifact.content_sha256:
            raise ProviderContentError("响应快照哈希与 provider 响应不一致")
        return {
            **stored.model_dump(),
            "kind": artifact.kind,
            "provider": artifact.provider,
            "source_url": artifact.source_url,
            "retrieved_at": artifact.retrieved_at,
            "content_sha256": stored.sha256,
            "content_length": stored.byte_size,
            "request_attempt_log": [
                dict(item) for item in artifact.request_attempt_log
            ],
            "local_immutable_snapshot": True,
            "is_primary_source": False,
        }

    def _materialize_retrieval_trace_artifacts(
        self,
        record: EvidenceRecord,
        *,
        provider: Any,
        investigation_id: str,
        relative_stem: str,
    ) -> tuple[EvidenceRecord, list[dict[str, Any]]]:
        frozen: list[dict[str, Any]] = []
        existing_hashes = {
            str(item.get("content_sha256") or item.get("sha256") or "")
            for item in record.artifacts
        }
        for artifact in tuple(
            getattr(provider, "last_retrieval_artifacts", ()) or ()
        ):
            if (
                not isinstance(artifact, RetrievedArtifact)
                or not artifact.kind.startswith("provider_")
                or artifact.content_sha256 in existing_hashes
            ):
                continue
            stored = self._materialize_provider_response_artifact(
                artifact,
                investigation_id=investigation_id,
                group="retrieval-chain",
                relative_stem=relative_stem,
            )
            frozen.append(stored)
            existing_hashes.add(artifact.content_sha256)
        if not frozen:
            return record, []
        return (
            replace(
                record,
                artifacts=(*record.artifacts, *frozen),
                provenance={
                    **dict(record.provenance),
                    "retrieval_attempt_artifact_hashes": [
                        item["content_sha256"] for item in frozen
                    ],
                },
            ),
            frozen,
        )

    def _materialize_tracked_media_failures(
        self,
        record: EvidenceRecord,
        *,
        investigation_id: str,
        relative_stem: str,
    ) -> EvidenceRecord:
        additions: list[dict[str, Any]] = []
        existing_keys = {
            (
                str(item.get("content_sha256") or item.get("sha256") or ""),
                str(item.get("kind") or item.get("artifact_type") or ""),
                str(item.get("source_url") or ""),
                json.dumps(
                    item.get("request_attempt_log") or (),
                    ensure_ascii=False,
                    sort_keys=True,
                    default=str,
                ),
            )
            for item in record.artifacts
        }
        for artifact in tuple(
            getattr(self.patent_retrieval_provider, "last_media_artifacts", ()) or ()
        ):
            if not isinstance(artifact, RetrievedArtifact):
                continue
            artifact_key = (
                artifact.content_sha256,
                artifact.kind,
                artifact.source_url,
                json.dumps(
                    [dict(item) for item in artifact.request_attempt_log],
                    ensure_ascii=False,
                    sort_keys=True,
                    default=str,
                ),
            )
            if artifact_key in existing_keys:
                continue
            stored = self._materialize_provider_response_artifact(
                artifact,
                investigation_id=investigation_id,
                group="media-failures",
                relative_stem=relative_stem,
            )
            additions.append(stored)
            existing_keys.add(artifact_key)
        if not additions:
            return record
        failure_hashes = list(
            record.provenance.get("media_failure_response_hashes") or ()
        )
        for item in additions:
            if item["content_sha256"] not in failure_hashes:
                failure_hashes.append(item["content_sha256"])
        return replace(
            record,
            artifacts=(*record.artifacts, *additions),
            provenance={
                **dict(record.provenance),
                "media_failure_response_hashes": failure_hashes,
            },
        )

    def _materialize_related_provider_metadata(
        self,
        record: EvidenceRecord,
        artifact: RetrievedArtifact,
        *,
        investigation_id: str,
        relative_stem: str,
    ) -> EvidenceRecord:
        metadata = artifact.source_metadata_artifact
        if metadata is None or not metadata.content:
            return record
        if any(
            item.get("content_sha256") == metadata.content_sha256
            or item.get("sha256") == metadata.content_sha256
            for item in record.artifacts
        ):
            return record
        suffix = _source_suffix(metadata.media_type, metadata.kind)
        stored = self.artifact_store.write_bytes(
            investigation_id,
            (
                f"evidence/{relative_stem}/provider-metadata/"
                f"{_safe_stem(metadata.kind)}-{metadata.content_sha256}{suffix}"
            ),
            metadata.content,
            artifact_type=metadata.kind,
            mime_type=metadata.media_type,
        )
        if stored.sha256 != metadata.content_sha256:
            raise ProviderContentError("媒体 metadata 快照哈希与 provider 响应不一致")
        summary = {
            **stored.model_dump(),
            "kind": metadata.kind,
            "provider": metadata.provider,
            "source_url": metadata.source_url,
            "retrieved_at": metadata.retrieved_at,
            "content_sha256": stored.sha256,
            "content_length": stored.byte_size,
            "request_attempt_log": [
                dict(item) for item in metadata.request_attempt_log
            ],
            "local_immutable_snapshot": True,
            "is_primary_source": False,
        }
        hashes = list(record.provenance.get("media_metadata_response_hashes") or ())
        if stored.sha256 not in hashes:
            hashes.append(stored.sha256)
        return replace(
            record,
            artifacts=(*record.artifacts, summary),
            provenance={
                **dict(record.provenance),
                "media_metadata_response_hashes": hashes,
            },
        )

    def _materialize_google(
        self,
        record: EvidenceRecord,
        *,
        investigation_id: str,
    ) -> EvidenceRecord:
        retrieval_provider = self.patent_retrieval_provider
        primary_artifact = record.primary_artifact
        if (
            isinstance(primary_artifact, RetrievedArtifact)
            and primary_artifact.media_type == "application/pdf"
        ):
            with_metadata = self._materialize_related_provider_metadata(
                record,
                primary_artifact,
                investigation_id=investigation_id,
                relative_stem=_safe_stem(record.external_id),
            )
            return self._materialize_pdf(
                with_metadata,
                investigation_id=investigation_id,
                pdf_content=primary_artifact.content,
                relative_stem=_safe_stem(record.external_id),
            )
        materialized = self._materialize_primary_source(
            record,
            investigation_id=investigation_id,
            relative_stem=_safe_stem(record.external_id),
        )
        stored: list[dict[str, Any]] = []
        images: list[str] = []
        media_errors: list[str] = []
        is_patsnap = isinstance(retrieval_provider, PatsnapProvider)
        image_budget = 2 if is_patsnap else min(2, len(record.image_urls))
        for index in range(image_budget):
            try:
                artifact = retrieval_provider.download_image(record, index=index)
            except ProviderError as exc:
                if is_patsnap:
                    media_errors.append(f"P042/image[{index}]: {_safe_error(exc)}")
                    try:
                        materialized = self._materialize_tracked_media_failures(
                            materialized,
                            investigation_id=investigation_id,
                            relative_stem=_safe_stem(record.external_id),
                        )
                    except Exception as freeze_exc:
                        media_errors.append(
                            "P042 failure response freeze: "
                            f"{type(freeze_exc).__name__}: {_safe_error(freeze_exc)}"
                        )
                    # A missing/forbidden metadata page cannot become valid by
                    # asking for a later index, and repeating it may be metered.
                    if index == 0:
                        break
                continue
            materialized = self._materialize_related_provider_metadata(
                materialized,
                artifact,
                investigation_id=investigation_id,
                relative_stem=_safe_stem(record.external_id),
            )
            suffix = _image_suffix(artifact.media_type)
            item = self.artifact_store.write_bytes(
                investigation_id,
                f"evidence/{_safe_stem(record.external_id)}/figure-{index + 1}{suffix}",
                artifact.content,
                artifact_type="prior_art_image",
                mime_type=artifact.media_type,
            )
            stored.append(
                {
                    **item.model_dump(),
                    "provider": artifact.provider,
                    "kind": artifact.kind,
                    "source_url": artifact.source_url,
                    "content_sha256": item.sha256,
                    "content_length": item.byte_size,
                    "source_metadata_sha256": (
                        artifact.source_metadata_artifact.content_sha256
                        if artifact.source_metadata_artifact
                        else None
                    ),
                    "request_attempt_log": [
                        dict(item) for item in artifact.request_attempt_log
                    ],
                }
            )
            images.append(item.uri)
        if not images and (record.pdf_url or is_patsnap):
            try:
                artifact = retrieval_provider.download_pdf(record)
                materialized = self._materialize_related_provider_metadata(
                    materialized,
                    artifact,
                    investigation_id=investigation_id,
                    relative_stem=_safe_stem(record.external_id),
                )
                pdf_record = materialized.with_artifact(artifact)
                pdf_record = replace(
                    pdf_record,
                    qualification_issues=materialized.qualification_issues,
                )
                return self._materialize_pdf(
                    pdf_record,
                    investigation_id=investigation_id,
                    pdf_content=artifact.content,
                    relative_stem=_safe_stem(record.external_id),
                )
            except Exception as exc:
                if not is_patsnap:
                    raise
                media_errors.append(f"P020/PDF: {_safe_error(exc)}")
                try:
                    materialized = self._materialize_tracked_media_failures(
                        materialized,
                        investigation_id=investigation_id,
                        relative_stem=_safe_stem(record.external_id),
                    )
                except Exception as freeze_exc:
                    media_errors.append(
                        "P020 failure response freeze: "
                        f"{type(freeze_exc).__name__}: {_safe_error(freeze_exc)}"
                    )
        if is_patsnap and not images:
            # P012/P018/P019 text has already been frozen.  Missing media is an
            # explicit evidence gap, not a reason to discard the document.
            return replace(
                materialized,
                image_urls=(),
                qualification_issues=tuple(
                    dict.fromkeys(
                        (*materialized.qualification_issues, "patsnap_media_unavailable")
                    )
                ),
                provenance={
                    **dict(materialized.provenance),
                    "media_retrieval": {
                        "status": "unavailable",
                        "errors": media_errors,
                    },
                },
            )
        return replace(
            materialized,
            image_urls=tuple(images),
            artifacts=(*materialized.artifacts, *stored),
            provenance={
                **dict(materialized.provenance),
                **(
                    {
                        "media_retrieval": {
                            "status": "images_retrieved",
                            "count": len(images),
                            "errors": media_errors,
                        }
                    }
                    if is_patsnap
                    else {}
                ),
            },
        )

    def _materialize_primary_source(
        self,
        record: EvidenceRecord,
        *,
        investigation_id: str,
        relative_stem: str,
    ) -> EvidenceRecord:
        artifact = record.primary_artifact
        if artifact is None or not artifact.content:
            raise ProviderContentError("已取回记录缺少原始响应字节，不能建立来源快照")
        suffix = _source_suffix(artifact.media_type, artifact.kind)
        stored = self.artifact_store.write_bytes(
            investigation_id,
            f"evidence/{relative_stem}/source{suffix}",
            artifact.content,
            artifact_type="prior_art_primary_source",
            mime_type=artifact.media_type,
        )
        if stored.sha256 != artifact.content_sha256:
            raise ProviderContentError("本地来源快照哈希与取回响应不一致")
        summary = _primary_artifact_summary(stored.model_dump(), artifact)
        readable_document = load_cached_readable_document(
            self.artifact_store,
            investigation_id,
            stored.sha256,
        )
        parse_issue: str | None = None
        if readable_document is None:
            try:
                readable_document = normalize_epo_bundle(
                    (
                        artifact.content
                        if "epo.ops.bundle" in artifact.media_type.lower()
                        else None
                    ),
                    source_sha256=stored.sha256,
                    title=record.title,
                    abstract=record.abstract,
                )
                if readable_document is None:
                    readable_document = _provider_structured_readable_document(
                        record,
                        source_sha256=stored.sha256,
                    )
            except Exception as exc:
                parse_issue = (
                    f"{type(exc).__name__}: provider 结构化全文标准化失败"
                )
                readable_document = _failed_readable_document(
                    stored.sha256,
                    parse_issue,
                )
        readable_json, readable_markdown = persist_readable_document(
            self.artifact_store,
            investigation_id,
            stored.sha256,
            readable_document,
        )
        readable_artifacts = _readable_artifact_summaries(
            readable_json.model_dump(),
            readable_markdown.model_dump(),
        )
        normalized_text = str(readable_document.get("full_text") or "").strip()
        return replace(
            record,
            full_text=normalized_text or record.full_text,
            content_sha256=stored.sha256,
            artifacts=(
                *_demote_primary_artifacts(record.artifacts),
                summary,
                *readable_artifacts,
            ),
            provenance={
                **dict(record.provenance),
                "primary_snapshot_verified": True,
                "primary_snapshot_uri": stored.uri,
                "primary_snapshot_sha256": stored.sha256,
                "readable_document_version": READABLE_PATENT_VERSION,
                "readable_document_cache_key": stored.sha256,
                **(
                    {"readable_document_error": parse_issue}
                    if parse_issue
                    else {}
                ),
            },
            readable_document=readable_document,
            analysis_ready=bool(readable_document.get("analysis_ready")),
            analysis_readiness_reason=str(
                readable_document.get("analysis_readiness_reason") or ""
            ),
            primary_artifact=None,
        )

    def _materialize_pdf(
        self,
        record: EvidenceRecord,
        *,
        investigation_id: str,
        pdf_content: bytes,
        relative_stem: str,
    ) -> EvidenceRecord:
        raw_artifact = record.primary_artifact
        if raw_artifact and raw_artifact.content_sha256 != _sha256_bytes(pdf_content):
            raise ProviderContentError("PDF 原始响应哈希不一致")
        pdf = self.artifact_store.write_bytes(
            investigation_id,
            f"evidence/{relative_stem}/document.pdf",
            pdf_content,
            artifact_type="prior_art_pdf",
            mime_type="application/pdf",
        )
        readable_document = load_cached_readable_document(
            self.artifact_store,
            investigation_id,
            pdf.sha256,
        )
        parse_issue: str | None = None
        if readable_document is None:
            try:
                readable_document = normalize_pdf(
                    pdf_content,
                    source_sha256=pdf.sha256,
                    title=record.title,
                    abstract=record.abstract,
                )
            except Exception as exc:
                parse_issue = f"{type(exc).__name__}: PDF 全页标准化/OCR失败"
                readable_document = _failed_readable_document(
                    pdf.sha256,
                    parse_issue,
                )
        readable_json, readable_markdown = persist_readable_document(
            self.artifact_store,
            investigation_id,
            pdf.sha256,
            readable_document,
        )
        pages = self.artifact_store.render_pdf_images(
            investigation_id,
            pdf.uri,
            f"evidence/{relative_stem}/pages",
            max_images=8,
            max_rendered_pixels=64_000_000,
            operation_timeout_seconds=90.0,
        )
        source_artifact = raw_artifact or _artifact_from_record_pdf(record, pdf_content)
        primary = _primary_artifact_summary(pdf.model_dump(), source_artifact)
        readable_artifacts = _readable_artifact_summaries(
            readable_json.model_dump(),
            readable_markdown.model_dump(),
        )
        normalized_text = str(readable_document.get("full_text") or "").strip()
        provenance = {
            **dict(record.provenance),
            "primary_snapshot_verified": True,
            "primary_snapshot_uri": pdf.uri,
            "primary_snapshot_sha256": pdf.sha256,
            "readable_document_version": READABLE_PATENT_VERSION,
            "readable_document_cache_key": pdf.sha256,
            **(
                {"readable_document_error": parse_issue}
                if parse_issue
                else {}
            ),
        }
        if str(provenance.get("retrieval_provider") or "") == "patsnap_p020":
            date_audit = verify_frozen_patent_pdf_front_page_date(
                pdf_content,
                source_sha256=pdf.sha256,
                publication_number=record.publication_number,
                publication_date=record.publication_date,
            )
            provenance["front_page_date_verification"] = date_audit
            if date_audit.get("verified"):
                date_evidence = (
                    "Patsnap P020 frozen patent PDF front page exact identity/date "
                    f"match; page=1; publication_number={record.publication_number}; "
                    f"publication_date={record.publication_date}; "
                    f"source_sha256={pdf.sha256}; "
                    f"extraction_method={date_audit.get('extraction_method')}"
                )
                provenance["public_date_evidence"] = date_evidence
                provenance["publication_date_verified"] = True
                provenance["date_evidence"] = {
                    **dict(provenance.get("date_evidence") or {}),
                    "publication_date": {
                        "value": record.publication_date,
                        "source": "frozen_p020_pdf_front_page",
                        "page": 1,
                        "source_sha256": pdf.sha256,
                        "extraction_method": date_audit.get("extraction_method"),
                    },
                }
        return replace(
            record,
            full_text=normalized_text or record.full_text,
            content_sha256=pdf.sha256,
            image_urls=tuple(item.uri for item in pages),
            artifacts=(
                *_demote_primary_artifacts(record.artifacts),
                primary,
                *readable_artifacts,
                *(item.model_dump() for item in pages),
            ),
            provenance=provenance,
            readable_document=readable_document,
            analysis_ready=bool(readable_document.get("analysis_ready")),
            analysis_readiness_reason=str(
                readable_document.get("analysis_readiness_reason") or ""
            ),
            primary_artifact=None,
        )


class RoundCheckpoint(BaseModel):
    # Automatic execution is one initial round plus at most five gap rounds.
    # Explicit continuations use later absolute iteration numbers without
    # mutating the already-closed automatic rounds.
    iteration_number: int = Field(ge=1)
    iteration_id: str | None = None
    plan: dict[str, Any] | None = None
    query_row_ids: list[str] = Field(default_factory=list)
    query_repository_ids: dict[str, str] = Field(default_factory=dict)
    query_statuses: dict[str, str] = Field(default_factory=dict)
    module_run_ids: dict[str, str] = Field(default_factory=dict)
    module_run_statuses: dict[str, str] = Field(default_factory=dict)
    document_keys: list[str] = Field(default_factory=list)
    provider_complete: bool = True
    provider_failures: list[str] = Field(default_factory=list)
    failure_records: list[dict[str, Any]] = Field(default_factory=list)
    qualified_documents_added: int = 0
    gaps_before: list[dict[str, Any]] = Field(default_factory=list)
    gaps_after: list[dict[str, Any]] = Field(default_factory=list)
    closest_prior_art: dict[str, Any] | None = None
    obviousness_precheck: dict[str, Any] | None = None
    combination: dict[str, Any] | None = None
    iteration_status: str = "pending"
    stop_reason: str | None = None
    complete: bool = False


class ClaimCheckpoint(BaseModel):
    claim_id: str
    claim_investigation_id: str | None = None
    status: str = ClaimStatus.QUEUED.value
    critical_date: str | None = None
    critical_date_basis: str | None = None
    target_publication_date: str | None = None
    target_publication_date_verified: bool = False
    current_round: int = 0
    limitations: list[dict[str, Any]] = Field(default_factory=list)
    limitation_row_ids: dict[str, str] = Field(default_factory=dict)
    rounds: dict[str, RoundCheckpoint] = Field(default_factory=dict)
    documents: dict[str, dict[str, Any]] = Field(default_factory=dict)
    comparisons: dict[str, dict[str, Any]] = Field(default_factory=dict)
    date_qualifications: dict[str, dict[str, Any]] = Field(default_factory=dict)
    current_closest_prior_art: dict[str, Any] | None = None
    current_obviousness_precheck: dict[str, Any] | None = None
    gaps: list[dict[str, Any]] = Field(default_factory=list)
    provider_failures: list[str] = Field(default_factory=list)
    analysis_failures: list[str] = Field(default_factory=list)
    historical_provider_failures: list[str] = Field(default_factory=list)
    historical_analysis_failures: list[str] = Field(default_factory=list)
    continuation_history: list[dict[str, Any]] = Field(default_factory=list)
    # Human review rebuilds the derived frontier but is not a search iteration.
    # Keep its audit trail separate so it never consumes or expands round budget.
    human_review_history: list[dict[str, Any]] = Field(default_factory=list)
    terminal_reason: str | None = None


class WorkflowCheckpoint(BaseModel):
    version: str = WORKFLOW_VERSION
    investigation_id: str
    target_source_sha256: str
    # Highest absolute iteration ever authorized. Initial search is iteration 1;
    # gap-search iterations 1..5 are persisted as absolute iterations 2..6.
    max_rounds: int = Field(ge=1)
    review_revision: int = Field(default=0, ge=0)
    last_applied_review_action_seq: int = Field(default=0, ge=0)
    claims: dict[str, ClaimCheckpoint] = Field(default_factory=dict)
    status: str = InvestigationStatus.QUEUED.value
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ClaimWorkflowResult(BaseModel):
    claim_id: str
    claim_investigation_id: str
    status: str
    rounds_completed: int
    novelty_document_id: str | None = None
    closest_prior_art_document_id: str | None = None
    unresolved_gaps: list[dict[str, Any]] = Field(default_factory=list)
    provider_failures: list[str] = Field(default_factory=list)
    analysis_failures: list[str] = Field(default_factory=list)
    terminal_reason: str | None = None


class WorkflowExecutionResult(BaseModel):
    investigation_id: str
    status: str
    claim_results: list[ClaimWorkflowResult]
    checkpoint: WorkflowCheckpoint
    provider_failures: list[str] = Field(default_factory=list)


class InvalidityWorkflow:
    """Durable I0 loop; technical/date judgments remain in injected modules."""

    def __init__(
        self,
        repository: WorkflowRepository,
        analysis_engine: InvalidityAnalysisEngine,
        evidence_gateway: EvidenceGateway,
        *,
        max_rounds: int = MAX_AUTOMATIC_GAP_SEARCH_ITERATIONS,
        pipeline_version: str = WORKFLOW_VERSION,
    ) -> None:
        if max_rounds < 1 or max_rounds > MAX_AUTOMATIC_GAP_SEARCH_ITERATIONS:
            raise ValueError(
                "自动 max_rounds 表示首轮后的 gap 检索次数，必须在 1..5"
            )
        self.repository = repository
        self.analysis_engine = analysis_engine
        self.evidence_gateway = evidence_gateway
        self.max_rounds = int(max_rounds)
        self.pipeline_version = pipeline_version

    def execute_job(
        self,
        job: Mapping[str, Any],
        module_run: Mapping[str, Any] | None = None,
        heartbeat: Callable[[], None] | None = None,
    ) -> WorkflowExecutionResult:
        module_code = str((module_run or {}).get("module_code") or "I0_ORCHESTRATE")
        if module_code not in {"I0_ORCHESTRATE", "I0_INVALIDITY_WORKFLOW"}:
            raise WorkflowInputError(f"I0 不处理模块作业 {module_code}")
        payload = job.get("payload") if isinstance(job.get("payload"), Mapping) else job
        investigation_id = str((payload or {}).get("investigation_id") or "").strip()
        if not investigation_id:
            raise WorkflowInputError("I0 作业缺少 investigation_id")
        kind = str((payload or {}).get("kind") or "initial").strip().lower()
        force_recompute = bool((payload or {}).get("force_recompute"))
        if kind == "continuation":
            return self.execute_continuation(
                investigation_id,
                continuation_batch_id=str(
                    (payload or {}).get("continuation_batch_id") or ""
                ).strip(),
                payload_round_plan=(payload or {}).get("round_plan"),
                heartbeat=heartbeat,
            )
        if kind not in {"initial", "investigation"}:
            raise WorkflowInputError(f"I0 不支持 job kind={kind}")
        return self.execute_investigation(
            investigation_id,
            force_recompute=force_recompute,
            heartbeat=heartbeat,
        )

    def execute_human_review_action(
        self,
        job: Mapping[str, Any],
        module_run: Mapping[str, Any] | None = None,
        heartbeat: Callable[[], None] | None = None,
    ) -> Mapping[str, Any]:
        """Apply one human fact revision by rebuilding the canonical checkpoint.

        The API transaction only freezes facts and queues this action.  This
        worker step independently derives date eligibility, executes or safely
        reuses an input-bound multimodal I4-S result, rebuilds every affected
        claim frontier, and publishes all derived rows plus the checkpoint in
        one repository transaction.
        """

        del module_run
        payload = job.get("payload") if isinstance(job.get("payload"), Mapping) else job
        action_id = str((payload or {}).get("review_action_id") or "").strip()
        investigation_id = str((payload or {}).get("investigation_id") or "").strip()
        if not action_id or not investigation_id:
            raise WorkflowInputError(
                "HUMAN_REVIEW_APPLY 作业缺少 investigation_id/review_action_id"
            )
        context = self.repository.get_human_review_apply_context(action_id)
        if context is None:
            raise WorkflowInputError("human review action 不存在")
        action = context.get("action") or {}
        if str(action.get("investigation_id")) != investigation_id:
            raise WorkflowInputError("human review action 与 investigation 不一致")
        if str(action.get("status")) == "applied":
            return {
                "status": "completed",
                "module_code": "HUMAN_REVIEW_APPLY",
                "review_action_id": action_id,
                "idempotent_replay": True,
                "result_summary": dict(action.get("result_summary") or {}),
            }
        if str(action.get("status")) not in {"queued", "running"}:
            raise WorkflowInputError(
                f"human review action 状态 {action.get('status')} 不允许执行"
            )

        investigation = context.get("investigation") or {}
        patent, target_images = _target_context(
            investigation.get("source_snapshot") or {}
        )
        if not target_images:
            raise WorkflowInputError(
                "目标专利缺少图像，HUMAN_REVIEW_APPLY 禁止纯文本降级"
            )
        workflow_state = investigation.get("workflow_state") or {}
        raw_checkpoint = (
            workflow_state.get(_CHECKPOINT_KEY)
            if isinstance(workflow_state, Mapping)
            else None
        )
        if not isinstance(raw_checkpoint, Mapping):
            raise WorkflowInputError("人工复核必须基于已持久化 workflow checkpoint")
        checkpoint = WorkflowCheckpoint.model_validate(raw_checkpoint)
        if checkpoint.investigation_id != investigation_id:
            raise WorkflowInputError("human review checkpoint 与 investigation 不一致")
        if checkpoint.target_source_sha256 != patent.source_sha256:
            raise WorkflowInputError("目标专利快照哈希已变化，拒绝人工复核重算")
        base_review_revision = int(action.get("base_review_revision") or 0)
        expected_review_revision = int(action.get("resulting_review_revision") or 0)
        if checkpoint.review_revision != base_review_revision:
            raise WorkflowInputError("human review action 的基础 checkpoint 已变化")
        if int(checkpoint.last_applied_review_action_seq or 0) >= int(
            action.get("action_seq") or 0
        ):
            raise WorkflowInputError("human review action sequence 已过期")
        checkpoint.review_revision = expected_review_revision

        document = context.get("document") or {}
        document_version = context.get("document_version") or {}
        document_source = context.get("document_source") or {}
        document_key = str(document.get("canonical_key") or "").strip()
        document_version_id = str(document_version.get("id") or "").strip()
        content_sha256 = str(document_version.get("content_sha256") or "").strip()
        if not document_key or not document_version_id or not content_sha256:
            raise WorkflowInputError("human review document version 元数据不完整")
        version_metadata = document_version.get("version_metadata") or {}
        if not isinstance(version_metadata, Mapping):
            version_metadata = {}
        document_text = str(version_metadata.get("full_text") or "").strip()
        rendered_images: list[dict[str, Any]] = []
        for raw in (
            *(version_metadata.get("rendered_images") or []),
            *(version_metadata.get("artifacts") or []),
        ):
            if not isinstance(raw, Mapping):
                continue
            kind = str(raw.get("kind") or raw.get("artifact_type") or "").lower()
            mime = str(raw.get("mime_type") or raw.get("media_type") or "").lower()
            if "image" not in kind and not mime.startswith("image/"):
                continue
            verified_uri = _verified_local_artifact_uri(raw)
            if not verified_uri:
                continue
            candidate = {**dict(raw), "uri": verified_uri}
            if all(
                str(item.get("uri")) != verified_uri for item in rendered_images
            ):
                rendered_images.append(candidate)
        document_images = [str(item["uri"]) for item in rendered_images]
        if not document_text or not document_images:
            raise WorkflowInputError(
                "人工导入版本必须同时具有可提取文本和可渲染页面，禁止纯文本降级"
            )
        source_uri = str(
            document_source.get("snapshot_uri")
            or version_metadata.get("source_url")
            or ""
        ).strip()
        source_url = str(
            document_source.get("source_url")
            or version_metadata.get("source_url")
            or source_uri
        ).strip()
        if (
            not source_uri
            or not Path(source_uri).expanduser().is_absolute()
            or not _verified_local_artifact_uri(
                {"uri": source_uri, "sha256": content_sha256}
            )
        ):
            raise WorkflowInputError("人工导入版本缺少本地不可变原件路径")
        primary_artifact = {
            "artifact_id": (
                str(document_version.get("content_artifact_id"))
                if document_version.get("content_artifact_id")
                else None
            ),
            "artifact_type": "human_imported_evidence",
            "kind": "pdf",
            "uri": source_uri,
            "source_url": source_url,
            "mime_type": str(document_version.get("mime_type") or "application/pdf"),
            "sha256": content_sha256,
            "content_sha256": content_sha256,
            "byte_size": document_version.get("byte_size"),
            "is_primary_source": True,
            "local_immutable_snapshot": True,
        }
        record_artifacts = (
            primary_artifact,
            *(
                {
                    **item,
                    "artifact_type": str(
                        item.get("artifact_type") or "rendered_patent_page"
                    ),
                    "kind": str(item.get("kind") or "page_image"),
                }
                for item in rendered_images
            ),
        )

        target_claim_ids = {
            str(value)
            for value in (
                (action.get("target_snapshot") or {}).get(
                    "claim_investigation_ids", []
                )
                if isinstance(action.get("target_snapshot"), Mapping)
                else []
            )
        }
        claim_rows = {
            str(row.get("id")): row
            for row in context.get("claims") or []
            if isinstance(row, Mapping)
        }
        if not target_claim_ids or set(claim_rows) != target_claim_ids:
            raise WorkflowInputError("human review action claim scope 不完整")
        scoped_patent_claims = _target_claims(patent)
        patent_claims = {item.claim_id: item for item in scoped_patent_claims}
        checkpoint_claims: dict[str, tuple[ClaimSnapshot, ClaimCheckpoint]] = {}
        for checkpoint_claim_id, claim_state in checkpoint.claims.items():
            repository_id = str(claim_state.claim_investigation_id or "")
            if repository_id not in target_claim_ids:
                continue
            patent_claim = patent_claims.get(checkpoint_claim_id)
            if patent_claim is None:
                raise WorkflowInputError(
                    f"目标权利要求 {checkpoint_claim_id} 不在目标专利快照"
                )
            checkpoint_claims[repository_id] = (patent_claim, claim_state)
        if set(checkpoint_claims) != target_claim_ids:
            raise WorkflowInputError("human review claim 不在 checkpoint 中")

        qualification_results: list[dict[str, Any]] = []
        comparison_results: list[dict[str, Any]] = []
        closest_prior_art_results: list[dict[str, Any]] = []
        combination_results: list[dict[str, Any]] = []
        gap_frontier_results: list[dict[str, Any]] = []
        claim_outcomes: dict[str, dict[str, Any]] = {}
        action_type = str(action.get("action_type") or "")
        for repository_claim_id in sorted(target_claim_ids):
            if heartbeat:
                heartbeat()
            patent_claim, claim_state = checkpoint_claims[repository_claim_id]
            claim_row = claim_rows[repository_claim_id]
            limitations = _limitations(claim_state)
            if not limitations:
                raise WorkflowInputError(
                    f"claim {repository_claim_id} 缺少 canonical limitation set"
                )
            critical_date = parse_date(
                claim_state.critical_date or claim_row.get("critical_date")
            )
            if critical_date is None:
                raise WorkflowInputError(
                    f"claim {repository_claim_id} 关键日尚未确认"
                )
            eligibility, facts, evidence_references, decision, human_confirmed = (
                _derive_human_review_eligibility(
                    context=context,
                    claim_state=claim_state,
                    claim_row=claim_row,
                    critical_date=critical_date,
                )
            )
            record = EvidenceRecord(
                provider=str(document_source.get("provider") or "human_import"),
                source_type=str(
                    facts.get("source_type")
                    or document.get("document_type")
                    or "document"
                ),
                external_id=document_key,
                title=str(document.get("title") or document_key),
                source_url=source_url,
                stage=EvidenceStage.RETRIEVED,
                publication_number=(
                    str(facts.get("publication_number"))
                    if facts.get("publication_number")
                    else None
                ),
                authority=(
                    str(facts.get("authority")) if facts.get("authority") else None
                ),
                language=(
                    str(document.get("language")) if document.get("language") else None
                ),
                publication_date=(
                    str(facts.get("publication_date"))
                    if facts.get("publication_date")
                    else None
                ),
                filing_date=(
                    str(facts.get("filing_date")) if facts.get("filing_date") else None
                ),
                priority_date=(
                    str(facts.get("priority_date"))
                    if facts.get("priority_date")
                    else None
                ),
                full_text=document_text,
                pdf_url=source_url,
                image_urls=tuple(document_images),
                content_sha256=content_sha256,
                retrieved_at=(
                    str(document_source.get("retrieved_at"))
                    if document_source.get("retrieved_at")
                    else None
                ),
                artifacts=tuple(record_artifacts),
                raw_metadata={
                    "document_id": str(document.get("id")),
                    "document_version_id": document_version_id,
                },
                provenance={
                    "human_review_action_id": action_id,
                    "human_confirmed": human_confirmed,
                    "primary_snapshot_verified": True,
                    "primary_snapshot_uri": source_uri,
                    "primary_snapshot_sha256": content_sha256,
                    "public_date_evidence": _date_evidence_summary(
                        evidence_references
                    ),
                },
                eligibility=eligibility,
            )
            claim_state.documents[document_key] = {
                "repository_document_id": str(document.get("id")),
                "repository_source_id": (
                    str(document_source.get("id"))
                    if document_source.get("id")
                    else None
                ),
                "repository_document_version_id": document_version_id,
                "repository_document_version_no": document_version.get("version_no"),
                "record": record.model_dump(),
            }
            claim_state.date_qualifications[document_key] = dict(eligibility)
            binding = _comparison_input_binding(
                claim_state,
                document_key,
                target_source_sha256=checkpoint.target_source_sha256,
            )
            reuse_comparison = bool(
                action_type == "document_date_confirmation"
                and _comparison_matches_current_inputs(
                    claim_state,
                    document_key,
                    target_source_sha256=checkpoint.target_source_sha256,
                )
            )
            if reuse_comparison:
                comparison = DocumentComparison.model_validate(
                    claim_state.comparisons[document_key]
                )
            else:
                comparison = self.analysis_engine.compare_single_reference(
                    claim_id=patent_claim.claim_id,
                    expanded_claim_text=(
                        patent_claim.expanded_claim_text or patent_claim.claim_text
                    ),
                    target_patent_text=target_patent_text_for_analysis(patent),
                    limitations=limitations,
                    document_id=document_key,
                    document_text=document_text,
                    target_images=target_images,
                    document_images=document_images,
                )
            _require_completed_structural_review(comparison)
            claim_state.comparisons[document_key] = {
                **comparison.model_dump(mode="json"),
                "_input_binding": binding,
            }
            qualified_record = qualify_evidence(
                record,
                date_eligibility=eligibility,
                content_relevance_verified=_comparison_has_verified_disclosure(
                    comparison
                ),
                source_chain_verified=_has_verified_primary_snapshot(record),
                public_date_evidence=_date_evidence_summary(evidence_references),
            )
            claim_state.documents[document_key]["record"] = qualified_record.model_dump()
            current_comparisons = [
                DocumentComparison.model_validate(value)
                for candidate_key, value in sorted(claim_state.comparisons.items())
                if _comparison_matches_current_inputs(
                    claim_state,
                    candidate_key,
                    target_source_sha256=checkpoint.target_source_sha256,
                )
            ]
            novelty_documents = [
                item.document_id
                for item in current_comparisons
                if single_reference_fully_discloses(
                    item,
                    limitations,
                    eligible_for_novelty=bool(
                        claim_state.date_qualifications.get(
                            item.document_id, {}
                        ).get("novelty_eligible")
                    ),
                )
            ]
            novelty_document = _select_novelty_document(
                claim_state, novelty_documents
            )
            claim_state.current_closest_prior_art = None
            combination_assessment: InventiveStepAssessment | None = None
            selection: ClosestPriorArtSelection | None = None
            if novelty_document:
                claim_state.gaps = []
                status = ClaimStatus.NOVELTY_EVIDENCE_COMPLETE.value
                terminal_reason = (
                    "人工事实重算后，一份日期合格文献单独完整披露全部必需特征"
                )
            else:
                inventive_comparisons = [
                    item
                    for item in current_comparisons
                    if bool(
                        claim_state.date_qualifications.get(
                            item.document_id, {}
                        ).get("inventive_step_eligible")
                    )
                ]
                if inventive_comparisons:
                    selection = select_closest_prior_art(
                        limitations=limitations,
                        comparisons=inventive_comparisons,
                        date_qualifications=claim_state.date_qualifications,
                    )
                    selection_payload = selection.model_dump(mode="json")
                    claim_state.current_closest_prior_art = selection_payload
                    claim_state.gaps = [
                        item.model_dump(mode="json") for item in selection.gaps
                    ]
                    closest_prior_art_results.append(
                        {
                            "claim_investigation_id": repository_claim_id,
                            "checkpoint_claim_id": patent_claim.claim_id,
                            "selection": selection_payload,
                            "document_id": claim_state.documents[
                                selection.document_id
                            ]["repository_document_id"],
                            "document_version_id": claim_state.documents[
                                selection.document_id
                            ].get("repository_document_version_id"),
                        }
                    )
                    candidates = _combination_candidates(
                        closest_document_id=selection.document_id,
                        comparisons=inventive_comparisons,
                        limitations=limitations,
                    )
                    if len(candidates) >= 2:
                        if heartbeat:
                            heartbeat()
                        for candidate in candidates:
                            if not _checkpoint_document_text(
                                claim_state, candidate.document_id
                            ) or not _checkpoint_document_images(
                                claim_state, candidate.document_id
                            ):
                                raise WorkflowInputError(
                                    "重建 I4-I 的当前版本缺少文本或文献图像"
                                )
                        combination_assessment = (
                            self.analysis_engine.analyze_inventive_step(
                                limitations=limitations,
                                closest_document_id=selection.document_id,
                                comparisons=candidates,
                                date_qualifications=claim_state.date_qualifications,
                                expanded_claim_text=(
                                    patent_claim.expanded_claim_text
                                    or patent_claim.claim_text
                                ),
                                target_images=target_images,
                                document_texts={
                                    item.document_id: _checkpoint_document_text(
                                        claim_state, item.document_id
                                    )
                                    for item in candidates
                                },
                                document_images={
                                    item.document_id: _checkpoint_document_images(
                                        claim_state, item.document_id
                                    )
                                    for item in candidates
                                },
                            )
                        )
                        assessment_payload = combination_assessment.model_dump(
                            mode="json"
                        )
                        claim_state.gaps = _merge_gaps(
                            selection.gaps,
                            combination_assessment.gaps,
                            resolved_feature_ids={
                                item.feature_id
                                for item in combination_assessment.feature_coverage
                                if item.covered
                            },
                        )
                        combination_results.append(
                            {
                                "claim_investigation_id": repository_claim_id,
                                "checkpoint_claim_id": patent_claim.claim_id,
                                "assessment": assessment_payload,
                                "document_ids": [
                                    claim_state.documents[item.document_id][
                                        "repository_document_id"
                                    ]
                                    for item in candidates
                                ],
                                "document_version_ids": [
                                    claim_state.documents[item.document_id].get(
                                        "repository_document_version_id"
                                    )
                                    for item in candidates
                                ],
                            }
                        )
                else:
                    provisional = _best_coverage_comparison(
                        current_comparisons, limitations
                    )
                    feature_gaps, _, _ = generate_feature_gaps(
                        limitations=limitations,
                        comparison=provisional,
                    )
                    claim_state.gaps = [
                        item.model_dump(mode="json") for item in feature_gaps
                    ]
                claim_state.gaps = _merge_raw_gap_frontier(
                    claim_state.gaps,
                    _pending_date_review_gaps(
                        claim_state,
                        require_substantive_disclosure=True,
                        target_source_sha256=checkpoint.target_source_sha256,
                    ),
                )
                if (
                    combination_assessment is not None
                    and combination_assessment.evidence_complete
                ):
                    status = ClaimStatus.INVENTIVE_STEP_EVIDENCE_COMPLETE.value
                    terminal_reason = (
                        "人工事实重算后，D1 与后续文献通过独立创造性组合证据守门"
                    )
                else:
                    status = ClaimStatus.NEEDS_HUMAN_REVIEW.value
                    terminal_reason = (
                        "人工事实已应用；仍有日期或技术特征缺口，可继续定向检索"
                    )
            # An apply is a new review-derived frontier, not another retrieval
            # round.  In particular, a review after round 3 must leave the next
            # continuation round at 4.
            _record_human_review_frontier(
                claim_state,
                review_action_id=action_id,
                review_revision=expected_review_revision,
                document_key=document_key,
                closest_prior_art=(
                    selection.model_dump(mode="json") if selection else None
                ),
                combination=(
                    combination_assessment.model_dump(mode="json")
                    if combination_assessment
                    else None
                ),
            )
            gap_frontier_results.append(
                {
                    "claim_investigation_id": repository_claim_id,
                    "checkpoint_claim_id": patent_claim.claim_id,
                    "review_action_id": action_id,
                    "open_gaps": list(claim_state.gaps),
                }
            )
            claim_state.status = status
            claim_state.terminal_reason = terminal_reason
            qualification_results.append(
                {
                    "claim_investigation_id": repository_claim_id,
                    "checkpoint_claim_id": patent_claim.claim_id,
                    "document_key": document_key,
                    "critical_date": critical_date.isoformat(),
                    "public_availability_date": facts.get(
                        "public_availability_date"
                    ),
                    "publication_date": facts.get("publication_date"),
                    "filing_date": facts.get("filing_date"),
                    "priority_date": facts.get("priority_date"),
                    "eligibility": dict(eligibility),
                    "decision": decision,
                    "human_confirmed": human_confirmed,
                    "date_rule_version": "date-rules-v1",
                    "evidence_references": evidence_references,
                }
            )
            comparison_results.append(
                {
                    "claim_investigation_id": repository_claim_id,
                    "comparison": comparison.model_dump(mode="json"),
                    "document_source_id": document_source.get("id"),
                    "limitation_set_sha256": binding["limitation_set_sha256"],
                    "target_source_sha256": checkpoint.target_source_sha256,
                    "reused": reuse_comparison,
                }
            )
            claim_outcomes[repository_claim_id] = {
                "status": status,
                "terminal_reason": terminal_reason,
                "result_summary": {
                    "human_review_action_id": action_id,
                    "document_key": document_key,
                    "document_version_id": document_version_id,
                    "date_eligibility": dict(eligibility),
                    "single_reference_novelty_complete": bool(novelty_document),
                    "novelty_document_id": novelty_document,
                    "closest_prior_art_document_id": (
                        selection.document_id if selection else None
                    ),
                    "combination_evidence_complete": bool(
                        combination_assessment
                        and combination_assessment.evidence_complete
                    ),
                    "unresolved_gaps": list(claim_state.gaps),
                },
            }

        checkpoint.status = _aggregate_investigation_status(
            [
                _claim_result(checkpoint.claims[claim.claim_id])
                for claim in scoped_patent_claims
                if claim.claim_id in checkpoint.claims
            ]
        )
        checkpoint.last_applied_review_action_seq = int(
            action.get("action_seq") or 0
        )
        if heartbeat:
            heartbeat()
        committed = self.repository.complete_human_review_apply(
            review_action_id=action_id,
            expected_review_revision=expected_review_revision,
            checkpoint=checkpoint.model_dump(mode="json"),
            qualification_results=qualification_results,
            comparison_results=comparison_results,
            closest_prior_art_results=closest_prior_art_results,
            combination_results=combination_results,
            gap_frontier_results=gap_frontier_results,
            claim_outcomes=claim_outcomes,
            investigation_status=checkpoint.status,
            actor="HUMAN_REVIEW_APPLY",
        )
        return {
            "status": "completed",
            "module_code": "HUMAN_REVIEW_APPLY",
            "review_action_id": action_id,
            "investigation_id": investigation_id,
            "investigation_status": checkpoint.status,
            "review_revision": expected_review_revision,
            "checkpoint_committed": True,
            "idempotent_replay": bool(committed.get("idempotent_replay")),
            "claim_outcomes": claim_outcomes,
        }

    def execute_continuation(
        self,
        investigation_id: str,
        *,
        continuation_batch_id: str,
        payload_round_plan: Any = None,
        heartbeat: Callable[[], None] | None = None,
    ) -> WorkflowExecutionResult:
        """Consume one API-created continuation batch without rewriting history.

        The repository-created batch is authoritative.  The job payload is
        checked against it so a stale or tampered worker payload cannot expand
        claim scope or round budget.  Replaying a terminal batch returns the
        persisted checkpoint and never calls I2/I3/I4 again.
        """

        if not continuation_batch_id:
            raise WorkflowInputError("continuation 作业缺少 continuation_batch_id")
        batch = self.repository.get_continuation_batch(continuation_batch_id)
        if batch is None:
            raise WorkflowInputError("continuation batch 不存在")
        if str(batch.get("investigation_id")) != str(investigation_id):
            raise WorkflowInputError("continuation batch 与 investigation 不一致")

        batch_status = str(batch.get("status") or "queued")
        if batch_status in _TERMINAL_CONTINUATION_BATCH_VALUES:
            checkpoint = self.load_checkpoint(investigation_id)
            if checkpoint is None:
                raise WorkflowInputError("terminal continuation batch 缺少 checkpoint")
            return _execution_result_from_checkpoint(checkpoint)

        try:
            plans = _normalize_continuation_round_plan(batch.get("round_plan"))
            if payload_round_plan is not None:
                payload_plans = _normalize_continuation_round_plan(payload_round_plan)
                if payload_plans != plans:
                    raise WorkflowInputError(
                        "continuation job round_plan 与持久化 batch 不一致"
                    )
            selected_ids = [str(item) for item in batch.get("claim_investigation_ids") or []]
            plan_ids = [item["claim_investigation_id"] for item in plans]
            if len(set(selected_ids)) != len(selected_ids) or set(selected_ids) != set(plan_ids):
                raise WorkflowInputError("continuation batch 的 claim 选择与 round_plan 不一致")
            requested_additional = int(batch.get("max_additional_rounds") or 0)
            if requested_additional < 1 or requested_additional > MAX_CONTINUATION_ROUNDS:
                raise WorkflowInputError("每个 continuation batch 只能新增 1..3 轮")
            if any(
                item["end_iteration_no"] - item["start_iteration_no"] + 1
                != requested_additional
                for item in plans
            ):
                raise WorkflowInputError("continuation round_plan 与批次轮数不一致")

            if batch_status == "queued":
                self.repository.update_continuation_batch(
                    continuation_batch_id,
                    status="running",
                    error_summary=None,
                    actor="I0",
                )

            investigation = self.repository.get_investigation(investigation_id)
            if investigation is None:
                raise WorkflowInputError(f"调查不存在: {investigation_id}")
            patent, target_images = _target_context(
                investigation.get("source_snapshot") or {}
            )
            target_claims = _target_claims(patent)
            if not target_images:
                raise WorkflowInputError(
                    "目标专利缺少图像，GLM-4.6V continuation 禁止纯文本降级"
                )
            checkpoint = self.load_checkpoint(investigation_id)
            if checkpoint is None:
                raise WorkflowInputError("continuation 必须基于已持久化 checkpoint")
            if checkpoint.target_source_sha256 != patent.source_sha256:
                raise WorkflowInputError("目标专利快照哈希已变化，禁止 continuation")
            if checkpoint.review_revision != int(
                investigation.get("review_revision") or 0
            ):
                raise WorkflowInputError(
                    "checkpoint review_revision 落后于人工事实，禁止 continuation"
                )

            claim_by_repository_id: dict[str, tuple[ClaimSnapshot, ClaimCheckpoint]] = {}
            patent_claims = {item.claim_id: item for item in target_claims}
            for claim_id, claim_state in checkpoint.claims.items():
                if not claim_state.claim_investigation_id:
                    continue
                patent_claim = patent_claims.get(claim_id)
                if patent_claim is not None:
                    claim_by_repository_id[str(claim_state.claim_investigation_id)] = (
                        patent_claim,
                        claim_state,
                    )
            missing = [item for item in plan_ids if item not in claim_by_repository_id]
            if missing:
                raise WorkflowInputError(
                    "continuation claim 不在 checkpoint/目标专利中: " + ", ".join(missing)
                )

            for plan in plans:
                _, claim_state = claim_by_repository_id[
                    plan["claim_investigation_id"]
                ]
                self._validate_continuation_claim_plan(
                    claim_state,
                    plan=plan,
                )
                checkpoint.max_rounds = max(
                    checkpoint.max_rounds, plan["end_iteration_no"]
                )

            checkpoint.status = InvestigationStatus.RUNNING.value
            self.repository.update_investigation(
                investigation_id,
                status=InvestigationStatus.RUNNING.value,
                completed_at=None,
                error_message=None,
                error_metadata={},
                actor="I0",
                event_type="workflow.continuation_started",
                event_payload={"continuation_batch_id": continuation_batch_id},
            )

            for plan in plans:
                if heartbeat:
                    heartbeat()
                claim, claim_state = claim_by_repository_id[
                    plan["claim_investigation_id"]
                ]
                planned_rounds = [
                    claim_state.rounds.get(str(number))
                    for number in range(
                        plan["start_iteration_no"],
                        plan["end_iteration_no"] + 1,
                    )
                ]
                terminal_from_this_batch = (
                    claim_state.status in _TERMINAL_CLAIM_VALUES
                    and any(item is not None and item.complete for item in planned_rounds)
                )
                if terminal_from_this_batch:
                    continue
                if claim_state.status in _TERMINAL_CLAIM_VALUES:
                    self._resume_claim_for_continuation(
                        claim_state,
                        continuation_batch_id=continuation_batch_id,
                        plan=plan,
                    )
                    self.save_checkpoint(investigation_id, checkpoint)
                self._execute_claim(
                    investigation_id=investigation_id,
                    investigation=investigation,
                    patent=patent,
                    claim=claim,
                    target_images=target_images,
                    checkpoint=checkpoint,
                    heartbeat=heartbeat,
                    round_start=plan["start_iteration_no"],
                    round_end=plan["end_iteration_no"],
                )

            claim_results = [
                _claim_result(checkpoint.claims[claim.claim_id])
                for claim in target_claims
            ]
            final_status = _aggregate_investigation_status(claim_results)
            checkpoint.status = final_status
            self.save_checkpoint(investigation_id, checkpoint)
            self.repository.update_investigation(
                investigation_id,
                status=final_status,
                completed_at=datetime.now(timezone.utc),
                workflow_state={
                    **dict(investigation.get("workflow_state") or {}),
                    _CHECKPOINT_KEY: checkpoint.model_dump(mode="json"),
                },
                actor="I0",
                event_type="workflow.continuation_finished",
                event_payload={
                    "continuation_batch_id": continuation_batch_id,
                    "claim_statuses": {
                        item.claim_id: item.status for item in claim_results
                    },
                },
            )
            selected_results = [
                item
                for item in claim_results
                if item.claim_investigation_id in set(plan_ids)
            ]
            continuation_status = _aggregate_continuation_batch_status(
                selected_results
            )
            error_summary = (
                {
                    "claim_statuses": {
                        item.claim_investigation_id: item.status
                        for item in selected_results
                    },
                    "provider_failures": [
                        failure
                        for item in selected_results
                        for failure in item.provider_failures
                    ],
                    "analysis_failures": [
                        failure
                        for item in selected_results
                        for failure in item.analysis_failures
                    ],
                }
                if continuation_status != "succeeded"
                else None
            )
            self.repository.update_continuation_batch(
                continuation_batch_id,
                status=continuation_status,
                error_summary=error_summary,
                actor="I0",
            )
            return WorkflowExecutionResult(
                investigation_id=investigation_id,
                status=final_status,
                claim_results=claim_results,
                checkpoint=checkpoint,
                provider_failures=[
                    failure
                    for item in claim_results
                    for failure in item.provider_failures
                ],
            )
        except Exception as exc:
            try:
                self.repository.update_continuation_batch(
                    continuation_batch_id,
                    status="failed",
                    error_summary={
                        "code": type(exc).__name__,
                        "message": _safe_error(exc),
                    },
                    actor="I0",
                )
            except Exception:
                pass
            raise

    def load_checkpoint(self, investigation_id: str) -> WorkflowCheckpoint | None:
        investigation = self.repository.get_investigation(investigation_id)
        if investigation is None:
            raise WorkflowInputError(f"调查不存在: {investigation_id}")
        workflow_state = investigation.get("workflow_state") or {}
        raw = workflow_state.get(_CHECKPOINT_KEY) if isinstance(workflow_state, Mapping) else None
        if not raw:
            return None
        checkpoint = WorkflowCheckpoint.model_validate(raw)
        if checkpoint.investigation_id != str(investigation_id):
            raise WorkflowInputError("checkpoint 与 investigation_id 不一致")
        return checkpoint

    def save_checkpoint(
        self,
        investigation_id: str,
        checkpoint: WorkflowCheckpoint,
        *,
        expected_state_version: int | None = None,
    ) -> Mapping[str, Any]:
        checkpoint.updated_at = datetime.now(timezone.utc)
        kwargs: dict[str, Any] = {
            "actor": "I0",
            "event_type": "workflow.checkpoint_saved",
        }
        current = self.repository.get_investigation(investigation_id)
        if current is None:
            raise WorkflowInputError(f"调查不存在: {investigation_id}")
        current_review_revision = int(current.get("review_revision") or 0)
        if checkpoint.review_revision != current_review_revision:
            raise WorkflowInputError(
                "checkpoint review_revision 落后于关系事实，拒绝旧 worker 覆盖人工复核"
            )
        kwargs["expected_state_version"] = (
            int(current["state_version"])
            if expected_state_version is None
            else expected_state_version
        )
        return self.repository.merge_investigation_workflow_state(
            investigation_id,
            {_CHECKPOINT_KEY: checkpoint.model_dump(mode="json")},
            **kwargs,
        )

    def execute_investigation(
        self,
        investigation_id: str,
        *,
        checkpoint: WorkflowCheckpoint | Mapping[str, Any] | None = None,
        force_recompute: bool = False,
        heartbeat: Callable[[], None] | None = None,
    ) -> WorkflowExecutionResult:
        investigation = self.repository.get_investigation(investigation_id)
        if investigation is None:
            raise WorkflowInputError(f"调查不存在: {investigation_id}")
        source = investigation.get("source_snapshot") or {}
        patent, target_images = _target_context(source)
        try:
            target_claims = _target_claims(patent)
        except WorkflowInputError as exc:
            self.repository.update_investigation(
                investigation_id,
                status=InvestigationStatus.FAILED.value,
                error_message=str(exc),
                error_metadata={"code": "independent_claim_missing"},
                completed_at=datetime.now(timezone.utc),
                actor="I0",
                event_type="workflow.input_failed",
            )
            raise
        if not target_images:
            self.repository.update_investigation(
                investigation_id,
                status=InvestigationStatus.FAILED.value,
                error_message="目标专利缺少图像，GLM-4.6V 工作流已 fail closed",
                error_metadata={"code": "target_images_missing"},
                actor="I0",
                event_type="workflow.input_failed",
            )
            raise WorkflowInputError("目标专利缺少图像，禁止降级为纯文本分析")

        if force_recompute:
            state = None
        elif checkpoint is None:
            state = self.load_checkpoint(investigation_id)
        elif isinstance(checkpoint, WorkflowCheckpoint):
            state = checkpoint
        else:
            state = WorkflowCheckpoint.model_validate(checkpoint)
        requested_rounds = _requested_max_rounds(investigation, self.max_rounds)
        if state is None:
            state = WorkflowCheckpoint(
                investigation_id=str(investigation_id),
                target_source_sha256=patent.source_sha256,
                max_rounds=requested_rounds,
            )
        elif state.target_source_sha256 != patent.source_sha256:
            raise WorkflowInputError("目标专利快照哈希已变化，禁止在旧 checkpoint 上续跑")
        elif state.review_revision != int(investigation.get("review_revision") or 0):
            raise WorkflowInputError(
                "checkpoint review_revision 落后于关系事实，必须先执行 human_review_apply"
            )
        # Never shrink an audit checkpoint that already contains explicitly
        # authorized continuation rounds.  This entry point still executes only
        # the original automatic budget below.
        state.max_rounds = max(state.max_rounds, requested_rounds)
        state.status = InvestigationStatus.RUNNING.value
        self.repository.update_investigation(
            investigation_id,
            status=InvestigationStatus.RUNNING.value,
            error_message=None,
            error_metadata={},
            actor="I0",
            event_type="workflow.started_or_resumed",
            event_payload={"checkpoint_version": state.version},
        )

        for claim in target_claims:
            self._ensure_claim(
                investigation_id=str(investigation_id),
                patent=patent,
                claim=claim,
                checkpoint=state,
            )
        self.save_checkpoint(str(investigation_id), state)

        for claim in target_claims:
            if heartbeat:
                heartbeat()
            claim_state = state.claims[claim.claim_id]
            if claim_state.status in _TERMINAL_CLAIM_VALUES:
                continue
            self._execute_claim(
                investigation_id=str(investigation_id),
                investigation=investigation,
                patent=patent,
                claim=claim,
                target_images=target_images,
                checkpoint=state,
                heartbeat=heartbeat,
                round_start=1,
                round_end=requested_rounds,
            )

        claim_results = [
            _claim_result(state.claims[claim.claim_id]) for claim in target_claims
        ]
        final_status = _aggregate_investigation_status(claim_results)
        state.status = final_status
        provider_failures = [
            failure
            for result in claim_results
            for failure in result.provider_failures
        ]
        self.save_checkpoint(str(investigation_id), state)
        update: dict[str, Any] = {
            "status": final_status,
            "workflow_state": {
                **dict(investigation.get("workflow_state") or {}),
                _CHECKPOINT_KEY: state.model_dump(mode="json"),
            },
            "actor": "I0",
            "event_type": "workflow.finished",
            "event_payload": {
                "claim_statuses": {item.claim_id: item.status for item in claim_results},
            },
        }
        if final_status in {
            InvestigationStatus.COMPLETED.value,
            InvestigationStatus.PARTIAL.value,
            InvestigationStatus.FAILED.value,
            InvestigationStatus.NEEDS_HUMAN_REVIEW.value,
            InvestigationStatus.CANCELLED.value,
        }:
            update["completed_at"] = datetime.now(timezone.utc)
        self.repository.update_investigation(str(investigation_id), **update)
        return WorkflowExecutionResult(
            investigation_id=str(investigation_id),
            status=final_status,
            claim_results=claim_results,
            checkpoint=state,
            provider_failures=provider_failures,
        )

    def _validate_continuation_claim_plan(
        self,
        state: ClaimCheckpoint,
        *,
        plan: Mapping[str, Any],
    ) -> None:
        start = int(plan["start_iteration_no"])
        end = int(plan["end_iteration_no"])
        existing_numbers = sorted(int(item) for item in state.rounds)
        previous_number = start - 1
        if previous_number:
            previous = state.rounds.get(str(previous_number))
            if previous is None:
                raise WorkflowInputError(
                    f"continuation round {start} 缺少父轮次 {previous_number}"
                )
            self._assert_round_closed(previous)
            expected_parent = str(previous.iteration_id or "") or None
            if plan.get("parent_iteration_id") != expected_parent:
                raise WorkflowInputError("continuation parent_iteration_id 与 checkpoint 不一致")
        elif plan.get("parent_iteration_id") is not None:
            raise WorkflowInputError("首轮 continuation 不应设置 parent_iteration_id")

        for number in existing_numbers:
            round_state = state.rounds[str(number)]
            if number < start:
                self._assert_round_closed(round_state)
            if number > end:
                raise WorkflowInputError(
                    "running continuation batch 后出现超出授权范围的新轮次"
                )
        highest_existing = max(existing_numbers, default=0)
        if highest_existing < previous_number:
            raise WorkflowInputError("continuation 起始轮次与 checkpoint 不连续")
        if highest_existing >= start:
            for number in range(start, highest_existing + 1):
                if str(number) not in state.rounds:
                    raise WorkflowInputError("continuation 已执行轮次存在编号断层")

        work_from_batch_exists = any(
            str(number) in state.rounds for number in range(start, end + 1)
        )
        if not work_from_batch_exists and state.status in _SUCCESSFUL_CLAIM_VALUES:
            raise WorkflowInputError(
                f"claim 状态 {state.status} 是成功终态；补强须另建关联调查"
            )
        if not work_from_batch_exists and state.status not in _CONTINUABLE_CLAIM_VALUES:
            raise WorkflowInputError(
                f"claim 状态 {state.status} 不允许开始 continuation"
            )

    def _resume_claim_for_continuation(
        self,
        state: ClaimCheckpoint,
        *,
        continuation_batch_id: str,
        plan: Mapping[str, Any],
    ) -> None:
        assert state.claim_investigation_id
        history_entry = {
            "continuation_batch_id": continuation_batch_id,
            "previous_status": state.status,
            "previous_terminal_reason": state.terminal_reason,
            "start_iteration_no": int(plan["start_iteration_no"]),
            "end_iteration_no": int(plan["end_iteration_no"]),
            "resumed_at": datetime.now(timezone.utc).isoformat(),
        }
        if not any(
            item.get("continuation_batch_id") == continuation_batch_id
            for item in state.continuation_history
        ):
            state.continuation_history.append(history_entry)
            for failure in state.provider_failures:
                _append_unique(state.historical_provider_failures, failure)
            for failure in state.analysis_failures:
                _append_unique(state.historical_analysis_failures, failure)
            state.provider_failures = []
            state.analysis_failures = []
        self.repository.update_claim_status(
            state.claim_investigation_id,
            ClaimStatus.QUEUED.value,
            terminal_reason=None,
            completed_at=None,
            result_summary_patch={
                "latest_continuation": history_entry,
            },
            actor="I0",
            event_type="claim.continuation_started",
            event_payload={
                "continuation_batch_id": continuation_batch_id,
                "from": state.status,
                "to": ClaimStatus.QUEUED.value,
            },
        )
        state.status = ClaimStatus.QUEUED.value
        state.terminal_reason = None

    def _ensure_claim(
        self,
        *,
        investigation_id: str,
        patent: PatentSnapshot,
        claim: ClaimSnapshot,
        checkpoint: WorkflowCheckpoint,
    ) -> None:
        state = checkpoint.claims.get(claim.claim_id)
        if state is not None and state.claim_investigation_id:
            return
        # 进程重启或 durable job 重试可能让调用方带着全新 checkpoint 进入这里；
        # 数据库中已冻结的权利要求行必须收养复用，不能重复插入触发唯一约束。
        existing = next(
            (
                row
                for row in self.repository.list_claim_investigations(investigation_id)
                if str(row.get("claim_id")) == str(claim.claim_id)
                and str(row.get("claim_variant_id") or "base") == "base"
            ),
            None,
        )
        if existing is not None:
            adopted = ClaimCheckpoint(
                claim_id=claim.claim_id,
                claim_investigation_id=str(existing["id"]),
                status=str(existing.get("status") or ClaimStatus.QUEUED.value),
            )
            critical_date = existing.get("critical_date")
            if critical_date:
                adopted.critical_date = str(critical_date)
                adopted.critical_date_basis = (
                    str(existing["critical_date_basis"])
                    if existing.get("critical_date_basis")
                    else None
                )
            target_date = existing.get("target_publication_date")
            if target_date:
                adopted.target_publication_date = str(target_date)
            checkpoint.claims[claim.claim_id] = adopted
            return
        row = self.repository.create_claim_investigation(
            investigation_id=investigation_id,
            claim_id=claim.claim_id,
            source_claim_text=claim.claim_text,
            expanded_claim_text=claim.expanded_claim_text or claim.claim_text,
            source_snapshot={
                "target_source_sha256": patent.source_sha256,
                "claim": claim.model_dump(mode="json"),
            },
            dependency_path=claim.parent_claim_ids,
            status=ClaimStatus.QUEUED.value,
            target_publication_date=parse_date(patent.publication_date),
        )
        checkpoint.claims[claim.claim_id] = ClaimCheckpoint(
            claim_id=claim.claim_id,
            claim_investigation_id=str(row["id"]),
        )

    def _execute_claim(
        self,
        *,
        investigation_id: str,
        investigation: Mapping[str, Any],
        patent: PatentSnapshot,
        claim: ClaimSnapshot,
        target_images: Sequence[str],
        checkpoint: WorkflowCheckpoint,
        heartbeat: Callable[[], None] | None,
        round_start: int = 1,
        round_end: int | None = None,
    ) -> None:
        state = checkpoint.claims[claim.claim_id]
        assert state.claim_investigation_id is not None
        target_patent_text = target_patent_text_for_analysis(patent)
        if not target_patent_text:
            raise WorkflowInputError("目标专利快照缺少可用于整体机构理解的文本")
        claim_round_limit = int(round_end or checkpoint.max_rounds)
        if round_start < 1 or claim_round_limit < round_start:
            raise WorkflowInputError("claim 轮次范围无效")
        if claim.dependency_uncertain:
            self._terminal_claim(
                state,
                ClaimStatus.NEEDS_HUMAN_REVIEW,
                "权利要求包含多重、范围或择一依附关系；MVP 禁止把替代分支合并为同一必需特征集合",
                {
                    "dependency_uncertain": True,
                    "parent_claim_ids": list(claim.parent_claim_ids),
                    "expanded_claim_text": claim.expanded_claim_text,
                },
            )
            self.save_checkpoint(investigation_id, checkpoint)
            return
        confirmation_getter = getattr(
            self.repository, "get_latest_critical_date_confirmation", None
        )
        confirmation = (
            confirmation_getter(state.claim_investigation_id)
            if callable(confirmation_getter)
            else None
        )
        critical = _resolve_claim_critical_date(
            investigation=investigation,
            patent=patent,
            claim_id=claim.claim_id,
            confirmation=confirmation,
        )
        self._set_claim_status(
            state,
            ClaimStatus.CRITICAL_DATE_REVIEW,
            result_summary_patch={"critical_date_resolution": critical.model_dump()},
        )
        if critical.requires_human_review or critical.critical_date is None:
            self._terminal_claim(
                state,
                ClaimStatus.NEEDS_HUMAN_REVIEW,
                "专利级关键日无法确定：申请日和优先权日均缺失或无法解析，需人工确认",
                {"critical_date_resolution": critical.model_dump()},
            )
            self.save_checkpoint(investigation_id, checkpoint)
            return
        state.critical_date = critical.critical_date.isoformat()
        state.critical_date_basis = critical.basis
        target_publication_date, target_publication_verified = (
            _resolve_target_publication_date(
                investigation=investigation,
                patent=patent,
                claim_id=claim.claim_id,
                confirmation=confirmation,
            )
        )
        state.target_publication_date = (
            target_publication_date.isoformat() if target_publication_date else None
        )
        state.target_publication_date_verified = target_publication_verified
        self.repository.update_claim_status(
            state.claim_investigation_id,
            ClaimStatus.CRITICAL_DATE_REVIEW.value,
            critical_date=critical.critical_date,
            critical_date_basis=critical.basis,
            target_publication_date=target_publication_date,
            result_summary_patch={
                "critical_date_basis": critical.basis,
                "critical_date_reason_codes": list(critical.reason_codes),
                "target_publication_date_verified": target_publication_verified,
            },
            actor="I0",
            event_type="claim.date_context_persisted",
            event_payload={
                "critical_date_basis": critical.basis,
                "target_publication_date_verified": target_publication_verified,
            },
        )

        for round_number in range(round_start, claim_round_limit + 1):
            if state.status in _TERMINAL_CLAIM_VALUES:
                return
            round_state = state.rounds.get(str(round_number))
            if round_state and round_state.complete:
                self._assert_round_closed(round_state)
                state.current_round = max(state.current_round, round_number)
                continue
            if round_state is None:
                previous_round = state.rounds.get(str(round_number - 1))
                if previous_round is not None:
                    self._assert_round_closed(previous_round)
                round_state = RoundCheckpoint(
                    iteration_number=round_number,
                    gaps_before=list(state.gaps),
                    gaps_after=list(state.gaps),
                )
                state.rounds[str(round_number)] = round_state

            search_status = (
                ClaimStatus.INITIAL_SEARCH if round_number == 1 else ClaimStatus.GAP_SEARCH
            )
            self._set_claim_status(
                state,
                search_status,
                current_iteration_no=round_number,
                result_summary_patch={"round": round_number},
            )
            if not round_state.iteration_id:
                parent = state.rounds.get(str(round_number - 1))
                iteration = self.repository.create_iteration(
                    claim_investigation_id=state.claim_investigation_id,
                    iteration_no=round_number,
                    purpose="initial_search" if round_number == 1 else "gap_search",
                    trigger_reason=(
                        "initial claim search"
                        if round_number == 1
                        else "unresolved feature/combination gaps"
                    ),
                    gap_feature_ids=_gap_feature_ids(state.gaps),
                    parent_iteration_id=parent.iteration_id if parent else None,
                    status="running",
                )
                round_state.iteration_id = str(iteration["id"])
                round_state.iteration_status = "running"
                self.save_checkpoint(investigation_id, checkpoint)
            gap_reuse_decision = None
            gap_distinguishing_features: list[Any] = []
            if round_number > 1 and state.current_closest_prior_art:
                d1_document_id = str(
                    state.current_closest_prior_art.get("document_id") or ""
                ).strip()
                d1_payload = state.comparisons.get(d1_document_id)
                if d1_document_id and isinstance(d1_payload, Mapping):
                    current_comparisons, reuse_keys = (
                        _current_gap_reuse_materials(
                            state,
                            target_source_sha256=checkpoint.target_source_sha256,
                        )
                    )
                    d1_comparison = DocumentComparison.model_validate(d1_payload)
                    differences = build_distinguishing_features(
                        limitations=_limitations(state),
                        d1_comparison=d1_comparison,
                    )
                    reuse_decision = evaluate_existing_corpus_gap_coverage(
                        differences=differences,
                        comparisons=current_comparisons,
                        date_qualifications=state.date_qualifications,
                        reuse_keys=reuse_keys,
                        gap_search_iteration=min(5, round_number - 1),
                        previous_iteration_failure_reason=(
                            _previous_iteration_failure_reason(
                                state.rounds.get(str(round_number - 1))
                            )
                        ),
                    )
                    gap_reuse_decision = reuse_decision
                    gap_distinguishing_features = list(differences)
                    difference_ids = [item.feature_id for item in differences]
                    large_cc = select_large_claim_chart_documents(
                        limitations=_limitations(state),
                        closest_document_id=d1_document_id,
                        comparisons=current_comparisons,
                        date_qualifications=state.date_qualifications,
                        difference_feature_ids=difference_ids,
                    )
                    state.current_closest_prior_art.update(
                        {
                            "distinguishing_features": [
                                item.model_dump(mode="json") for item in differences
                            ],
                            "gap_reuse_decision": reuse_decision.model_dump(
                                mode="json"
                            ),
                            "large_claim_chart_document_ids": [
                                item.document_id for item in large_cc
                            ],
                        }
                    )
                    covered_ids = set(
                        reuse_decision.covered_difference_feature_ids
                    )
                    state.gaps = [
                        gap
                        for gap in state.gaps
                        if not (
                            str(gap.get("kind") or "")
                            in {"feature_gap", "evidence_gap"}
                            and str(gap.get("feature_id") or "") in covered_ids
                        )
                    ]

            try:
                plan = self._round_plan(
                    investigation_id=investigation_id,
                    patent=patent,
                    claim=claim,
                    state=state,
                    round_state=round_state,
                    target_images=target_images,
                    gap_reuse_decision=gap_reuse_decision,
                    gap_distinguishing_features=gap_distinguishing_features,
                )
            except Exception as exc:
                initial_round = state.rounds.get("1")
                self._analysis_failed(
                    state,
                    round_state=round_state,
                    stage=(
                        "I2_QUERY_PLAN"
                        if round_number == 1
                        else "I2_GAP_QUERY_PLAN"
                    ),
                    error=exc,
                )
                self._persist_gaps(state=state, round_state=round_state)
                if (
                    round_number == 1
                    or initial_round is None
                    or not initial_round.plan
                ):
                    self._close_iteration(
                        round_state,
                        status="failed",
                        stop_reason="query_plan_failed",
                    )
                    self._terminal_claim(
                        state,
                        ClaimStatus.FAILED,
                        "首轮检索规划模型失败或输出未通过守门",
                        {"round": round_number},
                    )
                    self.save_checkpoint(investigation_id, checkpoint)
                    return
                # A failed I2-G round remains an auditable failed module run,
                # but it must not prevent later frozen gap rounds or I4-I from
                # consuming the corpus already obtained.  Use a zero-query
                # shell only as orchestration state; it is never presented as
                # a successful search plan and never fabricates provider hits.
                base_plan = QueryPlan.model_validate(initial_round.plan)
                strategy = gap_search_strategy_metadata(
                    min(5, max(1, round_number - 1))
                )
                failure_summary = (
                    f"第 {round_number - 1} 个 gap 轮 I2-G 失败：{_safe_error(exc)}；"
                    "本轮未执行检索，保留错误并继续后续轮次。"
                )
                plan = base_plan.model_copy(
                    update={
                        "iteration_number": round_number,
                        "round_kind": "gap",
                        "queries": [],
                        "rejected_queries": [],
                        "model": "deterministic-gap-error-shell",
                        "generation_source": "deterministic_gap_fallback",
                        "generation_warning": failure_summary,
                        "portfolio_warnings": [
                            *base_plan.portfolio_warnings,
                            failure_summary,
                        ],
                        "gap_search_iteration": min(5, round_number - 1),
                        "gap_search_strategy": strategy["code"],
                        "gap_search_strategy_label": strategy["label"],
                        "gap_search_strategy_description": strategy["description"],
                        "covered_difference_feature_ids": [],
                        "uncovered_difference_feature_ids": _gap_feature_ids(state.gaps),
                        "existing_corpus_reuse": [],
                        "previous_iteration_failure_reason": failure_summary,
                    }
                )
                round_state.plan = plan.model_dump(mode="json")

            if not state.limitations:
                state.limitations = [item.model_dump(mode="json") for item in plan.limitations]
                self._persist_limitations(state)
            self._persist_queries(
                round_state,
                plan,
                critical.critical_date,
                target_publication_date=target_publication_date,
                target_publication_date_verified=target_publication_verified,
            )
            self.save_checkpoint(investigation_id, checkpoint)

            novelty_documents = _complete_novelty_documents(state)
            round_search_contexts = self._prepare_round_candidates(
                investigation_id=investigation_id,
                claim=claim,
                state=state,
                round_state=round_state,
                plan=plan,
                round_number=round_number,
                critical_date=critical.critical_date,
                target_publication_date=target_publication_date,
                target_publication_date_verified=target_publication_verified,
                target_images=target_images,
                heartbeat=heartbeat,
            )
            for query in plan.queries:
                if round_state.query_statuses.get(query.query_id) in {
                    "completed",
                    "partial",
                    "failed",
                    "skipped",
                }:
                    continue
                if heartbeat:
                    heartbeat()
                query_processing_failed = False
                search_context = round_search_contexts.get(query.query_id) or {}
                search_module_run = search_context.get("search_module_run")
                if not isinstance(search_module_run, Mapping):
                    raise WorkflowExecutionError(
                        f"query={query.query_id} 缺少持久化 I3 检索上下文"
                    )
                fetch_module_run = self._module_run(
                    investigation_id=investigation_id,
                    state=state,
                    round_state=round_state,
                    module_code="I3_FETCH",
                    idempotency_key=(
                        f"{state.claim_id}:{round_number}:fetch:{query.query_id}"
                    ),
                    input_snapshot={"query_id": query.query_id},
                    model_version=None,
                )
                qualify_module_run = self._module_run(
                    investigation_id=investigation_id,
                    state=state,
                    round_state=round_state,
                    module_code="I3_QUALIFY",
                    idempotency_key=(
                        f"{state.claim_id}:{round_number}:qualify:{query.query_id}"
                    ),
                    input_snapshot={
                        "query_id": query.query_id,
                        "critical_date": critical.critical_date.isoformat(),
                        "date_channel": query.date_channel.value,
                    },
                    model_version=None,
                )
                try:
                    discovery_batch = _coerce_search_batch(
                        search_context.get("discovery_batch"),
                        query.provider_kind,
                    )
                    selected_records = tuple(
                        item
                        for item in search_context.get("selected_records") or ()
                        if isinstance(item, EvidenceRecord)
                    )
                    if bool(search_context.get("split_gateway")):
                        raw_retrieval = self.evidence_gateway.retrieve(
                            query,
                            selected_records,
                            investigation_id=investigation_id,
                            claim_id=claim.claim_id,
                            iteration_number=round_number,
                            critical_date=critical.critical_date,
                            target_publication_date=target_publication_date,
                            target_publication_date_verified=target_publication_verified,
                            cancel_check=heartbeat,
                        )
                        retrieval_batch = _coerce_search_batch(
                            raw_retrieval, query.provider_kind
                        )
                        batch = SearchBatch(
                            provider=retrieval_batch.provider,
                            records=retrieval_batch.records,
                            complete=(
                                discovery_batch.complete
                                and retrieval_batch.complete
                            ),
                            errors=(
                                *discovery_batch.errors,
                                *retrieval_batch.errors,
                            ),
                            network_used=(
                                discovery_batch.network_used
                                or retrieval_batch.network_used
                            ),
                            artifacts=(
                                *discovery_batch.artifacts,
                                *retrieval_batch.artifacts,
                            ),
                        )
                    else:
                        batch = replace(
                            discovery_batch,
                            records=selected_records,
                        )
                except JobCancellationRequested:
                    # 用户取消不是 provider 失败，必须立即向上传播。
                    raise
                except Exception as exc:
                    batch = SearchBatch(
                        provider=query.provider_kind,
                        complete=False,
                        errors=(f"{type(exc).__name__}: {_safe_error(exc)}",),
                    )
                if not batch.complete:
                    round_state.provider_complete = False
                for error in batch.errors:
                    failure = f"round={round_number} query={query.query_id} provider={batch.provider}: {error}"
                    _append_unique(round_state.provider_failures, failure)
                    _append_unique(state.provider_failures, failure)

                search_module_status = (
                    "succeeded"
                    if batch.complete
                    else ("partial" if batch.records else "failed")
                )
                self._finish_module_run(
                    round_state,
                    search_module_run,
                    status=search_module_status,
                    actual_provider=batch.provider,
                    network_used=batch.network_used,
                    output_snapshot={
                        "provider": batch.provider,
                        "network_used": batch.network_used,
                        "complete": batch.complete,
                        "errors": list(batch.errors),
                        "search_artifacts": [
                            {
                                "artifact_type": item.get("artifact_type"),
                                "sha256": item.get("sha256"),
                                "byte_size": item.get("byte_size"),
                                "mime_type": item.get("mime_type"),
                                "retrieved_at": item.get("retrieved_at"),
                            }
                            for item in batch.artifacts
                        ],
                        "records": [
                            {
                                "external_id": item.external_id,
                                "stage": item.stage.value,
                                "publication_number": item.publication_number,
                            }
                            for item in batch.records
                        ],
                    },
                    error=(
                        WorkflowExecutionError("; ".join(batch.errors))
                        if search_module_status == "failed"
                        else None
                    ),
                )
                fetch_complete = batch.complete and all(
                    item.stage is not EvidenceStage.LEAD for item in batch.records
                )
                fetch_status = (
                    "succeeded"
                    if fetch_complete
                    else ("partial" if batch.records else "failed")
                )
                self._finish_module_run(
                    round_state,
                    fetch_module_run,
                    status=fetch_status,
                    actual_provider=batch.provider,
                    network_used=batch.network_used,
                    output_snapshot={
                        "provider": batch.provider,
                        "network_used": batch.network_used,
                        "complete": fetch_complete,
                        "records": [
                            {
                                "external_id": item.external_id,
                                "stage": item.stage.value,
                                "source_url": item.source_url,
                                "content_sha256": item.content_sha256,
                                "retrieved_at": item.retrieved_at,
                            }
                            for item in batch.records
                        ],
                        "errors": list(batch.errors),
                    },
                    error=(
                        WorkflowExecutionError("; ".join(batch.errors) or "fetch failed")
                        if fetch_status == "failed"
                        else None
                    ),
                )
                qualification_complete = batch.complete and all(
                    item.eligibility is not None for item in batch.records
                )
                qualification_status = (
                    "succeeded"
                    if qualification_complete
                    else ("partial" if batch.records else "failed")
                )
                self._finish_module_run(
                    round_state,
                    qualify_module_run,
                    status=qualification_status,
                    actual_provider=batch.provider,
                    network_used=batch.network_used,
                    output_snapshot={
                        "provider": batch.provider,
                        "network_used": batch.network_used,
                        "complete": qualification_complete,
                        "records": [
                            {
                                "external_id": item.external_id,
                                "stage": item.stage.value,
                                "eligibility": dict(item.eligibility or {}),
                                "qualification_issues": list(item.qualification_issues),
                            }
                            for item in batch.records
                        ],
                        "errors": list(batch.errors),
                    },
                    error=(
                        WorkflowExecutionError(
                            "; ".join(batch.errors) or "qualification failed"
                        )
                        if qualification_status == "failed"
                        else None
                    ),
                )

                for record in batch.records:
                    # 逐文献协作式停止：取消请求不得等到整轮结束才生效。
                    if heartbeat:
                        heartbeat()
                    try:
                        document_key, became_qualified = self._persist_evidence(
                            investigation_id=investigation_id,
                            state=state,
                            record=record,
                            critical_date=critical.critical_date,
                        )
                    except Exception as exc:
                        query_processing_failed = True
                        round_state.provider_complete = False
                        failure = (
                            f"round={round_number} query={query.query_id} "
                            f"evidence_persist: {type(exc).__name__}: {_safe_error(exc)}"
                        )
                        _append_unique(round_state.provider_failures, failure)
                        _append_unique(state.provider_failures, failure)
                        continue
                    _append_unique(round_state.document_keys, document_key)
                    if not _record_ready_for_single_reference(record):
                        if record.provider not in _NON_LIVE_EVIDENCE_PROVIDERS:
                            round_state.provider_complete = False
                            reason = (
                                record.analysis_readiness_reason
                                or "未形成与冻结原文 SHA 绑定的全页可读文档"
                            )
                            failure = (
                                f"{document_key} 全文/OCR可读覆盖未完成: {reason}"
                            )
                            _append_unique(round_state.provider_failures, failure)
                            _append_unique(state.provider_failures, failure)
                        continue
                    if _comparison_matches_current_inputs(
                        state,
                        document_key,
                        target_source_sha256=checkpoint.target_source_sha256,
                    ):
                        continue
                    document_text = (
                        _record_text(record)
                        if record.provider in _NON_LIVE_EVIDENCE_PROVIDERS
                        else str(record.full_text or "").strip()
                    )
                    document_images = _record_images(record)
                    if not document_text or not document_images:
                        round_state.provider_complete = False
                        missing = []
                        if not document_text:
                            missing.append("全文/OCR文本")
                        if not document_images:
                            missing.append("文献图像")
                        failure = f"{document_key} 缺少{'、'.join(missing)}，未执行纯文本降级"
                        _append_unique(round_state.provider_failures, failure)
                        _append_unique(state.provider_failures, failure)
                        continue
                    try:
                        comparison = self.analysis_engine.compare_single_reference(
                            claim_id=claim.claim_id,
                            expanded_claim_text=claim.expanded_claim_text or claim.claim_text,
                            target_patent_text=target_patent_text,
                            limitations=_limitations(state),
                            document_id=document_key,
                            document_text=document_text,
                            target_images=target_images,
                            document_images=document_images,
                        )
                        i4s_module_run_id = self._persist_comparison(
                            investigation_id=investigation_id,
                            state=state,
                            round_state=round_state,
                            document_key=document_key,
                            comparison=comparison,
                            target_source_sha256=checkpoint.target_source_sha256,
                        )
                    except Exception as exc:
                        query_processing_failed = True
                        self._analysis_failed(
                            state,
                            round_state=round_state,
                            stage=f"I4_S_SINGLE_REFERENCE:{document_key}",
                            error=exc,
                        )
                        continue
                    state.comparisons[document_key] = {
                        **comparison.model_dump(mode="json"),
                        "_i4s_module_run_id": i4s_module_run_id,
                        "_input_binding": _comparison_input_binding(
                            state,
                            document_key,
                            target_source_sha256=checkpoint.target_source_sha256,
                        ),
                    }
                    if comparison.structural_review_status == "model_error":
                        query_processing_failed = True
                        round_state.provider_complete = False
                        failure = (
                            f"{document_key} 整体结构复核未完成: "
                            f"{comparison.structural_review_error_code}"
                        )
                        _append_unique(round_state.provider_failures, failure)
                        _append_unique(state.provider_failures, failure)
                        self.save_checkpoint(investigation_id, checkpoint)
                        continue
                    if record.stage is not EvidenceStage.QUALIFIED:
                        record = qualify_evidence(
                            record,
                            date_eligibility=dict(record.eligibility or {}),
                            content_relevance_verified=(
                                _comparison_has_verified_disclosure(comparison)
                            ),
                            source_chain_verified=_has_verified_primary_snapshot(record),
                            public_date_evidence=str(
                                record.provenance.get("public_date_evidence") or ""
                            ).strip()
                            or None,
                        )
                        _document_key_after, promoted = self._persist_evidence(
                            investigation_id=investigation_id,
                            state=state,
                            record=record,
                            critical_date=critical.critical_date,
                        )
                        became_qualified = became_qualified or promoted
                    if became_qualified:
                        round_state.qualified_documents_added += 1
                    eligibility = state.date_qualifications.get(document_key, {})
                    if (
                        record.stage is EvidenceStage.QUALIFIED
                        and single_reference_fully_discloses(
                        comparison,
                        _limitations(state),
                        eligible_for_novelty=bool(eligibility.get("novelty_eligible")),
                        )
                    ):
                        _append_unique(novelty_documents, document_key)
                    self.save_checkpoint(investigation_id, checkpoint)
                query_status = (
                    "completed"
                    if batch.complete and not query_processing_failed
                    else ("partial" if batch.records else "failed")
                )
                self._finish_query(
                    round_state,
                    query,
                    status=query_status,
                    diagnostic={
                        "provider": batch.provider,
                        "network_used": batch.network_used,
                        "record_count": len(batch.records),
                        "provider_complete": batch.complete,
                        "processing_failed": query_processing_failed,
                        "search_response_sha256": [
                            str(item.get("sha256") or "")
                            for item in batch.artifacts
                            if item.get("sha256")
                        ],
                        "errors": list(batch.errors),
                    },
                )

            self._set_claim_status(state, ClaimStatus.SINGLE_REFERENCE_REVIEW)
            novelty_document = _select_novelty_document(state, novelty_documents)
            if novelty_document:
                round_state.gaps_after = []
                state.gaps = []
                state.current_round = round_number
                self._persist_gaps(
                    state=state,
                    round_state=round_state,
                    closed_by_document_key=novelty_document,
                    closed_reason="single_reference_novelty_complete",
                )
                search_scope_complete = (
                    round_state.provider_complete and not state.analysis_failures
                )
                self._close_iteration(
                    round_state,
                    status="succeeded" if search_scope_complete else "partial",
                    stop_reason="single_reference_novelty_complete",
                    anchor_document_id=state.documents[novelty_document][
                        "repository_document_id"
                    ],
                )
                self._assert_round_closed(round_state)
                self._terminal_claim(
                    state,
                    ClaimStatus.NOVELTY_EVIDENCE_COMPLETE,
                    (
                        "一份日期合格文献单独完整披露全部必需特征"
                        if search_scope_complete
                        else "一份日期合格文献单独完整披露全部必需特征；"
                        "同轮其他检索/分析失败仅作为检索范围告警保留"
                    ),
                    {
                        "novelty_document_id": novelty_document,
                        "round": round_number,
                        "single_document_only": True,
                        "search_scope_complete": search_scope_complete,
                        "search_scope_warnings": [
                            *state.provider_failures,
                            *state.analysis_failures,
                        ],
                    },
                )
                self.save_checkpoint(investigation_id, checkpoint)
                return

            comparisons = [
                DocumentComparison.model_validate(value)
                for document_key, value in state.comparisons.items()
                if str(
                    (
                        state.documents.get(document_key, {}).get("record") or {}
                    ).get("stage")
                    or ""
                )
                == EvidenceStage.QUALIFIED.value
                and _comparison_matches_current_inputs(
                    state,
                    document_key,
                    target_source_sha256=checkpoint.target_source_sha256,
                )
            ]
            if not comparisons:
                state.current_round = round_number
                date_review_gaps = _pending_date_review_gaps(state)
                if (
                    date_review_gaps
                    and round_state.provider_complete
                    and not state.analysis_failures
                ):
                    state.gaps = _merge_raw_gap_frontier(
                        state.gaps,
                        date_review_gaps,
                    )
                    round_state.gaps_after = list(state.gaps)
                    self._persist_gaps(state=state, round_state=round_state)
                    self._close_iteration(
                        round_state,
                        status="succeeded",
                        stop_reason="candidate_date_evidence_needs_human_review",
                    )
                    self._assert_round_closed(round_state)
                    self._terminal_claim(
                        state,
                        ClaimStatus.NEEDS_HUMAN_REVIEW,
                        "候选文献已经取得真实内容，但公开日或日期来源链尚未核验",
                        {
                            "round": round_number,
                            "date_review_document_ids": sorted(
                                {
                                    document_id
                                    for gap in date_review_gaps
                                    for document_id in gap.get(
                                        "source_document_ids", []
                                    )
                                }
                            ),
                        },
                    )
                    self.save_checkpoint(investigation_id, checkpoint)
                    return
                round_state.gaps_after = list(state.gaps)
                self._persist_gaps(state=state, round_state=round_state)
                status = (
                    ClaimStatus.PARTIAL
                    if not round_state.provider_complete or state.analysis_failures
                    else ClaimStatus.EXHAUSTED
                )
                reason = (
                    "provider/模型未完整执行，不能把未命中写成检索穷尽"
                    if status is ClaimStatus.PARTIAL
                    else "完整执行的本轮没有取得可比对的合格文献"
                )
                self._close_iteration(
                    round_state,
                    status=("partial" if status is ClaimStatus.PARTIAL else "succeeded"),
                    stop_reason=(
                        "provider_or_analysis_incomplete"
                        if status is ClaimStatus.PARTIAL
                        else "no_qualified_comparable_document"
                    ),
                )
                self._assert_round_closed(round_state)
                self._terminal_claim(state, status, reason, {"round": round_number})
                self.save_checkpoint(investigation_id, checkpoint)
                return

            inventive_eligible = [
                comparison
                for comparison in comparisons
                if bool(
                    state.date_qualifications.get(comparison.document_id, {}).get(
                        "inventive_step_eligible"
                    )
                )
            ]
            if not inventive_eligible:
                provisional = _best_coverage_comparison(comparisons, _limitations(state))
                gaps, _, _ = generate_feature_gaps(
                    limitations=_limitations(state),
                    comparison=provisional,
                )
                state.gaps = [item.model_dump(mode="json") for item in gaps]
                round_state.gaps_after = list(state.gaps)
                state.current_round = round_number
                self._persist_gaps(state=state, round_state=round_state)
                if not round_state.provider_complete or state.analysis_failures:
                    self._close_iteration(
                        round_state,
                        status="partial",
                        stop_reason="no_inventive_eligible_d1_and_round_incomplete",
                    )
                    self._assert_round_closed(round_state)
                    self._terminal_claim(
                        state,
                        ClaimStatus.PARTIAL,
                        "没有创造性日期合格的 D1，且本轮执行不完整",
                        {"round": round_number},
                    )
                    self.save_checkpoint(investigation_id, checkpoint)
                    return
                if round_number >= claim_round_limit:
                    self._close_iteration(
                        round_state,
                        status="succeeded",
                        stop_reason="automatic_round_limit_without_inventive_d1",
                    )
                    self._assert_round_closed(round_state)
                    self._terminal_claim(
                        state,
                        ClaimStatus.EXHAUSTED,
                        "初始轮及五个 gap 检索轮已完成，仍没有创造性日期合格的 D1",
                        {"round": round_number, "unresolved_gaps": state.gaps},
                    )
                    self.save_checkpoint(investigation_id, checkpoint)
                    return
                self._close_iteration(
                    round_state,
                    status="succeeded",
                    stop_reason="continue_gap_search_without_inventive_d1",
                )
                self._assert_round_closed(round_state)
                self._set_claim_status(state, ClaimStatus.GAP_SEARCH)
                self.save_checkpoint(investigation_id, checkpoint)
                continue

            try:
                selection = select_closest_prior_art(
                    limitations=_limitations(state),
                    comparisons=comparisons,
                    date_qualifications=state.date_qualifications,
                )
                previous_score = float(
                    (state.current_closest_prior_art or {})
                    .get("score", {})
                    .get("total", 0)
                    or 0
                )
                selection_row = self._persist_closest_prior_art(
                    investigation_id=investigation_id,
                    state=state,
                    round_state=round_state,
                    selection=selection,
                )
            except Exception as exc:
                self._analysis_failed(
                    state,
                    round_state=round_state,
                    stage="I4_C_CLOSEST_PRIOR_ART",
                    error=exc,
                )
                round_state.gaps_after = list(state.gaps)
                self._persist_gaps(state=state, round_state=round_state)
                self._close_iteration(
                    round_state,
                    status="failed",
                    stop_reason="closest_prior_art_failed",
                )
                self._assert_round_closed(round_state)
                self._terminal_claim(
                    state,
                    ClaimStatus.FAILED,
                    "最接近现有技术选择或持久化失败",
                    {"round": round_number},
                )
                self.save_checkpoint(investigation_id, checkpoint)
                return
            selection_payload = selection.model_dump(mode="json")
            selection_payload["repository_selection_id"] = str(selection_row["id"])
            distinguishing_features = build_distinguishing_features(
                limitations=_limitations(state),
                d1_comparison=next(
                    item
                    for item in comparisons
                    if item.document_id == selection.document_id
                ),
            )
            large_claim_chart_documents = select_large_claim_chart_documents(
                limitations=_limitations(state),
                closest_document_id=selection.document_id,
                comparisons=comparisons,
                date_qualifications=state.date_qualifications,
                difference_feature_ids=[
                    item.feature_id for item in distinguishing_features
                ],
            )
            selection_payload["distinguishing_features"] = [
                item.model_dump(mode="json") for item in distinguishing_features
            ]
            selection_payload["large_claim_chart_document_ids"] = [
                item.document_id for item in large_claim_chart_documents
            ]
            if gap_reuse_decision is not None:
                selection_payload["gap_reuse_decision"] = (
                    gap_reuse_decision.model_dump(mode="json")
                    if isinstance(gap_reuse_decision, BaseModel)
                    else dict(gap_reuse_decision)
                )
            state.current_closest_prior_art = selection_payload
            round_state.closest_prior_art = selection_payload
            state.gaps = [item.model_dump(mode="json") for item in selection.gaps]
            # I2-G's pre-search decision can only see evidence frozen before the
            # current provider call.  Re-evaluate after every gap round so a
            # newly retrieved, date-qualified and current-version I4-S result
            # that closes every D1 difference stops the search immediately in
            # this same round.  Without this post-search check I0 created an
            # unnecessary following zero-query iteration before stopping.
            if round_number > 1 and distinguishing_features:
                current_comparisons, reuse_keys = _current_gap_reuse_materials(
                    state,
                    target_source_sha256=checkpoint.target_source_sha256,
                )
                post_search_gap_decision = evaluate_existing_corpus_gap_coverage(
                    differences=distinguishing_features,
                    comparisons=current_comparisons,
                    date_qualifications=state.date_qualifications,
                    reuse_keys=reuse_keys,
                    gap_search_iteration=min(5, round_number - 1),
                    previous_iteration_failure_reason=(
                        _previous_iteration_failure_reason(round_state)
                    ),
                )
                gap_reuse_decision = post_search_gap_decision
                selection_payload["gap_reuse_decision"] = (
                    post_search_gap_decision.model_dump(mode="json")
                )
                state.current_closest_prior_art = selection_payload
                round_state.closest_prior_art = selection_payload
                covered_ids = set(
                    post_search_gap_decision.covered_difference_feature_ids
                )
                state.gaps = [
                    gap
                    for gap in state.gaps
                    if not (
                        str(gap.get("kind") or "")
                        in {"feature_gap", "evidence_gap"}
                        and str(gap.get("feature_id") or "") in covered_ids
                    )
                ]
            self._set_claim_status(state, ClaimStatus.CLOSEST_PRIOR_ART_SELECTED)

            obviousness_precheck: ObviousnessPrecheckAssessment | None = None
            if distinguishing_features:
                self._set_claim_status(state, ClaimStatus.OBVIOUSNESS_PRECHECK)
                try:
                    obviousness_precheck = (
                        self.analysis_engine.analyze_obviousness_precheck(
                            differences=distinguishing_features,
                            closest_document_id=selection.document_id,
                            expanded_claim_text=(
                                claim.expanded_claim_text or claim.claim_text
                            ),
                            target_images=target_images,
                            document_text=_checkpoint_document_text(
                                state, selection.document_id
                            ),
                            document_images=_checkpoint_document_images(
                                state, selection.document_id
                            ),
                        )
                    )
                except Exception as exc:
                    self._analysis_failed(
                        state,
                        round_state=round_state,
                        stage="I4_O_OBVIOUSNESS_PRECHECK",
                        error=exc,
                    )
                if obviousness_precheck is not None:
                    round_state.obviousness_precheck = (
                        obviousness_precheck.model_dump(mode="json")
                    )
                    state.current_obviousness_precheck = (
                        obviousness_precheck.model_dump(mode="json")
                    )
                    try:
                        self._persist_obviousness_precheck(
                            investigation_id=investigation_id,
                            state=state,
                            round_state=round_state,
                            assessment=obviousness_precheck,
                        )
                    except Exception as exc:
                        self._analysis_failed(
                            state,
                            round_state=round_state,
                            stage="I4_O_OBVIOUSNESS_PRECHECK_PERSIST",
                            error=exc,
                        )
                    else:
                        resolved_ids = set(
                            obviousness_precheck.resolved_feature_ids
                        )
                        state.gaps = [
                            gap
                            for gap in state.gaps
                            if str(gap.get("feature_id") or "") not in resolved_ids
                        ]

            inventive_assessment: InventiveStepAssessment | None = None
            # I4-I must see the complete current, date-qualified I4-S corpus
            # from all completed gap rounds.  The analysis layer selects the
            # bounded D1+D2... combination and records both the considered
            # corpus and the documents actually used; truncating here used to
            # discard later-round evidence before the three-step analysis.
            combination_candidates = list(inventive_eligible)
            precheck_supports_d1_only_assessment = bool(
                obviousness_precheck
                and obviousness_precheck.evidence_complete
                and not obviousness_precheck.ordinary_structural_search_required
            )
            if combination_candidates and (
                round_number > 1
                or len(combination_candidates) >= 2
                or precheck_supports_d1_only_assessment
            ):
                self._set_claim_status(state, ClaimStatus.COMBINATION_REVIEW)
                try:
                    inventive_assessment = self.analysis_engine.analyze_inventive_step(
                        limitations=_limitations(state),
                        closest_document_id=selection.document_id,
                        comparisons=combination_candidates,
                        date_qualifications=state.date_qualifications,
                        expanded_claim_text=claim.expanded_claim_text or claim.claim_text,
                        target_images=target_images,
                        document_texts={
                            item.document_id: _checkpoint_document_text(state, item.document_id)
                            for item in combination_candidates
                        },
                        document_images={
                            item.document_id: _checkpoint_document_images(state, item.document_id)
                            for item in combination_candidates
                        },
                        obviousness_precheck=obviousness_precheck,
                        distinguishing_features=distinguishing_features,
                        upstream_gap_search_errors=_gap_search_failure_ledger(state),
                    )
                    if (
                        len(inventive_assessment.combination_document_ids) < 2
                        and not precheck_supports_d1_only_assessment
                    ):
                        inventive_assessment = inventive_assessment.model_copy(
                            update={
                                "combination_basis": "unresolved",
                                "evidence_complete": False,
                                "conclusion": (
                                    "insufficient_evidence_to_establish_lack_of_inventive_step"
                                ),
                                "conclusion_text": (
                                    "现有证据尚不足以证明不具备创造性"
                                ),
                            }
                        )
                    if obviousness_precheck is not None:
                        inventive_assessment = inventive_assessment.model_copy(
                            update={
                                "obviousness_precheck": obviousness_precheck,
                                "combination_basis": (
                                    "d1_plus_common_knowledge"
                                    if len(
                                        inventive_assessment.combination_document_ids
                                    ) == 1
                                    and any(
                                        group.search_route
                                        in {
                                            "skip_structural_gap_search",
                                            "search_common_knowledge_evidence",
                                        }
                                        for group in obviousness_precheck.feature_groups
                                    )
                                    else "multiple_prior_art_documents"
                                    if len(
                                        inventive_assessment.combination_document_ids
                                    ) >= 2
                                    else "unresolved"
                                ),
                            }
                        )
                except Exception as exc:
                    self._analysis_failed(
                        state,
                        round_state=round_state,
                        stage="I4_I_INVENTIVE_STEP",
                        error=exc,
                    )
                if inventive_assessment is not None:
                    round_state.combination = inventive_assessment.model_dump(mode="json")
                    try:
                        self._persist_combination(
                            investigation_id=investigation_id,
                            state=state,
                            round_state=round_state,
                            assessment=inventive_assessment,
                            comparison_ids=inventive_assessment.combination_document_ids,
                        )
                    except Exception as exc:
                        self._analysis_failed(
                            state,
                            round_state=round_state,
                            stage="I4_I_INVENTIVE_STEP_PERSIST",
                            error=exc,
                        )
                        inventive_assessment = None
                        round_state.combination = None
                    else:
                        resolved_feature_ids = (
                            {
                                item.feature_id
                                for item in inventive_assessment.feature_coverage
                                if item.covered
                            }
                            if inventive_assessment.combination_basis != "unresolved"
                            else set()
                        )
                        if gap_reuse_decision is not None:
                            decision_snapshot = (
                                gap_reuse_decision.model_dump(mode="json")
                                if isinstance(gap_reuse_decision, BaseModel)
                                else dict(gap_reuse_decision)
                            )
                            resolved_feature_ids.update(
                                str(item)
                                for item in decision_snapshot.get(
                                    "covered_difference_feature_ids", []
                                )
                                if str(item).strip()
                            )
                        state.gaps = _merge_gaps(
                            selection.gaps,
                            inventive_assessment.gaps,
                            resolved_feature_ids=resolved_feature_ids,
                        )

            state.gaps = _merge_raw_gap_frontier(
                state.gaps,
                _pending_date_review_gaps(
                    state,
                    require_substantive_disclosure=True,
                    target_source_sha256=checkpoint.target_source_sha256,
                ),
            )
            round_state.gaps_after = list(state.gaps)
            state.current_round = round_number
            self._persist_gaps(state=state, round_state=round_state)
            complete_combination = bool(
                inventive_assessment and inventive_assessment.evidence_complete
            )
            progress = RoundProgress(
                qualified_documents_added=round_state.qualified_documents_added,
                uncovered_before=len(_gap_feature_ids(round_state.gaps_before)),
                uncovered_after=len(_gap_feature_ids(round_state.gaps_after)),
                combination_gaps_before=len(_combination_gap_ids(round_state.gaps_before)),
                combination_gaps_after=len(_combination_gap_ids(round_state.gaps_after)),
                closest_prior_art_improved=selection.score.total > previous_score,
                provider_complete=round_state.provider_complete and not state.analysis_failures,
            )
            next_status = next_round_or_stop(
                current_round=round_number,
                max_rounds=claim_round_limit,
                progress=progress,
                has_complete_inventive_combination=complete_combination,
            )
            decisive_date_gaps = [
                item
                for item in state.gaps
                if str(item.get("kind") or "") == "date_gap"
            ]
            if (
                next_status is ClaimStatus.EXHAUSTED
                and decisive_date_gaps
                and progress.provider_complete
            ):
                self._close_iteration(
                    round_state,
                    status="succeeded",
                    stop_reason="candidate_date_evidence_needs_human_review",
                    anchor_document_id=state.documents[selection.document_id][
                        "repository_document_id"
                    ],
                )
                self._assert_round_closed(round_state)
                self._terminal_claim(
                    state,
                    ClaimStatus.NEEDS_HUMAN_REVIEW,
                    "至少一份具有实质披露的候选文献仍缺少可核验日期证据",
                    {
                        "round": round_number,
                        "date_review_document_ids": sorted(
                            {
                                document_id
                                for gap in decisive_date_gaps
                                for document_id in gap.get(
                                    "source_document_ids", []
                                )
                            }
                        ),
                    },
                )
                self.save_checkpoint(investigation_id, checkpoint)
                return
            if next_status is ClaimStatus.INVENTIVE_STEP_EVIDENCE_COMPLETE:
                self._close_iteration(
                    round_state,
                    status="succeeded" if progress.provider_complete else "partial",
                    stop_reason="inventive_step_evidence_complete",
                    anchor_document_id=state.documents[selection.document_id][
                        "repository_document_id"
                    ],
                )
                self._assert_round_closed(round_state)
                self._terminal_claim(
                    state,
                    next_status,
                    "D1 与后续文献通过独立创造性组合证据守门",
                    {
                        "round": round_number,
                        "closest_prior_art_document_id": selection.document_id,
                        "combination": round_state.combination,
                    },
                )
                self.save_checkpoint(investigation_id, checkpoint)
                return
            if (
                gap_reuse_decision is not None
                and not bool(
                    (
                        gap_reuse_decision.model_dump(mode="json")
                        if isinstance(gap_reuse_decision, BaseModel)
                        else dict(gap_reuse_decision)
                    ).get("search_required", True)
                )
                and str(
                    (
                        gap_reuse_decision.model_dump(mode="json")
                        if isinstance(gap_reuse_decision, BaseModel)
                        else dict(gap_reuse_decision)
                    ).get("stop_reason")
                    or ""
                )
                == "all_differences_covered"
                and next_status is not ClaimStatus.PARTIAL
            ):
                decision_snapshot = (
                    gap_reuse_decision.model_dump(mode="json")
                    if isinstance(gap_reuse_decision, BaseModel)
                    else dict(gap_reuse_decision)
                )
                search_skipped = not bool(plan.queries)
                self._close_iteration(
                    round_state,
                    status="succeeded",
                    stop_reason=(
                        "existing_corpus_covered_all_differences"
                        if search_skipped
                        else "new_gap_evidence_covered_all_differences"
                    ),
                    anchor_document_id=state.documents[selection.document_id][
                        "repository_document_id"
                    ],
                )
                self._assert_round_closed(round_state)
                self._terminal_claim(
                    state,
                    ClaimStatus.EXHAUSTED,
                    (
                        "D1 区别特征已由既有日期合格文献以相同结构角色/作用覆盖；"
                        "未新增检索，并已完成可运行的组合分析与大 Claim Chart"
                        if search_skipped
                        else "本轮新取得的日期合格文献已以相同结构角色/作用覆盖全部 "
                        "D1 区别特征；立即停止后续 gap 检索，并已完成可运行的组合分析"
                        "与大 Claim Chart"
                    ),
                    {
                        "round": round_number,
                        "gap_search_iteration": round_number - 1,
                        "gap_reuse_decision": decision_snapshot,
                        "combination": round_state.combination,
                        "large_claim_chart_document_ids": (
                            state.current_closest_prior_art.get(
                                "large_claim_chart_document_ids", []
                            )
                            if state.current_closest_prior_art
                            else []
                        ),
                    },
                )
                self.save_checkpoint(investigation_id, checkpoint)
                return
            if (
                next_status is ClaimStatus.PARTIAL
                and round_number > 1
                and round_number < claim_round_limit
            ):
                self._close_iteration(
                    round_state,
                    status="partial",
                    stop_reason="round_error_preserved_continue_gap_search",
                    anchor_document_id=state.documents[selection.document_id][
                        "repository_document_id"
                    ],
                )
                self._assert_round_closed(round_state)
                self._set_claim_status(state, ClaimStatus.GAP_SEARCH)
                self.save_checkpoint(investigation_id, checkpoint)
                continue
            if next_status in {ClaimStatus.EXHAUSTED, ClaimStatus.PARTIAL}:
                gap_search_exhausted = bool(
                    round_number >= 1 + MAX_AUTOMATIC_GAP_SEARCH_ITERATIONS
                )
                reason = (
                    "五个 gap 检索轮已完成；保留各轮错误和未覆盖区别特征，并已基于现有真实文献输出组合分析与大 Claim Chart"
                    if gap_search_exhausted
                    else "达到显式轮次上限，保留未解决 gap"
                    if next_status is ClaimStatus.EXHAUSTED
                    else "provider/模型执行不完整，不能写成穷尽或成功"
                )
                if gap_search_exhausted and state.current_closest_prior_art:
                    remaining_gap_ids = _gap_feature_ids(state.gaps)
                    previous_gap_decision = dict(
                        state.current_closest_prior_art.get("gap_reuse_decision")
                        or {}
                    )
                    previous_gap_decision.update(
                        {
                            "gap_search_iteration": 5,
                            "uncovered_difference_feature_ids": remaining_gap_ids,
                            "search_required": False,
                            "gap_search_exhausted": True,
                            "stop_reason": "gap_search_exhausted",
                            "previous_iteration_failure_reason": (
                                "第五个 gap 检索轮结束后仍有区别特征未覆盖"
                            ),
                        }
                    )
                    state.current_closest_prior_art["gap_reuse_decision"] = (
                        previous_gap_decision
                    )
                self._close_iteration(
                    round_state,
                    status=(
                        "partial" if next_status is ClaimStatus.PARTIAL else "succeeded"
                    ),
                    stop_reason=(
                        "gap_search_exhausted_with_errors"
                        if gap_search_exhausted
                        and next_status is ClaimStatus.PARTIAL
                        else "provider_or_analysis_incomplete"
                        if next_status is ClaimStatus.PARTIAL
                        else "automatic_round_limit_or_no_progress"
                    ),
                    anchor_document_id=state.documents[selection.document_id][
                        "repository_document_id"
                    ],
                )
                self._assert_round_closed(round_state)
                self._terminal_claim(
                    state,
                    next_status,
                    reason,
                    {
                        "round": round_number,
                        "unresolved_gaps": state.gaps,
                        "closest_prior_art_document_id": selection.document_id,
                        "gap_search_exhausted": gap_search_exhausted,
                        "gap_reuse_decision": (
                            state.current_closest_prior_art.get(
                                "gap_reuse_decision"
                            )
                            if state.current_closest_prior_art
                            else None
                        ),
                        "large_claim_chart_document_ids": (
                            state.current_closest_prior_art.get(
                                "large_claim_chart_document_ids", []
                            )
                            if state.current_closest_prior_art
                            else []
                        ),
                    },
                )
                self.save_checkpoint(investigation_id, checkpoint)
                return
            self._close_iteration(
                round_state,
                status="succeeded",
                stop_reason="continue_gap_search",
                anchor_document_id=state.documents[selection.document_id][
                    "repository_document_id"
                ],
            )
            self._assert_round_closed(round_state)
            self._set_claim_status(state, ClaimStatus.GAP_SEARCH)
            self.save_checkpoint(investigation_id, checkpoint)

    def _prepare_round_candidates(
        self,
        *,
        investigation_id: str,
        claim: ClaimSnapshot,
        state: ClaimCheckpoint,
        round_state: RoundCheckpoint,
        plan: QueryPlan,
        round_number: int,
        critical_date: date,
        target_publication_date: date | None,
        target_publication_date_verified: bool,
        target_images: Sequence[str],
        heartbeat: Callable[[], None] | None,
    ) -> dict[str, dict[str, Any]]:
        """Discover every query, persist one I3-F run, then return fetch queues."""

        pending_queries = [
            query
            for query in plan.queries
            if round_state.query_statuses.get(query.query_id)
            not in {"completed", "partial", "failed", "skipped"}
        ]
        if not pending_queries:
            return {}

        split_gateway = callable(getattr(self.evidence_gateway, "discover", None)) and callable(
            getattr(self.evidence_gateway, "retrieve", None)
        )
        contexts: dict[str, dict[str, Any]] = {}
        search_outputs: list[dict[str, Any]] = []
        for query in pending_queries:
            if heartbeat:
                heartbeat()
            search_module_run = self._module_run(
                investigation_id=investigation_id,
                state=state,
                round_state=round_state,
                module_code=(
                    "I3_PATENT_SEARCH"
                    if query.provider_kind == "patent"
                    else "I3_NPL_SEARCH"
                ),
                idempotency_key=(
                    f"{state.claim_id}:{round_number}:search:{query.query_id}"
                ),
                input_snapshot={
                    "query": query.model_dump(mode="json"),
                    "critical_date": critical_date.isoformat(),
                    "target_publication_date": (
                        target_publication_date.isoformat()
                        if target_publication_date
                        else None
                    ),
                    "target_publication_date_verified": (
                        target_publication_date_verified
                    ),
                    "discovery_only": split_gateway,
                },
                model_version=None,
            )
            try:
                if split_gateway:
                    raw_discovery = self.evidence_gateway.discover(
                        query,
                        investigation_id=investigation_id,
                        claim_id=claim.claim_id,
                        iteration_number=round_number,
                        critical_date=critical_date,
                        target_publication_date=target_publication_date,
                        target_publication_date_verified=(
                            target_publication_date_verified
                        ),
                    )
                else:
                    raw_discovery = self.evidence_gateway.search(
                        query,
                        investigation_id=investigation_id,
                        claim_id=claim.claim_id,
                        iteration_number=round_number,
                        critical_date=critical_date,
                        target_publication_date=target_publication_date,
                        target_publication_date_verified=(
                            target_publication_date_verified
                        ),
                    )
                discovery_batch = _coerce_search_batch(
                    raw_discovery, query.provider_kind
                )
            except Exception as exc:
                discovery_batch = SearchBatch(
                    provider=query.provider_kind,
                    complete=False,
                    errors=(f"{type(exc).__name__}: {_safe_error(exc)}",),
                )
            if not discovery_batch.complete:
                round_state.provider_complete = False
            for error in discovery_batch.errors:
                failure = (
                    f"round={round_number} query={query.query_id} "
                    f"provider={discovery_batch.provider}: {error}"
                )
                _append_unique(round_state.provider_failures, failure)
                _append_unique(state.provider_failures, failure)
            search_status = (
                "succeeded"
                if discovery_batch.complete
                else ("partial" if discovery_batch.records else "failed")
            )
            self._finish_module_run(
                round_state,
                search_module_run,
                status=search_status,
                actual_provider=discovery_batch.provider,
                network_used=discovery_batch.network_used,
                output_snapshot={
                    "provider": discovery_batch.provider,
                    "network_used": discovery_batch.network_used,
                    "complete": discovery_batch.complete,
                    "discovery_only": split_gateway,
                    "errors": list(discovery_batch.errors),
                    "search_artifacts": [
                        {
                            "artifact_type": item.get("artifact_type"),
                            "sha256": item.get("sha256"),
                            "byte_size": item.get("byte_size"),
                            "mime_type": item.get("mime_type"),
                            "retrieved_at": item.get("retrieved_at"),
                        }
                        for item in discovery_batch.artifacts
                    ],
                    "records": [
                        item.model_dump() for item in discovery_batch.records
                    ],
                },
                error=(
                    WorkflowExecutionError("; ".join(discovery_batch.errors))
                    if search_status == "failed"
                    else None
                ),
            )
            run_id = str(search_module_run["id"])
            contexts[query.query_id] = {
                "query": query,
                "search_module_run": search_module_run,
                "discovery_batch": discovery_batch,
                "split_gateway": split_gateway,
                "selected_records": [],
                "selected_candidate_ids": [],
            }
            search_outputs.append(
                {
                    "search_run_id": run_id,
                    "query_id": query.query_id,
                    "query_text": query.provider_expression or query.expression,
                    "provider_kind": query.provider_kind,
                    "documents": [
                        item.model_dump() for item in discovery_batch.records
                    ],
                }
            )

        filter_run = self._module_run(
            investigation_id=investigation_id,
            state=state,
            round_state=round_state,
            module_code="I3_CANDIDATE_FILTER",
            idempotency_key=f"{state.claim_id}:{round_number}:candidate-filter",
            input_snapshot={
                "source_search_run_ids": [
                    item["search_run_id"] for item in search_outputs
                ],
                "query_plan_sha256": stable_json_sha256(
                    plan.model_dump(mode="json")
                ),
                "top_n_per_search": 10,
                "prefetch_filter_required": split_gateway,
            },
            model_version=getattr(
                getattr(self.analysis_engine, "llm_client", None),
                "model",
                None,
            ),
        )
        raw_candidates = build_raw_candidates(
            search_outputs, top_n_per_search=10
        )
        groups, duplicates = group_candidates(raw_candidates)
        filter_context = target_filter_context(plan.model_dump(mode="json"))
        classifiable_groups = [
            group
            for group in groups
            if isinstance(group.get("representative_document"), Mapping)
            and metadata_is_sufficient(group["representative_document"])
            and not matched_positive_anchors(
                group["representative_document"], filter_context
            )
            and not matched_technical_terms(
                group["representative_document"], filter_context
            )
        ]
        model_attempted = False
        model_failure: str | None = None
        decisions: dict[str, dict[str, Any]] = {}
        audit_artifact: Mapping[str, Any] | None = None
        llm_client = getattr(self.analysis_engine, "llm_client", None)
        if classifiable_groups and llm_client is not None:
            model_attempted = True
            try:
                invoke_kwargs: dict[str, Any] = {}
                if isinstance(llm_client, VisionLLMClient):
                    invoke_kwargs = {
                        "request_timeout_seconds": min(
                            120.0,
                            float(getattr(llm_client, "timeout_seconds", 120.0)),
                        ),
                        "direct_attempt_timeout_seconds": min(
                            90.0,
                            float(
                                getattr(
                                    llm_client,
                                    "direct_attempt_timeout_seconds",
                                    90.0,
                                )
                            ),
                        ),
                    }
                decisions, audit = invoke_semantic_candidate_filter(
                    llm_client,
                    target_context=filter_context,
                    candidates=classifiable_groups,
                    target_images=list(target_images[:1]),
                    **invoke_kwargs,
                )
                audit_artifact = self._persist_candidate_filter_audit(
                    investigation_id=investigation_id,
                    module_run=filter_run,
                    audit=audit,
                )
            except Exception as exc:
                model_failure = (
                    str(getattr(exc, "reason_code", "") or "").strip()
                    or type(exc).__name__
                )
                try:
                    audit_artifact = self._persist_candidate_filter_audit(
                        investigation_id=investigation_id,
                        module_run=filter_run,
                        audit={
                            "model": str(
                                getattr(llm_client, "model", "") or ""
                            ),
                            "candidate_count": len(classifiable_groups),
                            "failure_type": type(exc).__name__,
                            "reason_code": str(
                                getattr(exc, "reason_code", "") or ""
                            ),
                            "message": _safe_error(exc),
                            "fail_open": True,
                        },
                    )
                except Exception:
                    audit_artifact = None
        elif classifiable_groups:
            model_failure = "semantic_model_unavailable"

        screened = apply_filter_decisions(
            groups,
            target_context=filter_context,
            model_decisions=decisions,
            model_failure=model_failure,
        )
        raw_by_candidate_id = {
            str(item.get("candidate_id") or ""): item
            for item in raw_candidates
        }
        for selected in screened["fetch_candidates"]:
            candidate_id = str(selected.get("candidate_id") or "")
            raw = raw_by_candidate_id.get(candidate_id) or {}
            query_id = str(raw.get("query_id") or "")
            rank = int(raw.get("source_rank") or 0)
            context = contexts.get(query_id)
            discovery_batch = (
                context.get("discovery_batch")
                if isinstance(context, Mapping)
                else None
            )
            records = (
                discovery_batch.records
                if isinstance(discovery_batch, SearchBatch)
                else ()
            )
            if context is None or rank < 1 or rank > len(records):
                continue
            context["selected_records"].append(records[rank - 1])
            context["selected_candidate_ids"].append(candidate_id)

        decision_sha256 = stable_json_sha256(
            {
                "candidate_groups": screened["candidate_groups"],
                "duplicate_candidates": duplicates,
                "excluded_candidates": screened["excluded_candidates"],
                "fetch_candidates": screened["fetch_candidates"],
            }
        )
        output = {
            "filter_contract_version": FILTER_CONTRACT_VERSION,
            "identity_rule_version": IDENTITY_RULE_VERSION,
            "semantic_rule_version": SEMANTIC_RULE_VERSION,
            "source_search_run_ids": [
                item["search_run_id"] for item in search_outputs
            ],
            "raw_candidate_count": len(raw_candidates),
            "unique_group_count": len(groups),
            "duplicate_count": len(duplicates),
            "excluded_count": len(screened["excluded_candidates"]),
            "fetch_candidate_count": len(screened["fetch_candidates"]),
            "prefetch_filter_applied": split_gateway,
            "raw_candidates": raw_candidates,
            "candidate_groups": screened["candidate_groups"],
            "duplicate_candidates": duplicates,
            "excluded_candidates": screened["excluded_candidates"],
            "fetch_candidates": screened["fetch_candidates"],
            "target_context": filter_context,
            "model_attempted": model_attempted,
            "model_failure": model_failure,
            "model_audit_artifact": audit_artifact,
            "input_sha256": stable_json_sha256(
                {
                    "search_outputs": search_outputs,
                    "target_context": filter_context,
                }
            ),
            "decision_sha256": decision_sha256,
        }
        output["audit_sha256"] = stable_json_sha256(
            {
                "filter_contract_version": FILTER_CONTRACT_VERSION,
                "identity_rule_version": IDENTITY_RULE_VERSION,
                "semantic_rule_version": SEMANTIC_RULE_VERSION,
                "input_sha256": output["input_sha256"],
                "decision_sha256": decision_sha256,
                "model_failure": model_failure,
            }
        )
        self._finish_module_run(
            round_state,
            filter_run,
            status="succeeded",
            output_snapshot=output,
            actual_provider=(
                "glm_semantic_candidate_filter"
                if model_attempted and not model_failure
                else (
                    "deterministic_fail_open"
                    if model_failure
                    else "deterministic_candidate_filter"
                )
            ),
            network_used=model_attempted,
        )
        return contexts

    def _persist_candidate_filter_audit(
        self,
        *,
        investigation_id: str,
        module_run: Mapping[str, Any],
        audit: Mapping[str, Any],
    ) -> Mapping[str, Any] | None:
        artifact_store = getattr(self.evidence_gateway, "artifact_store", None)
        if not isinstance(artifact_store, ArtifactStore):
            return None
        run_id = str(module_run.get("id") or "")
        artifact = artifact_store.write_json(
            investigation_id,
            f"model-responses/i3-candidate-filter/i0-{run_id}.json",
            dict(audit),
            artifact_type="i3_candidate_filter_audit",
        )
        artifact_id: str | None = None
        inserter = getattr(self.repository, "insert_artifact", None)
        if callable(inserter):
            row = inserter(
                investigation_id=investigation_id,
                module_run_id=run_id or None,
                artifact_type=artifact.artifact_type,
                uri=artifact.uri,
                sha256=artifact.sha256,
                mime_type=artifact.mime_type,
                byte_size=artifact.byte_size,
                metadata={
                    "stage": "I3_CANDIDATE_FILTER",
                    "prompt_sha256": str(audit.get("prompt_sha256") or ""),
                    "model": str(audit.get("model") or ""),
                    "candidate_count": int(audit.get("candidate_count") or 0),
                    "module_lab": False,
                },
                actor="I0",
            )
            artifact_id = str(row.get("id") or "") or None
        return {
            "artifact_id": artifact_id,
            "artifact_type": artifact.artifact_type,
            "sha256": artifact.sha256,
            "byte_size": artifact.byte_size,
            "prompt_sha256": str(audit.get("prompt_sha256") or ""),
        }

    def _round_plan(
        self,
        *,
        investigation_id: str,
        patent: PatentSnapshot,
        claim: ClaimSnapshot,
        state: ClaimCheckpoint,
        round_state: RoundCheckpoint,
        target_images: Sequence[str],
        gap_reuse_decision: Any = None,
        gap_distinguishing_features: Sequence[Any] = (),
    ) -> QueryPlan:
        if round_state.plan:
            return QueryPlan.model_validate(round_state.plan)
        existing = _limitations(state)
        is_gap_round = round_state.iteration_number > 1
        decision_payload = (
            gap_reuse_decision.model_dump(mode="json")
            if isinstance(gap_reuse_decision, BaseModel)
            else dict(gap_reuse_decision)
            if isinstance(gap_reuse_decision, Mapping)
            else {}
        )
        gap_ids = (
            list(decision_payload.get("uncovered_difference_feature_ids") or [])
            if decision_payload
            else _gap_feature_ids(state.gaps)
        )
        precheck_payload = (
            dict(state.current_obviousness_precheck)
            if isinstance(state.current_obviousness_precheck, Mapping)
            else {}
        )
        precheck_routes: dict[str, str] = {}
        for raw_target in precheck_payload.get("search_targets") or []:
            if not isinstance(raw_target, Mapping):
                continue
            route = str(raw_target.get("route") or "")
            for feature_id in raw_target.get("feature_ids") or []:
                if str(feature_id).strip():
                    precheck_routes[str(feature_id)] = route
        human_review_feature_ids: list[str] = []
        if is_gap_round and precheck_payload and decision_payload:
            routed_decision, gap_ids, human_review_feature_ids = (
                apply_obviousness_routes_to_gap_decision(
                    decision=GapSearchDecision.model_validate(decision_payload),
                    precheck=ObviousnessPrecheckAssessment.model_validate(
                        precheck_payload
                    ),
                )
            )
            decision_payload = routed_decision.model_dump(mode="json")
        previous_round = state.rounds.get(
            str(round_state.iteration_number - 1)
        )
        previous_plan = (
            QueryPlan.model_validate(previous_round.plan)
            if previous_round is not None and previous_round.plan
            else None
        )
        previous_query_expressions = (
            [item.expression for item in previous_plan.queries]
            if previous_plan is not None
            else []
        )
        previous_failure_reason = str(
            decision_payload.get("previous_iteration_failure_reason")
            or _previous_iteration_failure_reason(previous_round)
            or ""
        ).strip()
        reuse_evidence = list(decision_payload.get("coverage_evidence") or [])
        gap_strategy = (
            gap_search_strategy_metadata(min(5, round_state.iteration_number - 1))
            if is_gap_round
            else None
        )
        input_snapshot = {
            "claim_id": claim.claim_id,
            "expanded_claim_text": claim.expanded_claim_text or claim.claim_text,
            "target_patent_source_sha256": patent.source_sha256,
            "iteration_number": round_state.iteration_number,
            "round_kind": "gap" if is_gap_round else "initial",
            "gap_feature_ids": gap_ids,
            "gap_types": [str(item.get("kind") or "") for item in state.gaps],
            "closest_prior_art_context": state.current_closest_prior_art,
            "distinguishing_features": [
                item.model_dump(mode="json")
                if isinstance(item, BaseModel)
                else dict(item)
                for item in gap_distinguishing_features
                if isinstance(item, (BaseModel, Mapping))
            ],
            "gap_reuse_decision": decision_payload or None,
            "obviousness_precheck": precheck_payload or None,
            "previous_iteration_failure_reason": previous_failure_reason,
            "previous_query_expressions": previous_query_expressions,
            "gap_search_strategy": gap_strategy,
            "target_image_count": len(target_images),
        }
        profile_run: Mapping[str, Any] | None = None
        profile_plan: QueryPlan | None = None
        if not is_gap_round:
            # 自动流程与实验室链路一致：首轮先由独立的 I2_INVENTIVE_PROFILE
            # 运行总结核心发明点，I2_QUERY_PLAN 只消费该画像输出生成检索词，
            # 不再在同一 GLM 调用内自行通读全文生成画像。
            profile_run = self._module_run(
                investigation_id=investigation_id,
                state=state,
                round_state=round_state,
                module_code="I2_INVENTIVE_PROFILE",
                idempotency_key=(
                    f"{state.claim_id}:{round_state.iteration_number}:inventive-profile"
                ),
                input_snapshot=input_snapshot,
                model_version=str(
                    getattr(
                        getattr(self.analysis_engine, "llm_client", None),
                        "model",
                        "unknown",
                    )
                ),
            )
            try:
                profile_plan = self.analysis_engine.generate_inventive_profile(
                    claim_id=claim.claim_id,
                    expanded_claim_text=claim.expanded_claim_text or claim.claim_text,
                    target_images=target_images,
                    patent_context=patent,
                    iteration_number=round_state.iteration_number,
                    round_kind="initial",
                    existing_limitations=existing,
                )
            except Exception as exc:
                profile_audit = self._persist_i2_model_response(
                    investigation_id=investigation_id,
                    module_run=profile_run,
                    claim_id=claim.claim_id,
                    iteration_number=round_state.iteration_number,
                    stage="I2_INVENTIVE_PROFILE",
                )
                self._finish_module_run(
                    round_state,
                    profile_run,
                    status="failed",
                    output_snapshot={
                        "input": input_snapshot,
                        "model_response_audit": profile_audit,
                    },
                    error=exc,
                )
                raise
            profile_audit = self._persist_i2_model_response(
                investigation_id=investigation_id,
                module_run=profile_run,
                claim_id=claim.claim_id,
                iteration_number=round_state.iteration_number,
                stage="I2_INVENTIVE_PROFILE",
            )
            self._finish_module_run(
                round_state,
                profile_run,
                status="succeeded",
                output_snapshot={
                    **profile_plan.model_dump(mode="json"),
                    "model_response_audit": profile_audit,
                },
            )
        module_code = "I2_GAP_QUERY_PLAN" if is_gap_round else "I2_QUERY_PLAN"
        module_run = self._module_run(
            investigation_id=investigation_id,
            state=state,
            round_state=round_state,
            module_code=module_code,
            idempotency_key=(
                f"{state.claim_id}:{round_state.iteration_number}:gap-query-plan"
                if is_gap_round
                else f"{state.claim_id}:{round_state.iteration_number}:query-plan"
            ),
            input_snapshot=input_snapshot,
            model_version=str(
                getattr(
                    getattr(self.analysis_engine, "llm_client", None),
                    "model",
                    "unknown",
                )
            ),
        )
        try:
            if is_gap_round and decision_payload and not bool(
                decision_payload.get("search_required", True)
            ):
                initial_round = state.rounds.get("1")
                if initial_round is None or not initial_round.plan:
                    raise AnalysisValidationError(
                        "既有文献已覆盖全部区别特征，但缺少首轮 I2 画像，"
                        "无法形成可审计的零查询 I2-G 计划"
                    )
                base_plan = QueryPlan.model_validate(initial_round.plan)
                assert gap_strategy is not None
                plan = base_plan.model_copy(
                    update={
                        "iteration_number": round_state.iteration_number,
                        "round_kind": "gap",
                        "queries": [],
                        "rejected_queries": [],
                        "model": (
                            "deterministic-obviousness-precheck"
                            if precheck_payload.get("resolved_feature_ids")
                            else "deterministic-existing-corpus-reuse"
                        ),
                        "generation_source": (
                            "obviousness_precheck_resolved"
                            if precheck_payload.get("resolved_feature_ids")
                            else "existing_corpus_reuse"
                        ),
                        "gap_search_iteration": min(
                            5, round_state.iteration_number - 1
                        ),
                        "gap_search_strategy": gap_strategy["code"],
                        "gap_search_strategy_label": gap_strategy["label"],
                        "gap_search_strategy_description": gap_strategy[
                            "description"
                        ],
                        "covered_difference_feature_ids": list(
                            decision_payload.get(
                                "covered_difference_feature_ids"
                            )
                            or []
                        ),
                        "uncovered_difference_feature_ids": list(
                            decision_payload.get(
                                "uncovered_difference_feature_ids"
                            )
                            or []
                        ),
                        "existing_corpus_reuse": reuse_evidence,
                        "previous_iteration_failure_reason": (
                            previous_failure_reason
                        ),
                        "portfolio_warnings": [
                            *base_plan.portfolio_warnings,
                            "I4-O预分析与既有证据已关闭普通结构检索目标；"
                            "本轮不生成结构查询，直接进入相应补证或组合评价。",
                        ],
                    }
                )
            elif is_gap_round:
                plan = self.analysis_engine.plan_queries(
                    claim_id=claim.claim_id,
                    expanded_claim_text=claim.expanded_claim_text or claim.claim_text,
                    target_images=target_images,
                    patent_context=patent,
                    iteration_number=round_state.iteration_number,
                    round_kind="gap" if is_gap_round else "initial",
                    existing_limitations=existing,
                    invention_search_profile=(
                        QueryPlan.model_validate(state.rounds["1"].plan).invention_search_profile
                        if is_gap_round
                        and state.rounds.get("1") is not None
                        and state.rounds["1"].plan
                        else None
                    ),
                    gap_feature_ids=gap_ids,
                    closest_prior_art_context={
                        **(state.current_closest_prior_art or {}),
                        "obviousness_precheck": precheck_payload or None,
                    },
                    gap_types=(
                        list(
                            dict.fromkeys(
                                "combination_gap"
                                if precheck_routes.get(feature_id)
                                == "search_combination_evidence"
                                else (
                                    "evidence_gap"
                                    if precheck_routes.get(feature_id)
                                    == "search_common_knowledge_evidence"
                                    else "feature_gap"
                                )
                                for feature_id in gap_ids
                            )
                        )
                        if precheck_payload
                        else [str(item.get("kind") or "") for item in state.gaps]
                    ),
                    covered_difference_feature_ids=list(
                        decision_payload.get("covered_difference_feature_ids") or []
                    ),
                    existing_corpus_reuse=reuse_evidence,
                    previous_iteration_failure_reason=previous_failure_reason,
                    previous_query_expressions=previous_query_expressions,
                )
            else:
                assert profile_plan is not None and profile_run is not None
                plan = self.analysis_engine.generate_queries_from_inventive_profile(
                    previous_plan=profile_plan,
                    source_plan_run_id=str(profile_run.get("id") or ""),
                    source_sha256=profile_plan.source_sha256,
                )
        except Exception as exc:
            audit_artifact = (
                self._persist_i2_model_response(
                    investigation_id=investigation_id,
                    module_run=module_run,
                    claim_id=claim.claim_id,
                    iteration_number=round_state.iteration_number,
                )
                if not (
                    is_gap_round
                    and decision_payload
                    and not bool(decision_payload.get("search_required", True))
                )
                else None
            )
            self._finish_module_run(
                round_state,
                module_run,
                status="failed",
                output_snapshot={
                    "input": input_snapshot,
                    "model_response_audit": audit_artifact,
                },
                error=exc,
            )
            raise
        if is_gap_round:
            plan = bind_gap_search_strategy(
                plan,
                gap_search_iteration=min(5, round_state.iteration_number - 1),
            )
        audit_artifact = (
            self._persist_i2_model_response(
                investigation_id=investigation_id,
                module_run=module_run,
                claim_id=claim.claim_id,
                iteration_number=round_state.iteration_number,
            )
            if plan.generation_source != "existing_corpus_reuse"
            else None
        )
        gap_output = (
            {
                "distinguishing_features": input_snapshot[
                    "distinguishing_features"
                ],
                "gap_reuse_decision": decision_payload or None,
                "allowed_gap_feature_ids": gap_ids,
                "human_review_feature_ids": human_review_feature_ids,
                "closest_document_id": str(
                    (state.current_closest_prior_art or {}).get("document_id")
                    or ""
                ),
            }
            if is_gap_round
            else {}
        )
        self._finish_module_run(
            round_state,
            module_run,
            status="succeeded",
            output_snapshot={
                **plan.model_dump(mode="json"),
                **gap_output,
                "model_response_audit": audit_artifact,
            },
        )
        round_state.plan = plan.model_dump(mode="json")
        return plan

    def _persist_i2_model_response(
        self,
        *,
        investigation_id: str,
        module_run: Mapping[str, Any],
        claim_id: str,
        iteration_number: int,
        stage: str = "I2_QUERY_PLAN",
    ) -> dict[str, Any] | None:
        """Keep the complete model response local while publishing only its hash."""

        audit = getattr(self.analysis_engine, "last_invocation_audit", None)
        artifact_store = getattr(self.evidence_gateway, "artifact_store", None)
        if not isinstance(audit, Mapping) or not hasattr(artifact_store, "write_json"):
            return None
        module_run_id = str(module_run.get("id") or "")
        name_seed = hashlib.sha256(
            f"{claim_id}:{iteration_number}:{module_run_id}".encode("utf-8")
        ).hexdigest()[:16]
        stored = artifact_store.write_json(
            investigation_id,
            f"model-responses/i2/round-{iteration_number}-{name_seed}.json",
            dict(audit),
            artifact_type="i2_model_response",
        )
        repository_artifact_id: str | None = None
        if hasattr(self.repository, "insert_artifact"):
            row = self.repository.insert_artifact(
                investigation_id=investigation_id,
                module_run_id=module_run_id or None,
                artifact_type=stored.artifact_type,
                uri=stored.uri,
                sha256=stored.sha256,
                mime_type=stored.mime_type,
                byte_size=stored.byte_size,
                metadata={
                    "stage": stage,
                    "claim_id": claim_id,
                    "iteration_number": iteration_number,
                    "model": str(audit.get("model") or "unknown"),
                    "normalization": dict(audit.get("normalization") or {}),
                    "attempt_count": int(audit.get("attempt_count") or 1),
                    "successful_attempt": audit.get("successful_attempt"),
                },
                actor="I0",
            )
            repository_artifact_id = str(row.get("id") or "") or None
        return {
            "artifact_id": repository_artifact_id,
            "artifact_type": stored.artifact_type,
            "sha256": stored.sha256,
            "byte_size": stored.byte_size,
            "normalization": dict(audit.get("normalization") or {}),
            "attempt_count": int(audit.get("attempt_count") or 1),
            "successful_attempt": audit.get("successful_attempt"),
        }

    def _persist_limitations(self, state: ClaimCheckpoint) -> None:
        assert state.claim_investigation_id
        existing = self.repository.list_claim_limitations(state.claim_investigation_id)
        rows = existing or self.repository.insert_claim_limitations(
            state.claim_investigation_id,
            [
                {
                    "feature_key": item.feature_id,
                    "sequence_no": item.sequence,
                    "limitation_text": item.text,
                    "origin_claim_id": item.inherited_from_claim_id or item.claim_id,
                    "is_inherited": bool(item.inherited_from_claim_id),
                    "limitation_snapshot": item.model_dump(mode="json"),
                }
                for item in _limitations(state)
            ],
            snapshot_version=self.pipeline_version,
            actor="I0",
        )
        state.limitation_row_ids = {
            str(row["feature_key"]): str(row["id"]) for row in rows
        }

    def _persist_queries(
        self,
        round_state: RoundCheckpoint,
        plan: QueryPlan,
        critical_date: date,
        *,
        target_publication_date: date | None,
        target_publication_date_verified: bool,
    ) -> None:
        assert round_state.iteration_id
        for query in plan.queries:
            persisted_status = round_state.query_statuses.get(query.query_id, "planned")
            row = self.repository.upsert_query(
                iteration_id=round_state.iteration_id,
                query_type=query.purpose,
                channel=query.provider_kind,
                expression=query.expression,
                date_filter={
                    "critical_date": critical_date.isoformat(),
                    "target_publication_date": (
                        target_publication_date.isoformat()
                        if target_publication_date
                        else None
                    ),
                    "target_publication_date_verified": target_publication_date_verified,
                    "date_channel": query.date_channel.value,
                    "provider_filter_is_discovery_only": True,
                },
                feature_ids=query.feature_ids,
                language=query.language,
                rationale=query.rationale,
                provider_plan={
                    "provider_kind": query.provider_kind,
                    "query_role": query.query_role.value,
                    "query_variant": query.query_variant.value,
                    "search_scope": query.search_scope.value,
                    "scope_reason": query.scope_reason,
                    "subject_terms": list(query.subject_terms),
                    "feature_terms": list(query.feature_terms),
                    "feature_term_groups": [
                        list(group) for group in query.feature_term_groups
                    ],
                    "concept_ids": list(query.concept_ids),
                    "classification_anchors": list(query.classification_anchors),
                    "classification_anchor_sources": dict(
                        query.classification_anchor_sources
                    ),
                    "classification_anchor_roles": dict(
                        query.classification_anchor_roles
                    ),
                    "allow_zero_results": query.allow_zero_results,
                    "compact_fallback_allowed": query.compact_fallback_allowed,
                    "provider_expression": query.provider_expression,
                    "search_objective": query.search_objective.value,
                    "date_channel": query.date_channel.value,
                    "target_gap_type": (
                        query.target_gap_type.value if query.target_gap_type else None
                    ),
                    "gap_feature_ids": list(query.gap_feature_ids),
                    "anchor_document_id": query.anchor_document_id,
                },
                status=persisted_status,
                executed_at=(
                    datetime.now(timezone.utc)
                    if persisted_status in {"completed", "partial", "failed", "skipped"}
                    else None
                ),
                actor="I0",
            )
            _append_unique(round_state.query_row_ids, str(row["id"]))
            round_state.query_repository_ids[query.query_id] = str(row["id"])
            round_state.query_statuses.setdefault(query.query_id, "planned")

    def _finish_query(
        self,
        round_state: RoundCheckpoint,
        query: SearchQuery,
        *,
        status: str,
        diagnostic: Mapping[str, Any],
    ) -> None:
        if status not in {"completed", "partial", "failed", "skipped"}:
            raise WorkflowInputError(f"非法 query 终态: {status}")
        current = round_state.query_statuses.get(query.query_id)
        if current in {"completed", "partial", "failed", "skipped"}:
            return
        repository_id = round_state.query_repository_ids.get(query.query_id)
        if not repository_id:
            raise WorkflowInputError(f"query {query.query_id} 缺少 repository id")
        self.repository.update_query_execution(
            repository_id,
            status=status,
            diagnostic=dict(diagnostic),
            executed_at=datetime.now(timezone.utc),
            actor="I0",
        )
        round_state.query_statuses[query.query_id] = status

    def _skip_pending_queries(
        self,
        round_state: RoundCheckpoint,
        queries: Sequence[SearchQuery],
        *,
        reason: str,
    ) -> None:
        for query in queries:
            if round_state.query_statuses.get(query.query_id, "planned") == "planned":
                self._finish_query(
                    round_state,
                    query,
                    status="skipped",
                    diagnostic={"reason": reason},
                )

    def _close_iteration(
        self,
        round_state: RoundCheckpoint,
        *,
        status: str,
        stop_reason: str,
        anchor_document_id: str | None = None,
    ) -> None:
        if status not in {"succeeded", "partial", "failed", "cancelled"}:
            raise WorkflowInputError(f"非法 iteration 终态: {status}")
        if not round_state.iteration_id:
            raise WorkflowInputError("iteration 尚未持久化，不能收口")
        if round_state.iteration_status in {
            "succeeded",
            "partial",
            "failed",
            "cancelled",
        }:
            round_state.complete = True
            return
        metrics = {
            "query_statuses": dict(round_state.query_statuses),
            "module_run_statuses": dict(round_state.module_run_statuses),
            "provider_complete": round_state.provider_complete,
            "qualified_documents_added": round_state.qualified_documents_added,
            "document_count": len(round_state.document_keys),
            "gap_count": len(round_state.gaps_after),
        }
        self.repository.update_iteration(
            round_state.iteration_id,
            status=status,
            metrics=metrics,
            stop_reason=stop_reason,
            anchor_document_id=anchor_document_id,
            progress_signature=_round_progress_signature(round_state),
            actor="I0",
        )
        round_state.iteration_status = status
        round_state.stop_reason = stop_reason
        round_state.complete = True

    @staticmethod
    def _assert_round_closed(round_state: RoundCheckpoint) -> None:
        query_terminal = {"completed", "partial", "failed", "skipped"}
        module_terminal = {"succeeded", "partial", "failed", "cancelled"}
        iteration_terminal = {"succeeded", "partial", "failed", "cancelled"}
        open_queries = {
            key: value
            for key, value in round_state.query_statuses.items()
            if value not in query_terminal
        }
        open_modules = {
            key: value
            for key, value in round_state.module_run_statuses.items()
            if value not in module_terminal
        }
        if (
            not round_state.complete
            or round_state.iteration_status not in iteration_terminal
            or open_queries
            or open_modules
        ):
            raise WorkflowExecutionError(
                "上一轮尚未原子闭合，禁止创建下一轮: "
                f"iteration={round_state.iteration_status}, "
                f"queries={open_queries}, modules={open_modules}"
            )

    def _persist_evidence(
        self,
        *,
        investigation_id: str,
        state: ClaimCheckpoint,
        record: EvidenceRecord,
        critical_date: date,
    ) -> tuple[str, bool]:
        key = _document_key(record)
        previous_document = state.documents.get(key, {})
        previous = previous_document.get("record") or {}
        same_immutable_bytes = bool(
            record.content_sha256
            and str(previous.get("content_sha256") or "")
            == str(record.content_sha256)
        )
        previously_qualified = (
            str(previous.get("stage") or "")
            == EvidenceStage.QUALIFIED.value
        )
        if same_immutable_bytes and previously_qualified and record.stage is not EvidenceStage.QUALIFIED:
            # Provider hits may repeat in later queries with a weaker DTO.  A
            # stage is a property of the exact immutable version and therefore
            # moves monotonically.  Only a different SHA invalidates it.
            record = replace(
                record,
                stage=EvidenceStage.QUALIFIED,
                full_text=record.full_text or previous.get("full_text"),
                image_urls=(
                    record.image_urls
                    or tuple(str(item) for item in previous.get("image_urls") or [])
                ),
                artifacts=(
                    record.artifacts
                    or tuple(
                        dict(item)
                        for item in previous.get("artifacts") or []
                        if isinstance(item, Mapping)
                    )
                ),
                provenance={
                    **dict(previous.get("provenance") or {}),
                    **dict(record.provenance),
                },
                eligibility=(
                    dict(previous.get("eligibility") or {})
                    or dict(record.eligibility or {})
                ),
                qualification_issues=tuple(
                    str(item)
                    for item in previous.get("qualification_issues") or ()
                ),
                readable_document=(
                    record.readable_document
                    or dict(previous.get("readable_document") or {})
                    or None
                ),
                analysis_ready=(
                    record.analysis_ready
                    or bool(previous.get("analysis_ready"))
                ),
                analysis_readiness_reason=(
                    record.analysis_readiness_reason
                    or str(previous.get("analysis_readiness_reason") or "")
                ),
            )
        artifact_root = getattr(
            getattr(self.evidence_gateway, "artifact_store", None),
            "root",
            None,
        )
        primary_artifact = _primary_local_artifact(
            record,
            allowed_root=artifact_root,
        )
        if primary_artifact is None and _requires_local_source_snapshot(record):
            # Validate the immutable source before the first database write.
            # Otherwise a forged/missing snapshot could leave a durable
            # ``retrieved_document`` row even though evidence persistence then
            # fails half way through.
            raise WorkflowInputError(
                f"{key} 缺少与内容哈希匹配的本地不可变原文快照"
            )
        became_qualified = (
            record.stage is EvidenceStage.QUALIFIED and not previously_qualified
        )
        artifact_row: Mapping[str, Any] | None = None
        if primary_artifact is not None and hasattr(self.repository, "insert_artifact"):
            artifact_row = self.repository.insert_artifact(
                investigation_id=investigation_id,
                artifact_type=str(
                    primary_artifact.get("artifact_type")
                    or primary_artifact.get("kind")
                    or "prior_art_source_snapshot"
                ),
                uri=str(primary_artifact["uri"]),
                sha256=str(
                    primary_artifact.get("sha256")
                    or primary_artifact.get("content_sha256")
                    or record.content_sha256
                ),
                mime_type=str(
                    primary_artifact.get("mime_type")
                    or primary_artifact.get("media_type")
                    or ""
                )
                or None,
                byte_size=(
                    primary_artifact.get("byte_size")
                    or primary_artifact.get("content_length")
                ),
                metadata={
                    "provider": record.provider,
                    "external_id": record.external_id,
                    "document_key": key,
                },
                actor="I0",
            )
        row = self.repository.upsert_document(
            investigation_id=investigation_id,
            canonical_key=key,
            document_type=record.source_type,
            title=record.title or record.external_id,
            evidence_level=record.stage.value,
            language=record.language,
            identifiers={
                "external_id": record.external_id,
                "publication_number": record.publication_number,
                "authority": record.authority,
            },
            dates={
                "publication_date": record.publication_date,
                "filing_date": record.filing_date,
                "priority_date": record.priority_date,
            },
            metadata={
                "provider": record.provider,
                "source_url": record.source_url,
                "qualification_issues": list(record.qualification_issues),
                "analysis_ready": record.analysis_ready,
                "analysis_readiness_reason": record.analysis_readiness_reason,
                "readable_document_version": READABLE_PATENT_VERSION,
            },
            content_sha256=record.content_sha256,
            content_artifact_id=(
                artifact_row.get("id") if artifact_row is not None else None
            ),
            content_mime_type=(
                str(
                    primary_artifact.get("mime_type")
                    or primary_artifact.get("media_type")
                    or ""
                )
                or None
                if primary_artifact is not None
                else None
            ),
            content_byte_size=(
                primary_artifact.get("byte_size")
                or primary_artifact.get("content_length")
                if primary_artifact is not None
                else None
            ),
            acquisition_kind="automatic_retrieval",
            version_metadata={
                "provider": record.provider,
                "external_id": record.external_id,
                "full_text": record.full_text,
                "image_urls": list(record.image_urls),
                "artifacts": [dict(item) for item in record.artifacts],
                "analysis_ready": record.analysis_ready,
                "analysis_readiness_reason": record.analysis_readiness_reason,
                "readable_document_version": READABLE_PATENT_VERSION,
                "readable_document": (
                    dict(record.readable_document)
                    if isinstance(record.readable_document, Mapping)
                    else None
                ),
                "readable_document_audit": _readable_document_audit(record),
            },
            actor="I0",
        )
        source_id: str | None = None
        retrieved_at = _parse_datetime(record.retrieved_at)
        if retrieved_at and record.source_url:
            artifact = primary_artifact or next(iter(record.artifacts), {})
            source_url = str(artifact.get("source_url") or record.source_url)
            source = self.repository.upsert_document_source(
                document_id=row["id"],
                document_version_id=row.get("document_version_id"),
                provider=record.provider,
                source_url=source_url,
                provider_document_id=record.external_id,
                retrieved_at=retrieved_at,
                mime_type=str(artifact.get("media_type") or "") or None,
                snapshot_uri=str(
                    artifact.get("uri") or artifact.get("source_url") or record.source_url
                ),
                snapshot_sha256=str(
                    artifact.get("sha256") or artifact.get("content_sha256") or record.content_sha256 or ""
                )
                or None,
                snapshot_byte_size=artifact.get("byte_size") or artifact.get("content_length"),
                artifact_id=(
                    artifact_row.get("id") if artifact_row is not None else None
                ),
                raw_response={"provenance": dict(record.provenance)},
                status="qualified" if record.stage is EvidenceStage.QUALIFIED else "retrieved",
                actor="I0",
            )
            source_id = str(source["id"])
        eligibility = dict(record.eligibility or {})
        if eligibility:
            qualification = self.repository.upsert_document_qualification(
                document_id=row["id"],
                document_version_id=row.get("document_version_id"),
                claim_investigation_id=state.claim_investigation_id,
                critical_date=critical_date,
                earliest_priority_date=parse_date(record.priority_date),
                filing_date=parse_date(record.filing_date),
                publication_date=parse_date(record.publication_date),
                public_availability_date=parse_date(
                    eligibility.get("public_availability_date") or record.publication_date
                ),
                eligibility_type=str(eligibility.get("category") or "unknown"),
                novelty_eligible=bool(eligibility.get("novelty_eligible")),
                inventive_step_eligible=bool(
                    eligibility.get(
                        "inventive_step_eligible",
                        eligibility.get("inventive_eligible", False),
                    )
                ),
                verification_status=(
                    "needs_human_review"
                    if eligibility.get("requires_human_review")
                    else "verified"
                ),
                verification_reason=str(
                    eligibility.get("explanation")
                    or ";".join(eligibility.get("reason_codes") or [])
                ),
                date_rule_version="date-rules-v1",
                human_confirmed=bool(
                    record.provenance.get("human_confirmed", False)
                ),
                actor="I0",
            )
            eligibility["repository_qualification_id"] = str(qualification["id"])
        state.date_qualifications[key] = eligibility
        state.documents[key] = {
            "repository_document_id": str(row["id"]),
            "repository_source_id": (
                source_id or previous_document.get("repository_source_id")
            ),
            "repository_document_version_id": (
                str(row["document_version_id"])
                if row.get("document_version_id")
                else None
            ),
            "repository_document_version_no": row.get("document_version_no"),
            "record": record.model_dump(),
        }
        return key, became_qualified

    def _persist_comparison(
        self,
        *,
        investigation_id: str,
        state: ClaimCheckpoint,
        round_state: RoundCheckpoint,
        document_key: str,
        comparison: DocumentComparison,
        target_source_sha256: str,
    ) -> None:
        assert state.claim_investigation_id and round_state.iteration_id
        binding = _comparison_input_binding(
            state,
            document_key,
            target_source_sha256=target_source_sha256,
        )
        # The canonical key identifies the intellectual document, not immutable
        # bytes.  Include the exact version/limitation input digest so a new SHA
        # can never pick up an older terminal I4-S module run.
        binding_digest = hashlib.sha256(
            json.dumps(
                binding,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()[:24]
        module_run = self._module_run(
            investigation_id=investigation_id,
            state=state,
            round_state=round_state,
            module_code="I4_S_SINGLE_REFERENCE",
            idempotency_key=(
                f"{state.claim_id}:{round_state.iteration_number}:"
                f"{document_key}:{binding_digest}"
            ),
            input_snapshot={
                "claim_id": state.claim_id,
                "document_key": document_key,
                "input_binding": binding,
                "comparison": comparison.model_dump(mode="json"),
            },
            model_version=comparison.model,
        )
        document = state.documents[document_key]
        disclosures = []
        for item in comparison.disclosures:
            limitation_id = state.limitation_row_ids.get(item.feature_id)
            if not limitation_id:
                raise WorkflowInputError(f"特征 {item.feature_id} 缺少持久化 limitation")
            disclosures.append(
                {
                    "limitation_id": limitation_id,
                    "disclosure_status": item.status.value,
                    "excerpt": item.evidence_quote or None,
                    "locator": {"location": item.evidence_location},
                    "analysis": {
                        "reasoning": item.reasoning,
                        "confidence": item.confidence,
                        "model": comparison.model,
                        "used_target_images": comparison.used_target_images,
                        "used_document_images": comparison.used_document_images,
                        "target_structural_role": item.target_structural_role,
                        "reference_structure_mapping": (
                            item.reference_structure_mapping
                        ),
                        "mapping_basis": item.mapping_basis,
                        "structural_evidence": list(item.structural_evidence),
                        "integrated_structure_mapping": (
                            item.integrated_structure_mapping
                        ),
                        "structural_search_summary": (
                            item.structural_search_summary
                        ),
                        "necessity_chain": list(item.necessity_chain),
                        "reasonable_alternatives_excluded": (
                            item.reasonable_alternatives_excluded
                        ),
                        "alternative_path_analysis": (
                            item.alternative_path_analysis
                        ),
                        "target_mechanism_summary": (
                            comparison.target_mechanism_summary
                        ),
                        "reference_mechanism_summary": (
                            comparison.reference_mechanism_summary
                        ),
                        "structural_review_attempted": (
                            comparison.structural_review_attempted
                        ),
                        "structural_review_performed": (
                            comparison.structural_review_performed
                        ),
                        "structural_review_status": (
                            comparison.structural_review_status
                        ),
                        "structural_review_error_code": (
                            comparison.structural_review_error_code
                        ),
                        "structural_review_used_target_images": (
                            comparison.structural_review_used_target_images
                        ),
                        "structural_review_used_document_images": (
                            comparison.structural_review_used_document_images
                        ),
                        "analysis_pass_count": comparison.analysis_pass_count,
                        "analysis_rule_version": comparison.analysis_rule_version,
                    },
                }
            )
        try:
            self.repository.insert_feature_disclosures(
                module_run_id=module_run["id"],
                claim_investigation_id=state.claim_investigation_id,
                document_id=document["repository_document_id"],
                document_version_id=document.get(
                    "repository_document_version_id"
                ),
                document_source_id=document.get("repository_source_id"),
                disclosures=disclosures,
                rule_version=I4S_RULE_VERSION,
                model_version=comparison.model,
                prompt_version=I4S_PROMPT_VERSION,
                actor="I0",
            )
        except Exception as exc:
            self._finish_module_run(
                round_state,
                module_run,
                status="failed",
                output_snapshot={"document_key": document_key},
                error=exc,
            )
            raise
        self._finish_module_run(
            round_state,
            module_run,
            status=(
                "partial"
                if comparison.structural_review_status == "model_error"
                else "succeeded"
            ),
            output_snapshot=comparison.model_dump(mode="json"),
        )
        return str(module_run["id"])

    def _persist_closest_prior_art(
        self,
        *,
        investigation_id: str,
        state: ClaimCheckpoint,
        round_state: RoundCheckpoint,
        selection: ClosestPriorArtSelection,
    ) -> Mapping[str, Any]:
        assert state.claim_investigation_id and round_state.iteration_id
        d1_comparison = DocumentComparison.model_validate(
            state.comparisons[selection.document_id]
        )
        distinguishing_features = build_distinguishing_features(
            limitations=_limitations(state),
            d1_comparison=d1_comparison,
        )
        chart_comparisons = [
            DocumentComparison.model_validate(value)
            for value in state.comparisons.values()
            if isinstance(value, Mapping)
            and str(value.get("analysis_rule_version") or "") == I4S_RULE_VERSION
            and str(value.get("structural_review_status") or "not_needed")
            != "model_error"
        ]
        large_claim_chart_documents = select_large_claim_chart_documents(
            limitations=_limitations(state),
            closest_document_id=selection.document_id,
            comparisons=chart_comparisons,
            date_qualifications=state.date_qualifications,
            difference_feature_ids=[
                item.feature_id for item in distinguishing_features
            ],
        )
        module_run = self._module_run(
            investigation_id=investigation_id,
            state=state,
            round_state=round_state,
            module_code="I4_C_CLOSEST_PRIOR_ART",
            idempotency_key=f"{state.claim_id}:{round_state.iteration_number}:closest",
            input_snapshot=selection.model_dump(mode="json"),
            model_version=None,
        )
        try:
            row = self.repository.insert_closest_prior_art_version(
                claim_investigation_id=state.claim_investigation_id,
                iteration_id=round_state.iteration_id,
                document_id=state.documents[selection.document_id]["repository_document_id"],
                document_version_id=state.documents[selection.document_id].get(
                    "repository_document_version_id"
                ),
                metrics=selection.score.model_dump(mode="json"),
                rationale={
                    "selection_reason": selection.selection_reason,
                    "ranked_candidates": [
                        item.model_dump(mode="json")
                        for item in selection.ranked_candidates
                    ],
                    "distinguishing_features": [
                        item.model_dump(mode="json")
                        for item in distinguishing_features
                    ],
                    "large_claim_chart_document_ids": [
                        item.document_id for item in large_claim_chart_documents
                    ],
                    "large_claim_chart_repository_document_ids": [
                        str(state.documents[item.document_id]["repository_document_id"])
                        for item in large_claim_chart_documents
                        if item.document_id in state.documents
                    ],
                },
                selected_by="deterministic_i4_c",
                actor="I0",
            )
        except Exception as exc:
            self._finish_module_run(
                round_state,
                module_run,
                status="failed",
                output_snapshot={"document_id": selection.document_id},
                error=exc,
            )
            raise
        self._finish_module_run(
            round_state,
            module_run,
            status="succeeded",
            output_snapshot={
                **selection.model_dump(mode="json"),
                "repository_selection_id": str(row["id"]),
            },
        )
        return row

    def _persist_gaps(
        self,
        *,
        state: ClaimCheckpoint,
        round_state: RoundCheckpoint,
        closed_by_document_key: str | None = None,
        closed_reason: str | None = None,
    ) -> None:
        assert state.claim_investigation_id and round_state.iteration_id
        normalized_after, resolutions = _prepare_gap_reconciliation(
            state=state,
            round_state=round_state,
            closed_by_document_key=closed_by_document_key,
            closed_reason=closed_reason,
        )
        round_state.gaps_after = normalized_after
        state.gaps = list(normalized_after)
        rows: list[dict[str, Any]] = []
        for raw in round_state.gaps_after:
            feature_id = raw.get("feature_id")
            gap_type = str(raw.get("kind") or "")
            if gap_type not in {
                "feature_gap",
                "evidence_gap",
                "date_gap",
                "combination_gap",
            }:
                raise WorkflowInputError(f"非 canonical gap_type: {gap_type or '<empty>'}")
            limitation_id = state.limitation_row_ids.get(str(feature_id))
            if gap_type in {"feature_gap", "evidence_gap"} and not limitation_id:
                raise WorkflowInputError(
                    f"{gap_type} 缺少可追溯 limitation: {feature_id or '<empty>'}"
                )
            rows.append(
                {
                    "gap_key": str(raw.get("gap_key") or raw.get("gap_id") or ""),
                    "limitation_id": limitation_id,
                    "gap_type": gap_type,
                    "status": "open",
                    "description": str(raw.get("rationale") or ""),
                    "search_objective": raw,
                }
            )
        self.repository.reconcile_gap_frontier(
            iteration_id=round_state.iteration_id,
            claim_investigation_id=state.claim_investigation_id,
            open_gaps=rows,
            closed_gap_resolutions=resolutions,
            actor="I0",
        )

    def _persist_obviousness_precheck(
        self,
        *,
        investigation_id: str,
        state: ClaimCheckpoint,
        round_state: RoundCheckpoint,
        assessment: ObviousnessPrecheckAssessment,
    ) -> None:
        module_run = self._module_run(
            investigation_id=investigation_id,
            state=state,
            round_state=round_state,
            module_code="I4_O_OBVIOUSNESS_PRECHECK",
            idempotency_key=(
                f"{state.claim_id}:{round_state.iteration_number}:obviousness-precheck"
            ),
            input_snapshot={
                "closest_document_id": assessment.closest_document_id,
                "feature_ids": [
                    feature_id
                    for group in assessment.feature_groups
                    for feature_id in group.feature_ids
                ],
            },
            model_version=assessment.model,
        )
        self._finish_module_run(
            round_state,
            module_run,
            status="succeeded",
            output_snapshot=assessment.model_dump(mode="json"),
        )

    def _persist_combination(
        self,
        *,
        investigation_id: str,
        state: ClaimCheckpoint,
        round_state: RoundCheckpoint,
        assessment: InventiveStepAssessment,
        comparison_ids: Sequence[str],
    ) -> None:
        assert state.claim_investigation_id and round_state.iteration_id
        selection_id = str(
            (round_state.closest_prior_art or {}).get("repository_selection_id") or ""
        )
        if not selection_id:
            raise WorkflowInputError("创造性组合缺少已持久化 D1 版本")
        module_run = self._module_run(
            investigation_id=investigation_id,
            state=state,
            round_state=round_state,
            module_code="I4_I_INVENTIVE_STEP",
            idempotency_key=f"{state.claim_id}:{round_state.iteration_number}:combination",
            input_snapshot=assessment.model_dump(mode="json"),
            model_version=assessment.model,
        )
        try:
            self.repository.insert_combination(
                claim_investigation_id=state.claim_investigation_id,
                iteration_id=round_state.iteration_id,
                module_run_id=module_run["id"],
                closest_prior_art_version_id=selection_id,
                document_ids=[
                    state.documents[item]["repository_document_id"]
                    for item in comparison_ids
                ],
                document_version_ids=[
                    state.documents[item]["repository_document_version_id"]
                    for item in comparison_ids
                ],
                status=("evidence_complete" if assessment.evidence_complete else "gaps_open"),
                coverage_complete=assessment.combined_feature_coverage_complete,
                motivation_status=assessment.combination_motivation.status,
                analysis=assessment.model_dump(mode="json"),
                actor="I0",
            )
        except Exception as exc:
            self._finish_module_run(
                round_state,
                module_run,
                status="failed",
                output_snapshot={"comparison_ids": list(comparison_ids)},
                error=exc,
            )
            raise
        self._finish_module_run(
            round_state,
            module_run,
            status="succeeded",
            output_snapshot=assessment.model_dump(mode="json"),
        )

    def _module_run(
        self,
        *,
        investigation_id: str,
        state: ClaimCheckpoint,
        round_state: RoundCheckpoint,
        module_code: str,
        idempotency_key: str,
        input_snapshot: Mapping[str, Any],
        model_version: str | None,
        force_recompute: bool = True,
    ) -> Mapping[str, Any]:
        effective_idempotency_key = idempotency_key
        if force_recompute:
            effective_idempotency_key = f"{idempotency_key}:{uuid.uuid4().hex}"
        else:
            existing = self.repository.list_module_runs(
                investigation_id=investigation_id,
                module_code=module_code,
                limit=1000,
            )
            for row in existing:
                if str(row.get("idempotency_key")) == idempotency_key:
                    run_id = str(row["id"])
                    round_state.module_run_ids[idempotency_key] = run_id
                    round_state.module_run_statuses[run_id] = str(
                        row.get("status") or "running"
                    )
                    return row
        prompt_version = module_code.lower() + "-v1"
        rule_version = self.pipeline_version
        if module_code in {"I2_QUERY_PLAN", "I2_GAP_QUERY_PLAN"}:
            prompt_version = I2_PROMPT_VERSION
            rule_version = I2_RULE_VERSION
        elif module_code == "I4_I_INVENTIVE_STEP":
            prompt_version = I4I_PROMPT_VERSION
            rule_version = I4I_RULE_VERSION
        row = self.repository.create_module_run(
            investigation_id=investigation_id,
            claim_investigation_id=state.claim_investigation_id,
            iteration_id=round_state.iteration_id,
            module_code=module_code,
            contract_version="v1",
            input_snapshot=input_snapshot,
            idempotency_key=effective_idempotency_key,
            status="running",
            requested_mode="live",
            effective_mode="live",
            actual_provider=None,
            model_version=model_version,
            prompt_version=prompt_version,
            rule_version=rule_version,
        )
        run_id = str(row["id"])
        round_state.module_run_ids[effective_idempotency_key] = run_id
        round_state.module_run_statuses[run_id] = str(row.get("status") or "running")
        return row

    def _finish_module_run(
        self,
        round_state: RoundCheckpoint,
        module_run: Mapping[str, Any],
        *,
        status: str,
        output_snapshot: Mapping[str, Any],
        actual_provider: str | None = None,
        network_used: bool | None = None,
        error: Exception | None = None,
    ) -> None:
        run_id = str(module_run["id"])
        current = round_state.module_run_statuses.get(
            run_id, str(module_run.get("status") or "running")
        )
        if current in {"succeeded", "partial", "failed", "cancelled"}:
            return
        self.repository.finish_module_run(
            run_id,
            status=status,
            output_snapshot=dict(output_snapshot),
            error_code=(type(error).__name__ if error else None),
            error_message=(_safe_error(error) if error else None),
            retryable=False if error else None,
            actual_provider=actual_provider,
            network_used=network_used,
            actor="I0",
        )
        round_state.module_run_statuses[run_id] = status

    def _set_claim_status(
        self,
        state: ClaimCheckpoint,
        status: ClaimStatus,
        **kwargs: Any,
    ) -> None:
        if state.status == status.value and not kwargs:
            return
        if state.status in _TERMINAL_CLAIM_VALUES:
            return
        assert state.claim_investigation_id
        self.repository.update_claim_status(
            state.claim_investigation_id,
            status.value,
            actor="I0",
            event_payload={"from": state.status, "to": status.value},
            **kwargs,
        )
        state.status = status.value

    def _terminal_claim(
        self,
        state: ClaimCheckpoint,
        status: ClaimStatus,
        reason: str,
        summary: Mapping[str, Any],
    ) -> None:
        assert state.claim_investigation_id
        payload = {
            **dict(summary),
            "provider_failures": list(state.provider_failures),
            "analysis_failures": list(state.analysis_failures),
            "unresolved_gaps": list(state.gaps),
            "critical_date": state.critical_date,
            "critical_date_basis": state.critical_date_basis,
            "target_publication_date": state.target_publication_date,
            "target_publication_date_verified": state.target_publication_date_verified,
            "workflow_version": self.pipeline_version,
        }
        self.repository.mark_claim_terminal(
            state.claim_investigation_id,
            status=status.value,
            terminal_reason=reason,
            result_summary=payload,
            actor="I0",
        )
        state.status = status.value
        state.terminal_reason = reason

    @staticmethod
    def _analysis_failed(
        state: ClaimCheckpoint,
        *,
        round_state: RoundCheckpoint | None = None,
        stage: str,
        error: Exception,
    ) -> None:
        message = _safe_error(error)
        _append_unique(
            state.analysis_failures,
            f"{stage}: {type(error).__name__}: {message}",
        )
        if round_state is not None:
            record = {
                "iteration_number": round_state.iteration_number,
                "gap_search_iteration": max(0, round_state.iteration_number - 1),
                "stage": stage,
                "error_code": type(error).__name__,
                "safe_summary": message,
                "affected_feature_ids": _gap_feature_ids(
                    round_state.gaps_after or round_state.gaps_before or state.gaps
                ),
            }
            if record not in round_state.failure_records:
                round_state.failure_records.append(record)


def build_workflow(
    *,
    settings: Any,
    repository: WorkflowRepository,
    evidence_gateway: EvidenceGateway | None = None,
    analysis_engine: InvalidityAnalysisEngine | None = None,
) -> InvalidityWorkflow:
    """Build I0 without importing API/worker state."""

    if analysis_engine is None:
        analysis_engine = InvalidityAnalysisEngine(
            VisionLLMClient(
                base_url=settings.llm_base_url,
                api_key=settings.llm_api_key,
                model=settings.llm_model,
                timeout_seconds=settings.llm_timeout_seconds,
                direct_attempt_timeout_seconds=(
                    settings.llm_direct_attempt_timeout_seconds
                ),
                direct_probe_timeout_seconds=(
                    settings.llm_direct_probe_timeout_seconds
                ),
            ),
            i2_timeout_seconds=settings.llm_i2_timeout_seconds,
            i2_direct_attempt_timeout_seconds=(
                settings.llm_i2_direct_attempt_timeout_seconds
            ),
        )
    if evidence_gateway is None:
        evidence_gateway = build_evidence_gateway(settings)
    return InvalidityWorkflow(
        repository,
        analysis_engine,
        evidence_gateway,
        max_rounds=settings.max_rounds,
    )


def build_evidence_gateway(settings: Any) -> EvidenceGateway:
    """Build explicitly approved live providers or fail configuration loudly."""

    patent_name = str(settings.patent_provider or "").strip().lower()
    npl_name = str(settings.npl_provider or "").strip().lower()
    if patent_name in {"google_patents", "google-patents"}:
        patent_provider: Any = GooglePatentsProvider()
    elif patent_name in {"epo_ops", "epo-ops"}:
        consumer_key = str(getattr(settings, "epo_ops_consumer_key", "") or "")
        consumer_secret = str(
            getattr(settings, "epo_ops_consumer_secret", "") or ""
        )
        if not consumer_key or not consumer_secret:
            raise WorkflowInputError("EPO OPS provider 缺少当前环境 OAuth 凭据")
        patent_provider = EpoOpsProvider(
            consumer_key=consumer_key,
            consumer_secret=consumer_secret,
        )
    elif patent_name == "patsnap":
        api_key = str(getattr(settings, "patsnap_api_key", "") or "")
        if not api_key:
            raise WorkflowInputError("智慧芽 provider 缺少当前环境 REST API Key")
        patent_provider = PatsnapProvider(
            api_key=api_key,
            base_url=str(getattr(settings, "patsnap_base_url", "") or ""),
            count_path=str(getattr(settings, "patsnap_count_path", "") or ""),
            search_path=str(getattr(settings, "patsnap_search_path", "") or ""),
        )
    else:
        raise WorkflowInputError(
            f"默认 live I0 尚不支持专利 provider={settings.patent_provider}；"
            "fixture/manual 模式必须显式注入 EvidenceGateway"
        )
    if npl_name in {"arxiv", "arxiv_atom"}:
        npl_provider: Any = ArxivProvider()
    elif npl_name in {
        "composite_npl",
        "arxiv_openalex_crossref",
        "arxiv_openalex_crossref_web",
    }:
        providers: list[Any] = [
            ArxivProvider(),
            OpenAlexProvider(),
            CrossrefProvider(),
        ]
        if npl_name in {"composite_npl", "arxiv_openalex_crossref_web"}:
            providers.append(WebEvidenceProvider())
        npl_provider = CompositeNplProvider(providers)
    else:
        raise WorkflowInputError(
            f"默认 live I0 尚不支持 NPL provider={settings.npl_provider}；"
            "fixture/manual 模式必须显式注入 EvidenceGateway"
        )
    patent_retrieval_provider: Any | None = None
    if patent_name == "patsnap":
        consumer_key = str(getattr(settings, "epo_ops_consumer_key", "") or "")
        consumer_secret = str(
            getattr(settings, "epo_ops_consumer_secret", "") or ""
        )
        if not consumer_key or not consumer_secret:
            raise WorkflowInputError(
                "智慧芽只负责候选发现；按公开号取回原文必须配置当前环境的 "
                "EPO OPS OAuth 凭据"
            )
        patent_retrieval_provider = EpoFirstPatsnapPdfRetrievalProvider(
            epo_provider=EpoOpsProvider(
                consumer_key=consumer_key,
                consumer_secret=consumer_secret,
            ),
            patsnap_provider=patent_provider,
            close_patsnap=False,
        )
    return ConfiguredEvidenceGateway(
        patent_provider=patent_provider,
        patent_retrieval_provider=patent_retrieval_provider,
        npl_provider=npl_provider,
        artifact_store=ArtifactStore(settings.artifact_root, settings.environment),
        max_candidates_per_query=settings.max_candidates_per_query,
    )


def _target_context(source: Mapping[str, Any]) -> tuple[PatentSnapshot, list[str]]:
    raw_patent = source.get("patent_snapshot") or source.get("module1_snapshot") or source
    try:
        patent = PatentSnapshot.model_validate(raw_patent)
    except Exception as exc:
        raise WorkflowInputError("source_snapshot 缺少合法 PatentSnapshot") from exc
    explicit = source.get("target_images") or source.get("target_image_paths") or []
    images = [str(item) for item in explicit if str(item).strip()]
    if not images:
        for figure in patent.figures:
            for key in ("local_path", "uri", "url", "image_url", "file_url"):
                value = figure.get(key)
                if value:
                    images.append(str(value))
                    break
    return patent, list(dict.fromkeys(images))


def _requested_max_rounds(investigation: Mapping[str, Any], fallback: int) -> int:
    settings = investigation.get("settings") or {}
    budgets = investigation.get("budgets") or {}
    value = settings.get(
        "max_gap_search_iterations",
        settings.get("max_rounds", budgets.get("max_rounds", fallback)),
    )
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise WorkflowInputError("max_rounds 必须是 1..5 的整数") from exc
    if parsed < 1 or parsed > MAX_AUTOMATIC_GAP_SEARCH_ITERATIONS:
        raise WorkflowInputError(
            "自动 max_rounds 表示首轮后的 gap 检索次数，必须在 1..5"
        )
    return 1 + parsed


def _normalize_continuation_round_plan(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)) or not value:
        raise WorkflowInputError("continuation round_plan 必须是非空数组")
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in value:
        if not isinstance(raw, Mapping):
            raise WorkflowInputError("continuation round_plan 项必须是对象")
        claim_id = str(raw.get("claim_investigation_id") or "").strip()
        if not claim_id:
            raise WorkflowInputError("continuation round_plan 缺少 claim_investigation_id")
        if claim_id in seen:
            raise WorkflowInputError("continuation round_plan claim 不能重复")
        seen.add(claim_id)
        start_raw = raw.get("start_iteration_no")
        end_raw = raw.get("end_iteration_no")
        if isinstance(start_raw, bool) or isinstance(end_raw, bool):
            raise WorkflowInputError("continuation 轮次必须是整数")
        try:
            start = int(start_raw)
            end = int(end_raw)
        except (TypeError, ValueError) as exc:
            raise WorkflowInputError("continuation 轮次必须是整数") from exc
        if start < 1 or end < start or end - start + 1 > MAX_CONTINUATION_ROUNDS:
            raise WorkflowInputError("每个 continuation plan 必须连续新增 1..3 轮")
        parent = raw.get("parent_iteration_id")
        normalized.append(
            {
                "claim_investigation_id": claim_id,
                "parent_iteration_id": str(parent) if parent is not None else None,
                "start_iteration_no": start,
                "end_iteration_no": end,
            }
        )
    return normalized


def _record_human_review_frontier(
    state: ClaimCheckpoint,
    *,
    review_action_id: str,
    review_revision: int,
    document_key: str,
    closest_prior_art: Mapping[str, Any] | None,
    combination: Mapping[str, Any] | None,
) -> None:
    """Record review derivation without changing the search-round ledger."""

    state.human_review_history.append(
        {
            "review_action_id": review_action_id,
            "review_revision": review_revision,
            "document_key": document_key,
            "gaps": list(state.gaps),
            "closest_prior_art": (
                dict(closest_prior_art) if closest_prior_art is not None else None
            ),
            "combination": dict(combination) if combination is not None else None,
        }
    )


def _resolve_claim_critical_date(
    *,
    investigation: Mapping[str, Any],
    patent: PatentSnapshot,
    claim_id: str,
    confirmation: Mapping[str, Any] | None = None,
) -> CriticalDateResult:
    if confirmation:
        decision = str(confirmation.get("decision") or "").strip().lower()
        confirmed = parse_date(
            confirmation.get("confirmed_date")
            or confirmation.get("critical_date")
        )
        if confirmed is not None and decision in {
            "confirm_priority",
            "use_filing_date",
            "set_manual_date",
        }:
            return CriticalDateResult(
                critical_date=confirmed,
                basis=str(
                    confirmation.get("critical_date_basis") or "user_confirmation"
                ),
                requires_human_review=False,
                reason_codes=("persisted_user_confirmation",),
            )
        return CriticalDateResult(
            critical_date=None,
            basis="confirmation_pending_review",
            requires_human_review=True,
            reason_codes=("critical_date_confirmation_incomplete",),
        )
    source = investigation.get("source_snapshot") or {}
    settings = investigation.get("settings") or {}
    declared = source.get("declared_critical_date") or settings.get("declared_critical_date")
    if isinstance(declared, Mapping):
        declared = declared.get(claim_id)
    if declared is not None:
        parsed = parse_date(declared)
        return CriticalDateResult(
            critical_date=parsed,
            basis="user_declared_critical_date" if parsed else "unknown",
            requires_human_review=parsed is None,
            reason_codes=("user_declared_date",) if parsed else ("invalid_declared_date",),
        )
    verification = source.get("priority_verified", settings.get("priority_verified"))
    if isinstance(verification, Mapping):
        verification = verification.get(claim_id)
    if verification not in {True, False, None}:
        verification = None
    return resolve_critical_date(
        application_date=patent.application_date,
        priority_date=patent.priority_date,
        priority_verified=verification,
        application_date_verified=bool(settings.get("application_date_verified", True)),
    )


def _resolve_target_publication_date(
    *,
    investigation: Mapping[str, Any],
    patent: PatentSnapshot,
    claim_id: str,
    confirmation: Mapping[str, Any] | None = None,
) -> tuple[date | None, bool]:
    if confirmation:
        decision = str(confirmation.get("decision") or "").strip().lower()
        value = confirmation.get("target_publication_date")
        parsed = parse_date(value)
        verified = parsed is not None and decision in {
            "confirm_priority",
            "use_filing_date",
            "set_manual_date",
        }
        return parsed, verified

    source = investigation.get("source_snapshot") or {}
    settings = investigation.get("settings") or {}
    declared = source.get("target_publication_date") or settings.get(
        "target_publication_date"
    )
    if isinstance(declared, Mapping):
        declared = declared.get(claim_id)
    candidate = parse_date(declared or patent.publication_date)
    verified_value = source.get(
        "target_publication_date_verified",
        settings.get("target_publication_date_verified", False),
    )
    if isinstance(verified_value, Mapping):
        verified_value = verified_value.get(claim_id, False)
    return candidate, bool(verified_value and candidate is not None)


def _derive_human_review_eligibility(
    *,
    context: Mapping[str, Any],
    claim_state: ClaimCheckpoint,
    claim_row: Mapping[str, Any],
    critical_date: date,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], str, bool]:
    """Derive legal lanes from server-stored facts; never trust eligibility input."""

    action = context.get("action") or {}
    action_type = str(action.get("action_type") or "")
    if action_type == "document_date_confirmation":
        raw = context.get("date_fact_revision") or {}
        decision = str(raw.get("decision") or "reopen_review")
        human_confirmed = True
        facts = {
            key: raw.get(key)
            for key in (
                "public_availability_date",
                "publication_date",
                "filing_date",
                "priority_date",
                "source_type",
                "publication_number",
                "authority",
                "cn_application_scope",
                "date_channel",
            )
        }
        evidence = (
            dict(raw.get("date_evidence") or {})
            if isinstance(raw.get("date_evidence"), Mapping)
            else {}
        )
    elif action_type == "evidence_import":
        raw = context.get("evidence_import") or {}
        decision = "derive_from_declared_facts"
        human_confirmed = False
        declared = raw.get("declared_date_facts") or {}
        facts = dict(declared) if isinstance(declared, Mapping) else {}
        evidence = (
            dict(raw.get("date_evidence") or {})
            if isinstance(raw.get("date_evidence"), Mapping)
            else {}
        )
    else:
        raise WorkflowInputError(f"不支持 human review action_type={action_type}")

    target_publication = parse_date(
        claim_state.target_publication_date
        or claim_row.get("target_publication_date")
    )
    if decision == "exclude":
        eligibility = {
            "category": "post_date_lead",
            "novelty_eligible": False,
            "inventive_step_eligible": False,
            "inventive_eligible": False,
            "requires_human_review": False,
            "critical_date": critical_date.isoformat(),
            "public_availability_date": _iso_date(
                facts.get("public_availability_date")
                or facts.get("publication_date")
            ),
            "candidate_filing_date": _iso_date(facts.get("filing_date")),
            "candidate_priority_date": _iso_date(facts.get("priority_date")),
            "target_publication_date": _iso_date(target_publication),
            "discovery_date_channel": str(
                facts.get("date_channel") or "ordinary_prior_art"
            ),
            "reason_codes": ["human_review_excluded_document_version"],
            "explanation": "人工复核已排除该文献版本，不进入新颖性或创造性证据集合。",
        }
        return eligibility, facts, evidence, decision, human_confirmed

    public_fact_key = (
        "public_availability_date"
        if facts.get("public_availability_date") is not None
        else "publication_date"
    )
    public_verified = bool(
        decision != "reopen_review"
        and facts.get(public_fact_key) is not None
        and isinstance(evidence.get(public_fact_key), Mapping)
    )
    result = classify_date_eligibility(
        critical_date=critical_date,
        public_availability_date=facts.get("public_availability_date"),
        publication_date=facts.get("publication_date"),
        candidate_filing_date=facts.get("filing_date"),
        candidate_priority_date=facts.get("priority_date"),
        target_publication_date=target_publication,
        source_type=str(facts.get("source_type") or "document"),
        publication_number=(
            str(facts.get("publication_number"))
            if facts.get("publication_number")
            else None
        ),
        authority=(str(facts.get("authority")) if facts.get("authority") else None),
        cn_application_scope=facts.get("cn_application_scope"),
        critical_date_verified=True,
        public_date_verified=public_verified,
        candidate_filing_date_verified=bool(
            facts.get("filing_date") is not None
            and isinstance(evidence.get("filing_date"), Mapping)
        ),
        candidate_priority_date_verified=bool(
            facts.get("priority_date") is not None
            and isinstance(evidence.get("priority_date"), Mapping)
        ),
        target_publication_date_verified=bool(
            claim_state.target_publication_date_verified and target_publication
        ),
        date_channel=str(facts.get("date_channel") or "ordinary_prior_art"),
    )
    return result.model_dump(), facts, evidence, decision, human_confirmed


def _iso_date(value: Any) -> str | None:
    parsed = parse_date(value)
    return parsed.isoformat() if parsed else None


def _date_evidence_summary(evidence: Mapping[str, Any]) -> str:
    if not evidence:
        return ""
    return json.dumps(
        evidence,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _limitations(state: ClaimCheckpoint) -> list[Limitation]:
    return [Limitation.model_validate(item) for item in state.limitations]


def _previous_iteration_failure_reason(
    round_state: RoundCheckpoint | None,
) -> str:
    """Summarise why the prior query set did not close the frozen gap frontier."""

    if round_state is None:
        return ""
    parts: list[str] = []
    if round_state.stop_reason:
        parts.append(f"轮次收口原因：{round_state.stop_reason}")
    if round_state.qualified_documents_added == 0:
        parts.append("本轮没有新增日期合格文献")
    for failure in round_state.provider_failures[-3:]:
        if str(failure).strip():
            parts.append(f"provider：{str(failure).strip()}")
    for failure in round_state.failure_records[-3:]:
        stage = str(failure.get("stage") or "analysis").strip()
        summary = str(failure.get("safe_summary") or "").strip()
        if summary:
            parts.append(f"{stage}：{summary}")
    gap_reasons = []
    for gap in round_state.gaps_after:
        reason = str(gap.get("rationale") or "").strip()
        if reason and reason not in gap_reasons:
            gap_reasons.append(reason)
    if gap_reasons:
        parts.append("未解决：" + "；".join(gap_reasons[:3]))
    return "；".join(dict.fromkeys(parts))[:2000]


def _gap_search_failure_ledger(state: ClaimCheckpoint) -> list[dict[str, Any]]:
    """Return a bounded, user-safe error ledger for I4-I and the report.

    The ledger is descriptive only: it never upgrades a failed provider or
    analysis result into evidence and never invents a replacement document.
    """

    records: list[dict[str, Any]] = []
    for round_key in sorted(state.rounds, key=int):
        round_state = state.rounds[round_key]
        if round_state.iteration_number <= 1:
            continue
        for item in round_state.failure_records:
            record = dict(item)
            record.setdefault("iteration_number", round_state.iteration_number)
            record.setdefault(
                "gap_search_iteration", round_state.iteration_number - 1
            )
            records.append(record)
        for failure in round_state.provider_failures:
            summary = str(failure).strip()
            if not summary:
                continue
            records.append(
                {
                    "iteration_number": round_state.iteration_number,
                    "gap_search_iteration": round_state.iteration_number - 1,
                    "stage": "I3_PROVIDER_PIPELINE",
                    "error_code": "PROVIDER_PARTIAL",
                    "safe_summary": summary[:500],
                    "affected_feature_ids": _gap_feature_ids(
                        round_state.gaps_after
                        or round_state.gaps_before
                        or state.gaps
                    ),
                }
            )
        failed_queries = sorted(
            query_id
            for query_id, status in round_state.query_statuses.items()
            if status in {"failed", "partial"}
        )
        if failed_queries:
            records.append(
                {
                    "iteration_number": round_state.iteration_number,
                    "gap_search_iteration": round_state.iteration_number - 1,
                    "stage": "I3_SEARCH_FETCH_QUALIFY",
                    "error_code": "ROUND_QUERY_INCOMPLETE",
                    "safe_summary": (
                        "本轮存在未完整完成的查询：" + "、".join(failed_queries[:10])
                    ),
                    "affected_feature_ids": _gap_feature_ids(
                        round_state.gaps_after
                        or round_state.gaps_before
                        or state.gaps
                    ),
                }
            )
    unique: list[dict[str, Any]] = []
    seen: set[str] = set()
    for record in records:
        key = stable_json_sha256(record)
        if key in seen:
            continue
        seen.add(key)
        unique.append(record)
    return unique[-50:]


def _gap_feature_ids(gaps: Sequence[Mapping[str, Any]]) -> list[str]:
    return list(
        dict.fromkeys(
            str(item.get("feature_id"))
            for item in gaps
            if item.get("feature_id")
            and str(item.get("kind") or "") in {"feature_gap", "evidence_gap"}
        )
    )


def _combination_gap_ids(gaps: Sequence[Mapping[str, Any]]) -> list[str]:
    return [
        str(item.get("gap_id") or index)
        for index, item in enumerate(gaps)
        if item.get("kind") == "combination_gap"
    ]


def _pending_date_review_gaps(
    state: ClaimCheckpoint,
    *,
    require_substantive_disclosure: bool = False,
    target_source_sha256: str | None = None,
) -> list[dict[str, Any]]:
    gaps: list[dict[str, Any]] = []
    for document_key, eligibility in sorted(state.date_qualifications.items()):
        record = state.documents.get(document_key, {}).get("record") or {}
        if str(record.get("stage") or "") == EvidenceStage.LEAD.value:
            continue
        if require_substantive_disclosure:
            comparison_payload = state.comparisons.get(document_key)
            if not isinstance(comparison_payload, Mapping):
                continue
            if target_source_sha256 and not _comparison_matches_current_inputs(
                state,
                document_key,
                target_source_sha256=target_source_sha256,
            ):
                continue
            if _supported_count(
                DocumentComparison.model_validate(comparison_payload)
            ) < 1:
                continue
        if not (
            bool(eligibility.get("requires_human_review"))
            or str(eligibility.get("category") or "") == "unknown"
        ):
            continue
        reason_codes = [str(item) for item in eligibility.get("reason_codes") or []]
        rationale = str(eligibility.get("explanation") or "").strip()
        if not rationale:
            rationale = "候选文献的公开时间或日期证据链尚未核验"
        digest = hashlib.sha256(
            f"date_gap:{document_key}:{state.critical_date}".encode("utf-8")
        ).hexdigest()[:24]
        title = str(record.get("title") or document_key).strip()
        gaps.append(
            {
                "gap_id": f"date-{digest}",
                "kind": "date_gap",
                "subtype": (
                    reason_codes[0] if reason_codes else "date_qualification_pending"
                ),
                "feature_id": None,
                "feature_text": None,
                "source_document_ids": [document_key],
                "search_anchor": title,
                "rationale": rationale,
                "reason_codes": reason_codes,
            }
        )
    return gaps


def _merge_raw_gap_frontier(
    current: Sequence[Mapping[str, Any]],
    additions: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for item in (*current, *additions):
        payload = dict(item)
        key = str(payload.get("gap_key") or payload.get("gap_id") or "").strip()
        if not key:
            key = "gap-" + hashlib.sha256(
                json.dumps(
                    payload,
                    ensure_ascii=False,
                    sort_keys=True,
                    default=str,
                ).encode("utf-8")
            ).hexdigest()[:24]
            payload["gap_id"] = key
        result[key] = payload
    return list(result.values())


def _gap_key(value: Mapping[str, Any]) -> str:
    return str(value.get("gap_key") or value.get("gap_id") or "").strip()


def _gap_semantic_identity(value: Mapping[str, Any]) -> tuple[Any, ...]:
    kind = str(value.get("kind") or "")
    # A feature may progress from "not disclosed" to "disclosure uncertain".
    # That is an updated open frontier item, not a resolved feature followed by
    # an unrelated new gap.
    identity_kind = (
        "technical_feature_gap"
        if kind in {"feature_gap", "evidence_gap"}
        else kind
    )
    feature_id = str(value.get("feature_id") or "")
    subtype = (
        ""
        if identity_kind == "technical_feature_gap"
        else str(value.get("subtype") or "")
    )
    sources: tuple[str, ...] = ()
    if kind == "date_gap":
        sources = tuple(sorted(str(item) for item in value.get("source_document_ids") or []))
    return identity_kind, feature_id, subtype, sources


def _preserve_open_gap_keys(
    before: Sequence[Mapping[str, Any]],
    after: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    available: dict[tuple[Any, ...], list[str]] = {}
    for item in before:
        key = _gap_key(item)
        if key:
            available.setdefault(_gap_semantic_identity(item), []).append(key)
    result: list[dict[str, Any]] = []
    used: set[str] = set()
    for raw in after:
        item = dict(raw)
        generated_key = _gap_key(item)
        candidates = available.get(_gap_semantic_identity(item), [])
        prior_key = next((key for key in candidates if key not in used), None)
        if prior_key:
            used.add(prior_key)
            if generated_key and generated_key != prior_key:
                item["analysis_gap_id"] = generated_key
            item["gap_id"] = prior_key
            item["gap_key"] = prior_key
        result.append(item)
    return _merge_raw_gap_frontier([], result)


def _prepare_gap_reconciliation(
    *,
    state: ClaimCheckpoint,
    round_state: RoundCheckpoint,
    closed_by_document_key: str | None,
    closed_reason: str | None,
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]] | None]:
    after = _preserve_open_gap_keys(
        round_state.gaps_before,
        round_state.gaps_after,
    )
    after_keys = {_gap_key(item) for item in after if _gap_key(item)}
    resolutions: dict[str, dict[str, Any]] = {}
    unresolved: list[dict[str, Any]] = []
    for raw in round_state.gaps_before:
        key = _gap_key(raw)
        if not key or key in after_keys:
            continue
        if closed_reason:
            document = state.documents.get(closed_by_document_key or "", {})
            repository_document_id = document.get("repository_document_id")
            if not repository_document_id:
                raise WorkflowInputError(
                    f"关闭 gap {key} 缺少可追溯解决文献"
                )
            resolution = {
                "closed_reason": closed_reason,
                "resolved_by_document_id": repository_document_id,
                "resolution_evidence": {
                    "iteration_number": round_state.iteration_number,
                    "workflow_stop_reason": closed_reason,
                    "resolving_document_key": closed_by_document_key,
                    "gap_before": dict(raw),
                },
            }
        else:
            resolution = _infer_gap_resolution(
                state=state,
                round_state=round_state,
                gap=raw,
            )
        if resolution is None:
            # A gap without a document/human/analysis resolution remains open;
            # absence from a newly computed list is never treated as proof.
            unresolved.append(dict(raw))
            continue
        resolutions[key] = resolution

    if unresolved:
        after = _merge_raw_gap_frontier(after, unresolved)
    return after, resolutions or None


def _infer_gap_resolution(
    *,
    state: ClaimCheckpoint,
    round_state: RoundCheckpoint,
    gap: Mapping[str, Any],
) -> dict[str, Any] | None:
    kind = str(gap.get("kind") or "")
    if kind in {"feature_gap", "evidence_gap"}:
        feature_id = str(gap.get("feature_id") or "")
        supported = _supported_feature_resolution(state, feature_id)
        if supported:
            document_key, document_id, disclosure = supported
            return {
                "closed_reason": (
                    "feature_disclosure_verified"
                    if kind == "feature_gap"
                    else "disclosure_evidence_completed"
                ),
                "resolved_by_document_id": document_id,
                "resolution_evidence": {
                    "iteration_number": round_state.iteration_number,
                    "analysis_module": "I4-S",
                    "resolving_document_key": document_key,
                    "feature_id": feature_id,
                    "disclosure": disclosure,
                },
            }
        return None

    if kind == "date_gap":
        for document_key in gap.get("source_document_ids") or []:
            qualification = state.date_qualifications.get(str(document_key), {})
            category = str(qualification.get("category") or "")
            if not qualification or category == "unknown" or qualification.get(
                "requires_human_review"
            ):
                continue
            document_id = state.documents.get(str(document_key), {}).get(
                "repository_document_id"
            )
            if not document_id:
                continue
            return {
                "closed_reason": "date_qualification_determined",
                "resolved_by_document_id": document_id,
                "resolution_evidence": {
                    "iteration_number": round_state.iteration_number,
                    "analysis_module": "I1.5",
                    "resolving_document_key": str(document_key),
                    "date_qualification": dict(qualification),
                },
            }
        combination = dict(round_state.combination or {})
        if combination.get("all_documents_date_eligible"):
            return _combination_gap_resolution(
                state=state,
                round_state=round_state,
                gap=gap,
                closed_reason="combination_dates_verified",
            )
        return None

    if kind == "combination_gap":
        combination = dict(round_state.combination or {})
        subtype = str(gap.get("subtype") or "")
        criterion_name = {
            "technical_problem": "related_technical_problem",
            "combination_motivation": "combination_motivation",
            "teaching_away_review": "teaching_away",
            "technical_effect": "technical_effect",
            "common_knowledge_evidence": "combination_motivation",
        }.get(subtype)
        if combination and (
            combination.get("evidence_complete")
            or (criterion_name and combination.get(criterion_name))
        ):
            return _combination_gap_resolution(
                state=state,
                round_state=round_state,
                gap=gap,
                closed_reason=f"{subtype or 'combination'}_analysis_completed",
                criterion_name=criterion_name,
            )

        current_d1 = str(
            (round_state.closest_prior_art or {}).get("document_id") or ""
        )
        previous_sources = {
            str(item) for item in gap.get("source_document_ids") or []
        }
        if current_d1 and current_d1 not in previous_sources:
            document_id = state.documents.get(current_d1, {}).get(
                "repository_document_id"
            )
            if document_id:
                return {
                    "closed_reason": "superseded_by_d1_reselection",
                    "resolved_by_document_id": document_id,
                    "resolution_evidence": {
                        "iteration_number": round_state.iteration_number,
                        "analysis_module": "I4-C",
                        "analysis_decision": "D1 已版本化重选，旧组合问题不再适用于当前组合路径",
                        "previous_document_keys": sorted(previous_sources),
                        "replacement_d1_document_key": current_d1,
                        "closest_prior_art": dict(
                            round_state.closest_prior_art or {}
                        ),
                    },
                }
    return None


def _supported_feature_resolution(
    state: ClaimCheckpoint,
    feature_id: str,
) -> tuple[str, str, dict[str, Any]] | None:
    if not feature_id:
        return None
    for document_key, payload in sorted(state.comparisons.items()):
        binding = (
            payload.get("_input_binding")
            if isinstance(payload, Mapping)
            else None
        )
        if isinstance(binding, Mapping):
            if not _comparison_matches_current_inputs(
                state,
                document_key,
                target_source_sha256=str(
                    binding.get("target_source_sha256") or ""
                ),
            ):
                continue
        elif (
            state.documents.get(document_key, {}).get(
                "repository_document_version_id"
            )
            is not None
        ):
            # Persisted/versioned documents must never use an unbound legacy
            # comparison.  The fallback only preserves in-memory old checkpoints.
            continue
        record = state.documents.get(document_key, {}).get("record") or {}
        if str(record.get("stage") or "") != EvidenceStage.QUALIFIED.value:
            continue
        comparison = DocumentComparison.model_validate(payload)
        for disclosure in comparison.disclosures:
            if disclosure.feature_id != feature_id:
                continue
            if not (
                disclosure.status.value
                in {"explicit", "direct_and_unambiguous", "necessarily_implicit"}
                and disclosure.confidence >= 0.7
                and disclosure.evidence_quote.strip()
                and disclosure.evidence_location.strip()
            ):
                continue
            document_id = state.documents.get(document_key, {}).get(
                "repository_document_id"
            )
            if document_id:
                return (
                    document_key,
                    str(document_id),
                    disclosure.model_dump(mode="json"),
                )
    return None


def _combination_gap_resolution(
    *,
    state: ClaimCheckpoint,
    round_state: RoundCheckpoint,
    gap: Mapping[str, Any],
    closed_reason: str,
    criterion_name: str | None = None,
) -> dict[str, Any] | None:
    combination = dict(round_state.combination or {})
    document_keys = [
        str(item)
        for item in combination.get("combination_document_ids") or []
        if str(item)
    ]
    if not document_keys:
        document_keys = [
            str(item) for item in gap.get("source_document_ids") or [] if str(item)
        ]
    resolving_key = next(
        (
            item
            for item in document_keys
            if state.documents.get(item, {}).get("repository_document_id")
        ),
        None,
    )
    if resolving_key is None:
        return None
    evidence: dict[str, Any] = {
        "iteration_number": round_state.iteration_number,
        "analysis_module": "I4-I",
        "resolving_document_keys": document_keys,
        "combination_model": combination.get("model"),
        "evidence_complete": bool(combination.get("evidence_complete")),
    }
    if criterion_name:
        evidence["criterion_name"] = criterion_name
        evidence["criterion"] = combination.get(criterion_name)
    return {
        "closed_reason": closed_reason,
        "resolved_by_document_id": state.documents[resolving_key][
            "repository_document_id"
        ],
        "resolution_evidence": evidence,
    }


def _round_progress_signature(round_state: RoundCheckpoint) -> str:
    return "|".join(
        (
            str(round_state.iteration_number),
            ",".join(sorted(round_state.document_keys)),
            ",".join(
                sorted(
                    str(item.get("gap_id") or item.get("feature_id") or "")
                    for item in round_state.gaps_after
                )
            ),
            str(round_state.qualified_documents_added),
            "complete" if round_state.provider_complete else "partial",
        )
    )


def _coerce_search_batch(
    value: SearchBatch | Sequence[EvidenceRecord],
    provider: str,
) -> SearchBatch:
    if isinstance(value, SearchBatch):
        return value
    return SearchBatch(provider=provider, records=tuple(value), complete=True)


def _provider_failure(
    provider: str,
    operation: str,
    error: Exception,
    *,
    network_used: bool = False,
) -> SearchBatch:
    return SearchBatch(
        provider=provider,
        complete=False,
        errors=(f"{operation}: {type(error).__name__}: {_safe_error(error)}",),
        network_used=network_used,
    )


def _query_relevant(record: EvidenceRecord, query: SearchQuery) -> bool:
    """Conservative recall gate; I4-S still decides actual feature disclosure.

    ``technical_subject`` can legitimately be Chinese while an NPL query and
    the retrieved paper are English.  Requiring the Chinese string verbatim
    therefore rejects every cross-language hit.  A same-language hit must
    contain the subject plus another meaningful query anchor; when the subject
    is not present, at least two meaningful expression anchors must occur.
    This permits bilingual recall without treating one broad word as evidence.
    """

    raw_text = _record_text(record).lower()
    if not raw_text:
        return False
    compact_text = re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", raw_text)
    compact_subject = re.sub(
        r"[^0-9a-z\u4e00-\u9fff]+", "", query.technical_subject.lower()
    )
    subject_matches = bool(compact_subject and compact_subject in compact_text)

    anchors = _meaningful_relevance_anchors(query.expression)
    matched = {
        anchor
        for anchor in anchors
        if re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", anchor) in compact_text
    }
    if subject_matches:
        non_subject_matches = {
            anchor
            for anchor in matched
            if re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", anchor)
            != compact_subject
        }
        return bool(non_subject_matches)
    return len(matched) >= 2


_RELEVANCE_QUERY_STOPWORDS = {
    "a",
    "an",
    "and",
    "apparatus",
    "based",
    "by",
    "device",
    "for",
    "from",
    "in",
    "method",
    "of",
    "or",
    "patent",
    "system",
    "technology",
    "the",
    "to",
    "using",
    "with",
}


def _meaningful_relevance_anchors(expression: str) -> set[str]:
    """Return stable content anchors, excluding Boolean/common query words."""

    anchors: set[str] = set()
    for token in re.findall(r"[a-z][a-z0-9-]*|[\u4e00-\u9fff]{2,}", expression.lower()):
        normalized = token.strip("-")
        if not normalized or normalized in _RELEVANCE_QUERY_STOPWORDS:
            continue
        if re.fullmatch(r"[a-z0-9-]+", normalized) and len(normalized) < 3:
            continue
        anchors.add(normalized)
    return anchors


def _safe_stem(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", str(value).strip())
    return (cleaned or "document")[:120]


def _source_suffix(media_type: str, kind: str) -> str:
    lowered = str(media_type or "").lower()
    if "pdf" in lowered or kind == "pdf":
        return ".pdf"
    if "html" in lowered or kind == "html":
        return ".html"
    if "json" in lowered:
        return ".json"
    if "xml" in lowered:
        return ".xml"
    if lowered.startswith("text/") or kind == "text":
        return ".txt"
    return ".bin"


def _primary_artifact_summary(
    stored: Mapping[str, Any],
    artifact: RetrievedArtifact,
) -> dict[str, Any]:
    return {
        **dict(stored),
        "provider": artifact.provider,
        "kind": artifact.kind,
        "source_url": artifact.source_url,
        "media_type": str(stored.get("mime_type") or artifact.media_type),
        "content_sha256": str(stored.get("sha256") or artifact.content_sha256),
        "content_length": int(stored.get("byte_size") or len(artifact.content)),
        "retrieved_at": artifact.retrieved_at,
        "source_metadata_sha256": (
            artifact.source_metadata_artifact.content_sha256
            if artifact.source_metadata_artifact is not None
            else None
        ),
        "request_attempt_log": [
            dict(item) for item in artifact.request_attempt_log
        ],
        "is_primary_source": True,
        "local_immutable_snapshot": True,
    }


def _demote_primary_artifacts(
    artifacts: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, Any], ...]:
    return tuple(
        {
            **dict(item),
            **({"is_primary_source": False} if item.get("is_primary_source") else {}),
        }
        for item in artifacts
    )


def _has_verified_primary_snapshot(
    record: EvidenceRecord,
    *,
    allowed_root: str | Path | None = None,
) -> bool:
    return _primary_local_artifact(record, allowed_root=allowed_root) is not None


def _artifact_from_record_pdf(
    record: EvidenceRecord,
    content: bytes,
) -> RetrievedArtifact:
    return RetrievedArtifact(
        provider=record.provider,
        kind="pdf",
        source_url=record.pdf_url or record.source_url,
        media_type="application/pdf",
        content=content,
        retrieved_at=record.retrieved_at or "",
    )


def _sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _verified_local_artifact_uri(value: Mapping[str, Any]) -> str | None:
    """Return an immutable local artifact path only after a SHA check."""

    uri = str(value.get("uri") or value.get("local_path") or "").strip()
    digest = str(
        value.get("sha256") or value.get("content_sha256") or ""
    ).strip().lower()
    if not uri or len(digest) != 64:
        return None
    raw = Path(uri).expanduser()
    if not raw.is_absolute():
        return None
    try:
        path = raw.resolve(strict=True)
        if not path.is_file() or _sha256_file(path) != digest:
            return None
    except OSError:
        return None
    return str(path)


def _image_suffix(media_type: str) -> str:
    lowered = str(media_type or "").lower()
    if "png" in lowered:
        return ".png"
    if "webp" in lowered:
        return ".webp"
    if "gif" in lowered:
        return ".gif"
    return ".jpg"


def _document_key(record: EvidenceRecord) -> str:
    external = record.publication_number or record.external_id or record.content_sha256
    if not external:
        external = str(uuid.uuid5(uuid.NAMESPACE_URL, record.source_url))
    cleaned = re.sub(r"\s+", "", str(external)).strip()
    return f"{record.provider}:{cleaned}"


def _record_text(record: EvidenceRecord) -> str:
    return str(
        record.full_text
        or "\n\n".join(
            item
            for item in (record.title, record.abstract, record.description, record.claims)
            if item
        )
    ).strip()


def _provider_structured_readable_document(
    record: EvidenceRecord,
    *,
    source_sha256: str,
) -> dict[str, Any]:
    substantive = "\n\n".join(
        str(value or "").strip()
        for value in (record.description, record.claims)
        if str(value or "").strip()
    )
    ready = len(re.sub(r"\s+", "", substantive)) >= 200
    provider_full_text = str(record.full_text or "").strip()
    full_text = (
        provider_full_text
        if len(re.sub(r"\s+", "", provider_full_text)) >= 200
        else substantive
    )
    return {
        "schema_version": READABLE_PATENT_VERSION,
        "source_sha256": source_sha256,
        "source_kind": "provider_structured_text",
        "normalization_status": "completed" if ready else "incomplete",
        "analysis_ready": ready,
        "analysis_readiness_reason": (
            "provider supplied substantive description/claims text"
            if ready
            else "provider did not supply a complete readable document"
        ),
        "page_count": None,
        "processed_page_count": None,
        "text_page_count": None,
        "ocr_page_count": 0,
        "failed_pages": [],
        "sections": [
            {
                "section": key,
                "anchor": key,
                "kind": "provider_text",
                "text": str(value).strip(),
            }
            for key, value in (
                ("description", record.description),
                ("claims", record.claims),
            )
            if str(value or "").strip()
        ],
        "full_text": full_text,
    }


def _failed_readable_document(
    source_sha256: str,
    reason: str,
) -> dict[str, Any]:
    return {
        "schema_version": READABLE_PATENT_VERSION,
        "source_sha256": source_sha256,
        "source_kind": "normalization_failed",
        "normalization_status": "failed",
        "analysis_ready": False,
        "analysis_readiness_reason": reason,
        "page_count": None,
        "processed_page_count": None,
        "text_page_count": None,
        "ocr_page_count": 0,
        "failed_pages": [],
        "sections": [],
        "full_text": "",
    }


def _readable_artifact_summaries(
    json_artifact: Mapping[str, Any],
    markdown_artifact: Mapping[str, Any],
) -> tuple[dict[str, Any], ...]:
    return (
        {
            **dict(json_artifact),
            "kind": "readable_patent_json",
            "is_primary_source": False,
            "local_immutable_snapshot": True,
        },
        {
            **dict(markdown_artifact),
            "kind": "readable_patent_markdown",
            "is_primary_source": False,
            "local_immutable_snapshot": True,
        },
    )


def _readable_document_audit(record: EvidenceRecord) -> dict[str, Any]:
    readable = (
        dict(record.readable_document)
        if isinstance(record.readable_document, Mapping)
        else {}
    )
    full_text = str(readable.get("full_text") or "")
    sections = readable.get("sections")
    section_rows = (
        [dict(item) for item in sections if isinstance(item, Mapping)]
        if isinstance(sections, Sequence)
        and not isinstance(sections, (str, bytes, bytearray))
        else []
    )
    return {
        "schema_version": str(readable.get("schema_version") or ""),
        "source_sha256": str(readable.get("source_sha256") or ""),
        "source_kind": str(readable.get("source_kind") or ""),
        "normalization_status": str(
            readable.get("normalization_status") or ""
        ),
        "analysis_ready": record.analysis_ready,
        "analysis_readiness_reason": record.analysis_readiness_reason,
        "page_count": readable.get("page_count"),
        "processed_page_count": readable.get("processed_page_count"),
        "text_page_count": readable.get("text_page_count"),
        "ocr_page_count": readable.get("ocr_page_count"),
        "failed_pages": list(readable.get("failed_pages") or []),
        "section_count": len(section_rows),
        "section_page_starts": sorted(
            {
                int(item["page_start"])
                for item in section_rows
                if isinstance(item.get("page_start"), int)
                and int(item["page_start"]) > 0
            }
        ),
        "full_text_sha256": (
            hashlib.sha256(full_text.encode("utf-8")).hexdigest()
            if full_text
            else None
        ),
        "compact_char_count": len(re.sub(r"\s+", "", full_text)),
    }


def _record_images(record: EvidenceRecord) -> list[str]:
    images = [str(item) for item in record.image_urls if str(item).strip()]
    for artifact in record.artifacts:
        kind = str(artifact.get("kind") or artifact.get("artifact_type") or "").lower()
        mime = str(artifact.get("media_type") or artifact.get("mime_type") or "").lower()
        if kind.startswith("provider_"):
            continue
        if "image" not in kind and not mime.startswith("image/"):
            continue
        value = artifact.get("uri") or artifact.get("local_path") or artifact.get("source_url")
        if value:
            images.append(str(value))
    return list(dict.fromkeys(images))


def _primary_local_artifact(
    record: EvidenceRecord,
    *,
    allowed_root: str | Path | None = None,
) -> Mapping[str, Any] | None:
    expected = str(record.content_sha256 or "")
    resolved_root: Path | None = None
    if allowed_root is not None:
        try:
            resolved_root = Path(allowed_root).expanduser().resolve(strict=True)
        except OSError:
            return None
    for artifact in record.artifacts:
        if not artifact.get("is_primary_source"):
            continue
        if not artifact.get("local_immutable_snapshot"):
            continue
        uri = str(artifact.get("uri") or "")
        digest = str(artifact.get("sha256") or artifact.get("content_sha256") or "")
        if not uri or not digest or digest != expected:
            continue
        raw_path = Path(uri).expanduser()
        if not raw_path.is_absolute():
            continue
        try:
            path = raw_path.resolve(strict=True)
        except OSError:
            continue
        if not path.is_file():
            continue
        if resolved_root is not None and not (
            path == resolved_root or resolved_root in path.parents
        ):
            continue
        try:
            if _sha256_file(path) != digest:
                continue
        except OSError:
            continue
        return {**dict(artifact), "uri": str(path)}
    return None


def _requires_local_source_snapshot(record: EvidenceRecord) -> bool:
    return (
        record.stage is not EvidenceStage.LEAD
        and record.provider not in _NON_LIVE_EVIDENCE_PROVIDERS
        and str(record.source_url or "").startswith(("http://", "https://"))
    )


def _record_ready_for_single_reference(record: EvidenceRecord) -> bool:
    if record.provider in _NON_LIVE_EVIDENCE_PROVIDERS:
        return record.stage is EvidenceStage.QUALIFIED
    readable = record.readable_document
    analysis_ready = bool(
        record.analysis_ready
        and isinstance(readable, Mapping)
        and readable.get("schema_version") == READABLE_PATENT_VERSION
        and readable.get("analysis_ready") is True
        and str(readable.get("source_sha256") or "")
        == str(record.content_sha256 or "")
        and len(re.sub(r"\s+", "", str(readable.get("full_text") or ""))) >= 200
    )
    if analysis_ready and readable.get("page_count") is not None:
        page_count = readable.get("page_count")
        processed_page_count = readable.get("processed_page_count")
        failed_pages = readable.get("failed_pages")
        analysis_ready = bool(
            isinstance(page_count, int)
            and page_count > 0
            and processed_page_count == page_count
            and isinstance(failed_pages, list)
            and not failed_pages
        )
    if not analysis_ready or not _has_verified_primary_snapshot(record):
        return False
    if record.stage is EvidenceStage.QUALIFIED:
        return True
    # Technical comparison and date qualification are independent.  A real,
    # frozen document with an unresolved date must still enter I4-S so the
    # system can tell the reviewer whether the date gap is outcome-determinative.
    return bool(
        record.stage is EvidenceStage.RETRIEVED
        and _has_verified_primary_snapshot(record)
    )


def _limitation_set_sha256(state: ClaimCheckpoint) -> str:
    payload = [
        item.model_dump(mode="json")
        for item in sorted(
            _limitations(state), key=lambda value: (value.sequence, value.feature_id)
        )
    ]
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _comparison_input_binding(
    state: ClaimCheckpoint,
    document_key: str,
    *,
    target_source_sha256: str,
) -> dict[str, str | None]:
    document = state.documents.get(document_key, {})
    record = document.get("record") if isinstance(document, Mapping) else {}
    if not isinstance(record, Mapping):
        record = {}
    return {
        "target_source_sha256": str(target_source_sha256 or ""),
        "analysis_rule_version": I4S_RULE_VERSION,
        "prompt_version": I4S_PROMPT_VERSION,
        "limitation_set_sha256": _limitation_set_sha256(state),
        "document_id": (
            str(document.get("repository_document_id"))
            if document.get("repository_document_id")
            else None
        ),
        "document_version_id": (
            str(document.get("repository_document_version_id"))
            if document.get("repository_document_version_id")
            else None
        ),
        "document_content_sha256": str(record.get("content_sha256") or "") or None,
    }


def _comparison_matches_current_inputs(
    state: ClaimCheckpoint,
    document_key: str,
    *,
    target_source_sha256: str,
) -> bool:
    payload = state.comparisons.get(document_key)
    if not isinstance(payload, Mapping):
        return False
    if (
        str(payload.get("analysis_rule_version") or "") != I4S_RULE_VERSION
        or str(payload.get("structural_review_status") or "not_needed")
        == "model_error"
    ):
        return False
    binding = payload.get("_input_binding")
    return bool(
        isinstance(binding, Mapping)
        and dict(binding)
        == _comparison_input_binding(
            state,
            document_key,
            target_source_sha256=target_source_sha256,
        )
    )


def _current_gap_reuse_materials(
    state: ClaimCheckpoint,
    *,
    target_source_sha256: str,
) -> tuple[list[DocumentComparison], dict[str, dict[str, str]]]:
    """Return only current I4-S results plus their strict reusable version keys."""

    comparisons: list[DocumentComparison] = []
    reuse_keys: dict[str, dict[str, str]] = {}
    for document_key, comparison_payload in sorted(state.comparisons.items()):
        if not isinstance(comparison_payload, Mapping):
            continue
        if not _comparison_matches_current_inputs(
            state,
            document_key,
            target_source_sha256=target_source_sha256,
        ):
            continue
        comparisons.append(DocumentComparison.model_validate(comparison_payload))
        document = state.documents.get(document_key) or {}
        record = document.get("record") or {}
        qualification = state.date_qualifications.get(document_key) or {}
        binding = comparison_payload.get("_input_binding") or {}
        reuse_values = {
            "document_version_id": str(
                document.get("repository_document_version_id") or ""
            ),
            "content_sha256": str(
                (record if isinstance(record, Mapping) else {}).get(
                    "content_sha256"
                )
                or (binding if isinstance(binding, Mapping) else {}).get(
                    "document_content_sha256"
                )
                or ""
            ),
            "limitation_version_id": str(
                (binding if isinstance(binding, Mapping) else {}).get(
                    "limitation_set_sha256"
                )
                or ""
            ),
            "date_qualification_revision": str(
                (
                    qualification
                    if isinstance(qualification, Mapping)
                    else {}
                ).get("repository_qualification_id")
                or ""
            ),
            "i4s_run_id": str(
                comparison_payload.get("_i4s_module_run_id") or ""
            ),
            "i4s_rule_version": str(
                comparison_payload.get("analysis_rule_version") or ""
            ),
        }
        if all(reuse_values.values()):
            reuse_keys[document_key] = reuse_values
    return comparisons, reuse_keys


def _comparison_has_verified_disclosure(comparison: DocumentComparison) -> bool:
    return (
        comparison.structural_review_status != "model_error"
        and _supported_count(comparison) > 0
    )


def _require_completed_structural_review(
    comparison: DocumentComparison,
) -> None:
    """Keep human-review facts unapplied until the complete I4-S is usable."""

    if comparison.structural_review_status != "model_error":
        return
    error_code = (
        comparison.structural_review_error_code.strip()
        or "STRUCTURAL_REVIEW_MODEL_ERROR"
    )
    raise WorkflowExecutionError(
        "I4-S 整体结构复核未完成，人工复核事实尚未应用并将保留重试: "
        f"{error_code}"
    )


def _complete_novelty_documents(state: ClaimCheckpoint) -> list[str]:
    result: list[str] = []
    for document_key, value in state.comparisons.items():
        binding = value.get("_input_binding") if isinstance(value, Mapping) else None
        if not isinstance(binding, Mapping) or not _comparison_matches_current_inputs(
            state,
            document_key,
            target_source_sha256=str(binding.get("target_source_sha256") or ""),
        ):
            continue
        record = state.documents.get(document_key, {}).get("record") or {}
        if str(record.get("stage") or "") != EvidenceStage.QUALIFIED.value:
            continue
        if single_reference_fully_discloses(
            DocumentComparison.model_validate(value),
            _limitations(state),
            eligible_for_novelty=bool(
                state.date_qualifications.get(document_key, {}).get(
                    "novelty_eligible"
                )
            ),
        ):
            result.append(document_key)
    return result


def _select_novelty_document(
    state: ClaimCheckpoint,
    candidates: Sequence[str],
) -> str | None:
    unique = [item for item in dict.fromkeys(candidates) if item in state.comparisons]
    if not unique:
        return None
    return sorted(
        unique,
        key=lambda document_key: (
            -DocumentComparison.model_validate(
                state.comparisons[document_key]
            ).evidence_completeness,
            -_supported_count(
                DocumentComparison.model_validate(state.comparisons[document_key])
            ),
            document_key,
        ),
    )[0]


def _checkpoint_document_text(state: ClaimCheckpoint, document_key: str) -> str:
    record = state.documents.get(document_key, {}).get("record") or {}
    return str(
        record.get("full_text")
        or "\n\n".join(
            str(record.get(key) or "")
            for key in ("title", "abstract", "description", "claims")
            if record.get(key)
        )
    ).strip()


def _checkpoint_document_images(state: ClaimCheckpoint, document_key: str) -> list[str]:
    record = state.documents.get(document_key, {}).get("record") or {}
    images = [str(item) for item in record.get("image_urls") or [] if str(item).strip()]
    for artifact in record.get("artifacts") or []:
        kind = str(artifact.get("kind") or artifact.get("artifact_type") or "").lower()
        mime = str(artifact.get("media_type") or artifact.get("mime_type") or "").lower()
        if "image" in kind or mime.startswith("image/"):
            value = artifact.get("uri") or artifact.get("local_path") or artifact.get("source_url")
            if value:
                images.append(str(value))
    return list(dict.fromkeys(images))


def _parse_datetime(value: str | datetime | None) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _supported_count(comparison: DocumentComparison) -> int:
    return sum(
        1
        for item in comparison.disclosures
        if item.status.value
        in {"explicit", "direct_and_unambiguous", "necessarily_implicit"}
        and item.confidence >= 0.7
        and bool(item.evidence_quote)
        and bool(item.evidence_location)
    )


def _best_coverage_comparison(
    comparisons: Sequence[DocumentComparison],
    limitations: Sequence[Limitation],
) -> DocumentComparison:
    del limitations
    return sorted(
        comparisons,
        key=lambda item: (-_supported_count(item), -item.evidence_completeness, item.document_id),
    )[0]


def _combination_candidates(
    *,
    closest_document_id: str,
    comparisons: Sequence[DocumentComparison],
    limitations: Sequence[Limitation],
) -> list[DocumentComparison]:
    by_id = {item.document_id: item for item in comparisons}
    closest = by_id[closest_document_id]
    selected = [closest]
    covered = {
        item.feature_id
        for item in closest.disclosures
        if item.status.value
        in {"explicit", "direct_and_unambiguous", "necessarily_implicit"}
        and item.confidence >= 0.7
        and item.evidence_quote
        and item.evidence_location
    }
    mandatory = {item.feature_id for item in limitations if item.mandatory}
    remaining = [item for item in comparisons if item.document_id != closest_document_id]
    while remaining and len(selected) < 3 and not mandatory.issubset(covered):
        ranked = sorted(
            remaining,
            key=lambda comparison: (
                -len(
                    {
                        disclosure.feature_id
                        for disclosure in comparison.disclosures
                        if disclosure.feature_id not in covered
                        and disclosure.status.value
                        in {"explicit", "direct_and_unambiguous", "necessarily_implicit"}
                        and disclosure.confidence >= 0.7
                        and disclosure.evidence_quote
                        and disclosure.evidence_location
                    }
                ),
                -comparison.evidence_completeness,
                comparison.document_id,
            ),
        )
        candidate = ranked[0]
        selected.append(candidate)
        remaining.remove(candidate)
        covered.update(
            disclosure.feature_id
            for disclosure in candidate.disclosures
            if disclosure.status.value
            in {"explicit", "direct_and_unambiguous", "necessarily_implicit"}
            and disclosure.confidence >= 0.7
            and disclosure.evidence_quote
            and disclosure.evidence_location
        )
    return selected


def _merge_gaps(
    feature_gaps: Sequence[AnalysisGap],
    combination_gaps: Sequence[AnalysisGap],
    *,
    resolved_feature_ids: set[str] | None = None,
) -> list[dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    resolved = resolved_feature_ids or set()
    for item in feature_gaps:
        if item.feature_id and item.feature_id in resolved:
            continue
        result[item.gap_id] = item.model_dump(mode="json")
    for item in combination_gaps:
        result[item.gap_id] = item.model_dump(mode="json")
    return list(result.values())


def _claim_result(state: ClaimCheckpoint) -> ClaimWorkflowResult:
    novelty_document: str | None = None
    if state.status == ClaimStatus.NOVELTY_EVIDENCE_COMPLETE.value:
        for key, comparison in state.comparisons.items():
            binding = (
                comparison.get("_input_binding")
                if isinstance(comparison, Mapping)
                else None
            )
            if not isinstance(binding, Mapping) or not _comparison_matches_current_inputs(
                state,
                key,
                target_source_sha256=str(binding.get("target_source_sha256") or ""),
            ):
                continue
            if single_reference_fully_discloses(
                DocumentComparison.model_validate(comparison),
                _limitations(state),
                eligible_for_novelty=bool(
                    state.date_qualifications.get(key, {}).get("novelty_eligible")
                ),
            ):
                novelty_document = key
                break
    return ClaimWorkflowResult(
        claim_id=state.claim_id,
        claim_investigation_id=str(state.claim_investigation_id),
        status=state.status,
        rounds_completed=state.current_round,
        novelty_document_id=novelty_document,
        closest_prior_art_document_id=(state.current_closest_prior_art or {}).get(
            "document_id"
        ),
        unresolved_gaps=list(state.gaps),
        provider_failures=list(
            dict.fromkeys(
                [*state.historical_provider_failures, *state.provider_failures]
            )
        ),
        analysis_failures=list(
            dict.fromkeys(
                [*state.historical_analysis_failures, *state.analysis_failures]
            )
        ),
        terminal_reason=state.terminal_reason,
    )


def _execution_result_from_checkpoint(
    checkpoint: WorkflowCheckpoint,
) -> WorkflowExecutionResult:
    results = [
        _claim_result(checkpoint.claims[key]) for key in sorted(checkpoint.claims)
    ]
    return WorkflowExecutionResult(
        investigation_id=checkpoint.investigation_id,
        status=checkpoint.status,
        claim_results=results,
        checkpoint=checkpoint,
        provider_failures=[
            failure for item in results for failure in item.provider_failures
        ],
    )


def _aggregate_continuation_batch_status(
    results: Sequence[ClaimWorkflowResult],
) -> str:
    if not results:
        return "failed"
    if any(item.provider_failures or item.analysis_failures for item in results):
        return "partial"
    statuses = {item.status for item in results}
    if statuses == {ClaimStatus.FAILED.value}:
        return "failed"
    if statuses.intersection(
        {
            ClaimStatus.PARTIAL.value,
            ClaimStatus.FAILED.value,
            ClaimStatus.NEEDS_HUMAN_REVIEW.value,
            ClaimStatus.CANCELLED.value,
        }
    ):
        return "partial"
    return "succeeded"


def _aggregate_investigation_status(results: Sequence[ClaimWorkflowResult]) -> str:
    if not results:
        return InvestigationStatus.FAILED.value
    statuses = {item.status for item in results}
    if statuses == {ClaimStatus.FAILED.value}:
        return InvestigationStatus.FAILED.value
    if ClaimStatus.CANCELLED.value in statuses:
        return InvestigationStatus.CANCELLED.value
    if ClaimStatus.NEEDS_HUMAN_REVIEW.value in statuses:
        return InvestigationStatus.NEEDS_HUMAN_REVIEW.value
    # A claim may have a sufficient single-document/combination evidence pack
    # despite another configured source failing.  Preserve the claim's legal
    # evidence terminal state, but never label the overall search scope fully
    # completed when a provider or analysis branch was incomplete.
    if any(item.provider_failures or item.analysis_failures for item in results):
        return InvestigationStatus.PARTIAL.value
    if statuses.intersection({ClaimStatus.PARTIAL.value, ClaimStatus.FAILED.value}):
        return InvestigationStatus.PARTIAL.value
    if statuses.issubset(
        {
            ClaimStatus.NOVELTY_EVIDENCE_COMPLETE.value,
            ClaimStatus.INVENTIVE_STEP_EVIDENCE_COMPLETE.value,
            ClaimStatus.EXHAUSTED.value,
            ClaimStatus.LEGACY_EXHAUSTED.value,
        }
    ):
        return InvestigationStatus.COMPLETED.value
    return InvestigationStatus.PARTIAL.value


def _safe_error(error: Exception) -> str:
    text = str(error).strip()
    return text[:500] if text else type(error).__name__


def _append_unique(values: list[str], value: str) -> None:
    if value not in values:
        values.append(value)


__all__ = [
    "EvidenceGateway",
    "InvalidityWorkflow",
    "SearchBatch",
    "WorkflowCheckpoint",
    "WorkflowError",
    "WorkflowExecutionError",
    "WorkflowExecutionResult",
    "WorkflowInputError",
    "build_evidence_gateway",
    "build_workflow",
]
