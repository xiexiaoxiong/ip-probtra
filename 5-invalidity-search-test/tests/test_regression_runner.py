from __future__ import annotations

import copy
from pathlib import Path
import sys

import pytest


SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

import run_full_invalidity_regression as runner  # noqa: E402


def test_pipeline_stage_ledger_includes_candidate_filter_in_order() -> None:
    stages = runner.PIPELINE_STAGES

    assert "I3-F" in stages
    assert stages.index("I3-NPL") < stages.index("I3-F") < stages.index("I3-E")
    assert runner.StageLedger().dump()["I3-F"]["status"] == "not_run"


def _valid_live_report(*, source_category: str = "patent") -> dict[str, object]:
    is_patent = source_category == "patent"
    provider = "google_patents" if is_patent else "arxiv"
    document_type = "patent" if is_patent else "preprint"
    source_url = (
        "https://patents.google.com/patent/CN100000001A/zh"
        if is_patent
        else "https://arxiv.org/pdf/2001.00001"
    )
    content_sha256 = "d" * 64

    def query(
        query_id: str, channel: str, date_channel: str
    ) -> dict[str, object]:
        return {
            "id": query_id,
            "iteration_id": "iteration-1",
            "channel": channel,
            "status": "completed",
            "date_filter": {"date_channel": date_channel},
            "provider_plan": {
                "provider_kind": channel,
                "search_objective": "full_claim_single_reference",
                "date_channel": date_channel,
                "execution_diagnostic": {
                    "provider": provider,
                    "network_used": True,
                    "record_count": 1,
                },
            },
        }

    return {
        "contract_version": "v1",
        "report_kind": "invalidity_evidence_data",
        "report_snapshot_id": "snapshot-1",
        "snapshot_sha256": "a" * 64,
        "snapshot_hash_scope": (
            "canonical_persisted_report_data_v1_excluding_snapshot_sha256"
        ),
        "is_preview": False,
        "claim_investigations": [
            {"id": "claim-1", "claim_id": "1", "status": "partial"}
        ],
        "claim_limitations": [
            {
                "id": "limitation-1",
                "claim_investigation_id": "claim-1",
                "feature_key": "1A",
            }
        ],
        "iterations": [
            {
                "id": "iteration-1",
                "claim_investigation_id": "claim-1",
                "iteration_no": 1,
                "status": "partial",
            }
        ],
        "queries": [
            query("query-patent-ordinary", "patent", "ordinary_prior_art"),
        ],
        "module_runs": [
            {
                "id": "i4-run-1",
                "claim_investigation_id": "claim-1",
                "iteration_id": "iteration-1",
                "module_code": "I4_S_SINGLE_REFERENCE",
                "status": "succeeded",
                "input_sha256": "b" * 64,
                "output_sha256": "c" * 64,
                "requested_mode": "live",
                "effective_mode": "live",
                "model_version": "glm-4.6v-test",
                "used_target_images": 1,
                "used_document_images": 1,
                "input_binding": {
                    "document_id": "document-1",
                    "document_version_id": "document-version-1",
                    "document_content_sha256": content_sha256,
                    "limitation_set_sha256": "f" * 64,
                },
            },
            {
                "id": "i3-filter-run-1",
                "claim_investigation_id": "claim-1",
                "iteration_id": "iteration-1",
                "module_code": "I3_CANDIDATE_FILTER",
                "status": "succeeded",
                "input_sha256": "1" * 64,
                "output_sha256": "2" * 64,
                "requested_mode": "live",
                "effective_mode": "live",
                "actual_provider": "glm_semantic_candidate_filter",
                "completed_at": "2026-08-08T00:00:01+00:00",
            },
            {
                "id": "i3-fetch-run-1",
                "claim_investigation_id": "claim-1",
                "iteration_id": "iteration-1",
                "module_code": "I3_FETCH",
                "status": "succeeded",
                "input_sha256": "3" * 64,
                "output_sha256": "4" * 64,
                "requested_mode": "live",
                "effective_mode": "live",
                "actual_provider": provider,
                "completed_at": "2026-08-08T00:00:02+00:00",
            },
        ],
        "documents": [
            {
                "id": "document-1",
                "investigation_id": "investigation-1",
                "document_type": document_type,
                "evidence_level": "qualified_evidence",
                "content_sha256": content_sha256,
                "current_document_version_id": "document-version-1",
            }
        ],
        "document_versions": [
            {
                "id": "document-version-1",
                "document_id": "document-1",
                "version_no": 1,
                "content_sha256": content_sha256,
                "content_artifact_id": "artifact-1",
                "mime_type": "application/pdf",
                "byte_size": 4096,
                "version_metadata": {
                    "analysis_ready": True,
                    "analysis_readiness_reason": "all PDF pages processed",
                    "readable_document_version": "readable-patent-v1",
                    "readable_document": {
                        "schema_version": "readable-patent-v1",
                        "source_sha256": content_sha256,
                        "source_kind": "pdf_text",
                        "normalization_status": "completed",
                        "analysis_ready": True,
                        "analysis_readiness_reason": "all PDF pages processed",
                        "page_count": 1,
                        "processed_page_count": 1,
                        "text_page_count": 1,
                        "ocr_page_count": 0,
                        "failed_pages": [],
                        "sections": [
                            {
                                "section": "pdf_page",
                                "anchor": "page-1",
                                "kind": "pdf_text",
                                "page_start": 1,
                                "page_end": 1,
                                "text": "full technical disclosure " * 20,
                            }
                        ],
                        "full_text": "full technical disclosure " * 20,
                    },
                },
            }
        ],
        "document_sources": [
            {
                "id": "source-1",
                "document_id": "document-1",
                "document_version_id": "document-version-1",
                "provider": provider,
                "source_url": source_url,
                "artifact_id": "artifact-1",
                "snapshot_sha256": content_sha256,
                "snapshot_byte_size": 4096,
                "status": "qualified",
            }
        ],
        "document_qualifications": [
            {
                "id": "qualification-1",
                "claim_investigation_id": "claim-1",
                "document_id": "document-1",
                "document_version_id": "document-version-1",
                "assessment_version": 1,
                "eligibility_type": "ordinary_prior_art",
                "novelty_eligible": True,
                "inventive_step_eligible": True,
                "verification_status": "verified",
                "human_confirmed": False,
            }
        ],
        "feature_disclosures": [
            {
                "id": "disclosure-1",
                "module_run_id": "i4-run-1",
                "claim_investigation_id": "claim-1",
                "limitation_id": "limitation-1",
                "document_id": "document-1",
                "document_version_id": "document-version-1",
                "document_source_id": "source-1",
                "disclosure_status": "not_disclosed",
                "model_version": "glm-4.6v-test",
                "analysis": {
                    "used_target_images": 1,
                    "used_document_images": 1,
                },
            }
        ],
        "closest_prior_art_versions": [],
        "combinations": [],
        "artifacts": [
            {
                "id": "artifact-1",
                "investigation_id": "investigation-1",
                "artifact_type": "prior_art_primary_source",
                "sha256": content_sha256,
                "mime_type": "application/pdf",
                "byte_size": 4096,
            }
        ],
    }


