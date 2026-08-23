from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import hashlib
from pathlib import Path
from typing import Any, Mapping
import uuid

import fitz
import pytest

import invalidity.lab as lab_module
from invalidity.config import Settings
from invalidity.contracts import I4S_PROMPT_VERSION, I4S_RULE_VERSION
from invalidity.lab import (
    DEFAULT_FIXTURE_ID,
    LAB_MODE_MATRIX,
    LAB_MODULE_CODES,
    build_module_lab_handlers,
)
from invalidity.providers import (
    EvidenceRecord,
    EvidenceStage,
    ProviderContentError,
    ProviderRequestError,
    QueryValidationError,
    RetrievedArtifact,
)
from invalidity.patsnap import PatsnapApiError
from invalidity.report import ReportDataV1, build_report_data
from invalidity.worker import JobContext, PermanentJobError, RetryableJobError


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        environment="test",
        service_port=5209,
        api_base_url="http://127.0.0.1:5209",
        api_token="test-api-token-1234567890",
        database_url="postgresql://user:secret@localhost/invalidity",
        database_schema="invalidity_test",
        artifact_root=tmp_path / "invalidity" / "test",
        allowed_source_roots=(tmp_path.resolve(),),
        module1_api_url="http://127.0.0.1:5201/run",
        patent_provider="google_patents",
        npl_provider="arxiv",
        llm_base_url="https://example.invalid/v4",
        llm_api_key="test-secret",
        llm_model="glm-4.6v-test",
        module1_api_token="parser-token-1234567890",
        module1_auth_mode="bearer",
        parser_api_token="parser-token-1234567890",
        worker_poll_seconds=1,
        job_lease_seconds=60,
        parser_port=5201,
    )


class LabRepository:
    def __init__(self) -> None:
        self.investigations: dict[str, dict[str, Any]] = {}
        self.report_rows: dict[str, dict[str, Any]] = {}
        self.raw_report_data: dict[str, Any] | None = None
        self.artifacts: list[dict[str, Any]] = []
        self.module_runs: list[dict[str, Any]] = []

    def get_investigation(self, investigation_id: Any) -> dict[str, Any] | None:
        return self.investigations.get(str(investigation_id))

    def get_report_snapshot(
        self, investigation_id: Any, report_snapshot_id: Any = None
    ) -> dict[str, Any] | None:
        row = self.report_rows.get(str(investigation_id))
        if row is None or report_snapshot_id is None:
            return row
        return row if str(row.get("id")) == str(report_snapshot_id) else None

    def get_report_data(self, _investigation_id: Any) -> dict[str, Any] | None:
        return self.raw_report_data

    def insert_artifact(self, **values: Any) -> dict[str, Any]:
        row = {"id": str(uuid.uuid4()), **values}
        self.artifacts.append(row)
        return row

    def list_module_runs(self, **values: Any) -> list[dict[str, Any]]:
        result = list(self.module_runs)
        if values.get("investigation_id") is not None:
            result = [
                row
                for row in result
                if str(row.get("investigation_id"))
                == str(values["investigation_id"])
            ]
        if values.get("status") is not None:
            result = [
                row
                for row in result
                if str(row.get("status")) == str(values["status"])
            ]
        return result[: int(values.get("limit") or 100)]


def _live_investigation(repository: LabRepository) -> str:
    investigation_id = str(uuid.uuid4())
    repository.investigations[investigation_id] = {
        "id": investigation_id,
        "source_snapshot": {},
        "workflow_state": {},
        "settings": {},
    }
    return investigation_id


def _live_i2_lab_investigation(
    repository: LabRepository, tmp_path: Path
) -> tuple[str, str]:
    """Investigation with frozen patent facts/images for live I2 lab modules."""

    investigation_id = _live_investigation(repository)
    claim_investigation_id = str(uuid.uuid4())
    repository.investigations[investigation_id]["source_snapshot"] = {
        "target_images": [str(tmp_path / "target-1.png")],
        "patent_snapshot": {
            "source_sha256": "a" * 64,
            "patent_number": "CN222356540U",
            "title": "一种开放式头戴耳机",
            "claims": [
                {
                    "claim_id": "1",
                    "claim_type": "INDEPENDENT",
                    "claim_text": "一种开放式头戴耳机。",
                    "expanded_claim_text": "一种开放式头戴耳机。",
                }
            ],
        },
    }
    return investigation_id, claim_investigation_id


def _persist_filtered_fetch_candidate(
    repository: LabRepository,
    *,
    investigation_id: str,
    document: Mapping[str, Any],
    provider_kind: str = "patent",
    claim_id: str = "1",
    claim_investigation_id: str = "claim-1",
) -> dict[str, str]:
    investigation = repository.investigations[investigation_id]
    workflow_state = investigation.setdefault("workflow_state", {})
    checkpoint = workflow_state.setdefault("invalidity_workflow", {})
    claims = checkpoint.setdefault("claims", {})
    claim = claims.setdefault(
        claim_id,
        {
            "claim_id": claim_id,
            "claim_investigation_id": claim_investigation_id,
            "documents": {},
            "date_qualifications": {},
        },
    )
    claim.setdefault("claim_investigation_id", claim_investigation_id)
    filter_run_id = str(uuid.uuid4())
    candidate_id = hashlib.sha256(
        (
            f"{filter_run_id}|"
            f"{document.get('external_id') or document.get('publication_number')}"
        ).encode()
    ).hexdigest()[:20]
    repository.module_runs.append(
        {
            "id": filter_run_id,
            "investigation_id": investigation_id,
            "claim_investigation_id": claim_investigation_id,
            "module_code": "I3_CANDIDATE_FILTER",
            "status": "succeeded",
            "effective_mode": "live",
            "output_snapshot": {
                "output": {
                    "filter_contract_version": "i3-candidate-filter-v1",
                    "fetch_candidates": [
                        {
                            "candidate_id": candidate_id,
                            "provider_kind": provider_kind,
                            "document": dict(document),
                        }
                    ],
                }
            },
        }
    )
    return {
        "candidate_filter_run_id": filter_run_id,
        "candidate_id": candidate_id,
    }


def _persist_reusable_patent_fetch(
    repository: LabRepository,
    settings: Settings,
    *,
    publication_number: str = "US20180000001A1",
    source_content: bytes | None = None,
    mime_type: str = "text/plain",
    readable_version: str = "readable-patent-v1",
) -> tuple[str, Path]:
    content = source_content or (
        ("complete cached patent description and claims " * 12).encode("utf-8")
    )
    store = lab_module.ArtifactStore(settings.artifact_root, settings.environment)
    suffix = ".pdf" if mime_type == "application/pdf" else ".txt"
    stored = store.write_bytes(
        "historical-patent-cache",
        f"retrieved/google_patents/{publication_number}/source{suffix}",
        content,
        artifact_type="prior_art_primary_source",
        mime_type=mime_type,
    )
    full_text = "complete cached patent description and claims " * 12
    page_count = 1 if mime_type == "application/pdf" else None
    readable = {
        "schema_version": readable_version,
        "source_sha256": stored.sha256,
        "source_kind": "pdf" if page_count else "provider_structured_text",
        "normalization_status": "completed",
        "analysis_ready": True,
        "analysis_readiness_reason": "historical complete document",
        "page_count": page_count,
        "processed_page_count": page_count,
        "text_page_count": page_count,
        "ocr_page_count": 0,
        "failed_pages": [],
        "sections": [{"section": "description", "text": full_text}],
        "full_text": full_text,
    }
    readable_artifact = store.write_json(
        "historical-patent-cache",
        f"retrieved/google_patents/{publication_number}/readable.json",
        readable,
        artifact_type="readable_patent_json",
    )
    run_id = str(uuid.uuid4())
    repository.module_runs.append(
        {
            "id": run_id,
            "investigation_id": str(uuid.uuid4()),
            "claim_investigation_id": str(uuid.uuid4()),
            "module_code": "I3_FETCH",
            "status": "succeeded",
            "effective_mode": "live",
            "output_snapshot": {
                "output": {
                    "provider": "google_patents",
                    "source_type": "patent",
                    "external_id": publication_number,
                    "title": "historical cached patent",
                    "source_url": (
                        f"https://patents.google.com/patent/{publication_number}/en"
                    ),
                    "stage": "retrieved_document",
                    "publication_number": publication_number,
                    "full_text": full_text,
                    "content_sha256": stored.sha256,
                    "retrieved_at": "2026-08-01T00:00:00Z",
                    "artifacts": [
                        {
                            **stored.model_dump(),
                            "kind": "pdf" if page_count else "text",
                            "content_sha256": stored.sha256,
                            "content_length": stored.byte_size,
                            "local_immutable_snapshot": True,
                            "is_primary_source": True,
                        },
                        {
                            **readable_artifact.model_dump(),
                            "kind": "readable_patent_json",
                            "local_immutable_snapshot": True,
                            "is_primary_source": False,
                        },
                    ],
                    "raw_metadata": {},
                    "provenance": {
                        "discovery_provider": "google_patents",
                        "retrieval_provider": "google_patents",
                        "primary_snapshot_verified": True,
                    },
                    "eligibility": None,
                    "qualification_issues": [],
                    "readable_document": readable,
                    "analysis_ready": True,
                    "analysis_readiness_reason": "historical complete document",
                    "requested_mode": "live",
                    "effective_mode": "live",
                    "actual_provider": "google_patents",
                    "network_used": True,
                    "simulated": False,
                }
            },
        }
    )
    return run_id, Path(stored.uri)


class FakePatentSearchProvider:
    def __init__(self, provider_name: str) -> None:
        self.provider_name = provider_name
        self.last_search_artifact: RetrievedArtifact | None = None
        self.calls: list[tuple[str, Any, dict[str, Any]]] = []

    def __enter__(self) -> "FakePatentSearchProvider":
        return self

    def __exit__(self, *_args: Any) -> None:
        return None

    def _result(self, *, route: str, kind: str) -> list[EvidenceRecord]:
        endpoint = f"https://connect.zhihuiya.com/{route}"
        self.last_search_artifact = RetrievedArtifact(
            provider=self.provider_name,
            kind=kind,
            source_url=endpoint,
            media_type="application/json",
            content=f'{{"route":"{route}"}}'.encode(),
        )
        return [
            EvidenceRecord(
                provider=self.provider_name,
                source_type="patent",
                external_id=f"{self.provider_name}-lead-1",
                title="image or text search lead",
                source_url=endpoint,
                stage=EvidenceStage.LEAD,
                publication_number="CN305498776S",
            )
        ]

    def search(self, query: Any, **kwargs: Any) -> list[EvidenceRecord]:
        self.calls.append(("text", query, dict(kwargs)))
        return self._result(
            route="search/patent/query-search-patent/v2",
            kind="provider_search_response",
        )

    def search_single_image(
        self, image_url: str, **kwargs: Any
    ) -> list[EvidenceRecord]:
        self.calls.append(("image_single", image_url, dict(kwargs)))
        return self._result(
            route="search/patent/image-single/beta",
            kind="provider_image_search_response",
        )

    def search_multiple_images(
        self, image_urls: Any, **kwargs: Any
    ) -> list[EvidenceRecord]:
        self.calls.append(("image_multiple", list(image_urls), dict(kwargs)))
        return self._result(
            route="search/patent/image-multiple",
            kind="provider_image_search_response",
        )


class FailingPatentSearchProvider(FakePatentSearchProvider):
    def __init__(
        self,
        error: ProviderContentError | QueryValidationError,
        *,
        raw_response: bytes | None = None,
    ) -> None:
        super().__init__("patsnap")
        self.error = error
        self.raw_response = raw_response

    def search(self, query: Any, **kwargs: Any) -> list[EvidenceRecord]:
        self.calls.append(("text", query, dict(kwargs)))
        if self.raw_response is not None:
            self.last_search_artifact = RetrievedArtifact(
                provider=self.provider_name,
                kind="provider_search_response",
                source_url=(
                    "https://connect.zhihuiya.com/"
                    "search/patent/query-search-patent/v2"
                ),
                media_type="application/json",
                content=self.raw_response,
                request_attempt_log=(
                    {
                        "operation": "P002",
                        "request_id": "fixture-request-id",
                        "outcome": "success",
                    },
                ),
            )
        raise self.error


def _persisted_report(repository: LabRepository) -> str:
    investigation_id = str(uuid.uuid4())
    report_snapshot_id = str(uuid.uuid4())
    investigation = {
        "id": investigation_id,
        "analysis_session_id": "persisted-lab-report",
        "environment": "test",
        "status": "completed",
        "pipeline_version": "test-v1",
        "state_version": 4,
        "source_snapshot": {},
        "workflow_state": {},
        "settings": {},
    }
    repository.investigations[investigation_id] = investigation
    report = build_report_data(
        {"investigation": investigation},
        preview=False,
        report_snapshot_id=report_snapshot_id,
        generated_at=datetime(2026, 7, 19, tzinfo=timezone.utc),
    )
    repository.report_rows[investigation_id] = {
        "id": report_snapshot_id,
        "investigation_id": investigation_id,
        "contract_version": "v1",
        "report_snapshot": report,
        "report_sha256": "a" * 64,
    }
    return investigation_id


def _context(
    *,
    code: str,
    mode: str,
    data: Mapping[str, Any],
    repository: LabRepository,
    settings: Settings,
    investigation_id: str | None = None,
    claim_investigation_id: str | None = None,
) -> JobContext:
    request: dict[str, Any] = {
        "module_code": code,
        "input_mode": mode,
        "input": dict(data),
    }
    if investigation_id is not None:
        request["investigation_id"] = investigation_id
    if claim_investigation_id is not None:
        request["claim_investigation_id"] = claim_investigation_id
    return JobContext(
        job={
            "id": uuid.uuid4(),
            "payload": {"investigation_id": investigation_id},
        },
        module_run={
            "id": uuid.uuid4(),
            "investigation_id": investigation_id,
            "claim_investigation_id": claim_investigation_id,
            "module_code": code,
            "input_snapshot": {"request": request},
        },
        repository=repository,
        settings=settings,
        worker_id="lab-mode-test",
        _heartbeat=lambda: {"ok": True},
    )


def _document(*, source_type: str = "patent") -> dict[str, Any]:
    return {
        "source_type": source_type,
        "external_id": "manual-D1",
        "title": "optical sensor filter",
        "source_url": "manual://manual-D1",
        "publication_number": "US20180000001A1" if source_type == "patent" else None,
        "publication_date": "2018-01-02",
        "filing_date": "2017-01-02" if source_type == "patent" else None,
        "full_text": "optical sensor filter sealed housing",
        "public_date_evidence": "human supplied publication record",
    }


