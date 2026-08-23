#!/usr/bin/env python3
"""Evaluate frozen blind I4-S facts against separately stored decision oracles.

This script is deliberately outside ``src/invalidity``.  The oracle is opened
only after a blind comparison file has been frozen with ``oracle_loaded=false``;
neither the runtime nor the blind runner imports this module.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


DISCLOSED = {"explicit", "direct_and_unambiguous", "necessarily_implicit"}
USABLE_RULE_PREFIX = "structural-disclosure-v"


def _normalise(value: Any) -> str:
    return re.sub(r"[^0-9A-Za-z\u3400-\u9fff]+", "", str(value or "")).lower()


def _document_key(value: Any) -> str:
    return _normalise(Path(str(value or "")).stem)


def _usable(comparison: Any) -> bool:
    return (
        isinstance(comparison, dict)
        and str(comparison.get("analysis_rule_version") or "").startswith(
            USABLE_RULE_PREFIX
        )
        and str(comparison.get("structural_review_status") or "") != "model_error"
        and isinstance(comparison.get("disclosures"), list)
        and bool(comparison.get("disclosures"))
    )


def _latest_by_document(frozen: dict[str, Any]) -> dict[str, dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for item in frozen.get("comparisons") or []:
        if not isinstance(item, dict) or not _usable(item.get("comparison")):
            continue
        key = _document_key(item.get("document_file"))
        if key:
            latest[key] = item
    return latest


def _row_text(row: dict[str, Any]) -> str:
    return _normalise(
        " ".join(
            str(row.get(key) or "")
            for key in (
                "feature_id",
                "feature_text",
                "target_structural_role",
            )
        )
    )


def _matches(row: dict[str, Any], selector_groups: list[list[str]]) -> bool:
    haystack = _row_text(row)
    return all(
        any(_normalise(option) in haystack for option in group)
        for group in selector_groups
    )


def _evaluate_concept(
    disclosures: list[dict[str, Any]], concept: dict[str, Any]
) -> dict[str, Any]:
    groups = concept.get("feature_selector_groups") or []
    feature_ids = {
        str(item) for item in concept.get("feature_ids") or [] if str(item)
    }
    candidates = [
        row
        for row in disclosures
        if (
            str(row.get("feature_id") or "") in feature_ids
            if feature_ids
            else _matches(row, groups)
        )
    ]
    expected = str(concept.get("expected_disclosure") or "")
    if not candidates:
        return {
            "concept_id": concept.get("concept_id"),
            "label": concept.get("label"),
            "expected": expected,
            "actual": "unscorable",
            "passed": None,
            "reason": "冻结结果中没有与该人工概念对应的目标特征行",
        }

    statuses = [str(row.get("status") or "") for row in candidates]
    actual = "disclosed" if any(status in DISCLOSED for status in statuses) else (
        "not_disclosed" if statuses and all(status == "not_disclosed" for status in statuses)
        else "uncertain"
    )
    passed = actual == expected
    return {
        "concept_id": concept.get("concept_id"),
        "label": concept.get("label"),
        "expected": expected,
        "actual": actual,
        "passed": passed,
        "matched_rows": [
            {
                "feature_id": row.get("feature_id"),
                "feature_text": row.get("feature_text"),
                "status": row.get("status"),
                "reference_structure_mapping": row.get(
                    "reference_structure_mapping"
                ),
            }
            for row in candidates
        ],
    }


def evaluate(
    oracle: dict[str, Any],
    frozen: dict[str, Any],
    *,
    expected_rule_version: str | None = None,
) -> dict[str, Any]:
    if frozen.get("oracle_loaded") is not False:
        raise ValueError("拒绝评估：输入不是 oracle_loaded=false 的盲测冻结结果")
    if str(oracle.get("case_id")) != str(frozen.get("case_id")):
        raise ValueError("oracle 与冻结结果 case_id 不一致")
    if str(oracle.get("claim_id")) != str(frozen.get("claim_id")):
        raise ValueError("oracle 与冻结结果 claim_id 不一致")

    latest = _latest_by_document(frozen)
    latest_rule_versions = sorted(
        {
            str(item["comparison"].get("analysis_rule_version") or "")
            for item in latest.values()
            if str(item["comparison"].get("analysis_rule_version") or "")
        }
    )
    if len(latest_rule_versions) != 1:
        raise ValueError(
            "拒绝评估：各对比文件的最新可用结果不是同一 I4-S 规则版本: "
            + ", ".join(latest_rule_versions or ["没有可用结果"])
        )
    actual_rule_version = latest_rule_versions[0]
    if expected_rule_version and actual_rule_version != expected_rule_version:
        raise ValueError(
            "拒绝评估：冻结结果规则版本与指定版本不一致 "
            f"expected={expected_rule_version} actual={actual_rule_version}"
        )
    expected_publications = [
        str(item.get("publication_number") or "")
        for item in oracle.get("documents") or []
        if isinstance(item, dict) and str(item.get("publication_number") or "")
    ]
    missing_publications = [
        publication
        for publication in expected_publications
        if _document_key(publication) not in latest
    ]
    if missing_publications:
        raise ValueError(
            "拒绝评估：尚未逐篇完成决定书对比文件的同版本盲测: "
            + ", ".join(missing_publications)
        )
    documents: list[dict[str, Any]] = []
    scored = passed = failed = unscorable = 0
    for expected_document in oracle.get("documents") or []:
        publication_number = str(expected_document.get("publication_number") or "")
        scope = str(expected_document.get("human_evaluation_scope") or "")
        item = latest.get(_document_key(publication_number))
        result: dict[str, Any] = {
            "publication_number": publication_number,
            "human_evaluation_scope": scope,
            "human_reference_role": expected_document.get("human_reference_role"),
            "legal_reasoning": expected_document.get("legal_reasoning"),
            "comparison_available": item is not None,
        }
        if scope != "feature_mapping_available":
            result["concepts"] = []
            result["note"] = expected_document.get("note")
            documents.append(result)
            continue
        if item is None:
            result["concepts"] = []
            result["error"] = "没有该文献的可用盲测比对结果"
            failed += 1
            documents.append(result)
            continue
        disclosures = item["comparison"]["disclosures"]
        concept_results = [
            _evaluate_concept(disclosures, concept)
            for concept in expected_document.get("concepts") or []
        ]
        for concept_result in concept_results:
            if concept_result["passed"] is None:
                unscorable += 1
            elif concept_result["passed"]:
                scored += 1
                passed += 1
            else:
                scored += 1
                failed += 1
        result["concepts"] = concept_results
        documents.append(result)

    return {
        "evaluation_phase": "post_freeze_human_decision_evaluation",
        "runtime_oracle_loaded": False,
        "case_id": oracle.get("case_id"),
        "claim_id": oracle.get("claim_id"),
        "decision_source": oracle.get("decision_source"),
        "freeze_audit": {
            "analysis_rule_version": actual_rule_version,
            "expected_rule_version": expected_rule_version,
            "comparison_document_count": len(latest),
            "expected_document_count": len(expected_publications),
            "all_expected_documents_compared": True,
            "single_rule_version": True,
        },
        "documents": documents,
        "summary": {
            "scored_concepts": scored,
            "passed_concepts": passed,
            "failed_concepts": failed,
            "unscorable_concepts": unscorable,
            "pass_rate": round(passed / scored, 4) if scored else None,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--oracle", type=Path, required=True)
    parser.add_argument("--frozen", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--expected-rule-version")
    arguments = parser.parse_args()
    oracle = json.loads(arguments.oracle.read_text(encoding="utf-8"))
    frozen = json.loads(arguments.frozen.read_text(encoding="utf-8"))
    result = evaluate(
        oracle,
        frozen,
        expected_rule_version=arguments.expected_rule_version,
    )
    rendered = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True)
    if arguments.output:
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)


if __name__ == "__main__":
    main()