def test_live_runner_is_pinned_to_test_api_and_token() -> None:
    runner.validate_live_api_target(
        "http://127.0.0.1:5209", "INVALIDITY_TEST_API_TOKEN"
    )
    with pytest.raises(ValueError, match="5209"):
        runner.validate_live_api_target(
            "http://127.0.0.1:5109", "INVALIDITY_TEST_API_TOKEN"
        )
    with pytest.raises(ValueError, match="TEST_API_TOKEN"):
        runner.validate_live_api_target(
            "http://127.0.0.1:5209", "INVALIDITY_PROD_API_TOKEN"
        )


def test_live_upload_root_must_be_exact_test_namespace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service_root = tmp_path / "5-invalidity-search-test"
    expected = service_root / ".data" / "uploads" / "test"
    monkeypatch.setattr(runner, "SERVICE_ROOT", service_root)

    assert runner.resolve_live_upload_root(expected) == expected.resolve()
    with pytest.raises(ValueError, match="隔离测试上传根目录"):
        runner.resolve_live_upload_root(tmp_path / "5-invalidity-search-prod" / ".data" / "uploads" / "prod")


def test_staged_source_is_atomic_and_hash_preserving(tmp_path: Path) -> None:
    upload_root = tmp_path / "uploads" / "test"
    upload_root.mkdir(parents=True)
    source = tmp_path / "target.pdf"
    source.write_bytes(b"%PDF-1.7\nregression\n")

    staged = runner.stage_live_source(
        source, upload_root=upload_root, run_id="live/test/../run"
    )

    assert staged.is_file()
    assert staged.read_bytes() == source.read_bytes()
    assert runner.file_sha256(staged) == runner.file_sha256(source)
    assert not list(staged.parent.glob("*.tmp"))


