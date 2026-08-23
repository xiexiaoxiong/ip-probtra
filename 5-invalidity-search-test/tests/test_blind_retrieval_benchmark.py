from __future__ import annotations

import importlib.util
import hashlib
import json
from pathlib import Path

import fitz


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/fixtures/blind_benchmarks/water_gun_decision.json"
MODULE5_SOURCE_FIXTURE = (
    ROOT
    / "tests/fixtures/blind_benchmarks"
    / "water_gun_decision_2021227533927"
)
EVALUATOR = ROOT / "scripts/evaluate_blind_retrieval.py"
SEARCH_RUNNER = ROOT / "scripts/search_decision_query_plans.py"
I4S_RUNNER = ROOT / "scripts/run_decision_i4s_blind.py"
BENCHMARK_ROOT = ROOT / "tests/fixtures/blind_benchmarks"
RETRIEVAL_ORACLE_ROOT = ROOT / "tests/evaluation_oracles/decision_retrieval"


def _load_evaluator():
    spec = importlib.util.spec_from_file_location("blind_evaluator", EVALUATOR)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_i4s_runner():
    spec = importlib.util.spec_from_file_location("blind_i4s_runner", I4S_RUNNER)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_i4s_resume_identity_changes_with_limitations_and_rejects_legacy_document_only_rows() -> None:
    runner = _load_i4s_runner()
    base = {
        "claim_id": "1",
        "expanded_claim_text": "一种装置，包括壳体及连接件。",
        "limitations": [
            {
                "feature_id": "F1",
                "claim_id": "1",
                "sequence": 1,
                "text": "包括壳体及连接件",
            }
        ],
        "target_document_sha256": "a" * 64,
        "document_sha256": "b" * 64,
    }
    old_input_hash = runner._analysis_input_sha256(**base)
    changed = dict(base)
    changed["limitations"] = [
        {
            "feature_id": "F1a",
            "claim_id": "1",
            "sequence": 1,
            "text": "包括壳体",
        },
        {
            "feature_id": "F1b",
            "claim_id": "1",
            "sequence": 2,
            "text": "壳体设有连接件",
        },
    ]
    new_input_hash = runner._analysis_input_sha256(**changed)
    assert new_input_hash != old_input_hash

    comparisons = [
        {
            "module_run_id": "legacy-run",
            "document_sha256": "b" * 64,
            "comparison": {},
        },
        {
            "module_run_id": "old-plan-run",
            "analysis_input_sha256": old_input_hash,
            "comparison": {},
        },
        {
            "module_run_id": "new-plan-run",
            "analysis_input_sha256": new_input_hash,
            "comparison": {},
        },
    ]
    assert runner._pending_run_ids(comparisons, new_input_hash) == ["new-plan-run"]


def test_blind_search_runner_does_not_freeze_insufficient_balance_as_empty_results() -> None:
    source = SEARCH_RUNNER.read_text(encoding="utf-8")
    assert "if exc.error_code == 67200005" not in source
    assert "is_rate_limited = exc.error_code == 67200002" in source


def test_runtime_source_cannot_see_blind_expected_documents() -> None:
    identifiers = set()
    for path in BENCHMARK_ROOT.glob("*/retrieval_benchmark.json"):
        benchmark = json.loads(path.read_text(encoding="utf-8"))
        identifiers.update(
            row["publication_number"] for row in benchmark["expected_documents"]
        )
    benchmark = json.loads(FIXTURE.read_text(encoding="utf-8"))
    identifiers.update(row["publication_number"] for row in benchmark["expected_documents"])
    for path in BENCHMARK_ROOT.glob("*/manifest.json"):
        manifest = json.loads(path.read_text(encoding="utf-8"))
        identifiers.update(
            row["publication_number"] for row in manifest.get("documents") or []
        )
    runtime_text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (ROOT / "src/invalidity").glob("*.py")
    ).upper()
    for identifier in identifiers:
        assert identifier.upper() not in runtime_text
    assert "blind_benchmarks" not in runtime_text


