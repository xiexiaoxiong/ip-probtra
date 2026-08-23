from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/evaluate_decision_i4s.py"


def _module():
    spec = importlib.util.spec_from_file_location("decision_i4s_evaluator", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_evaluator_runs_only_after_blind_freeze_and_matches_equivalent_structure() -> None:
    module = _module()
    oracle = {
        "case_id": "case",
        "claim_id": "1",
        "documents": [
            {
                "publication_number": "CN1A",
                "human_evaluation_scope": "feature_mapping_available",
                "concepts": [
                    {
                        "concept_id": "rotary",
                        "label": "旋转连接",
                        "feature_ids": ["f1"],
                        "feature_selector_groups": [["不会命中的备用词"]],
                        "expected_disclosure": "disclosed",
                    }
                ],
            }
        ],
    }
    frozen = {
        "oracle_loaded": False,
        "case_id": "case",
        "claim_id": "1",
        "comparisons": [
            {
                "document_file": "/tmp/CN1A.pdf",
                "comparison": {
                    "analysis_rule_version": "structural-disclosure-v12",
                    "structural_review_status": "completed",
                    "disclosures": [
                        {
                            "feature_id": "f1",
                            "feature_text": "耳机本体内具有圆形凸台",
                            "status": "necessarily_implicit",
                        }
                    ],
                },
            }
        ],
    }
    result = module.evaluate(oracle, frozen)
    assert result["runtime_oracle_loaded"] is False
    assert result["freeze_audit"] == {
        "analysis_rule_version": "structural-disclosure-v12",
        "expected_rule_version": None,
        "comparison_document_count": 1,
        "expected_document_count": 1,
        "all_expected_documents_compared": True,
        "single_rule_version": True,
    }
    assert result["summary"] == {
        "scored_concepts": 1,
        "passed_concepts": 1,
        "failed_concepts": 0,
        "unscorable_concepts": 0,
        "pass_rate": 1.0,
    }


def test_evaluator_rejects_mixed_latest_rule_versions() -> None:
    module = _module()
    oracle = {
        "case_id": "case",
        "claim_id": "1",
        "documents": [
            {"publication_number": "CN1A", "human_evaluation_scope": "not_evaluated"},
            {"publication_number": "CN2A", "human_evaluation_scope": "not_evaluated"},
        ],
    }
    frozen = {
        "oracle_loaded": False,
        "case_id": "case",
        "claim_id": "1",
        "comparisons": [
            {
                "document_file": "/tmp/CN1A.pdf",
                "comparison": {
                    "analysis_rule_version": "structural-disclosure-v41",
                    "structural_review_status": "completed",
                    "disclosures": [{"feature_id": "f1", "status": "not_disclosed"}],
                },
            },
            {
                "document_file": "/tmp/CN2A.pdf",
                "comparison": {
                    "analysis_rule_version": "structural-disclosure-v44",
                    "structural_review_status": "completed",
                    "disclosures": [{"feature_id": "f1", "status": "not_disclosed"}],
                },
            },
        ],
    }

    with pytest.raises(ValueError, match="不是同一 I4-S 规则版本"):
        module.evaluate(oracle, frozen)


def test_evaluator_rejects_missing_document_or_wrong_expected_version() -> None:
    module = _module()
    oracle = {
        "case_id": "case",
        "claim_id": "1",
        "documents": [
            {"publication_number": "CN1A", "human_evaluation_scope": "not_evaluated"},
            {"publication_number": "CN2A", "human_evaluation_scope": "not_evaluated"},
        ],
    }
    frozen = {
        "oracle_loaded": False,
        "case_id": "case",
        "claim_id": "1",
        "comparisons": [
            {
                "document_file": "/tmp/CN1A.pdf",
                "comparison": {
                    "analysis_rule_version": "structural-disclosure-v44",
                    "structural_review_status": "completed",
                    "disclosures": [{"feature_id": "f1", "status": "not_disclosed"}],
                },
            }
        ],
    }

    with pytest.raises(ValueError, match="尚未逐篇完成"):
        module.evaluate(oracle, frozen)
    with pytest.raises(ValueError, match="指定版本不一致"):
        module.evaluate(
            {**oracle, "documents": oracle["documents"][:1]},
            frozen,
            expected_rule_version="structural-disclosure-v45",
        )


def test_runtime_source_never_mentions_decision_oracle_directory() -> None:
    forbidden = "evaluation_oracles"
    for path in (ROOT / "src/invalidity").rglob("*.py"):
        assert forbidden not in path.read_text(encoding="utf-8")