def test_fixture_repository_persists_versioned_gap_frontiers() -> None:
    repository = runner.RegressionRepository({}, runner.StageLedger())
    assert repository.investigation["settings"][
        "max_gap_search_iterations"
    ] == 5
    assert repository.investigation["budgets"][
        "max_gap_search_iterations"
    ] == 5
    claim = repository.create_claim_investigation(
        claim_id="1",
        claim_text="一种装置",
        expanded_claim_text="一种装置，包括特征甲和特征乙",
        status="initial_search",
    )
    limitation = repository.insert_claim_limitations(
        claim["id"],
        [{"feature_key": "1A", "limitation_text": "特征甲", "sequence_no": 1}],
    )[0]
    first = repository.create_iteration(
        claim_investigation_id=claim["id"], iteration_no=1, status="running"
    )
    opened = repository.reconcile_gap_frontier(
        iteration_id=first["id"],
        claim_investigation_id=claim["id"],
        open_gaps=[
            {
                "gap_key": "feature:1A",
                "limitation_id": limitation["id"],
                "gap_type": "feature_gap",
                "status": "open",
                "description": "尚未披露特征甲",
                "search_objective": {"feature_id": "1A"},
            }
        ],
    )
    second = repository.create_iteration(
        claim_investigation_id=claim["id"], iteration_no=2, status="running"
    )
    closed = repository.reconcile_gap_frontier(
        iteration_id=second["id"],
        claim_investigation_id=claim["id"],
        open_gaps=[],
        closed_gap_resolutions={
            "feature:1A": {
                "closed_reason": "qualified_document_disclosed_limitation",
                "resolution_evidence": {"disclosure_status": "explicit"},
            }
        },
    )

    assert opened[0]["status"] == "open"
    assert opened[0]["version_no"] == 1
    assert closed[0]["status"] == "closed"
    assert closed[0]["version_no"] == 2
    assert closed[0]["supersedes_id"] == opened[0]["id"]
    assert len(repository.persisted["gaps"]) == 2


def test_fixture_repository_appends_immutable_document_versions() -> None:
    repository = runner.RegressionRepository({}, runner.StageLedger())
    artifact_v1 = repository.insert_artifact(
        investigation_id=repository.investigation["id"],
        artifact_type="prior_art_primary_source",
        uri="fixture://document-v1.pdf",
        sha256="1" * 64,
        byte_size=101,
    )
    first = repository.upsert_document(
        investigation_id=repository.investigation["id"],
        canonical_key="patent:CN1",
        document_type="patent",
        title="CN1",
        content_sha256="1" * 64,
        content_artifact_id=artifact_v1["id"],
        content_byte_size=101,
        version_metadata={"frozen": "first"},
    )
    replay = repository.upsert_document(
        investigation_id=repository.investigation["id"],
        canonical_key="patent:CN1",
        document_type="patent",
        title="CN1 replay",
        content_sha256="1" * 64,
        content_artifact_id=artifact_v1["id"],
        content_byte_size=101,
        version_metadata={"frozen": "must-not-overwrite"},
    )
    artifact_v2 = repository.insert_artifact(
        investigation_id=repository.investigation["id"],
        artifact_type="prior_art_primary_source",
        uri="fixture://document-v2.pdf",
        sha256="2" * 64,
        byte_size=202,
    )
    second = repository.upsert_document(
        investigation_id=repository.investigation["id"],
        canonical_key="patent:CN1",
        document_type="patent",
        title="CN1 v2",
        content_sha256="2" * 64,
        content_artifact_id=artifact_v2["id"],
        content_byte_size=202,
    )

    assert replay["document_version_id"] == first["document_version_id"]
    assert second["document_version_id"] != first["document_version_id"]
    assert second["document_version_no"] == 2
    versions = repository.list_document_versions(first["id"])
    assert [item["content_sha256"] for item in versions] == ["1" * 64, "2" * 64]
    assert versions[0]["version_metadata"] == {"frozen": "first"}