def test_all_decision_source_fixtures_are_hash_verified_and_contain_no_conclusions() -> None:
    case_roots = sorted(path.parent for path in BENCHMARK_ROOT.glob("*/manifest.json"))
    assert len(case_roots) == 5
    for case_root in case_roots:
        manifest = json.loads((case_root / "manifest.json").read_text(encoding="utf-8"))
        assert manifest["conclusions_imported"] is False
        assert manifest["comparison_reasoning_imported"] is False
        assert not (case_root / "analysis_oracle.json").exists()
        target = manifest["target_document"]
        rows = [target, *(manifest.get("documents") or [])]
        for row in rows:
            path = case_root / row["file_name"]
            if not path.is_file():
                path = case_root / "source_documents" / row["file_name"]
            content = path.read_bytes()
            assert content.lstrip().startswith(b"%PDF")
            assert len(content) == row["byte_size"]
            assert hashlib.sha256(content).hexdigest() == row["sha256"]
            with fitz.open(stream=content, filetype="pdf") as document:
                assert document.page_count == row["page_count"]


def test_each_decision_case_has_a_post_freeze_d1_retrieval_oracle() -> None:
    manifests = {
        path.parent.name: json.loads(path.read_text(encoding="utf-8"))
        for path in BENCHMARK_ROOT.glob("*/manifest.json")
    }
    oracles = sorted(RETRIEVAL_ORACLE_ROOT.glob("*.json"))
    assert len(oracles) == len(manifests) == 5
    for oracle_path in oracles:
        oracle = json.loads(oracle_path.read_text(encoding="utf-8"))
        case_id = oracle_path.stem
        assert oracle["case_id"] == case_id
        assert oracle["runtime_visibility"] == "forbidden"
        assert case_id in manifests
        source_numbers = {
            row["publication_number"].upper().replace(" ", "")
            for row in manifests[case_id].get("documents") or []
        }
        expected = oracle["expected_documents"]
        required = [row for row in expected if row.get("required", True)]
        assert len(required) == 1
        assert required[0]["maximum_cumulative_rank"] == 10
        assert required[0]["publication_number"] in source_numbers
        assert all(row["publication_number"] in source_numbers for row in expected)


def test_module5_source_fixture_contains_only_verified_original_documents() -> None:
    manifest = json.loads(
        (MODULE5_SOURCE_FIXTURE / "manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["fixture_purpose"] == "module5_source_documents_only"
    assert manifest["conclusions_imported"] is False
    assert manifest["comparison_reasoning_imported"] is False
    assert manifest["analysis_contract"]["expected_disclosures"] is None
    assert manifest["analysis_contract"]["expected_legal_conclusion"] is None

    documents = manifest["documents"]
    assert len(documents) == 15
    assert {row["evidence_label"] for row in documents} == {
        *(f"evidence-{index:02d}" for index in range(1, 5)),
        *(f"evidence-{index:02d}" for index in range(7, 18)),
    }
    expected_files = {row["file_name"] for row in documents}
    actual_files = {
        path.name
        for path in (MODULE5_SOURCE_FIXTURE / "source_documents").glob("*.pdf")
    }
    assert actual_files == expected_files

    for row in documents:
        path = MODULE5_SOURCE_FIXTURE / "source_documents" / row["file_name"]
        content = path.read_bytes()
        assert content.lstrip().startswith(b"%PDF")
        assert len(content) == row["byte_size"]
        assert hashlib.sha256(content).hexdigest() == row["sha256"]
        with fitz.open(stream=content, filetype="pdf") as document:
            assert document.page_count == row["page_count"]


def test_blind_evaluator_checks_each_frozen_query_top_ten_without_role_stacking() -> None:
    benchmark = json.loads(FIXTURE.read_text(encoding="utf-8"))
    evaluator = _load_evaluator()
    result = evaluator.evaluate(
        benchmark,
        {
            "search_runs": [
                {
                    "query_role": "inventive_point_precision",
                    "documents": [
                        {"publication_number": "WO 2018/215646 A1"}
                    ],
                },
                {
                    "query_role": "claim_context_recall",
                    "query_id": "context-one",
                    "documents": [{"publication_number": f"noise-{index}"} for index in range(10)],
                },
                {
                    "query_role": "claim_context_recall",
                    "query_id": "context-two",
                    "documents": [
                        {"publication_number": "US5799827A"},
                        {"publication_number": "WO 95/00221 A1"},
                    ],
                },
            ]
        },
    )
    assert result["passed"] is True
    d1 = next(item for item in result["documents"] if item["label"] == "D1")
    assert d1["matched_publication_number"] == "WO2018215646A1"
    d2 = next(item for item in result["documents"] if item["label"] == "D2")
    assert d2["best_rank"] == 1
