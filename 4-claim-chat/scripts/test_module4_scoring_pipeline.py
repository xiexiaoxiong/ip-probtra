#!/usr/bin/env python3
"""Module4 token/review/rule scoring smoke tests.

Run from 4-claim-chat:
    python scripts/test_module4_scoring_pipeline.py
"""

from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

from graphs.nodes.apply_rules_node import apply_rules_node  # noqa: E402
from graphs.nodes.analyze_features_node import _downgrade_weak_enrichment_only_units  # noqa: E402
from graphs.nodes.review_analysis_node import review_analysis_node  # noqa: E402
from graphs.state import ApplyRulesInput, ReviewAnalysisInput  # noqa: E402
from utils.claim_scoring import (  # noqa: E402
    build_feature_segments,
    classify_unit_status,
    compute_claim_score,
    compute_feature_awarded_score,
    compute_feature_similarity_score,
    compute_product_score,
)


def _feature(feature_id: str, claim_id: str, text: str) -> dict:
    segments = build_feature_segments(text)
    return {
        "feature_id": feature_id,
        "claim_id": claim_id,
        "feature_text": text,
        "feature_segments": segments,
        "effective_length": sum(int(item["effective_length"]) for item in segments),
    }


def _raw_item(feature: dict, statuses: list[str], reasoning_type: str = "文字直接公开") -> dict:
    units = []
    for segment, status in zip(feature["feature_segments"], statuses):
        units.append({
            "text": segment["text"],
            "unit_status": status,
            "evidence": f"evidence for {segment['text']}",
            "reason": f"reason for {segment['text']}",
        })
    return {
        "feature_id": feature["feature_id"],
        "claim_id": feature["claim_id"],
        "evidence": "sample evidence",
        "reason": "sample reason",
        "reasoning_type": reasoning_type,
        "token_units": units,
    }


def test_status_normalization() -> None:
    assert classify_unit_status("match") == "match"
    assert classify_unit_status("明确相同") == "match"
    assert classify_unit_status("not_match") == "mismatch"
    assert classify_unit_status("明确不相同") == "mismatch"
    assert classify_unit_status("") == "uncertain"
    assert classify_unit_status("unknown-value") == "uncertain"


def test_token_mismatch_zeroes_claim_and_product() -> None:
    feature_a = _feature("1A", "1", "清洁机器人")
    feature_b = _feature("1B", "1", "具有拖地功能")
    raw_analysis = [
        _raw_item(feature_a, ["match"]),
        _raw_item(feature_b, ["mismatch"], reasoning_type="可判断不具有"),
    ]

    review_output = review_analysis_node(
        ReviewAnalysisInput(
            raw_analysis=raw_analysis,
            product_name="扫地机器人",
            product_data={"name": "扫地机器人", "description": "不具有拖地功能"},
            features=[feature_a, feature_b],
        ),
        config={},
        runtime=None,
    )
    reviewed = review_output.reviewed_analysis
    assert reviewed[0]["token_units"][0]["unit_status"] == "match"
    assert reviewed[1]["token_units"][0]["unit_status"] == "mismatch"

    output = apply_rules_node(
        ApplyRulesInput(
            reviewed_analysis=reviewed,
            product_name="扫地机器人",
            product_data={},
            features=[feature_a, feature_b],
        ),
        config={},
        runtime=None,
    )
    result = output.comparison_result
    assert result["features"][0]["similarity_score"] > 0
    assert result["features"][0]["zeroed_by_mismatch"] is False
    assert result["features"][1]["similarity_score"] == 0.0
    assert result["features"][1]["zeroed_by_mismatch"] is True
    assert result["claim_scores"][0]["similarity_score"] == 0.0
    assert result["claim_scores"][0]["zeroed_by_mismatch"] is True
    assert result["product_similarity_score"] == 0.0
    assert result["product_score_band"] == "明确不相同"