def test_summary_reports_per_case_live_truth(tmp_path: Path) -> None:
    results = [
        {
            "status": "passed",
            "real_world_search_attempted": True,
            "real_world_search_performed": True,
            "network_used": True,
            "live_truth_gate_passed": True,
            "patent_source_snapshot": True,
            "npl_source_snapshot": True,
            "multimodal_i4s": True,
            "workflow": {"status": "completed", "claims": []},
        },
        {
            "status": "passed",
            "real_world_search_attempted": False,
            "real_world_search_performed": False,
            "network_used": False,
            "live_truth_gate_passed": False,
            "patent_source_snapshot": False,
            "npl_source_snapshot": False,
            "multimodal_i4s": False,
            "workflow": {"status": "needs_human_review", "claims": []},
        },
    ]

    summary = runner.build_summary(
        run_id="live-test", mode="live", sample_root=tmp_path, results=results
    )

    assert summary["all_samples_completed"] is True
    assert summary["real_world_search_attempted_count"] == 1
    assert summary["all_cases_attempted_real_world_search"] is False
    assert summary["real_world_search_performed_count"] == 1
    assert summary["all_cases_performed_real_world_search"] is False
    assert summary["network_used_count"] == 1
    assert summary["all_cases_recorded_network_use"] is False
    assert summary["live_truth_gate_passed_count"] == 1
    assert summary["all_cases_passed_live_truth_gate"] is False
    assert summary["live_completion_gate_passed"] is False


def test_live_completion_gate_requires_every_case_to_use_real_search(
    tmp_path: Path,
) -> None:
    complete_patent = {
        "status": "passed",
        "real_world_search_attempted": True,
        "real_world_search_performed": True,
        "network_used": True,
        "live_truth_gate_passed": True,
        "patent_source_snapshot": True,
        "npl_source_snapshot": False,
        "multimodal_i4s": True,
        "workflow": {"status": "completed", "claims": []},
    }
    complete_npl = {
        **complete_patent,
        "patent_source_snapshot": False,
        "npl_source_snapshot": True,
    }

    live_summary = runner.build_summary(
        run_id="live-complete",
        mode="live",
        sample_root=tmp_path,
        results=[complete_patent, complete_npl],
    )
    fixture_summary = runner.build_summary(
        run_id="fixture-complete",
        mode="fixture",
        sample_root=tmp_path,
        results=[
                {
                    **complete_patent,
                    "real_world_search_attempted": False,
                    "real_world_search_performed": False,
                    "network_used": False,
            }
        ],
    )

    assert live_summary["live_completion_gate_passed"] is True
    assert fixture_summary["live_completion_gate_passed"] is False


def test_live_truth_audit_requires_replayable_multimodal_chain() -> None:
    audit = runner.audit_live_report_truth(_valid_live_report())

    assert audit["passed"] is True
    assert audit["patent_source_snapshot"] is True
    assert audit["npl_source_snapshot"] is False
    assert audit["complete_evidence_chain_count"] == 1
    assert all(audit["checks"].values())


def test_live_truth_audit_requires_candidate_filter_before_fetch() -> None:
    report = _valid_live_report()
    report["module_runs"] = [
        item
        for item in report["module_runs"]  # type: ignore[index]
        if item.get("module_code") != "I3_CANDIDATE_FILTER"
    ]

    missing = runner.audit_live_report_truth(report)

    assert missing["passed"] is False
    assert missing["checks"]["candidate_filter_fetch_ordering"] is False

    late_report = _valid_live_report()
    filter_run = next(
        item
        for item in late_report["module_runs"]  # type: ignore[index]
        if item.get("module_code") == "I3_CANDIDATE_FILTER"
    )
    filter_run["completed_at"] = "2026-08-08T00:00:03+00:00"

    late = runner.audit_live_report_truth(late_report)

    assert late["passed"] is False
    assert late["checks"]["candidate_filter_fetch_ordering"] is False