class FakeEngine:
    def plan_queries(self, **_kwargs: Any):
        return lab_module._fixture_query_plan().model_copy(
            update={"model": "glm-4.6v-test", "used_target_images": 99}
        )

    def generate_inventive_profile(self, **_kwargs: Any):
        return lab_module._fixture_inventive_profile().model_copy(
            update={
                "model": "glm-4.6v-test",
                "used_target_images": 99,
                # The real profile-only path always invokes the model live.
                "generation_source": "live_model",
            }
        )

    def recompile_initial_query_plan(self, **kwargs: Any):
        return lab_module._fixture_query_plan().model_copy(
            update={
                "model": "glm-4.6v-test",
                "used_target_images": 99,
                # The real deterministic recompile never calls the model.
                "generation_source": "same_source_cached_profile",
                "source_plan_run_id": kwargs.get("source_plan_run_id"),
                "source_sha256": kwargs.get("source_sha256"),
            }
        )

    def generate_queries_from_inventive_profile(self, **kwargs: Any):
        return lab_module._fixture_query_plan().model_copy(
            update={
                "model": "glm-4.6v-test",
                "used_target_images": 99,
                # The real module-4 path re-invokes GLM on the frozen profile.
                "generation_source": "module3_profile_live_model",
                "source_plan_run_id": kwargs.get("source_plan_run_id"),
                "source_sha256": kwargs.get("source_sha256"),
            }
        )

    def compare_single_reference(self, **kwargs: Any):
        return lab_module._fixture_comparison(str(kwargs["document_id"])).model_copy(
            update={
                "model": "glm-4.6v-test",
                "used_target_images": 99,
                "used_document_images": 99,
            }
        )

    def analyze_inventive_step(self, **_kwargs: Any):
        return lab_module._fixture_inventive_step().model_copy(
            update={
                "model": "glm-4.6v-test",
                "used_target_images": 99,
                "used_document_images": 99,
            }
        )

    def analyze_obviousness_precheck(self, **_kwargs: Any):
        return lab_module._fixture_obviousness_precheck().model_copy(
            update={
                "model": "glm-4.6v-test",
                "used_target_images": 99,
                "used_document_images": 99,
            }
        )


def _manual_input(code: str) -> dict[str, Any]:
    limitations = [item.model_dump(mode="json") for item in lab_module._fixture_limitations()]
    d1 = lab_module._fixture_comparison("D1").model_dump(mode="json")
    d2 = lab_module._fixture_comparison("D2").model_dump(mode="json")
    values: dict[str, dict[str, Any]] = {
        "I1_TARGET_SNAPSHOT": {
            "summary": {
                "patent_number": "CN209999999U",
                "title": "人工提供的目标专利摘要",
                "claim_count": 2,
            }
        },
        "I1_5_CLAIM_DATES": {
            "application_date": "2020-01-02",
            "priority_date": "2019-01-02",
            "priority_verified": True,
            "candidates": [_document()],
        },
        "I2_INVENTIVE_PROFILE": {
            "claim_id": "1",
            "expanded_claim_text": "一种光学检测装置",
            "target_images": ["target-1.png", "target-2.png"],
        },
        "I2_QUERY_PLAN": {
            "claim_id": "1",
            "expanded_claim_text": "一种光学检测装置",
            "target_images": ["target-1.png", "target-2.png"],
        },
        "I2_GAP_QUERY_PLAN": {
            "claim_id": "1",
            "expanded_claim_text": "一种光学检测装置",
            "target_images": ["target-1.png"],
            "patent_context": {
                "source_sha256": "a" * 64,
                "title": "光学检测装置",
                "claims": [
                    {
                        "claim_id": "1",
                        "claim_type": "INDEPENDENT",
                        "claim_text": "一种光学检测装置",
                        "expanded_claim_text": "一种光学检测装置",
                    }
                ],
            },
            "limitations": limitations,
            "comparisons": [d1],
            "date_qualifications": {"D1": {"inventive_step_eligible": True}},
            "d1_document_id": "D1",
            "reuse_keys": {},
            "gap_search_iteration": 1,
            "obviousness_precheck": (
                lab_module._fixture_obviousness_precheck().model_dump(mode="json")
            ),
        },
        "I3_PATENT_SEARCH": {
            "query": "optical sensor filter",
            "documents": [_document()],
        },
        "I3_NPL_SEARCH": {
            "query": "optical sensor filter",
            "documents": [_document(source_type="preprint")],
        },
        "I3_CANDIDATE_FILTER": {
            "documents": [_document()],
            "query_text": "optical sensor filter",
            "provider_kind": "patent",
            "query_plan": lab_module._fixture_query_plan().model_dump(mode="json"),
            "target_images": ["target-1.png"],
        },
        "I3_FETCH": {"document": _document()},
        "I3_QUALIFY": {
            "document": _document(),
            "critical_date": "2020-01-02",
            "content_relevance_verified": True,
            "source_chain_verified": True,
            "public_date_verified": True,
            "public_date_evidence": "human supplied publication record",
        },
        "I4_S_SINGLE_REFERENCE": {
            "claim_id": "1",
            "expanded_claim_text": "一种光学检测装置",
            "target_patent_text": (
                "目标专利说明书全文：光源、密闭检测腔、滤光件与传感器依次连接，"
                "滤光件在光路中抑制杂散光。"
            ),
            "limitations": limitations,
            "document_id": "D1",
            "document_text": _document()["full_text"],
            "target_images": ["target-1.png", "target-2.png"],
            "document_images": ["doc-1.png", "doc-2.png", "doc-3.png"],
        },
        "I4_C_CLOSEST_PRIOR_ART": {
            "limitations": limitations,
            "comparisons": [d1],
            "date_qualifications": {"D1": {"inventive_step_eligible": True}},
        },
        "I4_O_OBVIOUSNESS_PRECHECK": {
            "limitations": limitations,
            "closest_document_id": "D1",
            "comparisons": [d1],
            "expanded_claim_text": "一种光学检测装置",
            "document_text": _document()["full_text"],
            "target_images": ["target-1.png", "target-2.png"],
            "document_images": ["doc-1.png", "doc-2.png"],
        },
        "I4_I_INVENTIVE_STEP": {
            "limitations": limitations,
            "closest_document_id": "D1",
            "comparisons": [d1, d2],
            "date_qualifications": {
                "D1": {"inventive_step_eligible": True},
                "D2": {"inventive_step_eligible": True},
            },
            "expanded_claim_text": "一种光学检测装置",
            "target_images": ["target-1.png", "target-2.png"],
            "document_texts": {
                "D1": "document one text",
                "D2": "document two text",
            },
            "document_images": {
                "D1": ["d1-1.png"],
                "D2": ["d2-1.png", "d2-2.png"],
            },
        },
        "I5_REPORT": {},
    }
    return values[code]


def test_mode_matrix_covers_all_public_modules() -> None:
    assert set(LAB_MODE_MATRIX) == set(LAB_MODULE_CODES)
    assert all(set(row) == {"fixture", "manual", "live"} for row in LAB_MODE_MATRIX.values())


