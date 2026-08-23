from __future__ import annotations

from collections import defaultdict
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
from pathlib import Path
from typing import Any
import uuid

import fitz
import pytest

from invalidity.artifacts import ArtifactStore
from invalidity.analysis import (
    AnalysisGap,
    AnalysisValidationError,
    CombinationCriterion,
    FeatureCoverage,
    InventiveStepAssessment,
    ObviousnessPrecheckAssessment,
    QueryPlan,
)
from invalidity.contracts import (
    ClaimSnapshot,
    ClaimStatus,
    DateChannel,
    DisclosureStatus,
    DocumentComparison,
    FeatureDisclosure,
    I4S_PROMPT_VERSION,
    I4S_RULE_VERSION,
    InvestigationStatus,
    Limitation,
    PatentSnapshot,
    SearchQuery,
)
from invalidity.providers import (
    ArxivProvider,
    CompositeNplProvider,
    EvidenceRecord,
    EvidenceStage,
    ProviderContentError,
    ProviderRequestError,
    RetrievedArtifact,
    WebEvidenceProvider,
)
from invalidity.patsnap import PatsnapProvider
from invalidity.workflow import (
    ClaimCheckpoint,
    ConfiguredEvidenceGateway,
    InvalidityWorkflow,
    RoundCheckpoint,
    SearchBatch,
    WorkflowExecutionError,
    WorkflowInputError,
    _require_completed_structural_review,
    _prepare_gap_reconciliation,
    _query_relevant,
    _record_ready_for_single_reference,
    _record_human_review_frontier,
)


def _uuid() -> str:
    return str(uuid.uuid4())


def _analysis_ready_document(
    source_sha256: str,
    full_text: str,
    *,
    page_count: int | None = None,
) -> dict[str, Any]:
    sections = (
        [
            {
                "section": "pdf_page",
                "anchor": f"page-{page}",
                "kind": "pdf_text",
                "page_start": page,
                "page_end": page,
                "text": full_text,
            }
            for page in range(1, page_count + 1)
        ]
        if page_count is not None
        else [
            {
                "section": "description",
                "anchor": "description",
                "kind": "provider_text",
                "text": full_text,
            }
        ]
    )
    return {
        "schema_version": "readable-patent-v1",
        "source_sha256": source_sha256,
        "source_kind": "pdf_text" if page_count is not None else "provider_structured_text",
        "normalization_status": "completed",
        "analysis_ready": True,
        "analysis_readiness_reason": "test full-document coverage",
        "page_count": page_count,
        "processed_page_count": page_count,
        "text_page_count": page_count,
        "ocr_page_count": 0,
        "failed_pages": [],
        "sections": sections,
        "full_text": full_text,
    }


@pytest.mark.parametrize("provider", ["fixture", "fixture_simulation", "manual"])
def test_non_live_fixture_and_manual_records_keep_contract_compatibility(
    provider: str,
) -> None:
    record = EvidenceRecord(
        provider=provider,
        source_type="fixture_contract",
        external_id=f"{provider}-document",
        title="explicit non-live contract document",
        source_url=f"{provider}://contract/document",
        stage=EvidenceStage.QUALIFIED,
        full_text="short fixture text",
        content_sha256=hashlib.sha256(b"short fixture text").hexdigest(),
        provenance={
            "fixture_only": provider != "manual",
            "legal_evidence_allowed": False,
        },
    )

    assert _record_ready_for_single_reference(record) is True


class FakeRepository:
    def __init__(self, source_snapshot: dict[str, Any]) -> None:
        self.investigation = {
            "id": _uuid(),
            "status": "queued",
            "source_snapshot": source_snapshot,
            "settings": {"max_rounds": 3},
            "budgets": {},
            "workflow_state": {},
            "state_version": 0,
        }
        self.claims: dict[str, dict[str, Any]] = {}
        self.limitations: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self.module_runs: list[dict[str, Any]] = []
        self.status_history: dict[str, list[str]] = defaultdict(list)
        self.persisted: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self.continuation_batches: dict[str, dict[str, Any]] = {}
        self.critical_date_confirmations: dict[str, dict[str, Any]] = {}

    def get_investigation(self, investigation_id: str) -> dict[str, Any] | None:
        return self.investigation if investigation_id == self.investigation["id"] else None

    def update_investigation(self, investigation_id: str, **kwargs: Any) -> dict[str, Any]:
        assert investigation_id == self.investigation["id"]
        self.investigation.update(
            {key: value for key, value in kwargs.items() if key in {
                "status", "workflow_state", "error_message", "error_metadata", "completed_at"
            }}
        )
        self.investigation["state_version"] += 1
        return dict(self.investigation)

    def merge_investigation_workflow_state(
        self, investigation_id: str, patch: dict[str, Any], **_: Any
    ) -> dict[str, Any]:
        assert investigation_id == self.investigation["id"]
        self.investigation["workflow_state"].update(patch)
        self.investigation["state_version"] += 1
        return dict(self.investigation)

    def create_claim_investigation(self, **kwargs: Any) -> dict[str, Any]:
        row = {"id": _uuid(), "state_version": 0, **kwargs}
        self.claims[row["id"]] = row
        self.status_history[row["id"]].append(str(row["status"]))
        return row

    def list_claim_investigations(self, investigation_id: str) -> list[dict[str, Any]]:
        assert investigation_id == self.investigation["id"]
        return list(self.claims.values())

    def update_claim_status(self, claim_id: str, status: str, **kwargs: Any) -> dict[str, Any]:
        row = self.claims[claim_id]
        row["status"] = status
        row.update({key: value for key, value in kwargs.items() if key in {
            "current_iteration_no", "critical_date", "critical_date_basis",
            "target_publication_date", "terminal_reason", "completed_at"
        }})
        if kwargs.get("result_summary_patch"):
            row.setdefault("result_summary", {}).update(kwargs["result_summary_patch"])
        self.status_history[claim_id].append(status)
        return row

    def mark_claim_terminal(self, claim_id: str, **kwargs: Any) -> dict[str, Any]:
        row = self.claims[claim_id]
        row.update(kwargs)
        row["status"] = kwargs["status"]
        self.status_history[claim_id].append(kwargs["status"])
        return row

    def get_latest_critical_date_confirmation(
        self, claim_id: str
    ) -> dict[str, Any] | None:
        return self.critical_date_confirmations.get(str(claim_id))

    def get_continuation_batch(self, batch_id: str) -> dict[str, Any] | None:
        return self.continuation_batches.get(str(batch_id))

    def update_continuation_batch(
        self, batch_id: str, *, status: str, **kwargs: Any
    ) -> dict[str, Any]:
        row = self.continuation_batches[str(batch_id)]
        row["status"] = status
        row.update(kwargs)
        row.setdefault("status_history", []).append(status)
        return row

    def list_claim_limitations(self, claim_id: str) -> list[dict[str, Any]]:
        return self.limitations[claim_id]

    def insert_claim_limitations(
        self, claim_id: str, limitations: list[dict[str, Any]], **_: Any
    ) -> list[dict[str, Any]]:
        rows = [
            {"id": _uuid(), "feature_key": item["feature_key"], **item}
            for item in limitations
        ]
        self.limitations[claim_id] = rows
        return rows

    def create_iteration(self, **kwargs: Any) -> dict[str, Any]:
        row = {"id": _uuid(), **kwargs}
        self.persisted["iterations"].append(row)
        return row

    def update_iteration(self, iteration_id: str, **kwargs: Any) -> dict[str, Any]:
        row = next(item for item in self.persisted["iterations"] if item["id"] == iteration_id)
        row.update(kwargs)
        if kwargs.get("status") in {"succeeded", "partial", "failed", "cancelled"}:
            row["completed_at"] = datetime.now(timezone.utc).isoformat()
        return row

    def upsert_query(self, **kwargs: Any) -> dict[str, Any]:
        row = {"id": _uuid(), **kwargs}
        self.persisted["queries"].append(row)
        return row

    def update_query_execution(self, query_id: str, **kwargs: Any) -> dict[str, Any]:
        row = next(item for item in self.persisted["queries"] if item["id"] == query_id)
        row.update(kwargs)
        return row

    def upsert_document(self, **kwargs: Any) -> dict[str, Any]:
        existing = next(
            (
                item
                for item in self.persisted["documents"]
                if item["canonical_key"] == kwargs["canonical_key"]
            ),
            None,
        )
        if existing:
            existing.update(kwargs)
            return existing
        row = {
            "id": _uuid(),
            "document_version_id": _uuid(),
            "document_version_no": 1,
            **kwargs,
        }
        self.persisted["documents"].append(row)
        return row

    def upsert_document_source(self, **kwargs: Any) -> dict[str, Any]:
        row = {"id": _uuid(), **kwargs}
        self.persisted["sources"].append(row)
        return row

    def upsert_document_qualification(self, **kwargs: Any) -> dict[str, Any]:
        row = {"id": _uuid(), **kwargs}
        self.persisted["qualifications"].append(row)
        return row

    def list_module_runs(self, **kwargs: Any) -> list[dict[str, Any]]:
        return [
            item
            for item in self.module_runs
            if item["investigation_id"] == kwargs["investigation_id"]
            and item["module_code"] == kwargs["module_code"]
        ]

    def create_module_run(self, **kwargs: Any) -> dict[str, Any]:
        row = {"id": _uuid(), **kwargs}
        self.module_runs.append(row)
        return row

    def finish_module_run(self, module_run_id: str, **kwargs: Any) -> dict[str, Any]:
        row = next(item for item in self.module_runs if item["id"] == module_run_id)
        row.update(kwargs)
        row["completed_at"] = datetime.now(timezone.utc).isoformat()
        return row

    def insert_feature_disclosures(self, **kwargs: Any) -> list[dict[str, Any]]:
        self.persisted["disclosures"].append(kwargs)
        return list(kwargs["disclosures"])

    def insert_closest_prior_art_version(self, **kwargs: Any) -> dict[str, Any]:
        row = {"id": _uuid(), **kwargs}
        self.persisted["closest"].append(row)
        return row

    def insert_gaps(self, **kwargs: Any) -> list[dict[str, Any]]:
        self.persisted["gaps"].append(kwargs)
        return list(kwargs["gaps"])

    def reconcile_gap_frontier(self, **kwargs: Any) -> list[dict[str, Any]]:
        self.persisted["gap_frontiers"].append(kwargs)
        return [
            {"id": _uuid(), "version_no": 1, **item}
            for item in kwargs["open_gaps"]
        ]

    def insert_combination(self, **kwargs: Any) -> dict[str, Any]:
        row = {"id": _uuid(), **kwargs}
        self.persisted["combinations"].append(row)
        return row


class FakeAnalysisEngine:
    def __init__(self, coverage: dict[str, set[str]], complete_combinations: set[str] = set()) -> None:
        self.coverage = coverage
        self.complete_combinations = complete_combinations
        self.plan_calls: list[tuple[str, int]] = []
        self.compare_calls: list[str] = []
        self.compare_inputs: list[dict[str, Any]] = []
        self.inventive_step_inputs: list[dict[str, Any]] = []

    def plan_queries(self, **kwargs: Any) -> QueryPlan:
        claim_id = kwargs["claim_id"]
        iteration = kwargs["iteration_number"]
        self.plan_calls.append((claim_id, iteration))
        limitations = list(kwargs.get("existing_limitations") or ()) or [
            Limitation(
                feature_id=f"{claim_id}-f1",
                claim_id=claim_id,
                sequence=1,
                text="移动底盘",
                technical_subject="扫地机器人",
            ),
            Limitation(
                feature_id=f"{claim_id}-f2",
                claim_id=claim_id,
                sequence=2,
                text="升降清洁件",
                technical_subject="扫地机器人",
            ),
        ]
        selected_feature_ids = (
            list(kwargs.get("gap_feature_ids") or [])
            if iteration > 1
            else [item.feature_id for item in limitations]
        )
        feature_text = {
            item.feature_id: item.text for item in limitations
        }
        return QueryPlan(
            claim_id=claim_id,
            iteration_number=iteration,
            round_kind="initial" if iteration == 1 else "gap",
            technical_subject="扫地机器人",
            limitations=limitations,
            queries=[
                SearchQuery(
                    query_id=f"{claim_id}-q{iteration}",
                    provider_kind="patent",
                    purpose="initial" if iteration == 1 else "feature_uncovered",
                    technical_subject="扫地机器人",
                    feature_ids=selected_feature_ids,
                    expression=" ".join(
                        [
                            "扫地机器人",
                            *(feature_text[item] for item in selected_feature_ids),
                        ]
                    ),
                    language="zh",
                    rationale="fixture",
                )
            ],
            model="glm-4.6v-fixture",
            used_target_images=1,
            gap_search_iteration=(iteration - 1 if iteration > 1 else 0),
            covered_difference_feature_ids=list(
                kwargs.get("covered_difference_feature_ids") or []
            ),
            uncovered_difference_feature_ids=(
                selected_feature_ids if iteration > 1 else []
            ),
            existing_corpus_reuse=[
                dict(item) for item in kwargs.get("existing_corpus_reuse") or []
            ],
            previous_iteration_failure_reason=str(
                kwargs.get("previous_iteration_failure_reason") or ""
            ),
        )

    def generate_inventive_profile(self, **kwargs: Any) -> QueryPlan:
        claim_id = kwargs["claim_id"]
        limitations = list(kwargs.get("existing_limitations") or ()) or [
            Limitation(
                feature_id=f"{claim_id}-f1",
                claim_id=claim_id,
                sequence=1,
                text="移动底盘",
                technical_subject="扫地机器人",
            ),
            Limitation(
                feature_id=f"{claim_id}-f2",
                claim_id=claim_id,
                sequence=2,
                text="升降清洁件",
                technical_subject="扫地机器人",
            ),
        ]
        return QueryPlan(
            claim_id=claim_id,
            iteration_number=int(kwargs.get("iteration_number") or 1),
            round_kind="initial",
            technical_subject="扫地机器人",
            limitations=limitations,
            queries=[],
            model="glm-4.6v-fixture",
            used_target_images=1,
            generation_source="live_model",
        )

    def generate_queries_from_inventive_profile(self, **kwargs: Any) -> QueryPlan:
        previous = kwargs["previous_plan"]
        profile_plan = (
            previous
            if isinstance(previous, QueryPlan)
            else QueryPlan.model_validate(previous)
        )
        base = self.plan_queries(
            claim_id=profile_plan.claim_id,
            iteration_number=1,
            existing_limitations=profile_plan.limitations,
        )
        return base.model_copy(
            update={
                "generation_source": "module3_profile_live_model",
                "source_plan_run_id": kwargs.get("source_plan_run_id"),
                "source_sha256": kwargs.get("source_sha256"),
                "invention_search_profile": profile_plan.invention_search_profile,
            }
        )

    def compare_single_reference(self, **kwargs: Any) -> DocumentComparison:
        document_id = kwargs["document_id"]
        self.compare_calls.append(document_id)
        self.compare_inputs.append(dict(kwargs))
        covered = self.coverage.get(document_id, set())
        disclosures = []
        for limitation in kwargs["limitations"]:
            disclosed = limitation.feature_id in covered
            disclosures.append(
                FeatureDisclosure(
                    feature_id=limitation.feature_id,
                    status=(DisclosureStatus.EXPLICIT if disclosed else DisclosureStatus.NOT_DISCLOSED),
                    evidence_quote=(f"quote {limitation.feature_id}" if disclosed else ""),
                    evidence_location=("paragraph 1" if disclosed else "full document reviewed"),
                    reasoning="fixture",
                    confidence=0.95,
                    target_structural_role=f"role {limitation.feature_id}",
                    reference_structure_mapping=(
                        f"mapped role {limitation.feature_id}" if disclosed else ""
                    ),
                    mapping_basis="literal" if disclosed else "none",
                )
            )
        return DocumentComparison(
            document_id=document_id,
            disclosures=disclosures,
            field_alignment=0.8,
            purpose_alignment=0.8,
            effect_alignment=0.8,
            evidence_completeness=1.0,
            model="glm-4.6v-fixture",
            used_target_images=1,
            used_document_images=1,
            analysis_rule_version=I4S_RULE_VERSION,
        )

    def analyze_inventive_step(self, **kwargs: Any) -> InventiveStepAssessment:
        self.inventive_step_inputs.append(dict(kwargs))
        document_ids = [item.document_id for item in kwargs["comparisons"]]
        complete = any(item in self.complete_combinations for item in document_ids)
        coverage = [
            FeatureCoverage(
                feature_id=item.feature_id,
                disclosed_by_document_ids=document_ids,
                covered=True,
            )
            for item in kwargs["limitations"]
        ]
        criterion = CombinationCriterion(
            status="supported",
            evidence_quote="combination teaching",
            evidence_location="paragraph 2",
            reasoning="fixture evidence",
            confidence=0.95,
        )
        gaps = [] if complete else [
            AnalysisGap(
                gap_id="motivation-gap",
                kind="combination_gap",
                subtype="combination_motivation",
                source_document_ids=document_ids,
                search_anchor="扫地机器人 + 组合启示",
                rationale="缺少组合动机证据",
            )
        ]
        return InventiveStepAssessment(
            closest_document_id=kwargs["closest_document_id"],
            combination_document_ids=document_ids,
            feature_coverage=coverage,
            combined_feature_coverage_complete=True,
            all_documents_date_eligible=True,
            related_technical_problem=criterion.model_copy(update={"status": "same_or_related"}),
            combination_motivation=criterion,
            teaching_away=criterion.model_copy(update={"status": "absent"}),
            technical_effect=criterion.model_copy(update={"status": "predictable"}),
            evidence_complete=complete,
            gaps=gaps,
            model="glm-4.6v-fixture",
            used_target_images=1,
            used_document_images=len(document_ids),
        )

    def analyze_obviousness_precheck(
        self, **kwargs: Any
    ) -> ObviousnessPrecheckAssessment:
        groups = []
        targets = []
        unresolved: list[str] = []
        for index, difference in enumerate(kwargs["differences"], start=1):
            unresolved.append(difference.feature_id)
            group_id = f"G{index:02d}"
            criterion = {
                "status": "uncertain",
                "reasoning": "fixture keeps the search target open",
                "confidence": 0.5,
            }
            groups.append(
                {
                    "feature_group_id": group_id,
                    "feature_ids": [difference.feature_id],
                    "feature_texts": [difference.feature_text],
                    "objective_technical_problem": "fixture technical problem",
                    "d1_teaching": criterion,
                    "routine_means": criterion,
                    "modification_motivation": criterion,
                    "teaching_away": criterion,
                    "technical_effect": criterion,
                    "search_route": "search_direct_feature_evidence",
                    "ordinary_structural_search_required": True,
                    "reasoning": "fixture direct search",
                }
            )
            targets.append(
                {
                    "feature_group_id": group_id,
                    "feature_ids": [difference.feature_id],
                    "route": "search_direct_feature_evidence",
                    "target_gap_type": "feature_gap",
                    "search_anchor": difference.feature_text,
                    "rationale": "fixture direct search",
                }
            )
        return ObviousnessPrecheckAssessment.model_validate(
            {
                "closest_document_id": kwargs["closest_document_id"],
                "feature_groups": groups,
                "resolved_feature_ids": [],
                "unresolved_feature_ids": unresolved,
                "search_targets": targets,
                "ordinary_structural_search_required": bool(groups),
                "evidence_complete": False,
                "model": "glm-4.6v-fixture",
                "used_target_images": 1,
                "used_document_images": 1,
            }
        )