def test_product_score_sums_features_without_mismatch() -> None:
    features = [
        {"feature_awarded_score": 40.0, "feature_effective_length": 5, "matched_effective_length": 4, "zeroed_by_mismatch": False},
        {"feature_awarded_score": 35.0, "feature_effective_length": 5, "matched_effective_length": 3, "zeroed_by_mismatch": False},
    ]
    claim_score, matched_length, zeroed = compute_claim_score(features, claim_total_effective_length=10)
    assert claim_score == 75.0
    assert matched_length == 7
    assert zeroed is False
    assert compute_product_score([
        {"similarity_score": 40.0, "zeroed_by_mismatch": False},
        {"similarity_score": 35.0, "zeroed_by_mismatch": False},
    ]) == 75.0


def test_feature_score_caps_overlapping_matched_length() -> None:
    assert compute_feature_similarity_score(
        feature_effective_length=2,
        matched_effective_length=4,
        has_mismatch=False,
    ) == 100.0
    assert compute_feature_awarded_score(
        feature_full_score=40.0,
        feature_effective_length=2,
        matched_effective_length=4,
        has_mismatch=False,
    ) == 40.0

    features = [
        {"feature_awarded_score": 40.0, "feature_effective_length": 2, "matched_effective_length": 4, "zeroed_by_mismatch": False},
        {"feature_awarded_score": 30.0, "feature_effective_length": 3, "matched_effective_length": 2, "zeroed_by_mismatch": False},
    ]
    claim_score, matched_length, zeroed = compute_claim_score(features, claim_total_effective_length=5)
    assert claim_score == 70.0
    assert matched_length == 4
    assert zeroed is False


def test_weak_enrichment_only_match_is_downgraded() -> None:
    candidate = {
        "feature_id": "1B",
        "evidence": "[网页搜索/product_page/weak；低置信同品线索，不得单独作为结构确认依据:示例网页] 写到具有升降拖地",
        "reason": "网页补充资料显示升降拖地",
        "reasoning_type": "文字直接公开",
        "token_units": [
            {
                "text": "升降拖地",
                "unit_status": "match",
                "evidence": "[网页搜索/product_page/weak；低置信同品线索，不得单独作为结构确认依据:示例网页] 升降拖地",
                "reason": "仅由弱同品网页支持",
            }
        ],
    }

    downgraded = _downgrade_weak_enrichment_only_units(candidate)
    assert downgraded["reasoning_type"] == "相关信息缺失"
    assert downgraded["token_units"][0]["unit_status"] == "uncertain"
    assert "低置信同品线索" in downgraded["token_units"][0]["reason"]


def test_weak_enrichment_with_ocr_support_keeps_match() -> None:
    candidate = {
        "feature_id": "1B",
        "evidence": "[商品图片OCR] 显示拖地机；[网页搜索/product_page/weak；低置信同品线索，不得单独作为结构确认依据:示例网页] 提到拖地",
        "reason": "商品图片OCR和弱网页线索均提到拖地",
        "reasoning_type": "文字直接公开",
        "token_units": [
            {
                "text": "拖地",
                "unit_status": "match",
                "evidence": "[商品图片OCR] 拖地；[网页搜索/product_page/weak；低置信同品线索，不得单独作为结构确认依据:示例网页] 拖地",
                "reason": "有商品图片OCR支持，不是仅靠弱同品网页",
            }
        ],
    }

    downgraded = _downgrade_weak_enrichment_only_units(candidate)
    assert downgraded["reasoning_type"] == "文字直接公开"
    assert downgraded["token_units"][0]["unit_status"] == "match"


test_status_normalization()
test_token_mismatch_zeroes_claim_and_product()
test_product_score_sums_features_without_mismatch()
test_feature_score_caps_overlapping_matched_length()
test_weak_enrichment_only_match_is_downgraded()
test_weak_enrichment_with_ocr_support_keeps_match()
print("module4 scoring pipeline tests passed")