def test_live_truth_audit_requires_full_document_readability_binding() -> None:
    report = _valid_live_report()
    version = report["document_versions"][0]  # type: ignore[index]
    assert isinstance(version, dict)
    metadata = version["version_metadata"]
    assert isinstance(metadata, dict)
    metadata["analysis_ready"] = False

    audit = runner.audit_live_report_truth(report)

    assert audit["passed"] is False
    assert audit["checks"]["analysis_ready_document_versions"] is False
    assert audit["checks"]["multimodal_i4s_complete_chain"] is False


def test_live_truth_audit_requires_three_provider_lanes_only_for_gap_rounds() -> None:
    report = _valid_live_report()
    report["iterations"].append(  # type: ignore[union-attr]
        {
            "id": "iteration-2",
            "claim_investigation_id": "claim-1",
            "iteration_no": 2,
            "status": "partial",
        }
    )

    def gap_query(query_id: str, channel: str, date_channel: str) -> dict[str, object]:
        return {
            "id": query_id,
            "iteration_id": "iteration-2",
            "channel": channel,
            "status": "completed",
            "date_filter": {"date_channel": date_channel},
            "provider_plan": {
                "provider_kind": channel,
                "search_objective": "gap_or_combination",
                "date_channel": date_channel,
                "execution_diagnostic": {
                    "provider": "test-live-provider",
                    "network_used": True,
                    "record_count": 0,
                },
            },
        }

    report["queries"].extend(  # type: ignore[union-attr]
        [
            gap_query("gap-patent-ordinary", "patent", "ordinary_prior_art"),
            gap_query(
                "gap-patent-conflicting",
                "patent",
                "cn_conflicting_application",
            ),
            gap_query("gap-npl-ordinary", "npl", "ordinary_prior_art"),
        ]
    )

    assert runner.audit_live_report_truth(report)["checks"][
        "query_matrix_complete"
    ] is True

    report["queries"] = [  # type: ignore[index]
        query for query in report["queries"] if query["id"] != "gap-npl-ordinary"  # type: ignore[index]
    ]
    audit = runner.audit_live_report_truth(report)
    assert audit["checks"]["query_matrix_complete"] is False
    assert any("gap_or_combination lane=npl/ordinary_prior_art" in item for item in audit["errors"])


def test_live_truth_audit_rejects_terminal_or_boolean_only_success(
    tmp_path: Path,
) -> None:
    legacy = {
        "status": "passed",
        "real_world_search_attempted": True,
        "real_world_search_performed": True,
        "network_used": True,
        "workflow": {"status": "needs_human_review", "claims": []},
    }

    audit = runner.audit_live_report_truth({})
    summary = runner.build_summary(
        run_id="legacy-live", mode="live", sample_root=tmp_path, results=[legacy]
    )

    assert audit["passed"] is False
    assert summary["live_completion_gate_passed"] is False


@pytest.mark.parametrize("status", ["failed", "cancelled"])
def test_live_runner_does_not_wait_for_report_after_non_reportable_terminal_status(
    status: str,
) -> None:
    with pytest.raises(RuntimeError, match=f"terminal status {status}"):
        runner.require_live_reportable_status(
            {"status": status},
            investigation_id="investigation-terminal",
        )


@pytest.mark.parametrize("status", ["completed", "partial", "needs_human_review"])
def test_live_runner_waits_for_report_for_reportable_terminal_status(status: str) -> None:
    runner.require_live_reportable_status(
        {"status": status},
        investigation_id="investigation-reportable",
    )


def test_live_truth_audit_rejects_text_only_or_unbound_i4s() -> None:
    report = _valid_live_report(source_category="npl")
    module_run = report["module_runs"][0]  # type: ignore[index]
    assert isinstance(module_run, dict)
    module_run["used_document_images"] = 0

    audit = runner.audit_live_report_truth(report)

    assert audit["passed"] is False
    assert audit["npl_source_snapshot"] is True
    assert audit["checks"]["multimodal_i4s_complete_chain"] is False