class ScriptedGateway:
    def __init__(self, batches: dict[tuple[str, int], SearchBatch]) -> None:
        self.batches = batches
        self.calls: list[tuple[str, int]] = []

    def search(self, _query: SearchQuery, **kwargs: Any) -> SearchBatch:
        key = (kwargs["claim_id"], kwargs["iteration_number"])
        self.calls.append(key)
        return self.batches.get(key, SearchBatch(provider="fixture"))


class TwoQueryAnalysisEngine(FakeAnalysisEngine):
    def plan_queries(self, **kwargs: Any) -> QueryPlan:
        plan = super().plan_queries(**kwargs)
        first = plan.queries[0]
        second = first.model_copy(
            update={"query_id": f"{first.query_id}-second"}
        )
        return plan.model_copy(update={"queries": [first, second]})


class SplitScriptedGateway:
    def __init__(self) -> None:
        self.events: list[str] = []

    def discover(self, query: SearchQuery, **_kwargs: Any) -> SearchBatch:
        self.events.append(f"discover:{query.query_id}")
        publication = (
            "CN999999999A2"
            if query.query_id.endswith("-second")
            else "CN999999999A1"
        )
        lead = EvidenceRecord(
            provider="fixture",
            source_type="patent",
            external_id=publication,
            title="Cleaning mechanism candidate",
            source_url=f"https://example.invalid/{publication}",
            publication_number=publication,
            raw_metadata={"application_number": "CN201900000001"},
        )
        return SearchBatch(provider="fixture", records=(lead,))

    def retrieve(
        self,
        query: SearchQuery,
        records: tuple[EvidenceRecord, ...],
        **_kwargs: Any,
    ) -> SearchBatch:
        self.events.append(f"retrieve:{query.query_id}:{len(records)}")
        return SearchBatch(
            provider="fixture-retrieval",
            records=tuple(_evidence(item.external_id) for item in records),
            network_used=bool(records),
        )


class EmptyPatentProvider:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def search(self, expression: str, **kwargs: Any) -> list[EvidenceRecord]:
        self.calls.append({"expression": expression, **kwargs})
        return []

    def close(self) -> None:
        return None


class EmptyNplProvider:
    def close(self) -> None:
        return None


def _target(*claim_ids: str) -> dict[str, Any]:
    patent = PatentSnapshot(
        source_sha256="a" * 64,
        source_uri="fixture://target.pdf",
        patent_number="CNTESTU",
        application_date="2020-01-01",
        publication_date="2021-01-01",
        specification={
            "全文": "目标专利说明书全文：装置由移动底盘与升降清洁机构连接并协同工作。"
        },
        claims=[
            ClaimSnapshot(
                claim_id=claim_id,
                claim_type="INDEPENDENT",
                claim_text=f"权利要求 {claim_id}",
                expanded_claim_text=f"展开权利要求 {claim_id}",
            )
            for claim_id in claim_ids
        ],
    )
    return {
        "patent_snapshot": patent.model_dump(mode="json"),
        "target_images": ["target.png"],
        "declared_critical_date": "2020-01-01",
        "target_publication_date_verified": True,
    }


def _evidence(external_id: str, *, images: bool = True) -> EvidenceRecord:
    return EvidenceRecord(
        provider="fixture",
        source_type="patent",
        external_id=external_id,
        title=external_id,
        source_url=f"https://example.invalid/{external_id}",
        stage=EvidenceStage.QUALIFIED,
        publication_number=external_id,
        authority="CN",
        publication_date="2019-01-01",
        filing_date="2018-01-01",
        full_text=f"document {external_id} quote 1-f1 quote 1-f2 quote 2-f1 quote 2-f2",
        image_urls=(f"{external_id}.png",) if images else (),
        content_sha256=(external_id.lower() + "0" * 64)[:64],
        retrieved_at=datetime.now(timezone.utc).isoformat(),
        provenance={"public_date_evidence": "fixture registry"},
        eligibility={
            "category": "ordinary_prior_art",
            "novelty_eligible": True,
            "inventive_step_eligible": True,
            "requires_human_review": False,
            "public_availability_date": "2019-01-01",
            "explanation": "fixture verified",
        },
    )