@pytest.mark.parametrize("code", LAB_MODULE_CODES)
def test_each_module_accepts_only_the_versioned_fixture(
    code: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class NoExternalDependency:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            raise AssertionError("fixture must not construct network/model clients")

    monkeypatch.setattr(lab_module, "GooglePatentsProvider", NoExternalDependency)
    monkeypatch.setattr(lab_module, "ArxivProvider", NoExternalDependency)
    monkeypatch.setattr(lab_module, "VisionLLMClient", NoExternalDependency)
    repository = LabRepository()
    settings = _settings(tmp_path)
    handler = build_module_lab_handlers(settings=settings, repository=repository)[code]

    output = handler(
        _context(
            code=code,
            mode="fixture",
            data={"fixture": "default"},
            repository=repository,
            settings=settings,
        )
    )
    assert output["requested_mode"] == output["effective_mode"] == "fixture"
    assert output["simulated"] is True
    assert output["human_supplied"] is False
    assert output["network_used"] is False
    assert output["actual_provider"] == "fixture"
    assert output["model"] == "fixture-no-model"
    assert output["fixture_id"] == DEFAULT_FIXTURE_ID
    assert output["output"]["simulated"] is True

    with pytest.raises(PermanentJobError) as raised:
        handler(
            _context(
                code=code,
                mode="fixture",
                data={"fixture": "default", "fixture_output": {}},
                repository=repository,
                settings=settings,
            )
        )
    assert raised.value.error_code == "LAB_FIXTURE_INPUT_REJECTED"


def test_fixture_image_counts_are_zero_without_real_images(tmp_path: Path) -> None:
    repository = LabRepository()
    settings = _settings(tmp_path)
    handlers = build_module_lab_handlers(settings=settings, repository=repository)
    for code in (
        "I2_QUERY_PLAN",
        "I4_S_SINGLE_REFERENCE",
        "I4_O_OBVIOUSNESS_PRECHECK",
        "I4_I_INVENTIVE_STEP",
    ):
        output = handlers[code](
            _context(
                code=code,
                mode="fixture",
                data={"fixture": "default"},
                repository=repository,
                settings=settings,
            )
        )["output"]
        assert output["used_target_images"] == 0
        if code != "I2_QUERY_PLAN":
            assert output["used_document_images"] == 0


@pytest.mark.parametrize("code", LAB_MODULE_CODES)
def test_each_live_module_requires_an_explicit_persisted_investigation(
    code: str, tmp_path: Path
) -> None:
    repository = LabRepository()
    settings = _settings(tmp_path)
    handler = build_module_lab_handlers(settings=settings, repository=repository)[code]
    with pytest.raises(PermanentJobError) as raised:
        handler(
            _context(
                code=code,
                mode="live",
                data={},
                repository=repository,
                settings=settings,
            )
        )
    assert raised.value.error_code == "LAB_LIVE_INVESTIGATION_REQUIRED"


def test_live_target_snapshot_freezes_only_the_persisted_investigation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = LabRepository()
    investigation_id = _live_investigation(repository)
    settings = _settings(tmp_path)
    calls: list[dict[str, Any]] = []

    def fake_ensure_patent_snapshot(**kwargs: Any) -> dict[str, Any]:
        calls.append(dict(kwargs))
        return {
            "snapshot_state": "patent_snapshot_frozen",
            "target_images": [
                str(tmp_path / "invalidity" / "test" / "target-1.png"),
                str(tmp_path / "invalidity" / "test" / "target-2.png"),
            ],
            "patent_snapshot": {
                "source_sha256": "a" * 64,
                "source_uri": str(tmp_path / "uploaded-target.pdf"),
                "patent_number": "CN222356540U",
                "title": "一种开放式头戴耳机",
                "application_date": "2024-04-29",
                "publication_date": "2025-01-14",
                "claims": [
                    {
                        "claim_id": "1",
                        "claim_type": "INDEPENDENT",
                        "claim_text": "一种开放式头戴耳机。",
                        "expanded_claim_text": "一种开放式头戴耳机。",
                    },
                    {
                        "claim_id": "2",
                        "claim_type": "DEPENDENT",
                        "claim_text": "根据权利要求1所述的耳机。",
                        "parent_claim_ids": ["1"],
                        "expanded_claim_text": (
                            "一种开放式头戴耳机；根据权利要求1所述的耳机。"
                        ),
                    },
                ],
                "figures": [{"figure_id": "figure-1"}, {"figure_id": "figure-2"}],
            },
        }

    monkeypatch.setattr(
        lab_module,
        "ensure_patent_snapshot",
        fake_ensure_patent_snapshot,
    )
    output = build_module_lab_handlers(settings=settings, repository=repository)[
        "I1_TARGET_SNAPSHOT"
    ](
        _context(
            code="I1_TARGET_SNAPSHOT",
            mode="live",
            data={},
            repository=repository,
            settings=settings,
            investigation_id=investigation_id,
        )
    )

    assert calls == [
        {
            "settings": settings,
            "repository": repository,
            "investigation_id": investigation_id,
        }
    ]
    assert output["network_used"] is True
    assert output["actual_provider"] == "module1_parser"
    summary = output["output"]
    assert summary["snapshot_state"] == "patent_snapshot_frozen"
    assert summary["patent_number"] == "CN222356540U"
    assert summary["claim_count"] == 2
    assert summary["independent_claim_count"] == 1
    assert summary["first_independent_claim_id"] == "1"
    assert summary["independent_claims"] == [
        {
            "claim_id": "1",
            "claim_type": "INDEPENDENT",
            "claim_text": "一种开放式头戴耳机。",
            "expanded_claim_text": "一种开放式头戴耳机。",
            "parent_claim_ids": [],
            "dependency_uncertain": False,
            "sentence_units": [],
        }
    ]
    assert [item["claim_id"] for item in summary["claims"]] == ["1", "2"]
    assert summary["claims"][1]["claim_type"] == "DEPENDENT"
    assert summary["claims"][1]["parent_claim_ids"] == ["1"]
    assert [item["figure_id"] for item in summary["figure_overview"]] == [
        "figure-1",
        "figure-2",
    ]
    assert summary["figure_count"] == 2
    assert summary["target_image_count"] == 2


def test_persisted_patent_context_accepts_only_same_sha_i1_overlay() -> None:
    repository = LabRepository()
    investigation_id = _live_investigation(repository)
    frozen_sha = "a" * 64
    frozen_patent = {
        "source_sha256": frozen_sha,
        "source_uri": "fixture://target.pdf",
        "title": "冻结名称",
        "specification": {"全文": "冻结说明书全文"},
        "claims": [
            {
                "claim_id": "1",
                "claim_type": "INDEPENDENT",
                "claim_text": "一种测试装置。",
            }
        ],
    }
    investigation = repository.investigations[investigation_id]
    investigation["source_snapshot"] = {"patent_snapshot": frozen_patent}
    common = {
        "investigation_id": investigation_id,
        "module_code": "I1_TARGET_SNAPSHOT",
        "status": "succeeded",
        "effective_mode": "live",
    }
    repository.module_runs = [
        {
            **common,
            "id": "missing-sha",
            "output_snapshot": {
                "output": {
                    "title": "缺少 SHA 的错误覆盖",
                    "specification": {"全文": "错误说明书"},
                }
            },
        },
        {
            **common,
            "id": "wrong-sha",
            "output_snapshot": {
                "output": {
                    "source_sha256": "b" * 64,
                    "title": "不同来源的错误覆盖",
                    "specification": {"全文": "错误说明书"},
                }
            },
        },
        {
            **common,
            "id": "same-sha",
            "output_snapshot": {
                "output": {
                    "source_sha256": frozen_sha,
                    "title": "同源更新名称",
                    "specification": {"全文": "同源更新说明书全文"},
                }
            },
        },
    ]
    request = lab_module.LabRequest(
        requested_mode="live",
        effective_mode="live",
        data={},
        source={},
        rule=lab_module.LAB_MODE_MATRIX["I4_S_SINGLE_REFERENCE"]["live"],
        investigation_id=investigation_id,
        investigation=investigation,
        repository=repository,
    )

    context = lab_module._persisted_patent_context(request)

    assert context["title"] == "同源更新名称"
    assert context["specification"]["全文"] == "同源更新说明书全文"
    assert context["lab_source_overlay_run_id"] == "same-sha"


def test_live_lab_lineage_hydrates_i2_i3_i4_without_mutating_checkpoint(
    tmp_path: Path,
) -> None:
    repository = LabRepository()
    investigation_id = str(uuid.uuid4())
    canonical_claim = {
        "claim_id": "1",
        "claim_investigation_id": "claim-1",
        "limitations": [],
        "documents": {},
        "date_qualifications": {},
        "comparisons": {},
    }
    repository.investigations[investigation_id] = {
        "id": investigation_id,
        "source_snapshot": {},
        "settings": {},
        "workflow_state": {
            "invalidity_workflow": {"claims": {"1": canonical_claim}}
        },
    }
    common = {
        "investigation_id": investigation_id,
        "claim_investigation_id": "claim-1",
        "status": "succeeded",
        "effective_mode": "live",
    }
    fixture_comparison_payload = lab_module._fixture_comparison(
        "D1"
    ).model_copy(
        update={"analysis_rule_version": I4S_RULE_VERSION}
    ).model_dump(mode="json")
    fixture_comparison_payload["disclosures"][0]["feature_text"] = (
        "光学传感器设置于密闭壳体内"
    )
    fixture_comparison_payload["disclosures"][1]["feature_text"] = (
        "滤光件位于光源与传感器之间"
    )
    wrong_target_payload = lab_module._fixture_comparison("D3").model_copy(
        update={"analysis_rule_version": I4S_RULE_VERSION}
    ).model_dump(mode="json")
    wrong_target_payload["disclosures"][0]["feature_text"] = "上一目标的第三项特征"
    wrong_target_payload["disclosures"][1]["feature_text"] = "上一目标的第四项特征"
    repository.module_runs = [
        {
            **common,
            "id": "run-i4s",
            "module_code": "I4_S_SINGLE_REFERENCE",
            "prompt_version": I4S_PROMPT_VERSION,
            "rule_version": I4S_RULE_VERSION,
            "input_snapshot": {
                "request": {
                    "input": {"module6_batch_id": "batch-current"}
                }
            },
            "output_snapshot": {
                "output": fixture_comparison_payload
            },
        },
        {
            **common,
            "id": "run-i4s-other-module6-batch",
            "module_code": "I4_S_SINGLE_REFERENCE",
            "prompt_version": I4S_PROMPT_VERSION,
            "rule_version": I4S_RULE_VERSION,
            "input_snapshot": {
                "request": {"input": {"module6_batch_id": "batch-old"}}
            },
            "output_snapshot": {
                "output": lab_module._fixture_comparison("D2").model_copy(
                    update={"analysis_rule_version": I4S_RULE_VERSION}
                ).model_dump(mode="json")
            },
        },
        {
            **common,
            "id": "run-i4s-current-batch-wrong-target-features",
            "module_code": "I4_S_SINGLE_REFERENCE",
            "prompt_version": I4S_PROMPT_VERSION,
            "rule_version": I4S_RULE_VERSION,
            "input_snapshot": {
                "request": {"input": {"module6_batch_id": "batch-current"}}
            },
            "output_snapshot": {"output": wrong_target_payload},
        },
        {
            **common,
            "id": "run-i4s-legacy",
            "module_code": "I4_S_SINGLE_REFERENCE",
            # A succeeded row without the current prompt/rule binding must not
            # be mixed into the current structural-v7 module-lab lineage.
            "output_snapshot": {
                "output": lab_module._fixture_comparison("D2").model_dump(
                    mode="json"
                )
            },
        },
        {
            **common,
            "id": "run-qualify",
            "module_code": "I3_QUALIFY",
            "output_snapshot": {
                "output": {
                    **_document(),
                    "provider": "google_patents",
                    "external_id": "D1",
                    "eligibility": {
                        "novelty_eligible": True,
                        "inventive_step_eligible": True,
                    },
                }
            },
        },
        {
            **common,
            "id": "run-fetch",
            "module_code": "I3_FETCH",
            "output_snapshot": {
                "output": {
                    **_document(),
                    "provider": "google_patents",
                    "external_id": "D1",
                    "stage": "retrieved_document",
                }
            },
        },
        {
            **common,
            "id": "run-i2",
            "module_code": "I2_QUERY_PLAN",
            "output_snapshot": {
                "output": {
                    "claim_id": "1",
                    "limitations": [
                        item.model_dump(mode="json")
                        for item in lab_module._fixture_limitations()
                    ],
                }
            },
        },
    ]
    settings = _settings(tmp_path)
    request = lab_module._request(
        _context(
            code="I4_C_CLOSEST_PRIOR_ART",
            mode="live",
            data={"module6_batch_id": "batch-current"},
            repository=repository,
            settings=settings,
            investigation_id=investigation_id,
        )
    )

    overlaid = lab_module._workflow_claim(request)

    assert len(overlaid["limitations"]) == 2
    assert overlaid["documents"]["D1"]["record"]["provider"] == "google_patents"
    assert overlaid["date_qualifications"]["D1"]["inventive_step_eligible"] is True
    assert {
        key: value
        for key, value in overlaid["comparisons"]["D1"].items()
        if not key.startswith("_")
    } == fixture_comparison_payload
    assert overlaid["comparisons"]["D1"]["_i4s_module_run_id"] == "run-i4s"
    assert overlaid["comparisons"]["D1"]["_module6_batch_id"] == "batch-current"
    assert "D2" not in overlaid["comparisons"]
    assert "D3" not in overlaid["comparisons"]
    assert overlaid["module6_batch_id"] == "batch-current"
    assert overlaid["rejected_lab_comparisons"] == [
        {
            "document_id": "D3",
            "module_run_id": "run-i4s-current-batch-wrong-target-features",
            "reason": "技术特征 F1 的实质内容与当前权利要求不一致",
        }
    ]
    assert canonical_claim["limitations"] == []
    assert canonical_claim["documents"] == {}


def test_live_closest_prior_art_requires_explicit_current_module6_batch(
    tmp_path: Path,
) -> None:
    repository = LabRepository()
    investigation_id = _live_investigation(repository)
    settings = _settings(tmp_path)
    handler = build_module_lab_handlers(
        settings=settings,
        repository=repository,
    )["I4_C_CLOSEST_PRIOR_ART"]

    with pytest.raises(PermanentJobError) as raised:
        handler(
            _context(
                code="I4_C_CLOSEST_PRIOR_ART",
                mode="live",
                data={},
                repository=repository,
                settings=settings,
                investigation_id=investigation_id,
                claim_investigation_id="claim-1",
            )
        )

    assert raised.value.error_code == "LAB_MODULE6_BATCH_REQUIRED"
    assert "禁止读取历史工作流或其他批次" in raised.value.public_message


def test_module9_reuse_keys_only_accept_exact_current_batch_and_real_versions() -> None:
    current = lab_module._fixture_comparison("D-current")
    missing_version = lab_module._fixture_comparison("D-missing")
    historical = lab_module._fixture_comparison("D-history")
    claim = {
        "documents": {
            "D-current": {
                "repository_document_version_id": "version-current",
                "record": {"content_sha256": "a" * 64},
            },
            "D-missing": {
                "repository_document_version_id": None,
                "record": {"content_sha256": "b" * 64},
            },
            "D-history": {
                "repository_document_version_id": "version-history",
                "record": {"content_sha256": "c" * 64},
            },
        },
        "date_qualifications": {
            "D-current": {"repository_qualification_id": "qualification-current"},
            "D-missing": {"repository_qualification_id": None},
            "D-history": {"repository_qualification_id": "qualification-history"},
        },
        "comparisons": {
            "D-current": {
                **current.model_dump(mode="json"),
                "_module6_batch_id": "batch-current",
                "_i4s_module_run_id": "run-current",
                "_input_binding": {
                    "document_version_id": "version-current",
                    "document_content_sha256": "a" * 64,
                    "limitation_set_sha256": "limitations-current",
                },
            },
            "D-missing": {
                **missing_version.model_dump(mode="json"),
                "_module6_batch_id": "batch-current",
                "_i4s_module_run_id": "run-missing",
                "_input_binding": {
                    "document_version_id": None,
                    "document_content_sha256": "b" * 64,
                    "limitation_set_sha256": "limitations-current",
                },
            },
            "D-history": {
                **historical.model_dump(mode="json"),
                "_module6_batch_id": "batch-history",
                "_i4s_module_run_id": "run-history",
                "_input_binding": {
                    "document_version_id": "version-history",
                    "document_content_sha256": "c" * 64,
                    "limitation_set_sha256": "limitations-history",
                },
            },
        },
    }

    keys = lab_module._current_module6_reuse_keys(
        claim,
        comparisons=[current, missing_version, historical],
        module6_batch_id="batch-current",
    )

    assert list(keys) == ["D-current"]
    assert keys["D-current"]["document_version_id"] == "version-current"
    assert "D-missing" not in keys
    assert "D-history" not in keys


def test_module9_exact_lineage_rejects_other_module6_batch(tmp_path: Path) -> None:
    repository = LabRepository()
    investigation_id = _live_investigation(repository)
    claim = {"claim_id": "1", "claim_investigation_id": "claim-1"}
    common = {
        "investigation_id": investigation_id,
        "claim_investigation_id": "claim-1",
        "module_code": "I4_C_CLOSEST_PRIOR_ART",
        "status": "succeeded",
        "effective_mode": "live",
        "output_snapshot": {"output": {"document_id": "D1"}},
    }
    repository.module_runs = [
        {
            **common,
            "id": "run-current",
            "input_snapshot": {
                "request": {"input": {"module6_batch_id": "batch-current"}}
            },
        },
        {
            **common,
            "id": "run-history",
            "input_snapshot": {
                "request": {"input": {"module6_batch_id": "batch-history"}}
            },
        },
    ]
    request = lab_module._request(
        _context(
            code="I2_GAP_QUERY_PLAN",
            mode="live",
            data={
                "module6_batch_id": "batch-current",
                "closest_prior_art_run_id": "run-history",
            },
            repository=repository,
            settings=_settings(tmp_path),
            investigation_id=investigation_id,
            claim_investigation_id="claim-1",
        )
    )

    with pytest.raises(PermanentJobError) as raised:
        lab_module._required_live_lineage_run(
            request,
            claim=claim,
            input_key="closest_prior_art_run_id",
            module_code="I4_C_CLOSEST_PRIOR_ART",
            public_label="模块9",
        )

    assert raised.value.error_code == "LAB_CURRENT_LINEAGE_MISMATCH"
    assert "模块6批次" in raised.value.public_message


def test_downstream_scope_accepts_only_current_module6_or_module9_i4s() -> None:
    def row(*, module6: str = "", module9: str = "") -> dict[str, Any]:
        return {
            "module_code": "I4_S_SINGLE_REFERENCE",
            "input_snapshot": {
                "request": {
                    "input": {
                        "module6_batch_id": module6,
                        "module9_batch_id": module9,
                    }
                }
            },
        }

    assert lab_module._run_matches_requested_lab_scope(
        row(module6="module6-current"),
        module6_batch_id="module6-current",
        module9_batch_id="module9-current",
    )
    assert lab_module._run_matches_requested_lab_scope(
        row(module9="module9-current"),
        module6_batch_id="module6-current",
        module9_batch_id="module9-current",
    )
    assert not lab_module._run_matches_requested_lab_scope(
        row(module6="module6-history", module9="module9-history"),
        module6_batch_id="module6-current",
        module9_batch_id="module9-current",
    )


def test_live_npl_search_uses_composite_sources_and_exposes_partial_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeComposite:
        provider_name = "composite_npl"

        def search(self, *_args: Any, **_kwargs: Any):
            return lab_module.ProviderSearchBatch(
                records=(
                    EvidenceRecord(
                        provider="openalex",
                        source_type="journal_article",
                        external_id="W123",
                        title="water gun valve study",
                        source_url="https://openalex.org/W123",
                    ),
                ),
                complete=False,
                providers_attempted=("arxiv", "openalex", "crossref", "web_search"),
                providers_succeeded=("openalex",),
                errors=("arxiv: temporary failure",),
            )

        def close(self) -> None:
            return None

    repository = LabRepository()
    investigation_id = _live_investigation(repository)
    settings = replace(
        _settings(tmp_path),
        npl_provider="arxiv_openalex_crossref_web",
    )
    monkeypatch.setattr(
        lab_module,
        "_live_npl_provider",
        lambda _settings: FakeComposite(),
    )

    output = build_module_lab_handlers(
        settings=settings,
        repository=repository,
    )["I3_NPL_SEARCH"](
        _context(
            code="I3_NPL_SEARCH",
            mode="live",
            data={"query": "water gun valve", "max_results": 10},
            repository=repository,
            settings=settings,
            investigation_id=investigation_id,
        )
    )["output"]

    assert output["query_text"] == "water gun valve"
    assert output["count"] == 1
    assert output["complete"] is False
    assert output["providers_attempted"] == [
        "arxiv",
        "openalex",
        "crossref",
        "web_search",
    ]
    assert output["provider_errors"] == ["arxiv: temporary failure"]


def test_live_target_snapshot_rejects_client_parsing_input_before_module1(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = LabRepository()
    investigation_id = _live_investigation(repository)
    called = False

    def unexpected_snapshot(**_kwargs: Any) -> dict[str, Any]:
        nonlocal called
        called = True
        return {}

    monkeypatch.setattr(lab_module, "ensure_patent_snapshot", unexpected_snapshot)
    handler = build_module_lab_handlers(
        settings=_settings(tmp_path),
        repository=repository,
    )["I1_TARGET_SNAPSHOT"]
    with pytest.raises(PermanentJobError) as raised:
        handler(
            _context(
                code="I1_TARGET_SNAPSHOT",
                mode="live",
                data={"claim_id": "client-must-not-select"},
                repository=repository,
                settings=_settings(tmp_path),
                investigation_id=investigation_id,
            )
        )
    assert raised.value.error_code == "LAB_TARGET_SNAPSHOT_INPUT_MUST_BE_EMPTY"
    assert called is False


@pytest.mark.parametrize(
    "bad_input",
    [
        {"provider": "google_patents"},
        {"fixture_output": {}},
        {"documents": []},
        {"document": {"stage": "qualified_evidence"}},
        {"document": {"content_sha256": "a" * 64}},
        {"document": {"eligibility": {"novelty_eligible": True}}},
        {"public_date_verified": True},
        {"date_qualifications": {"D1": {"inventive_step_eligible": True}}},
    ],
)
def test_live_rejects_client_forged_truth_fields(
    bad_input: dict[str, Any], tmp_path: Path
) -> None:
    repository = LabRepository()
    settings = _settings(tmp_path)
    investigation_id = str(uuid.uuid4())
    repository.investigations[investigation_id] = {
        "id": investigation_id,
        "source_snapshot": {},
        "workflow_state": {},
        "settings": {},
    }
    handler = build_module_lab_handlers(settings=settings, repository=repository)[
        "I2_QUERY_PLAN"
    ]
    with pytest.raises(PermanentJobError) as raised:
        handler(
            _context(
                code="I2_QUERY_PLAN",
                mode="live",
                data=bad_input,
                repository=repository,
                settings=settings,
                investigation_id=investigation_id,
            )
        )
    assert raised.value.error_code == "LAB_LIVE_INPUT_FORGED"


def test_i3_patent_text_search_patsnap_override_is_lab_run_local(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = LabRepository()
    investigation_id = _live_investigation(repository)
    settings = replace(
        _settings(tmp_path),
        patent_provider="google_patents",
        patsnap_api_key=f"sk-{'x' * 32}",
    )
    patsnap = FakePatentSearchProvider("patsnap")
    google = FakePatentSearchProvider("google_patents")
    monkeypatch.setattr(lab_module, "PatsnapProvider", lambda **_kwargs: patsnap)
    monkeypatch.setattr(lab_module, "GooglePatentsProvider", lambda: google)
    handler = build_module_lab_handlers(settings=settings, repository=repository)[
        "I3_PATENT_SEARCH"
    ]

    override_result = handler(
        _context(
            code="I3_PATENT_SEARCH",
            mode="live",
            data={
                "search_provider": "patsnap",
                "query": {
                    "text": "optical sensor filter",
                    "subject_terms": ["optical sensor"],
                    "feature_terms": ["filter"],
                },
                "max_results": 3,
            },
            repository=repository,
            settings=settings,
            investigation_id=investigation_id,
        )
    )
    assert override_result["actual_provider"] == "patsnap"
    assert override_result["network_used"] is True
    assert patsnap.calls[0][0] == "text"
    assert patsnap.calls[0][2]["max_results"] == 3
    assert override_result["output"]["search_artifacts"][0]["kind"] == (
        "provider_search_response"
    )
    assert "search_modality" not in override_result["output"]

    default_result = handler(
        _context(
            code="I3_PATENT_SEARCH",
            mode="live",
            data={
                "query": {
                    "text": "sealed optical sensor",
                    "subject_terms": ["optical sensor"],
                    "feature_terms": ["sealed"],
                },
                "max_results": 2,
            },
            repository=repository,
            settings=settings,
            investigation_id=investigation_id,
        )
    )
    assert default_result["actual_provider"] == "google_patents"
    assert google.calls[0][0] == "text"
    assert settings.patent_provider == "google_patents"


def test_i3_patent_search_freezes_raw_response_before_local_target_exclusion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = LabRepository()
    investigation_id = _live_investigation(repository)
    settings = replace(
        _settings(tmp_path),
        patent_provider="patsnap",
        patsnap_api_key=f"sk-{'x' * 32}",
    )
    patsnap = FakePatentSearchProvider("patsnap")
    monkeypatch.setattr(lab_module, "PatsnapProvider", lambda **_kwargs: patsnap)
    handler = build_module_lab_handlers(settings=settings, repository=repository)[
        "I3_PATENT_SEARCH"
    ]

    result = handler(
        _context(
            code="I3_PATENT_SEARCH",
            mode="live",
            data={
                "search_provider": "patsnap",
                "query": {
                    "text": "(AN:(示例公司)) AND (TTL:(水枪) OR ABST:(水枪))",
                    "subject_terms": ["水枪"],
                    "feature_terms": [],
                },
                "excluded_publication": "CN305498776A",
                "max_results": 10,
            },
            repository=repository,
            settings=settings,
            investigation_id=investigation_id,
        )
    )

    output = result["output"]
    assert result["actual_provider"] == "patsnap"
    assert output["count"] == 0
    assert output["documents"] == []
    assert output["target_exclusion_applied"] is True
    assert output["excluded_publication"] == "CN305498776A"
    assert output["excluded_target_documents"] == [
        {
            "external_id": "patsnap-lead-1",
            "publication_number": "CN305498776S",
            "reason": "target_publication_excluded_locally",
        }
    ]
    assert output["search_artifacts"][0]["kind"] == "provider_search_response"
    assert patsnap.calls[0][0] == "text"
    assert "NOT PN" not in str(patsnap.calls[0][1]).upper()


def test_i3_patent_search_executes_exact_persisted_fixed_lane_without_old_guard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = LabRepository()
    investigation_id = _live_investigation(repository)
    claim_investigation_id = "claim-1"
    _persist_filtered_fetch_candidate(
        repository,
        investigation_id=investigation_id,
        document={"external_id": "seed", "publication_number": "CN100000001A"},
        claim_investigation_id=claim_investigation_id,
    )
    claim = repository.investigations[investigation_id]["workflow_state"][
        "invalidity_workflow"
    ]["claims"]["1"]
    claim["critical_date"] = "2020-01-02"
    plan_run_id = str(uuid.uuid4())
    provider_expression = "(AN:(示例公司)) AND (TTL:(水枪) OR ABST:(水枪))"
    repository.module_runs.append(
        {
            "id": plan_run_id,
            "investigation_id": investigation_id,
            "claim_investigation_id": claim_investigation_id,
            "module_code": "I2_QUERY_PLAN",
            "status": "succeeded",
            "effective_mode": "live",
            "input_snapshot": {
                "request": {
                    "claim_investigation_id": claim_investigation_id,
                    "input": {"claim_id": "1"},
                }
            },
            "output_snapshot": {
                "output": {
                    "queries": [
                        {
                            "query_id": "1-r1-q01",
                            "query_variant": "applicant_plus_object",
                            "provider_kind": "patent",
                            "date_channel": "ordinary_prior_art",
                            "search_objective": "full_claim_single_reference",
                            "provider_expression": provider_expression,
                            "excluded_publication": "CN305498776A",
                        }
                    ]
                }
            },
        }
    )
    settings = replace(
        _settings(tmp_path),
        patent_provider="patsnap",
        patsnap_api_key=f"sk-{'x' * 32}",
    )
    patsnap = FakePatentSearchProvider("patsnap")
    monkeypatch.setattr(lab_module, "PatsnapProvider", lambda **_kwargs: patsnap)
    handler = build_module_lab_handlers(settings=settings, repository=repository)[
        "I3_PATENT_SEARCH"
    ]

    result = handler(
        _context(
            code="I3_PATENT_SEARCH",
            mode="live",
            data={
                "search_provider": "patsnap",
                "query_plan_run_id": plan_run_id,
                "query_plan_query_id": "1-r1-q01",
                "query": {
                    "text": provider_expression,
                    "subject_terms": ["水枪"],
                    "feature_terms": [],
                },
                "server_before": "1999-01-01",
                "max_results": 10,
            },
            repository=repository,
            settings=settings,
            investigation_id=investigation_id,
            claim_investigation_id=claim_investigation_id,
        )
    )

    sent_query = patsnap.calls[0][1]
    assert sent_query.trusted_fixed_plan is True
    assert patsnap.calls[0][2]["subject_terms"] is None
    assert patsnap.calls[0][2]["feature_terms"] is None
    assert patsnap.calls[0][2]["server_before"] == "2020-01-02"
    assert result["output"]["frozen_plan_query_verified"] is True
    assert result["output"]["query_plan_run_id"] == plan_run_id
    assert result["output"]["query_plan_query_id"] == "1-r1-q01"


@pytest.mark.parametrize(
    ("date_channel", "expected_server_before", "expected_filing_before"),
    [
        ("ordinary_prior_art", "2020-01-02", None),
        ("cn_conflicting_application", None, "2020-01-02"),
    ],
)
def test_i3_patent_search_executes_exact_persisted_gap_lane(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    date_channel: str,
    expected_server_before: str | None,
    expected_filing_before: str | None,
) -> None:
    repository = LabRepository()
    investigation_id = _live_investigation(repository)
    claim_investigation_id = "claim-1"
    _persist_filtered_fetch_candidate(
        repository,
        investigation_id=investigation_id,
        document={"external_id": "seed", "publication_number": "CN100000001A"},
        claim_investigation_id=claim_investigation_id,
    )
    claim = repository.investigations[investigation_id]["workflow_state"][
        "invalidity_workflow"
    ]["claims"]["1"]
    claim["critical_date"] = "2020-01-02"
    plan_run_id = str(uuid.uuid4())
    provider_expression = "TACD:((喷水车 OR 玩具车) AND (吸水 OR 供水))"
    repository.module_runs.append(
        {
            "id": plan_run_id,
            "investigation_id": investigation_id,
            "claim_investigation_id": claim_investigation_id,
            "module_code": "I2_GAP_QUERY_PLAN",
            "status": "succeeded",
            "effective_mode": "live",
            "input_snapshot": {
                "request": {
                    "claim_investigation_id": claim_investigation_id,
                    "input": {"claim_id": "1"},
                }
            },
            "output_snapshot": {
                "output": {
                    "invention_search_profile": {"protected_subject": "喷水车"},
                    "queries": [
                        {
                            "query_id": "1-r2-q01",
                            "query_role": "gap_followup",
                            "query_variant": "gap_followup",
                            "provider_kind": "patent",
                            "date_channel": date_channel,
                            "search_objective": "gap_or_combination",
                            "provider_expression": provider_expression,
                        }
                    ],
                }
            },
        }
    )
    settings = replace(
        _settings(tmp_path),
        patent_provider="patsnap",
        patsnap_api_key=f"sk-{'x' * 32}",
    )
    patsnap = FakePatentSearchProvider("patsnap")
    monkeypatch.setattr(lab_module, "PatsnapProvider", lambda **_kwargs: patsnap)
    handler = build_module_lab_handlers(settings=settings, repository=repository)[
        "I3_PATENT_SEARCH"
    ]

    result = handler(
        _context(
            code="I3_PATENT_SEARCH",
            mode="live",
            data={
                "search_provider": "patsnap",
                "query_plan_run_id": plan_run_id,
                "query_plan_query_id": "1-r2-q01",
                "query": {"text": provider_expression},
                "server_before": "1999-01-01",
                "filing_before": "1999-01-01",
                "max_results": 10,
            },
            repository=repository,
            settings=settings,
            investigation_id=investigation_id,
            claim_investigation_id=claim_investigation_id,
        )
    )

    sent = patsnap.calls[0]
    assert sent[1].trusted_fixed_plan is True
    assert sent[2]["server_before"] == expected_server_before
    assert sent[2]["filing_before"] == expected_filing_before
    assert result["output"]["frozen_plan_query_verified"] is True
    assert result["output"]["query_plan_run_id"] == plan_run_id
    assert result["output"]["query_plan_query_id"] == "1-r2-q01"
    assert result["output"]["date_channel"] == date_channel


def test_i3_patent_search_rejects_fixed_lane_expression_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = LabRepository()
    investigation_id = _live_investigation(repository)
    claim_investigation_id = "claim-1"
    _persist_filtered_fetch_candidate(
        repository,
        investigation_id=investigation_id,
        document={"external_id": "seed", "publication_number": "CN100000001A"},
        claim_investigation_id=claim_investigation_id,
    )
    claim = repository.investigations[investigation_id]["workflow_state"][
        "invalidity_workflow"
    ]["claims"]["1"]
    claim["critical_date"] = "2020-01-02"
    plan_run_id = str(uuid.uuid4())
    repository.module_runs.append(
        {
            "id": plan_run_id,
            "investigation_id": investigation_id,
            "claim_investigation_id": claim_investigation_id,
            "module_code": "I2_QUERY_PLAN",
            "status": "succeeded",
            "effective_mode": "live",
            "input_snapshot": {
                "request": {"claim_investigation_id": claim_investigation_id}
            },
            "output_snapshot": {
                "output": {
                    "queries": [
                        {
                            "query_id": "1-r1-q01",
                            "query_variant": "applicant_plus_object",
                            "provider_kind": "patent",
                            "provider_expression": "(AN:(示例公司)) AND (TTL:(水枪))",
                        }
                    ]
                }
            },
        }
    )
    settings = replace(
        _settings(tmp_path),
        patent_provider="patsnap",
        patsnap_api_key=f"sk-{'x' * 32}",
    )
    patsnap = FakePatentSearchProvider("patsnap")
    monkeypatch.setattr(lab_module, "PatsnapProvider", lambda **_kwargs: patsnap)
    handler = build_module_lab_handlers(settings=settings, repository=repository)[
        "I3_PATENT_SEARCH"
    ]

    with pytest.raises(PermanentJobError) as raised:
        handler(
            _context(
                code="I3_PATENT_SEARCH",
                mode="live",
                data={
                    "search_provider": "patsnap",
                    "query_plan_run_id": plan_run_id,
                    "query_plan_query_id": "1-r1-q01",
                    "query": {"text": "水枪"},
                },
                repository=repository,
                settings=settings,
                investigation_id=investigation_id,
                claim_investigation_id=claim_investigation_id,
            )
        )

    assert raised.value.error_code == "LAB_FIXED_QUERY_EXPRESSION_MISMATCH"
    assert patsnap.calls == []


def test_i3_patent_content_failure_keeps_message_and_freezes_response_audit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = LabRepository()
    investigation_id = _live_investigation(repository)
    settings = replace(
        _settings(tmp_path),
        patent_provider="google_patents",
        patsnap_api_key=f"sk-{'x' * 32}",
    )
    raw_response = (
        b'{"status":true,"error_code":0,"data":'
        b'{"results":[],"total_search_result_count":1}}'
    )
    patsnap = FailingPatentSearchProvider(
        ProviderContentError("智慧芽 P002 返回数量与 results 不一致"),
        raw_response=raw_response,
    )
    monkeypatch.setattr(lab_module, "PatsnapProvider", lambda **_kwargs: patsnap)
    handler = build_module_lab_handlers(settings=settings, repository=repository)[
        "I3_PATENT_SEARCH"
    ]
    context = _context(
        code="I3_PATENT_SEARCH",
        mode="live",
        data={
            "search_provider": "patsnap",
            "query": {
                "text": "开放式头戴耳机 中空孔",
                "subject_terms": ["开放式头戴耳机"],
                "feature_terms": ["中空孔"],
            },
            "max_results": 10,
        },
        repository=repository,
        settings=settings,
        investigation_id=investigation_id,
    )

    with pytest.raises(PermanentJobError) as raised:
        handler(context)

    error = raised.value
    response_sha256 = hashlib.sha256(raw_response).hexdigest()
    assert error.error_code == "LAB_PROVIDER_CONTENT_INVALID"
    assert "智慧芽 P002 返回数量与 results 不一致" in error.public_message
    assert f"SHA-256={response_sha256}" in error.public_message
    assert len(patsnap.calls) == 1
    assert len(repository.artifacts) == 1
    artifact = repository.artifacts[0]
    assert artifact["investigation_id"] == investigation_id
    assert artifact["module_run_id"] == context.module_run["id"]
    assert artifact["artifact_type"] == "provider_search_failure_response"
    assert artifact["sha256"] == response_sha256
    assert artifact["metadata"]["failure_response"] is True
    assert artifact["metadata"]["request_attempt_log"][0]["operation"] == "P002"
    assert Path(artifact["uri"]).read_bytes() == raw_response


def test_i3_patent_query_failure_keeps_specific_safe_message_without_network_audit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = LabRepository()
    investigation_id = _live_investigation(repository)
    settings = replace(
        _settings(tmp_path),
        patent_provider="google_patents",
        patsnap_api_key=f"sk-{'x' * 32}",
    )
    patsnap = FailingPatentSearchProvider(
        QueryValidationError("检索式缺少技术主题锚点")
    )
    monkeypatch.setattr(lab_module, "PatsnapProvider", lambda **_kwargs: patsnap)
    handler = build_module_lab_handlers(settings=settings, repository=repository)[
        "I3_PATENT_SEARCH"
    ]

    with pytest.raises(PermanentJobError) as raised:
        handler(
            _context(
                code="I3_PATENT_SEARCH",
                mode="live",
                data={
                    "search_provider": "patsnap",
                    "query": {
                        "text": "中空孔",
                        "subject_terms": ["开放式头戴耳机"],
                        "feature_terms": ["中空孔"],
                    },
                },
                repository=repository,
                settings=settings,
                investigation_id=investigation_id,
            )
        )

    assert raised.value.error_code == "LAB_QUERY_INVALID"
    assert raised.value.public_message == "检索式缺少技术主题锚点"
    assert len(patsnap.calls) == 1
    assert repository.artifacts == []


def test_i2_analysis_failure_keeps_specific_safe_guard_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = LabRepository()
    investigation_id = _live_investigation(repository)
    settings = _settings(tmp_path)

    def reject_plan(_settings: Settings, _context: JobContext) -> Mapping[str, Any]:
        raise lab_module.AnalysisValidationError(
            "检索规划没有通过守门：紧凑查询只锚定一个技术特征"
        )

    monkeypatch.setattr(lab_module, "_query_plan_handler", reject_plan)
    handler = build_module_lab_handlers(settings=settings, repository=repository)[
        "I2_QUERY_PLAN"
    ]

    with pytest.raises(PermanentJobError) as raised:
        handler(
            _context(
                code="I2_QUERY_PLAN",
                mode="live",
                data={},
                repository=repository,
                settings=settings,
                investigation_id=investigation_id,
            )
        )

    assert raised.value.error_code == "LAB_ANALYSIS_INVALID"
    assert (
        raised.value.public_message
        == "检索规划没有通过守门：紧凑查询只锚定一个技术特征"
    )


@pytest.mark.parametrize(
    ("modality", "input_images", "expected_route", "expected_offset"),
    [
        (
            "image_single",
            {"image_url": "https://cdn.example.com/one.png"},
            "image-single/beta",
            0,
        ),
        (
            "image_multiple",
            {
                "image_urls": [
                    "https://cdn.example.com/one.png",
                    "https://cdn.example.com/two.png",
                    "https://cdn.example.com/three.png",
                ],
                "offset": 4,
            },
            "image-multiple",
            4,
        ),
    ],
)
def test_i3_patent_image_search_dispatches_and_freezes_real_artifact_kind(
    modality: str,
    input_images: dict[str, Any],
    expected_route: str,
    expected_offset: int,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = LabRepository()
    investigation_id = _live_investigation(repository)
    settings = replace(
        _settings(tmp_path),
        patent_provider="google_patents",
        patsnap_api_key=f"sk-{'x' * 32}",
    )
    patsnap = FakePatentSearchProvider("patsnap")
    monkeypatch.setattr(lab_module, "PatsnapProvider", lambda **_kwargs: patsnap)
    handler = build_module_lab_handlers(settings=settings, repository=repository)[
        "I3_PATENT_SEARCH"
    ]

    result = handler(
        _context(
            code="I3_PATENT_SEARCH",
            mode="live",
            data={
                "search_modality": modality,
                "search_provider": "patsnap",
                **input_images,
                "patent_type": "D",
                "model": 1,
                "max_results": 7,
            },
            repository=repository,
            settings=settings,
            investigation_id=investigation_id,
        )
    )

    assert result["actual_provider"] == "patsnap"
    assert result["network_used"] is True
    assert result["output"]["search_modality"] == modality
    assert result["output"]["input_image_count"] == (
        1 if modality == "image_single" else len(input_images["image_urls"])
    )
    assert result["output"]["count"] == 1
    document = result["output"]["documents"][0]
    assert document["stage"] == EvidenceStage.LEAD.value
    assert document["provider"] == "patsnap"

    method, passed_images, passed_options = patsnap.calls[0]
    assert method == modality
    if modality == "image_single":
        assert passed_images == input_images["image_url"]
    else:
        assert passed_images == input_images["image_urls"]
    assert passed_options == {
        "patent_type": "D",
        "model": 1,
        "max_results": 7,
        "offset": expected_offset,
    }

    search_artifact = result["output"]["search_artifacts"][0]
    assert search_artifact["artifact_type"] == "provider_image_search_response"
    assert search_artifact["kind"] == "provider_image_search_response"
    assert search_artifact["source_url"].endswith(expected_route)
    frozen = document["artifacts"][0]
    assert frozen["artifact_type"] == "provider_image_search_response"
    assert frozen["kind"] == "provider_image_search_response"
    assert frozen["source_url"].endswith(expected_route)
    assert Path(frozen["uri"]).is_file()


def test_i3_patent_image_search_requires_live_and_explicit_patsnap_override(
    tmp_path: Path,
) -> None:
    repository = LabRepository()
    settings = replace(
        _settings(tmp_path),
        patent_provider="patsnap",
        patsnap_api_key=f"sk-{'x' * 32}",
    )
    handler = build_module_lab_handlers(settings=settings, repository=repository)[
        "I3_PATENT_SEARCH"
    ]
    image_input = {
        "search_modality": "image_single",
        "image_url": "https://cdn.example.com/one.png",
        "patent_type": "D",
        "model": 1,
        "max_results": 7,
    }

    with pytest.raises(PermanentJobError) as manual_error:
        handler(
            _context(
                code="I3_PATENT_SEARCH",
                mode="manual",
                data=image_input,
                repository=repository,
                settings=settings,
            )
        )
    assert manual_error.value.error_code == "LAB_IMAGE_SEARCH_LIVE_ONLY"

    with pytest.raises(PermanentJobError) as fixture_error:
        handler(
            _context(
                code="I3_PATENT_SEARCH",
                mode="fixture",
                data={"fixture": "default", **image_input},
                repository=repository,
                settings=settings,
            )
        )
    assert fixture_error.value.error_code == "LAB_FIXTURE_INPUT_REJECTED"

    investigation_id = _live_investigation(repository)
    with pytest.raises(PermanentJobError) as live_error:
        handler(
            _context(
                code="I3_PATENT_SEARCH",
                mode="live",
                data=image_input,
                repository=repository,
                settings=settings,
                investigation_id=investigation_id,
            )
        )
    assert live_error.value.error_code == "LAB_IMAGE_SEARCH_PATSNAP_REQUIRED"


def test_i3_patent_search_rejects_unknown_or_non_live_override(
    tmp_path: Path,
) -> None:
    repository = LabRepository()
    settings = _settings(tmp_path)
    handler = build_module_lab_handlers(settings=settings, repository=repository)[
        "I3_PATENT_SEARCH"
    ]
    query = {
        "text": "optical sensor filter",
        "subject_terms": ["optical sensor"],
        "feature_terms": ["filter"],
    }
    with pytest.raises(PermanentJobError) as manual_error:
        handler(
            _context(
                code="I3_PATENT_SEARCH",
                mode="manual",
                data={"search_provider": "patsnap", "query": query, "documents": []},
                repository=repository,
                settings=settings,
            )
        )
    assert manual_error.value.error_code == "LAB_SEARCH_PROVIDER_LIVE_ONLY"

    investigation_id = _live_investigation(repository)
    with pytest.raises(PermanentJobError) as live_error:
        handler(
            _context(
                code="I3_PATENT_SEARCH",
                mode="live",
                data={"search_provider": "epo_ops", "query": query},
                repository=repository,
                settings=settings,
                investigation_id=investigation_id,
            )
        )
    assert live_error.value.error_code == "LAB_SEARCH_PROVIDER_UNSUPPORTED"


@pytest.mark.parametrize(
    ("bad_input", "error_code"),
    [
        (
            {
                "search_modality": "image_single",
                "image_url": "/tmp/local-image.png",
                "patent_type": "D",
                "model": 1,
                "max_results": 7,
            },
            "LAB_IMAGE_SEARCH_URL_INVALID",
        ),
        (
            {
                "search_modality": "image_multiple",
                "image_urls": ["https://cdn.example.com/one.png"],
                "patent_type": "D",
                "model": 1,
                "max_results": 7,
            },
            "LAB_IMAGE_SEARCH_INPUT_INVALID",
        ),
        (
            {
                "search_modality": "image_single",
                "image_url": "https://cdn.example.com/one.png",
                "patent_type": "D",
                "model": 1,
            },
            "LAB_IMAGE_SEARCH_INPUT_INVALID",
        ),
        (
            {
                "search_modality": "image_single",
                "image_url": "https://cdn.example.com/one.png",
                "patent_type": "D",
                "model": 1.5,
                "max_results": 7,
            },
            "LAB_IMAGE_SEARCH_INPUT_INVALID",
        ),
        (
            {
                "search_modality": "image_single",
                "image_url": "https://cdn.example.com/one.png",
                "patent_type": "D",
                "model": "1",
                "max_results": 7,
            },
            "LAB_IMAGE_SEARCH_INPUT_INVALID",
        ),
        (
            {
                "search_modality": "image_single",
                "image_url": "https://cdn.example.com/one.png",
                "patent_type": "D",
                "model": 1,
                "max_results": 7.5,
            },
            "LAB_IMAGE_SEARCH_INPUT_INVALID",
        ),
        (
            {
                "search_modality": "image_single",
                "image_url": "https://cdn.example.com/one.png",
                "patent_type": "D",
                "model": 1,
                "max_results": "7",
            },
            "LAB_IMAGE_SEARCH_INPUT_INVALID",
        ),
        (
            {
                "search_modality": "image_single",
                "image_url": "https://cdn.example.com/one.png",
                "patent_type": "D",
                "model": 1,
                "max_results": 7,
                "offset": 1.5,
            },
            "LAB_IMAGE_SEARCH_INPUT_INVALID",
        ),
        (
            {
                "search_modality": "image_single",
                "image_url": "https://cdn.example.com/one.png",
                "patent_type": "D",
                "model": 1,
                "max_results": 7,
                "offset": "1",
            },
            "LAB_IMAGE_SEARCH_INPUT_INVALID",
        ),
    ],
)
def test_i3_patent_image_search_validates_explicit_remote_inputs_before_call(
    bad_input: dict[str, Any],
    error_code: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unexpected_provider(**_kwargs: Any) -> Any:
        raise AssertionError("invalid image input must not construct PatsnapProvider")

    monkeypatch.setattr(lab_module, "PatsnapProvider", unexpected_provider)
    repository = LabRepository()
    investigation_id = _live_investigation(repository)
    settings = replace(
        _settings(tmp_path),
        patent_provider="google_patents",
        patsnap_api_key=f"sk-{'x' * 32}",
    )
    handler = build_module_lab_handlers(settings=settings, repository=repository)[
        "I3_PATENT_SEARCH"
    ]
    with pytest.raises(PermanentJobError) as raised:
        handler(
            _context(
                code="I3_PATENT_SEARCH",
                mode="live",
                data={"search_provider": "patsnap", **bad_input},
                repository=repository,
                settings=settings,
                investigation_id=investigation_id,
            )
        )
    assert raised.value.error_code == error_code


@pytest.mark.parametrize("code", LAB_MODULE_CODES)
def test_manual_mode_marks_human_truth_and_actual_execution(
    code: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(lab_module, "_engine", lambda _settings: FakeEngine())
    repository = LabRepository()
    investigation_id = _persisted_report(repository) if code == "I5_REPORT" else None
    settings = _settings(tmp_path)
    output = build_module_lab_handlers(settings=settings, repository=repository)[code](
        _context(
            code=code,
            mode="manual",
            data=_manual_input(code),
            repository=repository,
            settings=settings,
            investigation_id=investigation_id,
        )
    )
    assert output["requested_mode"] == output["effective_mode"] == "manual"
    assert output["simulated"] is False
    assert output["human_supplied"] is True
    assert output["fixture_id"] is None
    assert output["output"]["human_supplied"] is True
    model_modules = {
        "I2_INVENTIVE_PROFILE",
        "I2_QUERY_PLAN",
        "I2_GAP_QUERY_PLAN",
        "I4_S_SINGLE_REFERENCE",
        "I4_O_OBVIOUSNESS_PRECHECK",
        "I4_I_INVENTIVE_STEP",
    }
    assert output["network_used"] is (code in model_modules)
    assert output["model"] == ("glm-4.6v-test" if code in model_modules else None)


def test_gap_plan_keeps_human_review_features_unresolved_and_searchable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(lab_module, "_engine", lambda _settings: FakeEngine())
    repository = LabRepository()
    settings = _settings(tmp_path)
    data = _manual_input("I2_GAP_QUERY_PLAN")
    data["gap_query_matrix_id"] = "matrix-test-001"
    data["gap_query_matrix_version"] = 3
    precheck = dict(data["obviousness_precheck"])
    precheck["feature_groups"] = [
        {
            **dict(group),
            "search_route": "human_review",
            "ordinary_structural_search_required": False,
        }
        for group in precheck["feature_groups"]
    ]
    precheck["search_targets"] = [
        {
            **dict(target),
            "route": "human_review",
            "target_gap_type": "human_review",
        }
        for target in precheck["search_targets"]
    ]
    precheck["ordinary_structural_search_required"] = False
    data["obviousness_precheck"] = precheck

    result = build_module_lab_handlers(
        settings=settings, repository=repository
    )["I2_GAP_QUERY_PLAN"](
        _context(
            code="I2_GAP_QUERY_PLAN",
            mode="manual",
            data=data,
            repository=repository,
            settings=settings,
        )
    )
    output = result["output"]
    decision = output["gap_reuse_decision"]

    assert result["network_used"] is True
    assert output["closest_document_id"] == "D1"
    assert output["gap_query_matrix_id"] == "matrix-test-001"
    assert output["gap_query_matrix_version"] == 3
    assert output["queries"]
    assert output["gap_search_strategy"] == "adjacent_object_direct_structure"
    assert output["gap_search_strategy_label"] == "相邻产品类别 + 直接结构特征"
    assert all(
        item["gap_search_strategy"] == "adjacent_object_direct_structure"
        for item in output["queries"]
    )
    assert output["allowed_gap_feature_ids"] == ["F2"]
    assert output["human_review_feature_ids"] == ["F2"]
    assert output["uncovered_difference_feature_ids"] == ["F2"]
    assert decision["uncovered_difference_feature_ids"] == ["F2"]
    assert decision["covered_difference_feature_ids"] == []
    assert decision["search_required"] is True
    assert decision["stop_reason"] == "search_remaining_differences"


def test_gap_plan_cursor_cannot_reopen_feature_outside_requested_subset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(lab_module, "_engine", lambda _settings: FakeEngine())
    repository = LabRepository()
    settings = _settings(tmp_path)
    data = _manual_input("I2_GAP_QUERY_PLAN")
    data["gap_feature_ids"] = ["F2"]
    data["comparisons"][0]["disclosures"][0]["status"] = "not_disclosed"
    precheck = dict(data["obviousness_precheck"])
    group_f2 = dict(precheck["feature_groups"][0])
    target_f2 = dict(precheck["search_targets"][0])
    precheck["feature_groups"] = [
        {
            **group_f2,
            "feature_group_id": "G00",
            "feature_ids": ["F1"],
            "feature_texts": ["sensor in sealed housing"],
        },
        group_f2,
    ]
    precheck["unresolved_feature_ids"] = ["F1", "F2"]
    precheck["search_targets"] = [
        {
            **target_f2,
            "feature_group_id": "G00",
            "feature_ids": ["F1"],
            "search_anchor": "sensor in sealed housing",
        },
        target_f2,
    ]
    data["obviousness_precheck"] = precheck

    result = build_module_lab_handlers(
        settings=settings, repository=repository
    )["I2_GAP_QUERY_PLAN"](
        _context(
            code="I2_GAP_QUERY_PLAN",
            mode="manual",
            data=data,
            repository=repository,
            settings=settings,
        )
    )
    output = result["output"]

    assert output["allowed_gap_feature_ids"] == ["F2"]
    assert output["uncovered_difference_feature_ids"] == ["F2"]
    assert output["gap_reuse_decision"]["uncovered_difference_feature_ids"] == [
        "F2"
    ]


def test_manual_multimodal_counts_override_untrusted_model_counts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(lab_module, "_engine", lambda _settings: FakeEngine())
    repository = LabRepository()
    settings = _settings(tmp_path)
    handlers = build_module_lab_handlers(settings=settings, repository=repository)
    expected = {
        "I2_INVENTIVE_PROFILE": (2, None),
        "I2_QUERY_PLAN": (2, None),
        "I4_S_SINGLE_REFERENCE": (1, 2),
        "I4_O_OBVIOUSNESS_PRECHECK": (2, 2),
        "I4_I_INVENTIVE_STEP": (2, 3),
    }
    for code, counts in expected.items():
        output = handlers[code](
            _context(
                code=code,
                mode="manual",
                data=_manual_input(code),
                repository=repository,
                settings=settings,
            )
        )["output"]
        assert output["used_target_images"] == counts[0]
        if counts[1] is not None:
            assert output["used_document_images"] == counts[1]
        assert output["model"].startswith("glm-4.6v")


def test_test_env_i2_rejects_same_source_cached_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """2026-08-02: test phase must regenerate the plan on every click."""

    class CachedProfileEngine:
        last_invocation_audit = None

        def plan_queries(self, **_kwargs: Any):
            return lab_module._fixture_query_plan().model_copy(
                update={"generation_source": "same_source_cached_profile"}
            )

    monkeypatch.setattr(
        lab_module, "_engine", lambda _settings: CachedProfileEngine()
    )
    repository = LabRepository()
    settings = _settings(tmp_path)
    handler = build_module_lab_handlers(settings=settings, repository=repository)[
        "I2_QUERY_PLAN"
    ]
    with pytest.raises(PermanentJobError) as raised:
        handler(
            _context(
                code="I2_QUERY_PLAN",
                mode="manual",
                data=_manual_input("I2_QUERY_PLAN"),
                repository=repository,
                settings=settings,
            )
        )
    assert raised.value.error_code == "LAB_I2_CACHED_PROFILE_FORBIDDEN"


def test_fixture_inventive_profile_returns_profile_without_queries(
    tmp_path: Path,
) -> None:
    repository = LabRepository()
    settings = _settings(tmp_path)
    handler = build_module_lab_handlers(settings=settings, repository=repository)[
        "I2_INVENTIVE_PROFILE"
    ]

    output = handler(
        _context(
            code="I2_INVENTIVE_PROFILE",
            mode="fixture",
            data={"fixture": "default"},
            repository=repository,
            settings=settings,
        )
    )

    assert output["simulated"] is True
    assert output["model"] == "fixture-no-model"
    payload = output["output"]
    assert payload["simulated"] is True
    assert payload["invention_search_profile"] is not None
    assert payload["invention_search_profile"]["invention_summary"]
    assert payload["invention_search_profile"]["inventive_point_features"]
    assert payload["limitations"]
    assert payload["queries"] == []
    assert payload["rejected_queries"] == []


def test_manual_inventive_profile_contract_matches_i2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(lab_module, "_engine", lambda _settings: FakeEngine())
    repository = LabRepository()
    settings = _settings(tmp_path)
    handler = build_module_lab_handlers(settings=settings, repository=repository)[
        "I2_INVENTIVE_PROFILE"
    ]

    output = handler(
        _context(
            code="I2_INVENTIVE_PROFILE",
            mode="manual",
            data=_manual_input("I2_INVENTIVE_PROFILE"),
            repository=repository,
            settings=settings,
        )
    )

    assert output["simulated"] is False
    assert output["human_supplied"] is True
    assert output["network_used"] is True
    assert output["model"] == "glm-4.6v-test"
    payload = output["output"]
    assert payload["invention_search_profile"] is not None
    assert payload["limitations"]
    assert payload["queries"] == []
    assert payload["rejected_queries"] == []
    assert payload["generation_source"] == "live_model"
    # Handler-trusted image counts override the model's untrusted counts.
    assert payload["used_target_images"] == 2


def test_manual_query_plan_can_regenerate_from_explicit_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Blind examples use the same module3-profile -> module4-model path as live."""

    engine = FakeEngine()
    monkeypatch.setattr(lab_module, "_engine", lambda _settings: engine)
    repository = LabRepository()
    settings = _settings(tmp_path)
    source_run_id = str(uuid.uuid4())
    profile = lab_module._fixture_inventive_profile().model_dump(mode="json")
    output = build_module_lab_handlers(settings=settings, repository=repository)[
        "I2_QUERY_PLAN"
    ](
        _context(
            code="I2_QUERY_PLAN",
            mode="manual",
            data={
                "inventive_profile_plan": profile,
                "source_plan_run_id": source_run_id,
                "source_sha256": "a" * 64,
                "max_queries": 5,
            },
            repository=repository,
            settings=settings,
        )
    )

    assert output["network_used"] is True
    assert output["actual_provider"] == "manual"
    assert output["output"]["generation_source"] == "module3_profile_live_model"
    assert output["output"]["source_plan_run_id"] == source_run_id


def test_test_env_inventive_profile_rejects_same_source_cached_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The profile module keeps the same fail-closed defence as I2."""

    class CachedProfileEngine:
        last_invocation_audit = None

        def generate_inventive_profile(self, **_kwargs: Any):
            return lab_module._fixture_inventive_profile().model_copy(
                update={"generation_source": "same_source_cached_profile"}
            )

    monkeypatch.setattr(
        lab_module, "_engine", lambda _settings: CachedProfileEngine()
    )
    repository = LabRepository()
    settings = _settings(tmp_path)
    handler = build_module_lab_handlers(settings=settings, repository=repository)[
        "I2_INVENTIVE_PROFILE"
    ]
    with pytest.raises(PermanentJobError) as raised:
        handler(
            _context(
                code="I2_INVENTIVE_PROFILE",
                mode="manual",
                data=_manual_input("I2_INVENTIVE_PROFILE"),
                repository=repository,
                settings=settings,
            )
        )
    assert raised.value.error_code == "LAB_I2_CACHED_PROFILE_FORBIDDEN"


def test_live_query_plan_regenerates_queries_from_latest_inventive_profile_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """2026-08-05: live 模块4 输入仍是模块3画像输出，但必须重新调用 GLM 生成检索词。"""

    class RecordingEngine(FakeEngine):
        def __init__(self) -> None:
            self.plan_queries_calls: list[dict[str, Any]] = []
            self.recompile_calls: list[dict[str, Any]] = []
            self.generate_from_profile_calls: list[dict[str, Any]] = []
            self.last_invocation_audit = None

        def plan_queries(self, **kwargs: Any):
            self.plan_queries_calls.append(kwargs)
            return super().plan_queries(**kwargs)

        def recompile_initial_query_plan(self, **kwargs: Any):
            self.recompile_calls.append(kwargs)
            return super().recompile_initial_query_plan(**kwargs)

        def generate_queries_from_inventive_profile(self, **kwargs: Any):
            self.generate_from_profile_calls.append(kwargs)
            self.last_invocation_audit = {
                "stage": "I2_QUERY_PLAN",
                "attempt_number": 1,
                "model": "glm-4.6v-test",
                "prompt_sha256": "a" * 64,
                "normalization": {
                    "schema_normalized": False,
                    "unwrapped_root": None,
                    "query_source": "queries.list",
                    "query_count": 6,
                },
                "attempt_count": 1,
                "successful_attempt": 1,
            }
            return super().generate_queries_from_inventive_profile(**kwargs)

    engine = RecordingEngine()
    monkeypatch.setattr(lab_module, "_engine", lambda _settings: engine)
    repository = LabRepository()
    settings = _settings(tmp_path)
    investigation_id, claim_investigation_id = _live_i2_lab_investigation(
        repository, tmp_path
    )
    profile_run_id = str(uuid.uuid4())
    profile_output = lab_module._fixture_inventive_profile().model_dump(mode="json")
    other_claim_profile_output = lab_module._fixture_inventive_profile().model_copy(
        update={"technical_subject": "另一权利要求的画像"}
    ).model_dump(mode="json")
    repository.module_runs.extend(
        [
            # 失败的画像运行不得作为模块4输入。
            {
                "id": str(uuid.uuid4()),
                "investigation_id": investigation_id,
                "claim_investigation_id": claim_investigation_id,
                "module_code": "I2_INVENTIVE_PROFILE",
                "status": "failed",
                "effective_mode": "live",
                "output_snapshot": {"output": profile_output},
            },
            # 其他独立权利要求的画像运行不得串案。
            {
                "id": str(uuid.uuid4()),
                "investigation_id": investigation_id,
                "claim_investigation_id": str(uuid.uuid4()),
                "module_code": "I2_INVENTIVE_PROFILE",
                "status": "succeeded",
                "effective_mode": "live",
                "output_snapshot": {"output": other_claim_profile_output},
            },
            {
                "id": profile_run_id,
                "investigation_id": investigation_id,
                "claim_investigation_id": claim_investigation_id,
                "module_code": "I2_INVENTIVE_PROFILE",
                "status": "succeeded",
                "effective_mode": "live",
                "output_snapshot": {"output": profile_output},
            },
        ]
    )

    output = build_module_lab_handlers(settings=settings, repository=repository)[
        "I2_QUERY_PLAN"
    ](
        _context(
            code="I2_QUERY_PLAN",
            mode="live",
            data={"claim_id": "1", "max_queries": 5},
            repository=repository,
            settings=settings,
            investigation_id=investigation_id,
            claim_investigation_id=claim_investigation_id,
        )
    )

    payload = output["output"]
    assert payload["queries"]
    assert payload["generation_source"] == "module3_profile_live_model"
    assert payload["source_plan_run_id"] == profile_run_id
    assert payload["model"] == "glm-4.6v-test"
    # GLM 被真实重新调用，审计经 _persist_lab_i2_model_response 持久化。
    assert output["network_used"] is True
    assert output["actual_provider"] is None
    audit = payload["model_response_audit"]
    assert audit is not None
    assert audit["attempt_count"] == 1
    assert audit["successful_attempt"] == 1
    # 新路径不得回退全量 I2 生成，也不得走免模型确定性重编译。
    assert engine.plan_queries_calls == []
    assert engine.recompile_calls == []
    assert len(engine.generate_from_profile_calls) == 1
    call = engine.generate_from_profile_calls[0]
    assert call["previous_plan"]["invention_search_profile"]
    assert call["previous_plan"]["limitations"]
    assert call["previous_plan"]["queries"] == []
    assert call["source_plan_run_id"] == profile_run_id
    assert call["source_sha256"] is None
    assert call["max_queries"] == 5


def test_live_query_plan_requires_successful_inventive_profile_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """没有同案同 claim 成功模块3运行时，模块4 明确失败而不是回退 GLM。"""

    monkeypatch.setattr(lab_module, "_engine", lambda _settings: FakeEngine())
    repository = LabRepository()
    settings = _settings(tmp_path)
    investigation_id, claim_investigation_id = _live_i2_lab_investigation(
        repository, tmp_path
    )
    handler = build_module_lab_handlers(settings=settings, repository=repository)[
        "I2_QUERY_PLAN"
    ]
    with pytest.raises(PermanentJobError) as raised:
        handler(
            _context(
                code="I2_QUERY_PLAN",
                mode="live",
                data={"claim_id": "1"},
                repository=repository,
                settings=settings,
                investigation_id=investigation_id,
                claim_investigation_id=claim_investigation_id,
            )
        )
    assert raised.value.error_code == "LAB_I2_PROFILE_REQUIRED"
    assert "模块3" in raised.value.public_message


@pytest.mark.parametrize(
    ("plan_module_code", "omit_profile"),
    [
        ("I2_QUERY_PLAN", False),
        ("I2_GAP_QUERY_PLAN", False),
        ("I2_GAP_QUERY_PLAN", True),
    ],
)
def test_live_candidate_filter_uses_persisted_searches_and_fails_open_guards(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    plan_module_code: str,
    omit_profile: bool,
) -> None:
    repository = LabRepository()
    settings = _settings(tmp_path)
    investigation_id = _live_investigation(repository)
    claim_investigation_id = str(uuid.uuid4())
    repository.investigations[investigation_id].update(
        {
            "source_snapshot": {
                "target_images": [str(tmp_path / "target.png")],
            },
            "workflow_state": {
                "invalidity_workflow": {
                    "claims": {
                        claim_investigation_id: {
                            "claim_id": "1",
                            "claim_investigation_id": claim_investigation_id,
                            "limitations": [],
                            "documents": {},
                            "comparisons": {},
                        }
                    }
                }
            },
        }
    )
    plan = lab_module._fixture_query_plan().model_dump(mode="json")
    plan["invention_search_profile"]["protected_subject"] = "water gun"
    plan["invention_search_profile"]["subject_synonyms_en"] = ["water pistol"]
    plan["invention_search_profile"]["subject_synonyms_zh"] = ["水枪"]
    plan["invention_search_profile"]["common_context_features"] = []
    plan["invention_search_profile"]["inventive_point_features"] = []
    plan["invention_search_profile"]["mechanism_model"] = None
    if omit_profile:
        plan["technical_subject"] = "water gun"
        plan["invention_search_profile"] = None
    plan_run_id = str(uuid.uuid4())
    search_run_id = str(uuid.uuid4())
    common_run = {
        "investigation_id": investigation_id,
        "claim_investigation_id": claim_investigation_id,
        "status": "succeeded",
        "effective_mode": "live",
    }
    repository.module_runs.extend(
        [
            {
                **common_run,
                "id": plan_run_id,
                "module_code": plan_module_code,
                "output_snapshot": {"output": plan},
            },
            {
                **common_run,
                "id": search_run_id,
                "module_code": "I3_PATENT_SEARCH",
                "input_snapshot": {
                    "request": {
                        "claim_investigation_id": claim_investigation_id,
                        "input": {"query": {"query_id": "water-gun-q1"}},
                    }
                },
                "output_snapshot": {
                    "output": {
                        "provider_kind": "patent",
                        "query_text": "water gun valve tank",
                        "documents": [
                            {
                                **_document(),
                                "provider": "patsnap",
                                "external_id": "WO2019112939A3",
                                "publication_number": "WO2019112939A3",
                                "title": "Refrigerator assembly",
                                "raw_metadata": {
                                    "application_number": "PCT/US2018/063576"
                                },
                            },
                            {
                                **_document(),
                                "provider": "patsnap",
                                "external_id": "WO2019112939A4",
                                "publication_number": "WO2019112939A4",
                                "title": "Refrigerator assembly",
                                "raw_metadata": {
                                    "application_number": "PCT/US2018/063576"
                                },
                            },
                            {
                                **_document(),
                                "provider": "patsnap",
                                "external_id": "WO2019112939A2",
                                "publication_number": "WO2019112939A2",
                                "title": "Refrigerator assembly",
                                "raw_metadata": {
                                    "application_number": "PCT/US2018/063576"
                                },
                            },
                            {
                                **_document(),
                                "provider": "patsnap",
                                "external_id": "US20060210222A1",
                                "publication_number": "US20060210222A1",
                                "title": "Connector device for coupling optical fibres",
                            },
                            {
                                **_document(),
                                "provider": "patsnap",
                                "external_id": "US20100051848A1",
                                "publication_number": "US20100051848A1",
                                "title": "Toy water gun system",
                            },
                        ],
                    }
                },
            },
        ]
    )

    def fake_model(
        _settings: Settings,
        *,
        target_context: Mapping[str, Any],
        candidates: Any,
        target_images: Any,
    ) -> tuple[dict[str, dict[str, str]], dict[str, Any]]:
        assert target_context["protected_subject"] == "water gun"
        assert list(target_images)
        decisions = {
            str(candidate["representative_candidate_id"]): {
                "status": "exclude_obvious_unrelated",
                "reason": "题名明确属于冰箱或光纤连接领域",
                "confidence": "high",
                "exclusion_checks": {
                    "same_or_related_object": False,
                    "shared_medium_or_energy": False,
                    "shared_component_role": False,
                    "shared_operation_or_control": False,
                    "analogous_mechanism_possible": False,
                },
            }
            for candidate in candidates
        }
        return decisions, {
            "prompt_sha256": "b" * 64,
            "model": "glm-4.6v-test",
            "candidate_count": len(decisions),
            "raw_response": "{}",
        }

    monkeypatch.setattr(lab_module, "_candidate_filter_model", fake_model)
    output = build_module_lab_handlers(
        settings=settings, repository=repository
    )["I3_CANDIDATE_FILTER"](
        _context(
            code="I3_CANDIDATE_FILTER",
            mode="live",
            data={
                "search_run_ids": [search_run_id],
                "query_plan_run_id": plan_run_id,
            },
            repository=repository,
            settings=settings,
            investigation_id=investigation_id,
            claim_investigation_id=claim_investigation_id,
        )
    )
    result = output["output"]
    assert result["raw_candidate_count"] == 5
    assert result["unique_group_count"] == 3
    assert result["duplicate_count"] == 2
    assert result["excluded_count"] == 2
    assert result["fetch_candidate_count"] == 1
    assert (
        result["fetch_candidates"][0]["document"]["publication_number"]
        == "US20100051848A1"
    )
    assert result["estimated_fetch_requests_saved"] == 4
    assert result["filter_contract_version"] == "i3-candidate-filter-v1"
    assert len(result["input_sha256"]) == 64
    assert len(result["decision_sha256"]) == 64
    assert len(result["audit_sha256"]) == 64
    assert result["model_prompt_sha256"] == "b" * 64
    assert result["candidate_groups"][0]["representative_selection_reason"]
    assert output["network_used"] is True
    assert output["actual_provider"] == "glm_semantic_candidate_filter"


def test_live_fetch_rejects_client_supplied_candidate_document(
    tmp_path: Path,
) -> None:
    repository = LabRepository()
    settings = _settings(tmp_path)
    investigation_id = _live_investigation(repository)
    repository.investigations[investigation_id]["workflow_state"] = {
        "invalidity_workflow": {
            "claims": {
                "1": {
                    "claim_id": "1",
                    "claim_investigation_id": "claim-1",
                }
            }
        }
    }
    handler = build_module_lab_handlers(
        settings=settings,
        repository=repository,
    )["I3_FETCH"]
    with pytest.raises(
        PermanentJobError,
        match="拒绝客户端伪造字段",
    ):
        handler(
            _context(
                code="I3_FETCH",
                mode="live",
                data={
                    "provider_kind": "patent",
                    "source_provider": "google_patents",
                    "document": {
                        "provider": "google_patents",
                        "external_id": "FORGED",
                        "source_url": "https://example.test/forged",
                    },
                },
                repository=repository,
                settings=settings,
                investigation_id=investigation_id,
                claim_investigation_id="claim-1",
            )
        )


def test_manual_evidence_chain_recomputes_server_truth_before_i4s(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(lab_module, "_engine", lambda _settings: FakeEngine())
    repository = LabRepository()
    settings = _settings(tmp_path)
    handlers = build_module_lab_handlers(settings=settings, repository=repository)
    source = _document()
    forged = {
        **source,
        "provider": "google_patents",
        "stage": "qualified_evidence",
        "content_sha256": "0" * 64,
        "retrieved_at": "2000-01-01T00:00:00Z",
        "artifacts": [
            {
                "uri": "/tmp/client-forged.pdf",
                "sha256": "0" * 64,
                "is_primary_source": True,
            }
        ],
        "eligibility": {
            "category": "ordinary_prior_art",
            "novelty_eligible": True,
            "inventive_step_eligible": True,
        },
    }

    fetched = handlers["I3_FETCH"](
        _context(
            code="I3_FETCH",
            mode="manual",
            data={"document": forged},
            repository=repository,
            settings=settings,
        )
    )["output"]
    expected_sha256 = hashlib.sha256(source["full_text"].encode("utf-8")).hexdigest()
    assert fetched["provider"] == "manual"
    assert fetched["stage"] == EvidenceStage.RETRIEVED_DOCUMENT.value
    assert fetched["content_sha256"] == expected_sha256
    assert fetched["content_sha256"] != forged["content_sha256"]
    assert fetched["retrieved_at"] != forged["retrieved_at"]
    assert fetched["eligibility"] is None
    primary = next(
        item for item in fetched["artifacts"] if item.get("is_primary_source")
    )
    primary_path = Path(primary["uri"]).resolve()
    assert primary_path.is_relative_to(settings.artifact_root.resolve())
    assert hashlib.sha256(primary_path.read_bytes()).hexdigest() == expected_sha256
    assert all(item.get("uri") != "/tmp/client-forged.pdf" for item in fetched["artifacts"])

    qualified = handlers["I3_QUALIFY"](
        _context(
            code="I3_QUALIFY",
            mode="manual",
            data={
                "document": {
                    **forged,
                    "source_path": str(primary_path),
                },
                "critical_date": "2020-01-02",
                "content_relevance_verified": True,
                "source_chain_verified": True,
                "public_date_verified": True,
                "public_date_evidence": "human supplied publication record",
            },
            repository=repository,
            settings=settings,
        )
    )["output"]
    assert qualified["provider"] == "manual"
    assert qualified["stage"] == EvidenceStage.RETRIEVED_DOCUMENT.value
    assert qualified["content_sha256"] == expected_sha256
    assert qualified["eligibility"]["category"] == "ordinary_prior_art"
    assert qualified["eligibility"]["novelty_eligible"] is True
    assert "technical_disclosure_not_verified" in qualified["qualification_issues"]
    assert any(
        item.get("is_primary_source") and Path(item["uri"]).is_file()
        for item in qualified["artifacts"]
    )

    i4_input = _manual_input("I4_S_SINGLE_REFERENCE")
    i4_input["document_text"] = qualified["full_text"]
    compared = handlers["I4_S_SINGLE_REFERENCE"](
        _context(
            code="I4_S_SINGLE_REFERENCE",
            mode="manual",
            data=i4_input,
            repository=repository,
            settings=settings,
        )
    )["output"]
    assert compared["model"] == "glm-4.6v-test"
    assert compared["used_target_images"] == min(1, len(i4_input["target_images"]))
    assert compared["used_document_images"] == min(
        2, len(i4_input["document_images"])
    )
    assert "stage" not in compared


def test_live_fetch_and_qualify_never_construct_manual_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class NoManualProvider:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            raise AssertionError("live mode constructed ManualProvider")

    class FakeGoogleProvider:
        def __enter__(self):
            return self

        def __exit__(self, *_args: Any) -> None:
            return None

        def retrieve(self, record):
            artifact = RetrievedArtifact(
                provider="google_patents",
                kind="html",
                source_url=record.source_url,
                media_type="text/html",
                content=b"<html>retrieved real provider document text</html>",
            )
            return replace(
                record,
                stage=EvidenceStage.RETRIEVED_DOCUMENT,
                content_sha256=artifact.content_sha256,
                retrieved_at=artifact.retrieved_at,
                full_text="retrieved real provider document text",
                primary_artifact=artifact,
            )

    monkeypatch.setattr(lab_module, "ManualProvider", NoManualProvider)
    monkeypatch.setattr(lab_module, "GooglePatentsProvider", FakeGoogleProvider)
    repository = LabRepository()
    settings = _settings(tmp_path)
    investigation_id = str(uuid.uuid4())
    persisted_record = {
        **_document(),
        "provider": "google_patents",
        "stage": "retrieved_document",
        "content_sha256": "c" * 64,
        "retrieved_at": "2026-07-19T00:00:00Z",
    }
    repository.investigations[investigation_id] = {
        "id": investigation_id,
        "source_snapshot": {},
        "settings": {},
        "workflow_state": {
            "invalidity_workflow": {
                "claims": {
                    "1": {
                        "claim_id": "1",
                        "claim_investigation_id": "claim-1",
                        "documents": {"D1": {"record": persisted_record}},
                        "date_qualifications": {
                            "D1": {
                                "novelty_eligible": True,
                                "inventive_step_eligible": True,
                            }
                        },
                    }
                }
            }
        },
    }
    handlers = build_module_lab_handlers(settings=settings, repository=repository)
    filtered_candidate = _persist_filtered_fetch_candidate(
        repository,
        investigation_id=investigation_id,
        document={
            "provider": "google_patents",
            "external_id": "US20180000001A1",
            "source_type": "patent",
            "title": "provider lead",
            "source_url": (
                "https://patents.google.com/patent/US20180000001A1/en"
            ),
        },
    )
    fetched = handlers["I3_FETCH"](
        _context(
            code="I3_FETCH",
            mode="live",
            data=filtered_candidate,
            repository=repository,
            settings=settings,
            investigation_id=investigation_id,
        )
    )
    assert fetched["actual_provider"] == "google_patents"
    assert fetched["network_used"] is True
    primary = next(
        item
        for item in fetched["output"]["artifacts"]
        if item.get("is_primary_source")
    )
    assert Path(primary["uri"]).is_file()
    qualified = handlers["I3_QUALIFY"](
        _context(
            code="I3_QUALIFY",
            mode="live",
            data={"document_id": "D1"},
            repository=repository,
            settings=settings,
            investigation_id=investigation_id,
        )
    )
    assert qualified["actual_provider"] == "google_patents"
    assert qualified["network_used"] is False
    assert qualified["output"]["eligibility"]["novelty_eligible"] is True


def test_live_patent_fetch_reuses_exact_verified_historical_text_without_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class ProviderMustNotRun:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            raise AssertionError("exact historical patent text should skip provider")

    repository = LabRepository()
    settings = _settings(tmp_path)
    cache_run_id, _source_path = _persist_reusable_patent_fetch(
        repository,
        settings,
    )
    investigation_id = _live_investigation(repository)
    current_external_id = "current-filter-candidate"
    filtered_candidate = _persist_filtered_fetch_candidate(
        repository,
        investigation_id=investigation_id,
        document={
            "provider": "google_patents",
            "external_id": current_external_id,
            "source_type": "patent",
            "title": "current candidate title",
            "source_url": (
                "https://patents.google.com/patent/US20180000001A1/en"
            ),
            "publication_number": "US-2018-0000001-A1",
        },
    )
    monkeypatch.setattr(lab_module, "GooglePatentsProvider", ProviderMustNotRun)

    fetched = build_module_lab_handlers(
        settings=settings,
        repository=repository,
    )["I3_FETCH"](
        _context(
            code="I3_FETCH",
            mode="live",
            data=filtered_candidate,
            repository=repository,
            settings=settings,
            investigation_id=investigation_id,
            claim_investigation_id="claim-1",
        )
    )

    output = fetched["output"]
    provenance = output["provenance"]
    assert fetched["network_used"] is False
    assert fetched["actual_provider"] == "patent_text_cache"
    assert output["external_id"] == current_external_id
    assert output["publication_number"] == "US20180000001A1"
    assert output["analysis_ready"] is True
    assert output["readable_document"]["schema_version"] == "readable-patent-v1"
    assert provenance["retrieval_cache_hit"] is True
    assert provenance["retrieval_cache_source_run_id"] == cache_run_id
    assert provenance["retrieval_cache_readable_reused"] is True
    assert provenance["current_candidate_external_id"] == current_external_id
    assert "disclosures" not in output
    assert output["eligibility"] is None


@pytest.mark.parametrize(
    ("cached_publication", "current_publication", "corrupt_cache"),
    [
        ("US20180000001A1", "US20180000001B2", False),
        ("US20180000001A1", "US20180000001A1", True),
    ],
)
def test_live_patent_fetch_does_not_reuse_other_kind_or_corrupt_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    cached_publication: str,
    current_publication: str,
    corrupt_cache: bool,
) -> None:
    class CountingProvider:
        def __init__(self) -> None:
            self.calls = 0

        def __enter__(self):
            return self

        def __exit__(self, *_args: Any) -> None:
            return None

        def retrieve(self, record: EvidenceRecord) -> EvidenceRecord:
            self.calls += 1
            content = ("fresh provider patent text " * 12).encode("utf-8")
            artifact = RetrievedArtifact(
                provider="google_patents",
                kind="text",
                source_url=record.source_url,
                media_type="text/plain",
                content=content,
            )
            return replace(
                record,
                stage=EvidenceStage.RETRIEVED_DOCUMENT,
                description=content.decode("utf-8"),
                full_text=content.decode("utf-8"),
                content_sha256=artifact.content_sha256,
                retrieved_at=artifact.retrieved_at,
                primary_artifact=artifact,
            )

        def close(self) -> None:
            return None

    repository = LabRepository()
    settings = _settings(tmp_path)
    _cache_run_id, source_path = _persist_reusable_patent_fetch(
        repository,
        settings,
        publication_number=cached_publication,
    )
    if corrupt_cache:
        source_path.write_bytes(b"corrupted cached bytes")
    investigation_id = _live_investigation(repository)
    filtered_candidate = _persist_filtered_fetch_candidate(
        repository,
        investigation_id=investigation_id,
        document={
            "provider": "google_patents",
            "external_id": "current-filter-candidate",
            "source_type": "patent",
            "title": "current candidate title",
            "source_url": (
                f"https://patents.google.com/patent/{current_publication}/en"
            ),
            "publication_number": current_publication,
        },
    )
    provider = CountingProvider()
    monkeypatch.setattr(
        lab_module,
        "GooglePatentsProvider",
        lambda: provider,
    )

    fetched = build_module_lab_handlers(
        settings=settings,
        repository=repository,
    )["I3_FETCH"](
        _context(
            code="I3_FETCH",
            mode="live",
            data=filtered_candidate,
            repository=repository,
            settings=settings,
            investigation_id=investigation_id,
            claim_investigation_id="claim-1",
        )
    )

    assert provider.calls == 1
    assert fetched["network_used"] is True
    assert fetched["actual_provider"] == "google_patents"
    assert not fetched["output"]["provenance"].get("retrieval_cache_hit")


def test_live_patent_fetch_rebuilds_stale_readable_view_from_cached_pdf(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class ProviderMustNotRun:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            raise AssertionError("verified cached PDF should skip provider")

    document = fitz.open()
    try:
        page = document.new_page()
        for index in range(14):
            page.insert_text(
                (72, 72 + index * 18),
                f"cached patent full description claim structure line {index}",
            )
        pdf_content = document.tobytes(garbage=4, deflate=True)
    finally:
        document.close()

    repository = LabRepository()
    settings = _settings(tmp_path)
    _persist_reusable_patent_fetch(
        repository,
        settings,
        source_content=pdf_content,
        mime_type="application/pdf",
        readable_version="readable-patent-v0",
    )
    investigation_id = _live_investigation(repository)
    filtered_candidate = _persist_filtered_fetch_candidate(
        repository,
        investigation_id=investigation_id,
        document={
            "provider": "google_patents",
            "external_id": "current-filter-candidate",
            "source_type": "patent",
            "title": "current candidate title",
            "source_url": (
                "https://patents.google.com/patent/US20180000001A1/en"
            ),
            "publication_number": "US20180000001A1",
        },
    )
    monkeypatch.setattr(lab_module, "GooglePatentsProvider", ProviderMustNotRun)

    fetched = build_module_lab_handlers(
        settings=settings,
        repository=repository,
    )["I3_FETCH"](
        _context(
            code="I3_FETCH",
            mode="live",
            data=filtered_candidate,
            repository=repository,
            settings=settings,
            investigation_id=investigation_id,
            claim_investigation_id="claim-1",
        )
    )

    output = fetched["output"]
    assert fetched["network_used"] is False
    assert fetched["actual_provider"] == "patent_text_cache"
    assert output["analysis_ready"] is True
    assert output["readable_document"]["schema_version"] == "readable-patent-v1"
    assert output["provenance"]["retrieval_cache_readable_reused"] is False
    assert output["provenance"]["retrieval_cache_hit"] is True
    primary = next(
        item for item in output["artifacts"] if item.get("is_primary_source")
    )
    assert Path(primary["uri"]).is_file()
    assert "historical-patent-cache" not in primary["uri"]


def test_live_fetch_routes_patsnap_candidate_to_epo_full_document(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class FakeEpoProvider:
        provider_name = "epo_ops"

        def __init__(self) -> None:
            self.received: list[EvidenceRecord] = []

        def __enter__(self):
            return self

        def __exit__(self, *_args: Any) -> None:
            return None

        def retrieve(self, record: EvidenceRecord) -> EvidenceRecord:
            self.received.append(record)
            document = fitz.open()
            try:
                page = document.new_page()
                page.insert_text((72, 72), "EPO official full document")
                content = document.tobytes(garbage=4, deflate=True)
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
                    "https://ops.epo.org/3.2/rest-services/published-data/"
                    "images/EP/3456789/A1/fullimage?Range=1-1"
                ),
                media_type="application/pdf",
                content=content,
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
                stage=EvidenceStage.RETRIEVED_DOCUMENT,
                publication_number="EP3456789A1",
                provenance={
                    **dict(record.provenance),
                    "retrieval_provider": "epo_ops",
                    "retrieval_identity": {
                        "requested_publication_number": "EP3456789A1",
                        "returned_publication_number": "EP3456789A1",
                        "match": "exact",
                    },
                },
                primary_artifact=artifact,
            )

    epo = FakeEpoProvider()
    monkeypatch.setattr(lab_module, "EpoOpsProvider", lambda **_kwargs: epo)
    settings = replace(
        _settings(tmp_path),
        patent_provider="patsnap",
        patsnap_api_key=f"sk-{'x' * 32}",
        epo_ops_consumer_key="fixture-epo-key",
        epo_ops_consumer_secret="fixture-epo-secret",
    )
    repository = LabRepository()
    investigation_id = str(uuid.uuid4())
    repository.investigations[investigation_id] = {
        "id": investigation_id,
        "source_snapshot": {},
        "settings": {},
        "workflow_state": {},
    }
    handlers = build_module_lab_handlers(
        settings=settings,
        repository=repository,
    )
    filtered_candidate = _persist_filtered_fetch_candidate(
        repository,
        investigation_id=investigation_id,
        document={
            "provider": "patsnap",
            "external_id": "718ead9c-4f3c-4674-8f5a-24e126827269",
            "source_type": "patent",
            "title": "Patsnap-discovered lead",
            "source_url": (
                "https://connect.zhihuiya.com/"
                "search/patent/query-search-patent/v2"
            ),
            "publication_number": "EP3456789A1",
        },
    )

    fetched = handlers["I3_FETCH"](
        _context(
            code="I3_FETCH",
            mode="live",
            data=filtered_candidate,
            repository=repository,
            settings=settings,
            investigation_id=investigation_id,
        )
    )

    assert fetched["actual_provider"] == "epo_ops"
    assert epo.received[0].provider == "patsnap"
    assert fetched["output"]["provider"] == "epo_ops"
    assert fetched["output"]["provenance"]["discovery_provider"] == "patsnap"
    assert fetched["output"]["provenance"]["retrieval_provider"] == "epo_ops"
    primary = next(
        item
        for item in fetched["output"]["artifacts"]
        if item.get("is_primary_source")
    )
    assert Path(primary["uri"]).read_bytes().lstrip().startswith(b"%PDF")
    assert any(
        item.get("kind") == "provider_retrieval_bundle"
        for item in fetched["output"]["artifacts"]
    )
    approved, _values, approved_provider = lab_module._approved_persisted_record(
        settings,
        {"record": fetched["output"]},
    )
    assert approved.provider == "epo_ops"
    assert approved_provider == "epo_ops"


def test_live_fetch_falls_back_to_p020_only_after_epo_no_coverage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    patent_id = "718ead9c-4f3c-4674-8f5a-24e126827269"
    publication_number = "EP3456789A1"

    class FakeEpoProvider:
        provider_name = "epo_ops"

        def __init__(self, **_kwargs: Any) -> None:
            self.last_retrieval_artifacts = (
                RetrievedArtifact(
                    provider="epo_ops",
                    kind="provider_biblio_response",
                    source_url="https://ops.epo.org/3.2/rest-services/published-data",
                    media_type="application/xml",
                    content=b"<exchange-document/>",
                ),
            )

        def retrieve(self, record: EvidenceRecord) -> EvidenceRecord:
            return replace(
                record,
                provider="epo_ops",
                publication_number=publication_number,
                provenance={
                    **dict(record.provenance),
                    "retrieval_provider": "epo_ops",
                    "retrieval_identity": {
                        "requested_publication_number": publication_number,
                        "returned_publication_number": publication_number,
                        "match": "exact",
                    },
                },
                qualification_issues=("ops_fulltext_not_available",),
            )

        def close(self) -> None:
            return None

    class FakePatsnapProvider:
        provider_name = "patsnap"

        def __init__(self, **_kwargs: Any) -> None:
            self.last_media_artifacts: tuple[RetrievedArtifact, ...] = ()

        @staticmethod
        def _coerce_lead(value):
            assert isinstance(value, EvidenceRecord)
            return value

        def download_pdf(self, _record: EvidenceRecord) -> RetrievedArtifact:
            document = fitz.open()
            try:
                page = document.new_page()
                page.insert_text((72, 72), "P020 exact document")
                content = document.tobytes(garbage=4, deflate=True)
            finally:
                document.close()
            metadata = RetrievedArtifact(
                provider="patsnap",
                kind="provider_pdf_metadata_response",
                source_url="https://connect.zhihuiya.com/basic-patent-data/pdf-data",
                media_type="application/json",
                content=b'{"status":true,"error_code":0}',
            )
            self.last_media_artifacts = (metadata,)
            return RetrievedArtifact(
                provider="patsnap",
                kind="pdf",
                source_url="https://open.zhihuiya.com/redacted.pdf",
                media_type="application/pdf",
                content=content,
                source_metadata_artifact=metadata,
            )

        def close(self) -> None:
            return None

    monkeypatch.setattr(lab_module, "EpoOpsProvider", FakeEpoProvider)
    monkeypatch.setattr(lab_module, "PatsnapProvider", FakePatsnapProvider)
    settings = replace(
        _settings(tmp_path),
        patent_provider="patsnap",
        patsnap_api_key=f"sk-{'x' * 32}",
        epo_ops_consumer_key="fixture-epo-key",
        epo_ops_consumer_secret="fixture-epo-secret",
    )
    repository = LabRepository()
    investigation_id = str(uuid.uuid4())
    repository.investigations[investigation_id] = {
        "id": investigation_id,
        "source_snapshot": {},
        "settings": {},
        "workflow_state": {},
    }
    handlers = build_module_lab_handlers(
        settings=settings,
        repository=repository,
    )
    filtered_candidate = _persist_filtered_fetch_candidate(
        repository,
        investigation_id=investigation_id,
        document={
            "provider": "patsnap",
            "external_id": patent_id,
            "source_type": "patent",
            "title": "Patsnap-discovered lead",
            "source_url": (
                "https://connect.zhihuiya.com/"
                "search/patent/query-search-patent/v2"
            ),
            "publication_number": publication_number,
            "raw_metadata": {"patent_id": patent_id},
        },
    )

    fetched = handlers["I3_FETCH"](
        _context(
            code="I3_FETCH",
            mode="live",
            data=filtered_candidate,
            repository=repository,
            settings=settings,
            investigation_id=investigation_id,
        )
    )

    assert fetched["actual_provider"] == "patsnap_p020"
    assert fetched["output"]["provider"] == "patsnap"
    assert fetched["output"]["stage"] == "retrieved_document"
    assert fetched["output"]["provenance"]["retrieval_provider"] == "patsnap_p020"
    assert [
        item["status"]
        for item in fetched["output"]["provenance"]["retrieval_attempts"]
    ] == ["no_coverage", "retrieved"]
    kinds = {
        item.get("kind")
        for item in fetched["output"]["artifacts"]
        if item.get("kind")
    }
    assert "provider_biblio_response" in kinds
    assert "provider_pdf_metadata_response" in kinds
    primary = next(
        item
        for item in fetched["output"]["artifacts"]
        if item.get("is_primary_source")
    )
    assert Path(primary["uri"]).read_bytes().startswith(b"%PDF")


@pytest.mark.parametrize("mode", ["manual", "live"])
def test_i5_manual_and_live_only_read_persisted_report_data_v1(
    mode: str, tmp_path: Path
) -> None:
    repository = LabRepository()
    investigation_id = _persisted_report(repository)
    settings = _settings(tmp_path)
    handler = build_module_lab_handlers(settings=settings, repository=repository)[
        "I5_REPORT"
    ]
    output = handler(
        _context(
            code="I5_REPORT",
            mode=mode,
            data={},
            repository=repository,
            settings=settings,
            investigation_id=investigation_id,
        )
    )
    assert ReportDataV1.model_validate(output["output"]["report_data"])
    assert output["output"]["is_preview"] is False
    assert output["actual_provider"] == "persistent_report_snapshot"


def test_i5_manual_refuses_to_synthesise_from_raw_report_tables(tmp_path: Path) -> None:
    repository = LabRepository()
    investigation_id = str(uuid.uuid4())
    investigation = {
        "id": investigation_id,
        "analysis_session_id": "raw-only",
        "environment": "test",
        "status": "completed",
        "pipeline_version": "test-v1",
        "state_version": 2,
    }
    repository.investigations[investigation_id] = investigation
    repository.raw_report_data = {"investigation": investigation}
    settings = _settings(tmp_path)
    handler = build_module_lab_handlers(settings=settings, repository=repository)[
        "I5_REPORT"
    ]
    with pytest.raises(PermanentJobError) as raised:
        handler(
            _context(
                code="I5_REPORT",
                mode="manual",
                data={},
                repository=repository,
                settings=settings,
                investigation_id=investigation_id,
            )
        )
    assert raised.value.error_code == "LAB_REPORT_SNAPSHOT_REQUIRED"


def test_guarded_marks_patsnap_permission_failure_permanent(tmp_path: Path) -> None:
    repository = LabRepository()
    settings = _settings(tmp_path)

    def rejected(_context: JobContext) -> Mapping[str, Any]:
        raise PatsnapApiError(
            "provider business error",
            error_code=67200004,
            retryable=False,
        )

    handler = lab_module._guarded(rejected)
    with pytest.raises(PermanentJobError) as raised:
        handler(
            _context(
                code="I3_PATENT_SEARCH",
                mode="fixture",
                data={},
                repository=repository,
                settings=settings,
            )
        )

    assert raised.value.error_code == "PATSNAP_PERMISSION_DENIED"
    assert "不会自动重试" in raised.value.public_message


def test_guarded_marks_patsnap_insufficient_balance_permanent(tmp_path: Path) -> None:
    repository = LabRepository()
    settings = _settings(tmp_path)

    def rejected(_context: JobContext) -> Mapping[str, Any]:
        raise PatsnapApiError(
            "provider business error",
            error_code=67200005,
            retryable=False,
        )

    handler = lab_module._guarded(rejected)
    with pytest.raises(PermanentJobError) as raised:
        handler(
            _context(
                code="I3_PATENT_SEARCH",
                mode="fixture",
                data={},
                repository=repository,
                settings=settings,
            )
        )

    assert raised.value.error_code == "PATSNAP_BALANCE_INSUFFICIENT"
    assert "不会记为零命中" in raised.value.public_message


def test_guarded_keeps_retryable_provider_failure_retryable(tmp_path: Path) -> None:
    repository = LabRepository()
    settings = _settings(tmp_path)

    def unavailable(_context: JobContext) -> Mapping[str, Any]:
        exc = ProviderRequestError("temporary provider outage")
        exc.retryable = True
        raise exc

    handler = lab_module._guarded(unavailable)
    with pytest.raises(RetryableJobError) as raised:
        handler(
            _context(
                code="I3_PATENT_SEARCH",
                mode="fixture",
                data={},
                repository=repository,
                settings=settings,
            )
        )

    assert raised.value.error_code == "LAB_DEPENDENCY_FAILED"