def test_live_truth_audit_rejects_source_hash_version_mismatch() -> None:
    report = _valid_live_report()
    source = report["document_sources"][0]  # type: ignore[index]
    assert isinstance(source, dict)
    source["snapshot_sha256"] = "9" * 64

    audit = runner.audit_live_report_truth(report)

    assert audit["passed"] is False
    assert audit["checks"]["source_version_bindings"] is False
    assert audit["valid_source_snapshot_count"] == 0


def test_live_truth_audit_rejects_cross_version_stitching() -> None:
    report = _valid_live_report()
    first_version = report["document_versions"][0]  # type: ignore[index]
    assert isinstance(first_version, dict)
    second_version_metadata = copy.deepcopy(first_version["version_metadata"])
    second_readable = second_version_metadata["readable_document"]
    assert isinstance(second_readable, dict)
    second_readable["source_sha256"] = "2" * 64
    report["artifacts"].append(  # type: ignore[union-attr]
        {
            "id": "artifact-2",
            "investigation_id": "investigation-1",
            "artifact_type": "prior_art_primary_source",
            "sha256": "2" * 64,
            "byte_size": 8192,
        }
    )
    report["document_versions"].append(  # type: ignore[union-attr]
        {
            "id": "document-version-2",
            "document_id": "document-1",
            "version_no": 2,
            "content_sha256": "2" * 64,
            "content_artifact_id": "artifact-2",
            "mime_type": "application/pdf",
            "byte_size": 8192,
            "version_metadata": second_version_metadata,
        }
    )
    report["document_sources"].append(  # type: ignore[union-attr]
        {
            "id": "source-2",
            "document_id": "document-1",
            "document_version_id": "document-version-2",
            "provider": "google_patents",
            "source_url": "https://patents.google.com/patent/CN100000001A/en",
            "artifact_id": "artifact-2",
            "snapshot_sha256": "2" * 64,
            "snapshot_byte_size": 8192,
            "status": "qualified",
        }
    )
    disclosure = report["feature_disclosures"][0]  # type: ignore[index]
    run = report["module_runs"][0]  # type: ignore[index]
    assert isinstance(disclosure, dict) and isinstance(run, dict)
    disclosure["document_version_id"] = "document-version-2"
    disclosure["document_source_id"] = "source-2"
    binding = run["input_binding"]
    assert isinstance(binding, dict)
    binding["document_version_id"] = "document-version-2"
    binding["document_content_sha256"] = "2" * 64

    audit = runner.audit_live_report_truth(report)

    assert audit["passed"] is False
    assert audit["checks"]["cross_version_stitching_free"] is False
    assert audit["checks"]["multimodal_i4s_complete_chain"] is False
    assert any("qualification uses" in item for item in audit["binding_errors"])


def test_live_truth_audit_validates_d1_and_combination_version_pairs() -> None:
    report = _valid_live_report()
    report["module_runs"].append(  # type: ignore[union-attr]
        {
            "id": "i4i-run-1",
            "claim_investigation_id": "claim-1",
            "iteration_id": "iteration-1",
            "module_code": "I4_I_INVENTIVE_STEP",
            "status": "succeeded",
            "requested_mode": "live",
            "effective_mode": "live",
            "prompt_version": runner.I4I_PROMPT_VERSION,
            "rule_version": runner.I4I_RULE_VERSION,
        }
    )
    report["closest_prior_art_versions"] = [
        {
            "id": "d1-selection-1",
            "claim_investigation_id": "claim-1",
            "iteration_id": "iteration-1",
            "document_id": "document-1",
            "document_version_id": "document-version-1",
            "version_no": 1,
            "is_current": True,
        }
    ]
    report["combinations"] = [
        {
            "id": "combination-1",
            "claim_investigation_id": "claim-1",
            "iteration_id": "iteration-1",
            "module_run_id": "i4i-run-1",
            "closest_prior_art_version_id": "d1-selection-1",
            "document_ids": ["document-1"],
            "document_version_ids": ["document-version-1"],
        }
    ]

    assert runner.audit_live_report_truth(report)["passed"] is True

    report["combinations"][0]["document_version_ids"] = ["missing-version"]  # type: ignore[index]
    audit = runner.audit_live_report_truth(report)
    assert audit["passed"] is False
    assert audit["checks"]["combination_version_bindings"] is False