def _continuation_job(
    repository: FakeRepository,
    *,
    claim_investigation_ids: list[str],
    start_iteration_no: int,
    end_iteration_no: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    batch_id = _uuid()
    round_plan = []
    for claim_id in claim_investigation_ids:
        parent = next(
            (
                row
                for row in repository.persisted["iterations"]
                if row["claim_investigation_id"] == claim_id
                and row["iteration_no"] == start_iteration_no - 1
            ),
            None,
        )
        round_plan.append(
            {
                "claim_investigation_id": claim_id,
                "parent_iteration_id": parent["id"] if parent else None,
                "start_iteration_no": start_iteration_no,
                "end_iteration_no": end_iteration_no,
            }
        )
    batch = {
        "id": batch_id,
        "investigation_id": repository.investigation["id"],
        "claim_investigation_ids": list(claim_investigation_ids),
        "max_additional_rounds": end_iteration_no - start_iteration_no + 1,
        "round_plan": round_plan,
        "status": "queued",
    }
    repository.continuation_batches[batch_id] = batch
    job = {
        "payload": {
            "kind": "continuation",
            "investigation_id": repository.investigation["id"],
            "continuation_batch_id": batch_id,
            "round_plan": round_plan,
        }
    }
    return batch, job


def test_i0_discovers_all_queries_then_filters_duplicates_before_fetch() -> None:
    repository = FakeRepository(_target("1"))
    engine = TwoQueryAnalysisEngine(
        {"fixture:CN999999999A1": {"1-f1", "1-f2"}}
    )
    gateway = SplitScriptedGateway()

    result = InvalidityWorkflow(
        repository, engine, gateway
    ).execute_investigation(repository.investigation["id"])

    assert result.claim_results[0].status == ClaimStatus.NOVELTY_EVIDENCE_COMPLETE.value
    assert gateway.events == [
        "discover:1-q1",
        "discover:1-q1-second",
        "retrieve:1-q1:1",
        "retrieve:1-q1-second:0",
    ]
    filter_run = next(
        item
        for item in repository.module_runs
        if item["module_code"] == "I3_CANDIDATE_FILTER"
    )
    output = filter_run["output_snapshot"]
    assert filter_run["status"] == "succeeded"
    assert output["prefetch_filter_applied"] is True
    assert output["raw_candidate_count"] == 2
    assert output["unique_group_count"] == 1
    assert output["duplicate_count"] == 1
    assert output["fetch_candidate_count"] == 1
    assert output["model_failure"] == "semantic_model_unavailable"


def test_i0_start_retry_adopts_existing_claim_rows_instead_of_duplicate_insert() -> None:
    repository = FakeRepository(_target("1"))
    engine = TwoQueryAnalysisEngine(
        {"fixture:CN999999999A1": {"1-f1", "1-f2"}}
    )
    gateway = SplitScriptedGateway()
    workflow = InvalidityWorkflow(repository, engine, gateway)

    first = workflow.execute_investigation(repository.investigation["id"])
    assert first.claim_results[0].status == ClaimStatus.NOVELTY_EVIDENCE_COMPLETE.value
    created_rows = len(repository.claims)
    gateway_events = len(gateway.events)

    # 进程重启后 durable start job 重试（历史上带 force_recompute）时：
    # 必须收养数据库中已冻结的权利要求行，而不是重复插入触发唯一约束；
    # 已到达终态的权利要求不得重跑。
    second = workflow.execute_investigation(
        repository.investigation["id"], force_recompute=True
    )
    assert len(repository.claims) == created_rows
    assert len(gateway.events) == gateway_events
    assert second.claim_results[0].status == ClaimStatus.NOVELTY_EVIDENCE_COMPLETE.value


def test_human_review_apply_rejects_incomplete_structural_review_for_retry() -> None:
    comparison = DocumentComparison(
        document_id="fixture:review-document",
        disclosures=[],
        field_alignment=0.0,
        purpose_alignment=0.0,
        effect_alignment=0.0,
        evidence_completeness=0.0,
        model="glm-4.6v-fixture",
        structural_review_attempted=True,
        structural_review_performed=False,
        structural_review_status="model_error",
        structural_review_error_code="glm_direct_transport_error",
        analysis_pass_count=1,
        analysis_rule_version=I4S_RULE_VERSION,
    )

    with pytest.raises(
        WorkflowExecutionError,
        match="人工复核事实尚未应用并将保留重试",
    ) as caught:
        _require_completed_structural_review(comparison)

    assert caught.value.retryable is True


def test_human_review_apply_accepts_completed_or_unneeded_structural_review() -> None:
    comparison = DocumentComparison(
        document_id="fixture:review-document",
        disclosures=[],
        field_alignment=0.0,
        purpose_alignment=0.0,
        effect_alignment=0.0,
        evidence_completeness=0.0,
        model="glm-4.6v-fixture",
        analysis_rule_version=I4S_RULE_VERSION,
    )

    assert _require_completed_structural_review(comparison) is None


def test_claims_stop_independently_and_combination_has_separate_gate() -> None:
    repository = FakeRepository(_target("1", "2"))
    c1 = _evidence("C1-D1")
    c2d1 = _evidence("C2-D1")
    c2d2 = _evidence("C2-D2")
    engine = FakeAnalysisEngine(
        {
            "fixture:C1-D1": {"1-f1", "1-f2"},
            "fixture:C2-D1": {"2-f1"},
            "fixture:C2-D2": {"2-f2"},
        },
        complete_combinations={"fixture:C2-D2"},
    )
    gateway = ScriptedGateway(
        {
            ("1", 1): SearchBatch(provider="fixture", records=(c1,)),
            ("2", 1): SearchBatch(provider="fixture", records=(c2d1,)),
            ("2", 2): SearchBatch(provider="fixture", records=(c2d2,)),
        }
    )
    workflow = InvalidityWorkflow(repository, engine, gateway)

    result = workflow.execute_investigation(repository.investigation["id"])

    by_claim = {item.claim_id: item for item in result.claim_results}
    assert by_claim["1"].status == ClaimStatus.NOVELTY_EVIDENCE_COMPLETE.value
    assert by_claim["1"].rounds_completed == 1
    assert by_claim["2"].status == ClaimStatus.INVENTIVE_STEP_EVIDENCE_COMPLETE.value
    assert by_claim["2"].rounds_completed == 2
    assert by_claim["2"].unresolved_gaps == []
    assert repository.claims[by_claim["2"].claim_investigation_id][
        "current_iteration_no"
    ] == 2
    assert ("1", 2) not in gateway.calls
    assert repository.persisted["combinations"][0]["motivation_status"] == "supported"
    claim_two_frontiers = [
        item
        for item in repository.persisted["gap_frontiers"]
        if item["claim_investigation_id"] == by_claim["2"].claim_investigation_id
    ]
    assert claim_two_frontiers[0]["open_gaps"]
    assert claim_two_frontiers[1]["open_gaps"] == []
    resolution = next(
        iter(claim_two_frontiers[1]["closed_gap_resolutions"].values())
    )
    assert resolution["closed_reason"] == "feature_disclosure_verified"
    assert resolution["resolution_evidence"]["resolving_document_key"] == (
        "fixture:C2-D2"
    )
    assert resolution["resolved_by_document_id"]
    persisted_disclosures = [
        disclosure
        for batch in repository.persisted["disclosures"]
        for disclosure in batch["disclosures"]
    ]
    assert persisted_disclosures
    assert all(
        disclosure["analysis"]["model"].startswith("glm-4.6v")
        and disclosure["analysis"]["used_target_images"] == 1
        and disclosure["analysis"]["used_document_images"] == 1
        for disclosure in persisted_disclosures
    )
    assert all(
        item["status"] in {"succeeded", "partial", "failed", "cancelled"}
        and item.get("completed_at")
        for item in repository.persisted["iterations"]
    )
    assert all(
        item["status"] in {"completed", "partial", "failed", "skipped"}
        for item in repository.persisted["queries"]
    )


def test_novelty_hit_does_not_skip_other_available_single_document_reviews() -> None:
    repository = FakeRepository(_target("1"))
    first = _evidence("A-FULL")
    second = _evidence("B-FULL")
    engine = FakeAnalysisEngine(
        {
            "fixture:A-FULL": {"1-f1", "1-f2"},
            "fixture:B-FULL": {"1-f1", "1-f2"},
        }
    )
    workflow = InvalidityWorkflow(
        repository,
        engine,
        ScriptedGateway(
            {
                ("1", 1): SearchBatch(
                    provider="fixture",
                    records=(first, second),
                )
            }
        ),
    )

    result = workflow.execute_investigation(repository.investigation["id"])

    assert result.claim_results[0].status == ClaimStatus.NOVELTY_EVIDENCE_COMPLETE.value
    assert engine.compare_calls == ["fixture:A-FULL", "fixture:B-FULL"]
    assert all(item["status"] != "skipped" for item in repository.persisted["queries"])


def test_i4s_promotes_date_eligible_local_snapshot_only_after_disclosure(
    tmp_path: Path,
) -> None:
    repository = FakeRepository(_target("1"))
    content = b"immutable prior art PDF snapshot bytes"
    source = tmp_path / "prior-art.pdf"
    source.write_bytes(content)
    digest = hashlib.sha256(content).hexdigest()
    full_text = "robot cleaner mobile chassis and lifting cleaning pad " * 8
    readable_document = _analysis_ready_document(digest, full_text, page_count=1)
    record = EvidenceRecord(
        provider="arxiv",
        source_type="preprint",
        external_id="2401.00001",
        title="Robot cleaner lifting pad",
        source_url="https://arxiv.org/abs/2401.00001",
        stage=EvidenceStage.RETRIEVED,
        publication_date="2019-01-01",
        full_text=full_text,
        image_urls=(str(source),),
        content_sha256=digest,
        retrieved_at=datetime.now(timezone.utc).isoformat(),
        artifacts=(
            {
                "uri": str(source),
                "sha256": digest,
                "byte_size": len(content),
                "mime_type": "application/pdf",
                "artifact_type": "prior_art_primary_source",
                "kind": "pdf",
                "source_url": "https://arxiv.org/pdf/2401.00001",
                "is_primary_source": True,
                "local_immutable_snapshot": True,
            },
        ),
        provenance={"public_date_evidence": "arXiv Atom entry/published"},
        readable_document=readable_document,
        analysis_ready=True,
        analysis_readiness_reason="test full-document coverage",
        eligibility={
            "category": "ordinary_prior_art",
            "novelty_eligible": True,
            "inventive_step_eligible": True,
            "requires_human_review": False,
            "public_availability_date": "2019-01-01",
            "explanation": "verified source date",
        },
        qualification_issues=("technical_disclosure_not_verified",),
    )
    engine = FakeAnalysisEngine(
        {"arxiv:2401.00001": {"1-f1", "1-f2"}}
    )

    result = InvalidityWorkflow(
        repository,
        engine,
        ScriptedGateway(
            {("1", 1): SearchBatch(provider="arxiv", records=(record,))}
        ),
    ).execute_investigation(repository.investigation["id"])

    assert result.claim_results[0].status == ClaimStatus.NOVELTY_EVIDENCE_COMPLETE.value
    assert engine.compare_calls == ["arxiv:2401.00001"]
    assert repository.persisted["documents"][0]["evidence_level"] == (
        EvidenceStage.QUALIFIED.value
    )
    assert repository.persisted["sources"][-1]["snapshot_uri"] == str(source)


def test_real_arxiv_adapter_freezes_source_before_i4s_promotion(
    tmp_path: Path,
) -> None:
    atom_url = "https://export.arxiv.org/api/query"
    pdf_url = "https://arxiv.org/pdf/1803.00001v2"
    atom = (Path(__file__).parent / "fixtures" / "arxiv_atom.xml").read_bytes()
    document = fitz.open()
    try:
        page = document.new_page()
        page.insert_textbox(
            fitz.Rect(72, 72, 520, 760),
            "robot cleaner mobile chassis lifting cleaning pad controller " * 12,
            fontsize=11,
        )
        pdf_content = document.tobytes()
    finally:
        document.close()

    class Response:
        def __init__(self, url: str, content: bytes, media_type: str) -> None:
            self.url = url
            self.status_code = 200
            self.content = content
            self.text = content.decode("utf-8", errors="replace")
            self.headers = {"content-type": media_type}

    class Transport:
        @staticmethod
        def resolve(_hostname: str) -> list[str]:
            return ["93.184.216.34"]

        def get(self, url: str, **_kwargs: Any) -> Response:
            if url == atom_url:
                return Response(url, atom, "application/atom+xml")
            if url == pdf_url:
                return Response(url, pdf_content, "application/pdf")
            raise AssertionError(f"unexpected URL: {url}")

    artifact_store = ArtifactStore(tmp_path / "invalidity/test", "test")
    gateway = ConfiguredEvidenceGateway(
        patent_provider=EmptyPatentProvider(),  # type: ignore[arg-type]
        npl_provider=ArxivProvider(transport=Transport()),
        artifact_store=artifact_store,
        max_candidates_per_query=3,
    )
    query = SearchQuery(
        query_id="arxiv-real-adapter",
        provider_kind="npl",
        purpose="initial",
        technical_subject="扫地机器人",
        feature_ids=["1-f1", "1-f2"],
        expression="robot cleaner lifting cleaning pad",
        language="en",
        rationale="real provider evidence-chain regression",
    )

    discovery = gateway.discover(
        query,
        investigation_id="investigation-arxiv",
        claim_id="1",
        iteration_number=1,
        critical_date=datetime(2020, 1, 1, tzinfo=timezone.utc).date(),
        target_publication_date=None,
        target_publication_date_verified=False,
    )
    assert discovery.complete is True
    assert len(discovery.records) == 1
    assert discovery.records[0].stage is EvidenceStage.LEAD

    batch = gateway.retrieve(
        query,
        discovery.records,
        investigation_id="investigation-arxiv",
        claim_id="1",
        iteration_number=1,
        critical_date=datetime(2020, 1, 1, tzinfo=timezone.utc).date(),
        target_publication_date=None,
        target_publication_date_verified=False,
    )

    assert batch.complete is True
    assert len(batch.records) == 1
    retrieved = batch.records[0]
    assert retrieved.stage is EvidenceStage.RETRIEVED
    assert retrieved.primary_artifact is None
    assert retrieved.eligibility
    assert retrieved.eligibility["novelty_eligible"] is True
    assert "technical_disclosure_not_verified" in retrieved.qualification_issues
    primary = next(
        item for item in retrieved.artifacts if item.get("is_primary_source")
    )
    primary_path = Path(str(primary["uri"]))
    assert artifact_store.root in primary_path.parents
    assert primary_path.read_bytes().startswith(b"%PDF")
    assert hashlib.sha256(primary_path.read_bytes()).hexdigest() == retrieved.content_sha256
    assert retrieved.image_urls
    assert retrieved.analysis_ready is True

    repository = FakeRepository(_target("1"))
    engine = FakeAnalysisEngine({"arxiv:1803.00001": {"1-f1", "1-f2"}})
    result = InvalidityWorkflow(
        repository,
        engine,
        ScriptedGateway(
            {("1", 1): SearchBatch(provider="arxiv", records=(retrieved,))}
        ),
    ).execute_investigation(repository.investigation["id"])

    assert result.claim_results[0].status == ClaimStatus.NOVELTY_EVIDENCE_COMPLETE.value
    assert engine.compare_calls == ["arxiv:1803.00001"]
    assert repository.persisted["documents"][-1]["evidence_level"] == (
        EvidenceStage.QUALIFIED.value
    )


def test_tampered_primary_snapshot_is_rejected_before_document_upsert(
    tmp_path: Path,
) -> None:
    source = tmp_path / "tampered.pdf"
    expected = b"original evidence"
    source.write_bytes(b"different content")
    record = EvidenceRecord(
        provider="arxiv",
        source_type="preprint",
        external_id="tampered",
        title="tampered source",
        source_url="https://arxiv.org/abs/tampered",
        stage=EvidenceStage.RETRIEVED,
        publication_date="2018-01-01",
        full_text="robot cleaner lifting pad technical disclosure",
        image_urls=(str(source),),
        content_sha256=hashlib.sha256(expected).hexdigest(),
        retrieved_at=datetime.now(timezone.utc).isoformat(),
        artifacts=(
            {
                "uri": str(source),
                "sha256": hashlib.sha256(expected).hexdigest(),
                "byte_size": len(expected),
                "mime_type": "application/pdf",
                "artifact_type": "prior_art_primary_source",
                "kind": "pdf",
                "source_url": "https://arxiv.org/pdf/tampered",
                "is_primary_source": True,
                "local_immutable_snapshot": True,
            },
        ),
        eligibility={
            "category": "ordinary_prior_art",
            "novelty_eligible": True,
            "inventive_step_eligible": True,
            "requires_human_review": False,
            "public_availability_date": "2018-01-01",
        },
        provenance={"public_date_evidence": "fixture date evidence"},
    )
    repository = FakeRepository(_target("1"))

    result = InvalidityWorkflow(
        repository,
        FakeAnalysisEngine({}),
        ScriptedGateway(
            {("1", 1): SearchBatch(provider="arxiv", records=(record,))}
        ),
    ).execute_investigation(repository.investigation["id"])

    assert result.claim_results[0].status == ClaimStatus.PARTIAL.value
    assert repository.persisted["documents"] == []
    assert repository.persisted["sources"] == []


def test_retrieved_candidate_with_unknown_date_stops_for_human_review(
    tmp_path: Path,
) -> None:
    content = b"retrieved patent source with unverified public date"
    source = tmp_path / "candidate.html"
    source.write_bytes(content)
    digest = hashlib.sha256(content).hexdigest()
    full_text = "robot cleaner chassis lifting pad controller technical disclosure " * 8
    readable_document = _analysis_ready_document(digest, full_text)
    record = EvidenceRecord(
        provider="google_patents",
        source_type="patent",
        external_id="CN-UNKNOWN-DATE",
        title="robot cleaner lifting pad patent",
        source_url="https://patents.google.com/patent/CNUNKNOWN/en",
        stage=EvidenceStage.RETRIEVED,
        publication_date="2018-01-01",
        full_text=full_text,
        image_urls=(str(source),),
        content_sha256=digest,
        retrieved_at=datetime.now(timezone.utc).isoformat(),
        artifacts=(
            {
                "uri": str(source),
                "sha256": digest,
                "byte_size": len(content),
                "mime_type": "text/html",
                "artifact_type": "prior_art_primary_source",
                "kind": "html",
                "source_url": "https://patents.google.com/patent/CNUNKNOWN/en",
                "is_primary_source": True,
                "local_immutable_snapshot": True,
            },
        ),
        eligibility={
            "category": "unknown",
            "novelty_eligible": False,
            "inventive_step_eligible": False,
            "requires_human_review": True,
            "public_availability_date": "2018-01-01",
            "reason_codes": ["public_date_not_verified"],
            "explanation": "公开日期尚未核验",
        },
        qualification_issues=("date_eligibility_unknown",),
        readable_document=readable_document,
        analysis_ready=True,
        analysis_readiness_reason="test full-document coverage",
    )
    repository = FakeRepository(_target("1"))
    engine = FakeAnalysisEngine({})

    result = InvalidityWorkflow(
        repository,
        engine,
        ScriptedGateway(
            {
                ("1", 1): SearchBatch(
                    provider="google_patents",
                    records=(record,),
                )
            }
        ),
    ).execute_investigation(repository.investigation["id"])

    claim = result.claim_results[0]
    assert claim.status == ClaimStatus.NEEDS_HUMAN_REVIEW.value
    # The multimodal comparison is required to decide whether an unknown-date
    # document is substantive enough to keep as a human-review date gap.
    assert engine.compare_calls == ["google_patents:CN-UNKNOWN-DATE"]
    assert claim.unresolved_gaps[0]["kind"] == "date_gap"
    assert claim.unresolved_gaps[0]["source_document_ids"] == [
        "google_patents:CN-UNKNOWN-DATE"
    ]
    assert repository.persisted["gap_frontiers"][0]["open_gaps"][0][
        "limitation_id"
    ] is None


def test_later_novelty_round_commits_empty_frontier_and_closes_prior_gap() -> None:
    repository = FakeRepository(_target("1"))
    partial = _evidence("ROUND1-PARTIAL")
    full = _evidence("ROUND2-FULL")
    engine = FakeAnalysisEngine(
        {
            "fixture:ROUND1-PARTIAL": {"1-f1"},
            "fixture:ROUND2-FULL": {"1-f1", "1-f2"},
        }
    )

    result = InvalidityWorkflow(
        repository,
        engine,
        ScriptedGateway(
            {
                ("1", 1): SearchBatch(provider="fixture", records=(partial,)),
                ("1", 2): SearchBatch(provider="fixture", records=(full,)),
            }
        ),
    ).execute_investigation(repository.investigation["id"])

    claim = result.claim_results[0]
    assert claim.status == ClaimStatus.NOVELTY_EVIDENCE_COMPLETE.value
    assert claim.rounds_completed == 2
    assert claim.unresolved_gaps == []
    frontiers = repository.persisted["gap_frontiers"]
    assert frontiers[0]["open_gaps"]
    assert frontiers[1]["open_gaps"] == []
    resolutions = frontiers[1]["closed_gap_resolutions"]
    assert resolutions
    assert all(
        item["closed_reason"] == "single_reference_novelty_complete"
        for item in resolutions.values()
    )
    assert all(
        item["status"] in {"succeeded", "partial", "failed", "cancelled"}
        and item.get("completed_at")
        for item in repository.module_runs
    )
    assert {
        "I2_QUERY_PLAN",
        "I3_PATENT_SEARCH",
        "I3_FETCH",
        "I3_QUALIFY",
        "I4_S_SINGLE_REFERENCE",
        "I4_C_CLOSEST_PRIOR_ART",
    }.issubset({item["module_code"] for item in repository.module_runs})
    assert "I4_I_INVENTIVE_STEP" not in {
        item["module_code"] for item in repository.module_runs
    }
    assert engine.compare_inputs
    assert all(
        "目标专利说明书全文" in item["target_patent_text"]
        for item in engine.compare_inputs
    )
    assert all(
        item["rule_version"] == I4S_RULE_VERSION
        and item["prompt_version"] == I4S_PROMPT_VERSION
        for item in repository.persisted["disclosures"]
    )
    persisted_analysis = repository.persisted["disclosures"][0]["disclosures"][0][
        "analysis"
    ]
    assert {
        "mapping_basis",
        "structural_evidence",
        "integrated_structure_mapping",
        "structural_search_summary",
        "necessity_chain",
        "reasonable_alternatives_excluded",
        "alternative_path_analysis",
        "analysis_rule_version",
    }.issubset(persisted_analysis)
    i4s_run = next(
        item
        for item in repository.module_runs
        if item["module_code"] == "I4_S_SINGLE_REFERENCE"
    )
    binding = i4s_run["input_snapshot"]["input_binding"]
    assert binding["analysis_rule_version"] == I4S_RULE_VERSION
    assert binding["prompt_version"] == I4S_PROMPT_VERSION


@pytest.mark.parametrize(
    ("gap_kind", "expected_reason"),
    [
        ("feature_gap", "feature_disclosure_verified"),
        ("evidence_gap", "disclosure_evidence_completed"),
    ],
)
def test_disappeared_feature_or_evidence_gap_has_traceable_i4s_resolution(
    gap_kind: str,
    expected_reason: str,
) -> None:
    document_key = "fixture:RESOLVING-DOCUMENT"
    document_id = _uuid()
    record = _evidence("RESOLVING-DOCUMENT")
    comparison = FakeAnalysisEngine({document_key: {"1-f2"}}).compare_single_reference(
        document_id=document_key,
        limitations=[
            Limitation(
                feature_id="1-f2",
                claim_id="1",
                sequence=2,
                text="升降清洁件",
                technical_subject="扫地机器人",
            )
        ],
    )
    state = ClaimCheckpoint(
        claim_id="1",
        claim_investigation_id=_uuid(),
        documents={
            document_key: {
                "repository_document_id": document_id,
                "record": record.model_dump(),
            }
        },
        comparisons={document_key: comparison.model_dump(mode="json")},
    )
    round_state = RoundCheckpoint(
        iteration_number=2,
        iteration_id=_uuid(),
        gaps_before=[
            {
                "gap_id": "gap-feature-f2",
                "kind": gap_kind,
                "subtype": "feature_uncovered",
                "feature_id": "1-f2",
                "source_document_ids": ["fixture:OLD-D1"],
                "search_anchor": "扫地机器人 + 升降清洁件",
                "rationale": "旧证据不足",
            }
        ],
        gaps_after=[],
    )

    after, resolutions = _prepare_gap_reconciliation(
        state=state,
        round_state=round_state,
        closed_by_document_key=None,
        closed_reason=None,
    )

    assert after == []
    assert resolutions
    resolution = resolutions["gap-feature-f2"]
    assert resolution["closed_reason"] == expected_reason
    assert resolution["resolved_by_document_id"] == document_id
    assert resolution["resolution_evidence"]["analysis_module"] == "I4-S"
    assert resolution["resolution_evidence"]["disclosure"][
        "evidence_location"
    ] == "paragraph 1"


def test_feature_gap_becoming_evidence_gap_stays_open_under_same_key() -> None:
    state = ClaimCheckpoint(
        claim_id="1",
        claim_investigation_id=_uuid(),
    )
    round_state = RoundCheckpoint(
        iteration_number=2,
        iteration_id=_uuid(),
        gaps_before=[
            {
                "gap_id": "stable-feature-gap",
                "kind": "feature_gap",
                "subtype": "feature_uncovered",
                "feature_id": "1-f2",
                "source_document_ids": ["fixture:D1"],
                "search_anchor": "升降清洁件",
                "rationale": "尚未披露",
            }
        ],
        gaps_after=[
            {
                "gap_id": "new-analysis-gap-id",
                "kind": "evidence_gap",
                "subtype": "disclosure_uncertain",
                "feature_id": "1-f2",
                "source_document_ids": ["fixture:D1-v2"],
                "search_anchor": "升降清洁件证据定位",
                "rationale": "疑似披露但证据仍不足",
            }
        ],
    )

    after, resolutions = _prepare_gap_reconciliation(
        state=state,
        round_state=round_state,
        closed_by_document_key=None,
        closed_reason=None,
    )

    assert len(after) == 1
    assert after[0]["gap_id"] == "stable-feature-gap"
    assert after[0]["gap_key"] == "stable-feature-gap"
    assert after[0]["analysis_gap_id"] == "new-analysis-gap-id"
    assert after[0]["kind"] == "evidence_gap"
    assert resolutions is None


def test_date_gap_closes_only_after_deterministic_qualification() -> None:
    document_key = "fixture:DATE-DOCUMENT"
    document_id = _uuid()
    state = ClaimCheckpoint(
        claim_id="1",
        claim_investigation_id=_uuid(),
        documents={
            document_key: {
                "repository_document_id": document_id,
                "record": _evidence("DATE-DOCUMENT").model_dump(),
            }
        },
        date_qualifications={
            document_key: {
                "category": "ordinary_prior_art",
                "novelty_eligible": True,
                "inventive_step_eligible": True,
                "requires_human_review": False,
                "reason_codes": ["public_before_critical_date"],
            }
        },
    )
    gap = {
        "gap_id": "gap-date-document",
        "kind": "date_gap",
        "subtype": "date_qualification",
        "feature_id": None,
        "source_document_ids": [document_key],
        "search_anchor": "公开日期与关键日证据",
        "rationale": "日期待核验",
    }
    round_state = RoundCheckpoint(
        iteration_number=2,
        iteration_id=_uuid(),
        gaps_before=[gap],
        gaps_after=[],
    )

    after, resolutions = _prepare_gap_reconciliation(
        state=state,
        round_state=round_state,
        closed_by_document_key=None,
        closed_reason=None,
    )

    assert after == []
    assert resolutions
    resolution = resolutions["gap-date-document"]
    assert resolution["closed_reason"] == "date_qualification_determined"
    assert resolution["resolved_by_document_id"] == document_id
    assert resolution["resolution_evidence"]["analysis_module"] == "I1.5"

    state.date_qualifications[document_key] = {
        "category": "unknown",
        "novelty_eligible": False,
        "inventive_step_eligible": False,
        "requires_human_review": True,
    }
    still_open, unresolved_resolution = _prepare_gap_reconciliation(
        state=state,
        round_state=round_state,
        closed_by_document_key=None,
        closed_reason=None,
    )
    assert [_gap["gap_id"] for _gap in still_open] == ["gap-date-document"]
    assert unresolved_resolution is None


def test_combination_gap_closure_records_i4i_documents_and_criterion() -> None:
    d1_key = "fixture:D1"
    d2_key = "fixture:D2"
    d1_id = _uuid()
    state = ClaimCheckpoint(
        claim_id="1",
        claim_investigation_id=_uuid(),
        documents={
            d1_key: {
                "repository_document_id": d1_id,
                "record": _evidence("D1").model_dump(),
            },
            d2_key: {
                "repository_document_id": _uuid(),
                "record": _evidence("D2").model_dump(),
            },
        },
    )
    round_state = RoundCheckpoint(
        iteration_number=2,
        iteration_id=_uuid(),
        gaps_before=[
            {
                "gap_id": "gap-combination-motivation",
                "kind": "combination_gap",
                "subtype": "combination_motivation",
                "feature_id": None,
                "source_document_ids": [d1_key, d2_key],
                "search_anchor": "组合启示",
                "rationale": "组合动机证据不足",
            }
        ],
        gaps_after=[],
            combination={
                "combination_document_ids": [d1_key, d2_key],
                "evidence_complete": True,
                "model": "glm-4.6v-fixture",
                "combination_motivation": {
                "status": "supported",
                "evidence_quote": "combination teaching",
                "evidence_location": "paragraph 2",
                "reasoning": "traceable fixture",
                "confidence": 0.95,
            },
        },
    )

    after, resolutions = _prepare_gap_reconciliation(
        state=state,
        round_state=round_state,
        closed_by_document_key=None,
        closed_reason=None,
    )

    assert after == []
    assert resolutions
    resolution = resolutions["gap-combination-motivation"]
    assert resolution["resolved_by_document_id"] == d1_id
    assert resolution["closed_reason"] == (
        "combination_motivation_analysis_completed"
    )
    assert resolution["resolution_evidence"]["analysis_module"] == "I4-I"
    assert resolution["resolution_evidence"]["resolving_document_keys"] == [
        d1_key,
        d2_key,
    ]


def test_provider_failure_is_partial_not_success_or_exhausted() -> None:
    repository = FakeRepository(_target("1"))
    evidence = _evidence("D1")
    engine = FakeAnalysisEngine({"fixture:D1": {"1-f1"}})
    gateway = ScriptedGateway(
        {
            ("1", 1): SearchBatch(
                provider="fixture",
                records=(evidence,),
                complete=False,
                errors=("provider timeout",),
            )
        }
    )

    result = InvalidityWorkflow(repository, engine, gateway).execute_investigation(
        repository.investigation["id"]
    )

    assert result.status == "partial"
    assert result.claim_results[0].status == ClaimStatus.PARTIAL.value
    assert "provider timeout" in result.claim_results[0].provider_failures[0]
    assert repository.investigation.get("completed_at") is not None


def test_complete_single_document_survives_other_provider_warning() -> None:
    repository = FakeRepository(_target("1"))
    evidence = _evidence("D1-COMPLETE")
    engine = FakeAnalysisEngine(
        {"fixture:D1-COMPLETE": {"1-f1", "1-f2"}}
    )
    gateway = ScriptedGateway(
        {
            ("1", 1): SearchBatch(
                provider="fixture",
                records=(evidence,),
                complete=False,
                errors=("secondary source timeout",),
            )
        }
    )

    result = InvalidityWorkflow(repository, engine, gateway).execute_investigation(
        repository.investigation["id"]
    )

    claim = result.claim_results[0]
    assert result.status == InvestigationStatus.PARTIAL.value
    assert claim.status == ClaimStatus.NOVELTY_EVIDENCE_COMPLETE.value
    assert claim.novelty_document_id == "fixture:D1-COMPLETE"
    assert "secondary source timeout" in claim.provider_failures[0]
    row = repository.claims[claim.claim_investigation_id]
    assert row["result_summary"]["search_scope_complete"] is False
    assert row["result_summary"]["single_document_only"] is True


def test_complete_empty_search_stops_as_exhausted_after_single_reference_review() -> None:
    repository = FakeRepository(_target("1"))

    result = InvalidityWorkflow(
        repository,
        FakeAnalysisEngine({}),
        ScriptedGateway({}),
    ).execute_investigation(repository.investigation["id"])

    claim = result.claim_results[0]
    assert claim.status == ClaimStatus.EXHAUSTED.value
    claim_row_id = claim.claim_investigation_id
    assert ClaimStatus.SINGLE_REFERENCE_REVIEW.value in repository.status_history[claim_row_id]
    claim_row = repository.claims[claim_row_id]
    assert claim_row["critical_date"].isoformat() == "2020-01-01"
    assert claim_row["target_publication_date"].isoformat() == "2021-01-01"
    assert claim_row["result_summary"]["critical_date_basis"] == (
        "user_declared_critical_date"
    )


def test_network_used_is_persisted_for_zero_hit_query_and_module_runs() -> None:
    repository = FakeRepository(_target("1"))
    gateway = ScriptedGateway(
        {
            ("1", 1): SearchBatch(
                provider="google_patents",
                records=(),
                complete=True,
                network_used=True,
            )
        }
    )

    result = InvalidityWorkflow(
        repository,
        FakeAnalysisEngine({}),
        gateway,
    ).execute_investigation(repository.investigation["id"])

    assert result.claim_results[0].status == ClaimStatus.EXHAUSTED.value
    assert repository.persisted["queries"][0]["diagnostic"]["network_used"] is True
    provider_modules = {
        "I3_PATENT_SEARCH",
        "I3_FETCH",
        "I3_QUALIFY",
    }
    audited_runs = [
        item for item in repository.module_runs if item["module_code"] in provider_modules
    ]
    assert {item["module_code"] for item in audited_runs} == provider_modules
    assert all(
        item["requested_mode"] == item["effective_mode"] == "live"
        for item in audited_runs
    )
    assert all(item["actual_provider"] == "google_patents" for item in audited_runs)
    assert all(item["network_used"] is True for item in audited_runs)
    assert all(item["output_snapshot"]["network_used"] is True for item in audited_runs)


def test_cross_language_relevance_uses_two_technical_anchors() -> None:
    record = replace(
        _evidence("EN-NPL"),
        full_text=(
            "A robotic vacuum cleaner uses lidar navigation and a lifting mop "
            "assembly for floor cleaning."
        ),
    )
    query = SearchQuery(
        query_id="npl-cross-language",
        provider_kind="npl",
        purpose="initial",
        technical_subject="扫地机器人",
        feature_ids=["1-f1", "1-f2"],
        expression="robotic vacuum lidar navigation lifting mop",
        language="en",
        rationale="cross-language fixture",
    )

    assert _query_relevant(record, query)


def test_cross_language_relevance_rejects_one_broad_word() -> None:
    record = replace(
        _evidence("EN-BROAD"),
        full_text="A general robot platform for industrial use.",
    )
    query = SearchQuery(
        query_id="npl-broad",
        provider_kind="npl",
        purpose="initial",
        technical_subject="扫地机器人",
        feature_ids=["1-f1", "1-f2"],
        expression="robot",
        language="en",
        rationale="broad fixture",
    )

    assert not _query_relevant(record, query)


def test_configured_gateway_fails_closed_then_uses_confirmed_conflicting_window() -> None:
    patent_provider = EmptyPatentProvider()
    gateway = ConfiguredEvidenceGateway(
        patent_provider=patent_provider,  # type: ignore[arg-type]
        npl_provider=EmptyNplProvider(),  # type: ignore[arg-type]
        artifact_store=None,  # type: ignore[arg-type]
        max_candidates_per_query=5,
    )
    query = SearchQuery(
        query_id="cn-conflicting",
        provider_kind="patent",
        purpose="initial",
        technical_subject="扫地机器人",
        feature_ids=["f1", "f2"],
        expression="扫地机器人 移动底盘 升降清洁件",
        language="zh",
        rationale="dual date lane",
        date_channel=DateChannel.CN_CONFLICTING_APPLICATION,
    )

    blocked = gateway.search(
        query,
        investigation_id="investigation",
        claim_id="1",
        iteration_number=1,
        critical_date=datetime(2020, 1, 1, tzinfo=timezone.utc).date(),
        target_publication_date=None,
        target_publication_date_verified=False,
    )
    assert blocked.complete is False
    assert blocked.network_used is False
    assert patent_provider.calls == []

    completed = gateway.search(
        query,
        investigation_id="investigation",
        claim_id="1",
        iteration_number=1,
        critical_date=datetime(2020, 1, 1, tzinfo=timezone.utc).date(),
        target_publication_date=datetime(2021, 1, 1, tzinfo=timezone.utc).date(),
        target_publication_date_verified=True,
    )
    assert completed.complete is True
    assert completed.network_used is True
    assert patent_provider.calls[0]["country"] == "CN"
    assert patent_provider.calls[0]["server_after"].isoformat() == "2020-01-01"
    assert patent_provider.calls[0]["server_before"].isoformat() == "2021-01-01"
    assert patent_provider.calls[0]["filing_before"].isoformat() == "2020-01-01"


def test_configured_gateway_freezes_raw_patent_search_response_even_for_lead(
    tmp_path: Path,
) -> None:
    raw_xml = b"<ops:world-patent-data xmlns:ops='http://ops.epo.org'/>"

    class RawSearchPatentProvider(EmptyPatentProvider):
        provider_name = "epo_ops"

        def __init__(self) -> None:
            super().__init__()
            self.last_search_artifact = RetrievedArtifact(
                provider="epo_ops",
                kind="provider_search_response",
                source_url=(
                    "https://ops.epo.org/3.2/rest-services/"
                    "published-data/search/biblio"
                ),
                media_type="application/exchange+xml",
                content=raw_xml,
            )

        def search(self, expression: str, **kwargs: Any) -> list[EvidenceRecord]:
            self.calls.append({"expression": expression, **kwargs})
            return [
                EvidenceRecord(
                    provider="epo_ops",
                    source_type="patent",
                    external_id="EP1234567A1",
                    title="Frozen raw response fixture",
                    source_url="https://ops.epo.org/fixture/EP1234567A1",
                    publication_number="EP1234567A1",
                )
            ]

        @staticmethod
        def retrieve(record: EvidenceRecord) -> EvidenceRecord:
            return record

    store = ArtifactStore(tmp_path / "invalidity/test", "test")
    gateway = ConfiguredEvidenceGateway(
        patent_provider=RawSearchPatentProvider(),
        npl_provider=EmptyNplProvider(),  # type: ignore[arg-type]
        artifact_store=store,
        max_candidates_per_query=5,
    )
    query = SearchQuery(
        query_id="epo-raw-response",
        provider_kind="patent",
        purpose="initial",
        technical_subject="robot cleaner",
        feature_ids=["f1", "f2"],
        expression="robot cleaner lifting pad",
        language="en",
        rationale="raw response audit",
    )

    batch = gateway.search(
        query,
        investigation_id="investigation-epo-raw",
        claim_id="1",
        iteration_number=1,
        critical_date=datetime(2020, 1, 1, tzinfo=timezone.utc).date(),
        target_publication_date=None,
        target_publication_date_verified=False,
    )

    assert batch.complete is False  # the fixture intentionally remains a lead
    assert len(batch.artifacts) == 1
    frozen = batch.artifacts[0]
    assert frozen["artifact_type"] == "provider_search_response"
    assert frozen["sha256"] == hashlib.sha256(raw_xml).hexdigest()
    assert Path(str(frozen["uri"])).read_bytes() == raw_xml
    record = batch.records[0]
    assert record.provenance["search_snapshot_sha256"] == frozen["sha256"]
    assert any(
        item.get("artifact_type") == "provider_search_response"
        for item in record.artifacts
    )


def test_configured_gateway_uses_patsnap_for_discovery_and_epo_for_full_document(
    tmp_path: Path,
) -> None:
    publication_number = "EP3456789A1"
    patent_id = "718ead9c-4f3c-4674-8f5a-24e126827269"

    class PatsnapDiscovery(EmptyPatentProvider):
        provider_name = "patsnap"

        def __init__(self) -> None:
            super().__init__()
            self.last_search_artifact = RetrievedArtifact(
                provider="patsnap",
                kind="provider_search_response",
                source_url=(
                    "https://connect.zhihuiya.com/"
                    "search/patent/query-search-patent/v2"
                ),
                media_type="application/json",
                content=b'{"status":true,"error_code":0}',
            )

        def search(self, expression: str, **kwargs: Any) -> list[EvidenceRecord]:
            self.calls.append({"expression": expression, **kwargs})
            return [
                EvidenceRecord(
                    provider="patsnap",
                    source_type="patent",
                    external_id=patent_id,
                    title="Patsnap-discovered lead",
                    source_url=self.last_search_artifact.source_url,
                    publication_number=publication_number,
                    provenance={"discovery_provider": "patsnap"},
                )
            ]

    class EpoRetrieval(EmptyPatentProvider):
        provider_name = "epo_ops"

        def __init__(self) -> None:
            super().__init__()
            self.received: list[EvidenceRecord] = []

        def retrieve(self, record: EvidenceRecord) -> EvidenceRecord:
            self.received.append(record)
            document = fitz.open()
            try:
                page = document.new_page()
                page.insert_text((72, 72), "Official OPS full document")
                pdf = document.tobytes(garbage=4, deflate=True)
            finally:
                document.close()
            metadata = RetrievedArtifact(
                provider="epo_ops",
                kind="provider_retrieval_bundle",
                source_url="https://ops.epo.org/3.2/rest-services/published-data",
                media_type="application/vnd.epo.ops.bundle+json",
                content=b'{"format":"epo_ops_response_bundle_v1"}',
            )
            artifact = RetrievedArtifact(
                provider="epo_ops",
                kind="pdf",
                source_url=(
                    "https://ops.epo.org/3.2/rest-services/"
                    "published-data/images/EP/3456789/A1/fullimage?Range=1-1"
                ),
                media_type="application/pdf",
                content=pdf,
                source_metadata_artifact=metadata,
            )
            return replace(
                record,
                provider="epo_ops",
                external_id=record.external_id,
                source_url=(
                    "https://ops.epo.org/3.2/rest-services/published-data/"
                    "publication/epodoc/EP3456789.A1/biblio"
                ),
                stage=EvidenceStage.RETRIEVED,
                publication_number=publication_number,
                publication_date="2019-11-20",
                filing_date="2018-01-15",
                priority_date="2017-01-16",
                provenance={
                    **dict(record.provenance),
                    "retrieval_provider": "epo_ops",
                    "publication_date_verified": True,
                    "filing_date_verified": True,
                    "priority_date_verified": True,
                    "public_date_evidence": "EPO OPS biblio",
                },
                primary_artifact=artifact,
            )

        @staticmethod
        def qualify(record: EvidenceRecord, **_kwargs: Any) -> EvidenceRecord:
            return record

    discovery = PatsnapDiscovery()
    retrieval = EpoRetrieval()
    gateway = ConfiguredEvidenceGateway(
        patent_provider=discovery,
        patent_retrieval_provider=retrieval,
        npl_provider=EmptyNplProvider(),  # type: ignore[arg-type]
        artifact_store=ArtifactStore(tmp_path / "invalidity/test", "test"),
        max_candidates_per_query=1,
    )
    query = SearchQuery(
        query_id="patsnap-to-epo",
        provider_kind="patent",
        purpose="initial",
        technical_subject="robot cleaner",
        feature_ids=["f1"],
        expression="robot cleaner lifting pad",
        language="en",
        rationale="provider role split",
    )

    batch = gateway.search(
        query,
        investigation_id="investigation-patsnap-to-epo",
        claim_id="1",
        iteration_number=1,
        critical_date=datetime(2020, 1, 1, tzinfo=timezone.utc).date(),
        target_publication_date=None,
        target_publication_date_verified=False,
    )

    assert batch.complete is True
    assert batch.provider == "patsnap+epo_ops_retrieval"
    assert retrieval.received[0].provider == "patsnap"
    assert retrieval.received[0].external_id == patent_id
    result = batch.records[0]
    assert result.provider == "epo_ops"
    assert result.publication_number == publication_number
    assert result.provenance["discovery_provider"] == "patsnap"
    assert result.provenance["retrieval_provider"] == "epo_ops"
    primary = next(
        item for item in result.artifacts if item.get("is_primary_source")
    )
    assert Path(str(primary["uri"])).read_bytes().lstrip().startswith(b"%PDF")
    assert any(
        item.get("kind") == "provider_retrieval_bundle"
        for item in result.artifacts
    )


def test_configured_gateway_records_network_attempt_on_provider_failure() -> None:
    class FailingPatentProvider(EmptyPatentProvider):
        def search(self, expression: str, **kwargs: Any) -> list[EvidenceRecord]:
            self.calls.append({"expression": expression, **kwargs})
            raise ProviderRequestError("upstream timeout")

    gateway = ConfiguredEvidenceGateway(
        patent_provider=FailingPatentProvider(),  # type: ignore[arg-type]
        npl_provider=EmptyNplProvider(),  # type: ignore[arg-type]
        artifact_store=None,  # type: ignore[arg-type]
        max_candidates_per_query=5,
    )
    query = SearchQuery(
        query_id="network-failure",
        provider_kind="patent",
        purpose="initial",
        technical_subject="扫地机器人",
        feature_ids=["f1", "f2"],
        expression="扫地机器人 移动底盘 升降清洁件",
        language="zh",
        rationale="network audit",
    )

    batch = gateway.search(
        query,
        investigation_id="investigation",
        claim_id="1",
        iteration_number=1,
        critical_date=datetime(2020, 1, 1, tzinfo=timezone.utc).date(),
        target_publication_date=None,
        target_publication_date_verified=False,
    )

    assert batch.complete is False
    assert batch.records == ()
    assert batch.network_used is True
    assert "upstream timeout" in batch.errors[0]


def test_patsnap_search_failure_freezes_raw_response_and_attempt_log(
    tmp_path: Path,
) -> None:
    raw_error = b'{"status":false,"error_code":68300001,"message":"denied"}'
    attempt_log = (
        {
            "attempt": 1,
            "status_code": 403,
            "error_code": 68300001,
            "correlation_id": "corr-fixture",
            "outcome": "provider_error",
            "retry_scheduled": False,
        },
    )

    class FailingPatsnapProvider(EmptyPatentProvider):
        provider_name = "patsnap"

        def __init__(self) -> None:
            super().__init__()
            self.last_search_artifact = RetrievedArtifact(
                provider="patsnap",
                kind="provider_error_response",
                source_url=(
                    "https://connect.zhihuiya.com/"
                    "search/patent/query-search-patent/v2"
                ),
                media_type="application/json",
                content=raw_error,
                request_attempt_log=attempt_log,
            )

        def search(self, expression: str, **kwargs: Any) -> list[EvidenceRecord]:
            self.calls.append({"expression": expression, **kwargs})
            raise ProviderRequestError("Patsnap HTTP 403")

    store = ArtifactStore(tmp_path / "invalidity/test", "test")
    gateway = ConfiguredEvidenceGateway(
        patent_provider=FailingPatsnapProvider(),  # type: ignore[arg-type]
        npl_provider=EmptyNplProvider(),  # type: ignore[arg-type]
        artifact_store=store,
        max_candidates_per_query=5,
    )
    query = SearchQuery(
        query_id="patsnap-failure-audit",
        provider_kind="patent",
        purpose="initial",
        technical_subject="optical sensor",
        feature_ids=["f1", "f2"],
        expression="optical sensor sealed chamber",
        language="en",
        rationale="freeze provider failure",
    )

    batch = gateway.search(
        query,
        investigation_id="investigation-patsnap-failure-audit",
        claim_id="1",
        iteration_number=1,
        critical_date=datetime(2020, 1, 1, tzinfo=timezone.utc).date(),
        target_publication_date=None,
        target_publication_date_verified=False,
    )

    assert batch.complete is False
    assert batch.records == ()
    assert batch.network_used is True
    assert len(batch.artifacts) == 1
    frozen = batch.artifacts[0]
    assert frozen["artifact_type"] == "provider_error_response"
    assert Path(str(frozen["uri"])).read_bytes() == raw_error
    assert frozen["request_attempt_log"][0]["correlation_id"] == "corr-fixture"


def test_patsnap_detail_failure_freezes_every_received_provider_response(
    tmp_path: Path,
) -> None:
    raw_search = b'{"status":true,"error_code":0,"data":{"results":[]}}'
    raw_biblio = b'{"status":true,"error_code":0,"data":{"bad":"shape"}}'

    class DetailFailPatsnapProvider(EmptyPatentProvider):
        provider_name = "patsnap"

        def __init__(self) -> None:
            super().__init__()
            self.last_search_artifact = RetrievedArtifact(
                provider="patsnap",
                kind="provider_search_response",
                source_url=(
                    "https://connect.zhihuiya.com/"
                    "search/patent/query-search-patent/v2"
                ),
                media_type="application/json",
                content=raw_search,
            )
            self.last_retrieval_artifacts: tuple[RetrievedArtifact, ...] = ()

        def search(self, expression: str, **kwargs: Any) -> list[EvidenceRecord]:
            self.calls.append({"expression": expression, **kwargs})
            return [
                EvidenceRecord(
                    provider="patsnap",
                    source_type="patent",
                    external_id="718ead9c-4f3c-4674-8f5a-24e126827269",
                    title="Malformed detail",
                    source_url=(
                        "https://connect.zhihuiya.com/"
                        "basic-patent-data/bibliography"
                    ),
                    publication_number="US20190300001A1",
                )
            ]

        def retrieve(self, _record: EvidenceRecord) -> EvidenceRecord:
            self.last_retrieval_artifacts = (
                RetrievedArtifact(
                    provider="patsnap",
                    kind="provider_bibliography_response",
                    source_url=(
                        "https://connect.zhihuiya.com/"
                        "basic-patent-data/bibliography"
                    ),
                    media_type="application/json",
                    content=raw_biblio,
                    request_attempt_log=({"attempt": 1, "outcome": "success"},),
                ),
            )
            raise ProviderContentError("P012 schema invalid")

    store = ArtifactStore(tmp_path / "invalidity/test", "test")
    gateway = ConfiguredEvidenceGateway(
        patent_provider=DetailFailPatsnapProvider(),  # type: ignore[arg-type]
        npl_provider=EmptyNplProvider(),  # type: ignore[arg-type]
        artifact_store=store,
        max_candidates_per_query=1,
    )
    query = SearchQuery(
        query_id="patsnap-detail-failure-audit",
        provider_kind="patent",
        purpose="initial",
        technical_subject="optical sensor",
        feature_ids=["f1", "f2"],
        expression="optical sensor sealed chamber",
        language="en",
        rationale="freeze malformed detail",
    )

    batch = gateway.search(
        query,
        investigation_id="investigation-patsnap-detail-failure",
        claim_id="1",
        iteration_number=1,
        critical_date=datetime(2020, 1, 1, tzinfo=timezone.utc).date(),
        target_publication_date=None,
        target_publication_date_verified=False,
    )

    assert batch.complete is False
    detail = next(
        item
        for item in batch.artifacts
        if item.get("kind") == "provider_bibliography_response"
    )
    assert Path(str(detail["uri"])).read_bytes() == raw_biblio
    assert detail["request_attempt_log"][0]["outcome"] == "success"


def test_patsnap_incomplete_detail_lead_still_freezes_all_raw_responses(
    tmp_path: Path,
) -> None:
    patent_id = "718ead9c-4f3c-4674-8f5a-24e126827269"
    raw_responses = {
        "provider_bibliography_response": b'{"data":{"bibliography":{}}}',
        "provider_claims_response": b'{"data":{"claims":[]}}',
        "provider_description_response": b'{"data":{"description":[]}}',
    }

    class IncompletePatsnapProvider(EmptyPatentProvider):
        provider_name = "patsnap"

        def __init__(self) -> None:
            super().__init__()
            self.last_search_artifact = RetrievedArtifact(
                provider="patsnap",
                kind="provider_search_response",
                source_url=(
                    "https://connect.zhihuiya.com/"
                    "search/patent/query-search-patent/v2"
                ),
                media_type="application/json",
                content=b'{"status":true,"error_code":0,"data":{"results":[]}}',
            )
            self.last_retrieval_artifacts: tuple[RetrievedArtifact, ...] = ()

        def search(self, _expression: str, **_kwargs: Any) -> list[EvidenceRecord]:
            return [
                EvidenceRecord(
                    provider="patsnap",
                    source_type="patent",
                    external_id=patent_id,
                    title="No full text",
                    source_url=(
                        "https://connect.zhihuiya.com/"
                        "basic-patent-data/bibliography"
                    ),
                    publication_number="US20190300001A1",
                )
            ]

        def retrieve(self, record: EvidenceRecord) -> EvidenceRecord:
            self.last_retrieval_artifacts = tuple(
                RetrievedArtifact(
                    provider="patsnap",
                    kind=kind,
                    source_url=(
                        "https://connect.zhihuiya.com/basic-patent-data/" + kind
                    ),
                    media_type="application/json",
                    content=content,
                    request_attempt_log=({"attempt": 1, "outcome": "success"},),
                )
                for kind, content in raw_responses.items()
            )
            return replace(
                record,
                qualification_issues=("patsnap_fulltext_not_available",),
            )

    gateway = ConfiguredEvidenceGateway(
        patent_provider=IncompletePatsnapProvider(),  # type: ignore[arg-type]
        npl_provider=EmptyNplProvider(),  # type: ignore[arg-type]
        artifact_store=ArtifactStore(tmp_path / "invalidity/test", "test"),
        max_candidates_per_query=1,
    )
    query = SearchQuery(
        query_id="patsnap-incomplete-detail-audit",
        provider_kind="patent",
        purpose="initial",
        technical_subject="optical sensor",
        feature_ids=["f1", "f2"],
        expression="optical sensor sealed chamber",
        language="en",
        rationale="freeze incomplete detail",
    )

    batch = gateway.search(
        query,
        investigation_id="investigation-patsnap-incomplete-detail",
        claim_id="1",
        iteration_number=1,
        critical_date=datetime(2020, 1, 1, tzinfo=timezone.utc).date(),
        target_publication_date=None,
        target_publication_date_verified=False,
    )

    assert batch.complete is False
    assert batch.records[0].stage is EvidenceStage.LEAD
    frozen_by_kind = {
        str(item.get("kind")): item
        for item in batch.artifacts
        if str(item.get("kind")) in raw_responses
    }
    assert set(frozen_by_kind) == set(raw_responses)
    for kind, content in raw_responses.items():
        assert Path(str(frozen_by_kind[kind]["uri"])).read_bytes() == content


def test_patsnap_media_failure_keeps_frozen_text_document(tmp_path: Path) -> None:
    raw_search = b'{"data":{"results":[]},"status":true,"error_code":0}'
    raw_detail = b'{"p012":{},"p018":{},"p019":{}}'
    raw_image_metadata = b'{"status":true,"error_code":0,"data":{"fulltext_image":[]}}'
    raw_pdf_error = b'{"status":false,"error_code":68300001}'

    class MediaUnavailablePatsnapProvider(PatsnapProvider):
        def __init__(self) -> None:
            self._last_search_artifact = RetrievedArtifact(
                provider="patsnap",
                kind="provider_search_response",
                source_url="https://connect.zhihuiya.com/search/patent/query-search-patent/v2",
                media_type="application/json",
                content=raw_search,
            )
            self._last_media_artifacts: tuple[RetrievedArtifact, ...] = ()

        def search(self, _expression: str, **_kwargs: Any) -> list[EvidenceRecord]:
            return [
                EvidenceRecord(
                    provider="patsnap",
                    source_type="patent",
                    external_id="718ead9c-4f3c-4674-8f5a-24e126827269",
                    title="Text survives missing media",
                    source_url="https://connect.zhihuiya.com/basic-patent-data/bibliography",
                    publication_number="US20190300001A1",
                    publication_date="2019-12-31",
                    raw_metadata={
                        "patent_id": "718ead9c-4f3c-4674-8f5a-24e126827269"
                    },
                )
            ]

        @staticmethod
        def retrieve(record: EvidenceRecord) -> EvidenceRecord:
            artifact = RetrievedArtifact(
                provider="patsnap",
                kind="source_bundle",
                source_url=record.source_url,
                media_type="application/vnd.patsnap.bundle+json",
                content=raw_detail,
            )
            return replace(
                record.with_artifact(artifact),
                claims="1. A text-only retrieved claim.",
                description="Retrieved description.",
                full_text="Retrieved description. 1. A text-only retrieved claim.",
                provenance={
                    "publication_date_verified": True,
                    "public_date_evidence": "fixture P012 response",
                },
            )

        def download_image(
            self, _record: EvidenceRecord, *, index: int = 0
        ) -> RetrievedArtifact:
            self._last_media_artifacts = (
                RetrievedArtifact(
                    provider="patsnap",
                    kind="provider_image_metadata_response",
                    source_url=(
                        "https://connect.zhihuiya.com/"
                        "basic-patent-data/fulltext-image"
                    ),
                    media_type="application/json",
                    content=raw_image_metadata,
                    request_attempt_log=({"attempt": 1, "outcome": "success"},),
                ),
            )
            raise ProviderContentError(f"P042 image {index} unavailable")

        def download_pdf(self, _record: EvidenceRecord) -> RetrievedArtifact:
            self._last_media_artifacts = (
                RetrievedArtifact(
                    provider="patsnap",
                    kind="provider_error_response",
                    source_url=(
                        "https://connect.zhihuiya.com/"
                        "basic-patent-data/pdf-data"
                    ),
                    media_type="application/json",
                    content=raw_pdf_error,
                    request_attempt_log=(
                        {"attempt": 1, "outcome": "provider_error"},
                    ),
                ),
            )
            raise ProviderContentError("P020 unavailable")

        def close(self) -> None:
            return None

    store = ArtifactStore(tmp_path / "invalidity/test", "test")
    gateway = ConfiguredEvidenceGateway(
        patent_provider=MediaUnavailablePatsnapProvider(),
        npl_provider=EmptyNplProvider(),  # type: ignore[arg-type]
        artifact_store=store,
        max_candidates_per_query=1,
    )
    query = SearchQuery(
        query_id="patsnap-media-gap",
        provider_kind="patent",
        purpose="initial",
        technical_subject="optical sensor",
        feature_ids=["f1", "f2"],
        expression="optical sensor sealed chamber",
        language="en",
        rationale="preserve retrieved text",
    )

    batch = gateway.search(
        query,
        investigation_id="investigation-patsnap-media-gap",
        claim_id="1",
        iteration_number=1,
        critical_date=datetime(2020, 1, 1, tzinfo=timezone.utc).date(),
        target_publication_date=None,
        target_publication_date_verified=False,
    )

    assert batch.complete is False
    assert len(batch.records) == 1
    record = batch.records[0]
    assert record.stage is EvidenceStage.RETRIEVED
    assert "text-only retrieved claim" in str(record.full_text)
    assert "patsnap_media_unavailable" in record.qualification_issues
    assert record.provenance["media_retrieval"]["status"] == "unavailable"
    primary = next(item for item in record.artifacts if item.get("is_primary_source"))
    assert Path(str(primary["uri"])).read_bytes() == raw_detail
    image_failure = next(
        item
        for item in record.artifacts
        if item.get("kind") == "provider_image_metadata_response"
    )
    pdf_failure = next(
        item
        for item in record.artifacts
        if item.get("kind") == "provider_error_response"
    )
    assert Path(str(image_failure["uri"])).read_bytes() == raw_image_metadata
    assert Path(str(pdf_failure["uri"])).read_bytes() == raw_pdf_error


def test_patsnap_image_keeps_frozen_p042_metadata_link(tmp_path: Path) -> None:
    metadata_raw = (
        b'{"data":{"patent_id":"718ead9c-4f3c-4674-8f5a-24e126827269",'
        b'"fulltext_image":[{"path":"https://open.zhihuiya.com/image?sign=x"}]},'
        b'"status":true,"error_code":0}'
    )
    detail_raw = b'{"p012":{},"p018":{},"p019":{}}'
    metadata = RetrievedArtifact(
        provider="patsnap",
        kind="provider_image_metadata_response",
        source_url="https://connect.zhihuiya.com/basic-patent-data/fulltext-image",
        media_type="application/json",
        content=metadata_raw,
        request_attempt_log=({"attempt": 1, "outcome": "success"},),
    )

    class ImagePatsnapProvider(PatsnapProvider):
        def __init__(self) -> None:
            pass

        @staticmethod
        def download_image(
            _record: EvidenceRecord, *, index: int = 0
        ) -> RetrievedArtifact:
            if index:
                raise ProviderContentError("no second image")
            return RetrievedArtifact(
                provider="patsnap",
                kind="image",
                source_url="https://open.zhihuiya.com/image",
                media_type="image/png",
                content=b"\x89PNG\r\n\x1a\nfixture",
                source_metadata_artifact=metadata,
                request_attempt_log=metadata.request_attempt_log,
            )

        @staticmethod
        def download_pdf(_record: EvidenceRecord) -> RetrievedArtifact:
            raise AssertionError("image success must not fall back to PDF")

        def close(self) -> None:
            return None

    store = ArtifactStore(tmp_path / "invalidity/test", "test")
    gateway = ConfiguredEvidenceGateway(
        patent_provider=ImagePatsnapProvider(),
        npl_provider=EmptyNplProvider(),  # type: ignore[arg-type]
        artifact_store=store,
        max_candidates_per_query=1,
    )
    source = RetrievedArtifact(
        provider="patsnap",
        kind="source_bundle",
        source_url="https://connect.zhihuiya.com/basic-patent-data/bibliography",
        media_type="application/vnd.patsnap.bundle+json",
        content=detail_raw,
    )
    record = EvidenceRecord(
        provider="patsnap",
        source_type="patent",
        external_id="718ead9c-4f3c-4674-8f5a-24e126827269",
        title="P042 linkage",
        source_url=source.source_url,
        stage=EvidenceStage.RETRIEVED,
        publication_number="US20190300001A1",
        primary_artifact=source,
    )

    result = gateway._materialize_google(
        record,
        investigation_id="investigation-patsnap-p042-link",
    )

    metadata_row = next(
        item
        for item in result.artifacts
        if item.get("kind") == "provider_image_metadata_response"
    )
    assert Path(str(metadata_row["uri"])).read_bytes() == metadata_raw
    assert metadata_row["request_attempt_log"][0]["outcome"] == "success"
    image_row = next(item for item in result.artifacts if item.get("kind") == "image")
    assert image_row["source_url"] == "https://open.zhihuiya.com/image"
    assert image_row["source_metadata_sha256"] == metadata.content_sha256


def test_patsnap_pdf_preserves_issues_and_frozen_p020_metadata_link(
    tmp_path: Path,
) -> None:
    document = fitz.open()
    try:
        page = document.new_page()
        page.insert_text((72, 72), "Patsnap PDF fixture text")
        pdf_content = document.tobytes()
    finally:
        document.close()

    metadata_raw = (
        b'{"data":{"patent_id":"718ead9c-4f3c-4674-8f5a-24e126827269",'
        b'"pdf_path":"https://open.zhihuiya.com/pdf?sign=x"},'
        b'"status":true,"error_code":0}'
    )
    metadata = RetrievedArtifact(
        provider="patsnap",
        kind="provider_pdf_metadata_response",
        source_url="https://connect.zhihuiya.com/basic-patent-data/pdf-data",
        media_type="application/json",
        content=metadata_raw,
        request_attempt_log=({"attempt": 1, "outcome": "success"},),
    )

    class PdfPatsnapProvider(PatsnapProvider):
        def __init__(self) -> None:
            pass

        @staticmethod
        def download_image(
            _record: EvidenceRecord, *, index: int = 0
        ) -> RetrievedArtifact:
            raise ProviderContentError(f"P042 image {index} unavailable")

        @staticmethod
        def download_pdf(_record: EvidenceRecord) -> RetrievedArtifact:
            return RetrievedArtifact(
                provider="patsnap",
                kind="pdf",
                source_url="https://open.zhihuiya.com/pdf",
                media_type="application/pdf",
                content=pdf_content,
                source_metadata_artifact=metadata,
                request_attempt_log=metadata.request_attempt_log,
            )

        def close(self) -> None:
            return None

    source = RetrievedArtifact(
        provider="patsnap",
        kind="source_bundle",
        source_url="https://connect.zhihuiya.com/basic-patent-data/bibliography",
        media_type="application/vnd.patsnap.bundle+json",
        content=b'{"p012":{},"p018":{},"p019":{}}',
    )
    record = EvidenceRecord(
        provider="patsnap",
        source_type="patent",
        external_id="718ead9c-4f3c-4674-8f5a-24e126827269",
        title="P020 linkage",
        source_url=source.source_url,
        stage=EvidenceStage.RETRIEVED,
        publication_number="US20190300001A1",
        qualification_issues=("patsnap_date_conflict",),
        primary_artifact=source,
    )
    store = ArtifactStore(tmp_path / "invalidity/test", "test")
    gateway = ConfiguredEvidenceGateway(
        patent_provider=PdfPatsnapProvider(),
        npl_provider=EmptyNplProvider(),  # type: ignore[arg-type]
        artifact_store=store,
        max_candidates_per_query=1,
    )

    result = gateway._materialize_google(
        record,
        investigation_id="investigation-patsnap-p020-link",
    )

    assert "patsnap_date_conflict" in result.qualification_issues
    metadata_row = next(
        item
        for item in result.artifacts
        if item.get("kind") == "provider_pdf_metadata_response"
    )
    assert Path(str(metadata_row["uri"])).read_bytes() == metadata_raw
    primary = next(item for item in result.artifacts if item.get("is_primary_source"))
    assert primary["kind"] == "pdf"
    assert primary["provider"] == "patsnap"
    assert primary["source_metadata_sha256"] == metadata.content_sha256
    assert primary["request_attempt_log"][0]["outcome"] == "success"


def test_patsnap_empty_media_failures_keep_distinct_call_audits(
    tmp_path: Path,
) -> None:
    class EmptyFailurePatsnapProvider(PatsnapProvider):
        def __init__(self) -> None:
            self._last_media_artifacts = (
                RetrievedArtifact(
                    provider="patsnap",
                    kind="provider_error_response",
                    source_url=(
                        "https://connect.zhihuiya.com/"
                        "basic-patent-data/fulltext-image"
                    ),
                    media_type="application/octet-stream",
                    content=b"",
                    request_attempt_log=(
                        {
                            "request_id": "image-attempt",
                            "operation": "P042_signed_image_download",
                            "outcome": "transport_or_bounds_error",
                        },
                    ),
                ),
                RetrievedArtifact(
                    provider="patsnap",
                    kind="provider_error_response",
                    source_url=(
                        "https://connect.zhihuiya.com/"
                        "basic-patent-data/pdf-data"
                    ),
                    media_type="application/octet-stream",
                    content=b"",
                    request_attempt_log=(
                        {
                            "request_id": "pdf-attempt",
                            "operation": "P020_signed_pdf_download",
                            "outcome": "transport_or_bounds_error",
                        },
                    ),
                ),
            )

        def close(self) -> None:
            return None

    gateway = ConfiguredEvidenceGateway(
        patent_provider=EmptyFailurePatsnapProvider(),
        npl_provider=EmptyNplProvider(),  # type: ignore[arg-type]
        artifact_store=ArtifactStore(tmp_path / "invalidity/test", "test"),
        max_candidates_per_query=1,
    )
    record = EvidenceRecord(
        provider="patsnap",
        source_type="patent",
        external_id="718ead9c-4f3c-4674-8f5a-24e126827269",
        title="empty failures",
        source_url="https://connect.zhihuiya.com/basic-patent-data/bibliography",
    )

    materialized = gateway._materialize_tracked_media_failures(
        record,
        investigation_id="investigation-empty-media-failures",
        relative_stem="718ead9c",
    )

    failures = [
        item
        for item in materialized.artifacts
        if item.get("kind") == "provider_error_response"
    ]
    assert len(failures) == 2
    assert {
        item["request_attempt_log"][0]["request_id"] for item in failures
    } == {"image-attempt", "pdf-attempt"}


def test_configured_gateway_preserves_composite_npl_failure_as_partial() -> None:
    class BlockedSource:
        provider_name = "blocked_npl"

        def search(self, *_args: Any, **_kwargs: Any) -> list[EvidenceRecord]:
            raise ProviderRequestError("blocked_npl HTTP 403")

        def close(self) -> None:
            return None

    class MetadataSource:
        provider_name = "crossref"

        def search(self, *_args: Any, **_kwargs: Any) -> list[EvidenceRecord]:
            return [
                EvidenceRecord(
                    provider="crossref",
                    source_type="paper",
                    external_id="10.1000/fixture",
                    title="Robot cleaner lifting actuator",
                    source_url="https://doi.org/10.1000/fixture",
                )
            ]

        def close(self) -> None:
            return None

    gateway = ConfiguredEvidenceGateway(
        patent_provider=EmptyPatentProvider(),  # type: ignore[arg-type]
        npl_provider=CompositeNplProvider([BlockedSource(), MetadataSource()]),
        artifact_store=None,  # type: ignore[arg-type]
        max_candidates_per_query=5,
    )
    query = SearchQuery(
        query_id="npl-composite",
        provider_kind="npl",
        purpose="initial",
        technical_subject="扫地机器人",
        feature_ids=["f1", "f2"],
        expression="robot cleaner lifting actuator",
        language="en",
        rationale="composite source failure",
    )

    batch = gateway.search(
        query,
        investigation_id="investigation",
        claim_id="1",
        iteration_number=1,
        critical_date=datetime(2020, 1, 1, tzinfo=timezone.utc).date(),
        target_publication_date=None,
        target_publication_date_verified=False,
    )

    assert batch.complete is False
    assert [record.external_id for record in batch.records] == ["10.1000/fixture"]
    assert any("HTTP 403" in error for error in batch.errors)
    assert any("仅返回题录 lead" in error for error in batch.errors)


def test_composite_npl_fetches_declared_open_access_pdf_but_keeps_date_unverified(
    tmp_path: Path,
) -> None:
    document = fitz.open()
    try:
        page = document.new_page()
        page.insert_text(
            (72, 72),
            "robot cleaner lifting actuator controller technical disclosure",
        )
        pdf_content = document.tobytes()
    finally:
        document.close()

    class MetadataSource:
        provider_name = "openalex"

        def search(self, *_args: Any, **_kwargs: Any) -> list[EvidenceRecord]:
            return [
                EvidenceRecord(
                    provider="openalex",
                    source_type="paper",
                    external_id="W123",
                    title="Robot cleaner lifting actuator",
                    source_url="https://openalex.org/W123",
                    pdf_url="https://publisher.example/W123.pdf",
                    publication_date="2018-04-03",
                    provenance={"metadata_only": True},
                )
            ]

        def close(self) -> None:
            return None

    class SafeWebRetriever(WebEvidenceProvider):
        provider_name = "web_search"

        def __init__(self) -> None:
            pass

        def search(self, *_args: Any, **_kwargs: Any) -> list[EvidenceRecord]:
            return []

        def retrieve(self, lead: EvidenceRecord) -> EvidenceRecord:
            assert lead.source_url == "https://publisher.example/W123.pdf"
            return lead.with_artifact(
                RetrievedArtifact(
                    provider=self.provider_name,
                    kind="pdf",
                    source_url=lead.source_url,
                    media_type="application/pdf",
                    content=pdf_content,
                )
            )

        def close(self) -> None:
            return None

    artifact_store = ArtifactStore(tmp_path / "invalidity/test", "test")
    gateway = ConfiguredEvidenceGateway(
        patent_provider=EmptyPatentProvider(),  # type: ignore[arg-type]
        npl_provider=CompositeNplProvider([MetadataSource(), SafeWebRetriever()]),
        artifact_store=artifact_store,
        max_candidates_per_query=5,
    )
    query = SearchQuery(
        query_id="npl-open-access-pdf",
        provider_kind="npl",
        purpose="initial",
        technical_subject="扫地机器人",
        feature_ids=["f1", "f2"],
        expression="robot cleaner lifting actuator",
        language="en",
        rationale="retrieve declared open-access PDF",
    )

    batch = gateway.search(
        query,
        investigation_id="investigation-open-access",
        claim_id="1",
        iteration_number=1,
        critical_date=datetime(2020, 1, 1, tzinfo=timezone.utc).date(),
        target_publication_date=None,
        target_publication_date_verified=False,
    )

    assert batch.complete is False
    assert len(batch.records) == 1
    retrieved = batch.records[0]
    assert retrieved.provider == "openalex"
    assert retrieved.stage is EvidenceStage.RETRIEVED
    assert retrieved.source_url == "https://publisher.example/W123.pdf"
    assert retrieved.provenance["bibliographic_source_url"] == "https://openalex.org/W123"
    assert "public_date_evidence_missing" in retrieved.qualification_issues
    assert "date_eligibility_unknown" in retrieved.qualification_issues
    assert any("公开日期/来源链待人工核验" in error for error in batch.errors)
    primary = next(item for item in retrieved.artifacts if item.get("is_primary_source"))
    primary_path = Path(str(primary["uri"]))
    assert artifact_store.root in primary_path.parents
    assert primary_path.read_bytes().startswith(b"%PDF")


def test_patent_without_independent_claim_fails_before_claim_creation() -> None:
    source = _target("1")
    claim = source["patent_snapshot"]["claims"][0]
    claim["claim_type"] = "DEPENDENT"
    claim["parent_claim_ids"] = ["2", "3"]
    claim["dependency_uncertain"] = True
    claim["expanded_claim_text"] = "权利要求2【或】权利要求3，加上本项限制"
    repository = FakeRepository(source)
    gateway = ScriptedGateway({})

    with pytest.raises(WorkflowInputError, match="未识别到独立权利要求"):
        InvalidityWorkflow(
            repository,
            FakeAnalysisEngine({}),
            gateway,
        ).execute_investigation(repository.investigation["id"])

    assert repository.claims == {}
    assert repository.investigation["status"] == InvestigationStatus.FAILED.value
    assert gateway.calls == []


def test_dependent_claim_is_frozen_but_not_investigated() -> None:
    source = _target("1", "2")
    dependent = source["patent_snapshot"]["claims"][1]
    dependent["claim_type"] = "DEPENDENT"
    dependent["parent_claim_ids"] = ["1"]
    dependent["dependency_uncertain"] = True
    repository = FakeRepository(source)
    gateway = ScriptedGateway({})

    result = InvalidityWorkflow(
        repository,
        FakeAnalysisEngine({}),
        gateway,
    ).execute_investigation(repository.investigation["id"])

    assert [item.claim_id for item in result.claim_results] == ["1"]
    assert {row["claim_id"] for row in repository.claims.values()} == {"1"}
    assert source["patent_snapshot"]["claims"][1]["claim_id"] == "2"


def test_missing_document_image_fails_closed_as_partial() -> None:
    repository = FakeRepository(_target("1"))
    gateway = ScriptedGateway(
        {("1", 1): SearchBatch(provider="fixture", records=(_evidence("NOIMG", images=False),))}
    )
    result = InvalidityWorkflow(
        repository,
        FakeAnalysisEngine({"fixture:NOIMG": {"1-f1", "1-f2"}}),
        gateway,
    ).execute_investigation(repository.investigation["id"])

    claim = result.claim_results[0]
    assert claim.status == ClaimStatus.PARTIAL.value
    assert any("文献图像" in item for item in claim.provider_failures)


def test_automatic_loop_never_exceeds_initial_plus_five_gap_rounds_and_checkpoint_replays() -> None:
    repository = FakeRepository(_target("1"))
    repository.investigation["settings"]["max_gap_search_iterations"] = 5
    records = [_evidence(f"D{index}") for index in range(1, 7)]
    gateway = ScriptedGateway(
        {
            ("1", index): SearchBatch(provider="fixture", records=(records[index - 1],))
            for index in range(1, 7)
        }
    )
    engine = FakeAnalysisEngine(
        {f"fixture:D{index}": {"1-f1"} for index in range(1, 7)}
    )
    with pytest.raises(ValueError, match="1..5"):
        InvalidityWorkflow(repository, engine, gateway, max_rounds=9)
    workflow = InvalidityWorkflow(repository, engine, gateway, max_rounds=5)

    first = workflow.execute_investigation(repository.investigation["id"])
    calls_after_first = list(gateway.calls)
    replay = workflow.execute_job(
        {"payload": {"investigation_id": repository.investigation["id"]}},
        {"module_code": "I0_ORCHESTRATE"},
    )

    assert first.claim_results[0].status == ClaimStatus.EXHAUSTED.value
    assert first.claim_results[0].rounds_completed == 6
    assert calls_after_first == [("1", index) for index in range(1, 7)]
    assert gateway.calls == calls_after_first
    frontiers = repository.persisted["gap_frontiers"]
    assert len(frontiers) == 6
    assert all(item["open_gaps"] for item in frontiers)
    feature_keys = [
        {
            gap["gap_key"]
            for gap in item["open_gaps"]
            if gap["gap_type"] in {"feature_gap", "evidence_gap"}
        }
        for item in frontiers
    ]
    assert feature_keys[0]
    assert all(keys == feature_keys[0] for keys in feature_keys)
    assert all(not item["closed_gap_resolutions"] for item in frontiers)
    gap_plan_runs = [
        item
        for item in repository.module_runs
        if item["module_code"] == "I2_GAP_QUERY_PLAN"
    ]
    assert len(gap_plan_runs) == 5
    assert all(item["status"] == "succeeded" for item in gap_plan_runs)
    expected_gap_strategies = [
        "adjacent_object_direct_structure",
        "broader_object_structural_family",
        "same_function_object_action_role",
        "subsystem_component_relation_path",
        "analogous_domain_principle_effect",
    ]
    assert [
        item["input_snapshot"]["gap_search_strategy"]["code"]
        for item in gap_plan_runs
    ] == expected_gap_strategies
    assert [
        item["output_snapshot"]["gap_search_strategy"]
        for item in gap_plan_runs
    ] == expected_gap_strategies
    assert all(
        query["gap_search_strategy"]
        == item["output_snapshot"]["gap_search_strategy"]
        for item in gap_plan_runs
        for query in item["output_snapshot"]["queries"]
    )
    assert all(
        item["output_snapshot"]["allowed_gap_feature_ids"] == ["1-f2"]
        for item in gap_plan_runs
    )
    assert all(
        {
            feature_id
            for query in item["output_snapshot"]["queries"]
            for feature_id in query["feature_ids"]
        }
        == {"1-f2"}
        for item in gap_plan_runs
    )
    initial_plan_runs = [
        item
        for item in repository.module_runs
        if item["module_code"] == "I2_QUERY_PLAN"
    ]
    assert len(initial_plan_runs) == 1
    assert initial_plan_runs[0]["input_snapshot"]["iteration_number"] == 1
    assert (
        first.claim_results[0].terminal_reason
        == "五个 gap 检索轮已完成；保留各轮错误和未覆盖区别特征，并已基于现有真实文献输出组合分析与大 Claim Chart"
    )
    assert len(engine.inventive_step_inputs) == 5
    final_combination_ids = {
        item.document_id
        for item in engine.inventive_step_inputs[-1]["comparisons"]
    }
    # I4-I receives D1 plus the complete current I4-S corpus from all five gap
    # rounds.  The production analysis engine, rather than I0, selects the
    # bounded actual combination and records the full considered set.
    assert len(final_combination_ids) == 6
    assert "fixture:D1" in final_combination_ids
    checkpoint_claim = next(iter(first.checkpoint.claims.values()))
    assert checkpoint_claim.rounds["6"].combination is not None
    assert set(
        checkpoint_claim.current_closest_prior_art[
            "large_claim_chart_document_ids"
        ]
    ) == {f"fixture:D{index}" for index in range(1, 7)}
    assert replay.checkpoint.model_dump(mode="json") == workflow.load_checkpoint(
        repository.investigation["id"]
    ).model_dump(mode="json")


def test_gap_plan_errors_are_frozen_and_later_rounds_and_i4i_still_run() -> None:
    class TwoFailedGapPlans(FakeAnalysisEngine):
        def plan_queries(self, **kwargs: Any) -> QueryPlan:
            if int(kwargs.get("iteration_number") or 0) in {3, 4}:
                raise AnalysisValidationError("检索策略与上一轮同族")
            return super().plan_queries(**kwargs)

    repository = FakeRepository(_target("1"))
    repository.investigation["settings"]["max_gap_search_iterations"] = 5
    gateway = ScriptedGateway(
        {
            ("1", index): SearchBatch(
                provider="fixture",
                records=(_evidence(f"D{index}"),),
            )
            for index in (1, 2, 5, 6)
        }
    )
    engine = TwoFailedGapPlans(
        {f"fixture:D{index}": {"1-f1"} for index in (1, 2, 5, 6)}
    )

    result = InvalidityWorkflow(repository, engine, gateway).execute_investigation(
        repository.investigation["id"]
    )

    claim = result.claim_results[0]
    assert claim.rounds_completed == 6
    assert claim.status == ClaimStatus.PARTIAL.value
    assert gateway.calls == [("1", 1), ("1", 2), ("1", 5), ("1", 6)]
    checkpoint_claim = result.checkpoint.claims["1"]
    assert checkpoint_claim.rounds["3"].stop_reason == (
        "round_error_preserved_continue_gap_search"
    )
    assert checkpoint_claim.rounds["4"].stop_reason == (
        "round_error_preserved_continue_gap_search"
    )
    failed_plans = [
        item
        for item in repository.module_runs
        if item["module_code"] == "I2_GAP_QUERY_PLAN"
        and item["status"] == "failed"
    ]
    assert len(failed_plans) == 2
    assert engine.inventive_step_inputs
    final_ledger = engine.inventive_step_inputs[-1][
        "upstream_gap_search_errors"
    ]
    assert {
        (item["gap_search_iteration"], item["stage"])
        for item in final_ledger
        if item["stage"] == "I2_GAP_QUERY_PLAN"
    } == {(2, "I2_GAP_QUERY_PLAN"), (3, "I2_GAP_QUERY_PLAN")}


def test_new_gap_document_covering_all_differences_stops_in_same_round() -> None:
    repository = FakeRepository(_target("1"))
    gateway = ScriptedGateway(
        {
            ("1", 1): SearchBatch(
                provider="fixture", records=(_evidence("D1"),)
            ),
            ("1", 2): SearchBatch(
                provider="fixture", records=(_evidence("D2"),)
            ),
        }
    )
    engine = FakeAnalysisEngine(
        {
            "fixture:D1": {"1-f1"},
            "fixture:D2": {"1-f2"},
        }
    )

    result = InvalidityWorkflow(
        repository,
        engine,
        gateway,
        max_rounds=5,
    ).execute_investigation(repository.investigation["id"])

    claim = result.claim_results[0]
    assert claim.status == ClaimStatus.EXHAUSTED.value
    assert claim.rounds_completed == 2
    assert gateway.calls == [("1", 1), ("1", 2)]
    assert engine.compare_calls == ["fixture:D1", "fixture:D2"]
    assert len(engine.inventive_step_inputs) == 1
    gap_runs = [
        item
        for item in repository.module_runs
        if item["module_code"] == "I2_GAP_QUERY_PLAN"
    ]
    assert len(gap_runs) == 1
    assert gap_runs[0]["output_snapshot"]["allowed_gap_feature_ids"] == [
        "1-f2"
    ]
    checkpoint_claim = next(iter(result.checkpoint.claims.values()))
    assert checkpoint_claim.rounds["2"].combination is not None
    assert set(
        checkpoint_claim.current_closest_prior_art[
            "large_claim_chart_document_ids"
        ]
    ) == {"fixture:D1", "fixture:D2"}
    assert (
        checkpoint_claim.current_closest_prior_art["gap_reuse_decision"][
            "stop_reason"
        ]
        == "all_differences_covered"
    )
    assert "立即停止后续 gap 检索" in claim.terminal_reason


def test_existing_corpus_full_difference_coverage_skips_search_but_runs_combination() -> None:
    repository = FakeRepository(_target("1"))
    d1 = _evidence("D1")
    d2 = _evidence("D2")
    gateway = ScriptedGateway(
        {
            ("1", 1): SearchBatch(provider="fixture", records=(d1, d2)),
        }
    )
    engine = FakeAnalysisEngine(
        {
            "fixture:D1": {"1-f1"},
            "fixture:D2": {"1-f2"},
        }
    )

    result = InvalidityWorkflow(
        repository,
        engine,
        gateway,
        max_rounds=5,
    ).execute_investigation(repository.investigation["id"])

    claim = result.claim_results[0]
    assert claim.status == ClaimStatus.EXHAUSTED.value
    assert claim.rounds_completed == 2
    assert gateway.calls == [("1", 1)]
    gap_runs = [
        item
        for item in repository.module_runs
        if item["module_code"] == "I2_GAP_QUERY_PLAN"
    ]
    assert len(gap_runs) == 1
    gap_output = gap_runs[0]["output_snapshot"]
    assert gap_output["queries"] == []
    assert gap_output["generation_source"] == "existing_corpus_reuse"
    assert gap_output["allowed_gap_feature_ids"] == []
    assert gap_output["gap_reuse_decision"]["stop_reason"] == (
        "all_differences_covered"
    )
    assert len(repository.persisted["combinations"]) == 2
    checkpoint_claim = next(iter(result.checkpoint.claims.values()))
    assert set(
        checkpoint_claim.current_closest_prior_art[
            "large_claim_chart_document_ids"
        ]
    ) == {"fixture:D1", "fixture:D2"}
    assert "已完成可运行的组合分析" in claim.terminal_reason


def test_incomplete_provider_on_third_round_is_partial_not_exhausted() -> None:
    repository = FakeRepository(_target("1"))
    records = [_evidence(f"D{index}") for index in range(1, 4)]
    gateway = ScriptedGateway(
        {
            ("1", 1): SearchBatch(provider="fixture", records=(records[0],)),
            ("1", 2): SearchBatch(provider="fixture", records=(records[1],)),
            ("1", 3): SearchBatch(
                provider="fixture",
                records=(records[2],),
                complete=False,
                errors=("third-round timeout",),
            ),
        }
    )
    engine = FakeAnalysisEngine(
        {f"fixture:D{index}": {"1-f1"} for index in range(1, 4)}
    )

    result = InvalidityWorkflow(repository, engine, gateway).execute_investigation(
        repository.investigation["id"]
    )

    claim = result.claim_results[0]
    # The incomplete third round is retained as partial, but no longer blocks
    # the remaining configured round.
    assert claim.rounds_completed == 4
    assert claim.status == ClaimStatus.EXHAUSTED.value
    assert result.status == InvestigationStatus.PARTIAL.value
    assert "third-round timeout" in " ".join(claim.provider_failures)


def test_continuation_extends_absolute_rounds_three_to_six_and_replay_is_idempotent() -> None:
    repository = FakeRepository(_target("1"))
    repository.investigation["settings"]["max_gap_search_iterations"] = 2
    records = [_evidence(f"D{index}") for index in range(1, 7)]
    gateway = ScriptedGateway(
        {
            ("1", index): SearchBatch(
                provider="fixture", records=(records[index - 1],)
            )
            for index in range(1, 7)
        }
    )
    engine = FakeAnalysisEngine(
        {f"fixture:D{index}": {"1-f1"} for index in range(1, 7)}
    )
    workflow = InvalidityWorkflow(repository, engine, gateway)

    initial = workflow.execute_investigation(repository.investigation["id"])
    assert initial.claim_results[0].rounds_completed == 3
    assert initial.claim_results[0].status == ClaimStatus.EXHAUSTED.value
    batch, job = _continuation_job(
        repository,
        claim_investigation_ids=[initial.claim_results[0].claim_investigation_id],
        start_iteration_no=4,
        end_iteration_no=6,
    )

    continued = workflow.execute_job(job, {"module_code": "I0_ORCHESTRATE"})
    calls_after_continuation = list(gateway.calls)
    iteration_ids_after_continuation = [
        item["id"] for item in repository.persisted["iterations"]
    ]
    replay = workflow.execute_job(job, {"module_code": "I0_ORCHESTRATE"})

    assert continued.claim_results[0].rounds_completed == 6
    assert continued.claim_results[0].status == ClaimStatus.EXHAUSTED.value
    assert calls_after_continuation == [("1", index) for index in range(1, 7)]
    assert gateway.calls == calls_after_continuation
    assert [item["id"] for item in repository.persisted["iterations"]] == (
        iteration_ids_after_continuation
    )
    assert replay.checkpoint.model_dump(mode="json") == (
        continued.checkpoint.model_dump(mode="json")
    )
    assert batch["status"] == "succeeded"
    assert batch["status_history"] == ["running", "succeeded"]


def test_human_review_after_round_three_does_not_consume_round_four() -> None:
    repository = FakeRepository(_target("1"))
    repository.investigation["settings"]["max_gap_search_iterations"] = 2
    records = [_evidence(f"D{index}") for index in range(1, 5)]
    gateway = ScriptedGateway(
        {
            ("1", index): SearchBatch(
                provider="fixture", records=(records[index - 1],)
            )
            for index in range(1, 5)
        }
    )
    engine = FakeAnalysisEngine(
        {f"fixture:D{index}": {"1-f1"} for index in range(1, 5)}
    )
    workflow = InvalidityWorkflow(repository, engine, gateway)
    initial = workflow.execute_investigation(repository.investigation["id"])
    checkpoint = workflow.load_checkpoint(repository.investigation["id"])
    assert checkpoint is not None
    state = checkpoint.claims["1"]
    assert state.current_round == checkpoint.max_rounds == 3
    round_keys_before = set(state.rounds)

    _record_human_review_frontier(
        state,
        review_action_id=_uuid(),
        review_revision=1,
        document_key="fixture:D3",
        closest_prior_art=state.current_closest_prior_art,
        combination=None,
    )
    assert state.current_round == 3
    assert checkpoint.max_rounds == 3
    assert set(state.rounds) == round_keys_before
    workflow.save_checkpoint(repository.investigation["id"], checkpoint)

    _, job = _continuation_job(
        repository,
        claim_investigation_ids=[initial.claim_results[0].claim_investigation_id],
        start_iteration_no=4,
        end_iteration_no=4,
    )
    continued = workflow.execute_job(job, {"module_code": "I0_ORCHESTRATE"})

    assert continued.claim_results[0].rounds_completed == 4
    assert "4" in continued.checkpoint.claims["1"].rounds
    assert gateway.calls[-1] == ("1", 4)


def test_continuation_resumes_confirmed_critical_date_at_round_one() -> None:
    source = _target("1")
    source.pop("declared_critical_date")
    # 宪章 2026-07-25 决定后，只有专利级申请日/优先权日全缺才会停车等人工。
    source["patent_snapshot"]["application_date"] = None
    source["patent_snapshot"]["priority_date"] = None
    repository = FakeRepository(source)
    workflow = InvalidityWorkflow(
        repository,
        FakeAnalysisEngine({}),
        ScriptedGateway({}),
    )

    initial = workflow.execute_investigation(repository.investigation["id"])
    claim_id = initial.claim_results[0].claim_investigation_id
    assert initial.claim_results[0].status == ClaimStatus.NEEDS_HUMAN_REVIEW.value
    assert initial.claim_results[0].rounds_completed == 0
    repository.critical_date_confirmations[claim_id] = {
        "decision": "confirm_priority",
        "confirmed_date": "2019-06-01",
        "critical_date_basis": "confirmed_priority",
        "target_publication_date": "2021-01-01",
    }
    batch, job = _continuation_job(
        repository,
        claim_investigation_ids=[claim_id],
        start_iteration_no=1,
        end_iteration_no=1,
    )

    result = workflow.execute_job(job, {"module_code": "I0_ORCHESTRATE"})

    assert result.claim_results[0].rounds_completed == 1
    assert result.claim_results[0].status == ClaimStatus.EXHAUSTED.value
    assert repository.claims[claim_id]["critical_date"].isoformat() == "2019-06-01"
    assert batch["status"] == "succeeded"


def test_patent_level_priority_date_auto_applies_without_human_stop() -> None:
    # 宪章 2026-07-25 决定：有可解析优先权日时直接作为整件专利关键日，
    # 自动适用于全部权利要求，不再逐项停车等人工核验。
    source = _target("1")
    source.pop("declared_critical_date")
    source["patent_snapshot"]["priority_date"] = "2019-06-01"
    repository = FakeRepository(source)
    workflow = InvalidityWorkflow(
        repository,
        FakeAnalysisEngine({}),
        ScriptedGateway({}),
    )

    result = workflow.execute_investigation(repository.investigation["id"])

    claim = result.claim_results[0]
    assert claim.status in {
        ClaimStatus.EXHAUSTED.value,
        ClaimStatus.SEARCH_BUDGET_EXHAUSTED.value,
    }
    assert claim.rounds_completed >= 1
    claim_row = repository.claims[claim.claim_investigation_id]
    assert claim_row["critical_date"].isoformat() == "2019-06-01"
    assert claim_row["result_summary"]["critical_date_basis"] == (
        "patent_level_priority_date"
    )


def test_patent_level_application_date_used_when_no_priority() -> None:
    source = _target("1")
    source.pop("declared_critical_date")
    repository = FakeRepository(source)
    workflow = InvalidityWorkflow(
        repository,
        FakeAnalysisEngine({}),
        ScriptedGateway({}),
    )

    result = workflow.execute_investigation(repository.investigation["id"])

    claim = result.claim_results[0]
    assert claim.status == ClaimStatus.EXHAUSTED.value
    claim_row = repository.claims[claim.claim_investigation_id]
    assert claim_row["critical_date"].isoformat() == "2020-01-01"
    assert claim_row["result_summary"]["critical_date_basis"] == "application_date"


def test_continuation_only_runs_selected_claims() -> None:
    repository = FakeRepository(_target("1", "2"))
    repository.investigation["settings"]["max_rounds"] = 1
    gateway = ScriptedGateway(
        {
            ("1", 1): SearchBatch(provider="fixture", records=(_evidence("C1-D1"),)),
            ("1", 2): SearchBatch(provider="fixture", records=(_evidence("C1-D2"),)),
            ("2", 1): SearchBatch(provider="fixture", records=(_evidence("C2-D1"),)),
            ("2", 2): SearchBatch(provider="fixture", records=(_evidence("C2-D2"),)),
            ("2", 3): SearchBatch(provider="fixture", records=(_evidence("C2-D3"),)),
        }
    )
    engine = FakeAnalysisEngine(
        {
            "fixture:C1-D1": {"1-f1"},
            "fixture:C1-D2": {"1-f1"},
            "fixture:C2-D1": {"2-f1"},
            "fixture:C2-D2": {"2-f1"},
            "fixture:C2-D3": {"2-f1"},
        }
    )
    workflow = InvalidityWorkflow(repository, engine, gateway)
    initial = workflow.execute_investigation(repository.investigation["id"])
    by_claim = {item.claim_id: item for item in initial.claim_results}
    _, job = _continuation_job(
        repository,
        claim_investigation_ids=[by_claim["2"].claim_investigation_id],
        start_iteration_no=3,
        end_iteration_no=3,
    )

    result = workflow.execute_job(job, {"module_code": "I0_ORCHESTRATE"})
    continued_by_claim = {item.claim_id: item for item in result.claim_results}

    assert ("1", 3) not in gateway.calls
    assert ("2", 3) in gateway.calls
    assert continued_by_claim["1"].rounds_completed == 2
    assert continued_by_claim["2"].rounds_completed == 3


@pytest.mark.parametrize(
    "success_status",
    [
        ClaimStatus.NOVELTY_EVIDENCE_COMPLETE,
        ClaimStatus.INVENTIVE_STEP_EVIDENCE_COMPLETE,
    ],
)
def test_continuation_rejects_both_success_terminal_claim_states(
    success_status: ClaimStatus,
) -> None:
    repository = FakeRepository(_target("1"))
    if success_status is ClaimStatus.NOVELTY_EVIDENCE_COMPLETE:
        gateway = ScriptedGateway(
            {("1", 1): SearchBatch(provider="fixture", records=(_evidence("D1"),))}
        )
        engine = FakeAnalysisEngine({"fixture:D1": {"1-f1", "1-f2"}})
    else:
        gateway = ScriptedGateway(
            {
                ("1", 1): SearchBatch(
                    provider="fixture", records=(_evidence("D1"),)
                ),
                ("1", 2): SearchBatch(
                    provider="fixture", records=(_evidence("D2"),)
                ),
            }
        )
        engine = FakeAnalysisEngine(
            {
                "fixture:D1": {"1-f1"},
                "fixture:D2": {"1-f2"},
            },
            complete_combinations={"fixture:D2"},
        )
    workflow = InvalidityWorkflow(repository, engine, gateway)
    initial = workflow.execute_investigation(repository.investigation["id"])
    claim = initial.claim_results[0]
    assert claim.status == success_status.value
    start = claim.rounds_completed + 1
    batch, job = _continuation_job(
        repository,
        claim_investigation_ids=[claim.claim_investigation_id],
        start_iteration_no=start,
        end_iteration_no=start,
    )
    calls_before = list(gateway.calls)

    with pytest.raises(WorkflowInputError, match="成功终态"):
        workflow.execute_job(job, {"module_code": "I0_ORCHESTRATE"})

    assert gateway.calls == calls_before
    assert batch["status"] == "failed"


def test_continuation_rejects_unclosed_parent_round_and_marks_batch_failed() -> None:
    repository = FakeRepository(_target("1"))
    repository.investigation["settings"]["max_rounds"] = 1
    workflow = InvalidityWorkflow(
        repository,
        FakeAnalysisEngine({"fixture:D1": {"1-f1"}}),
        ScriptedGateway(
            {("1", 1): SearchBatch(provider="fixture", records=(_evidence("D1"),))}
        ),
    )
    initial = workflow.execute_investigation(repository.investigation["id"])
    claim_id = initial.claim_results[0].claim_investigation_id
    checkpoint = repository.investigation["workflow_state"]["invalidity_workflow"]
    checkpoint["claims"]["1"]["rounds"]["1"]["complete"] = False
    batch, job = _continuation_job(
        repository,
        claim_investigation_ids=[claim_id],
        start_iteration_no=2,
        end_iteration_no=2,
    )

    with pytest.raises(Exception, match="上一轮尚未原子闭合"):
        workflow.execute_job(job, {"module_code": "I0_ORCHESTRATE"})

    assert batch["status"] == "failed"


def test_initial_round_creates_profile_run_and_query_plan_consumes_it() -> None:
    repository = FakeRepository(_target("1"))

    InvalidityWorkflow(
        repository,
        FakeAnalysisEngine({}),
        ScriptedGateway({}),
    ).execute_investigation(repository.investigation["id"])

    profile_runs = [
        item
        for item in repository.module_runs
        if item["module_code"] == "I2_INVENTIVE_PROFILE"
    ]
    query_runs = [
        item
        for item in repository.module_runs
        if item["module_code"] == "I2_QUERY_PLAN"
    ]
    assert len(profile_runs) == 1
    assert len(query_runs) == 1
    assert profile_runs[0]["status"] == "succeeded"
    assert profile_runs[0]["output_snapshot"]["queries"] == []
    assert profile_runs[0]["output_snapshot"]["limitations"]
    query_output = query_runs[0]["output_snapshot"]
    assert query_output["generation_source"] == "module3_profile_live_model"
    assert query_output["source_plan_run_id"] == profile_runs[0]["id"]
    assert query_output["limitations"] == profile_runs[0]["output_snapshot"]["limitations"]


class _FailingProfileEngine(FakeAnalysisEngine):
    def generate_inventive_profile(self, **kwargs: Any) -> QueryPlan:
        raise WorkflowExecutionError("fixture profile failure")


def test_initial_round_profile_failure_fails_claim_before_query_plan() -> None:
    repository = FakeRepository(_target("1"))

    result = InvalidityWorkflow(
        repository,
        _FailingProfileEngine({}),
        ScriptedGateway({}),
    ).execute_investigation(repository.investigation["id"])

    assert result.claim_results[0].status == ClaimStatus.FAILED.value
    profile_runs = [
        item
        for item in repository.module_runs
        if item["module_code"] == "I2_INVENTIVE_PROFILE"
    ]
    assert len(profile_runs) == 1
    assert profile_runs[0]["status"] == "failed"
    assert not [
        item
        for item in repository.module_runs
        if item["module_code"] == "I2_QUERY_PLAN"
    ]
