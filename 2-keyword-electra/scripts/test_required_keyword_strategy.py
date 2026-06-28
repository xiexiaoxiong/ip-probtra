#!/usr/bin/env python3
"""
模块2必要检索特征回归测试。

不调用 LLM，只验证确定性兜底和关键词护栏，防止“拖地/升降/扫地机器人”这类主检索骨架再次丢失。
"""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from graphs.nodes.required_feature_extraction_node import _fallback_features  # noqa: E402
from graphs.nodes.keyword_combination_node import (  # noqa: E402
    _apply_guardrails,
    _build_context_extension_keywords,
    _build_required_feature_keywords,
)
from graphs.state import RequiredFeatureExtractionInput  # noqa: E402


CLAIM_TEXT = """1.一种具备拖地功能的扫地机器人，其特征在于，包括：
扫地机器人本体，底部沿前后向依次设有安装位和中扫吸尘口；
拖布结构和地刷结构，两者可择其一安装于所述安装位的位置；
中扫结构，活动安装于所述中扫吸尘口；以及
升降控制结构，安装于所述扫地机器人本体内，所述升降控制结构与所述中扫结构活动相接，
以当所述扫地机器人本体处于拖地模式时，控制所述中扫结构的底端面收纳于所述中扫吸尘口中，
当所述扫地机器人本体处于刷地模式时，控制所述中扫结构的底端面伸出所述中扫吸尘口。"""


def main() -> None:
    state = RequiredFeatureExtractionInput(
        claim_text=CLAIM_TEXT,
        abstract_text="具备拖地功能的扫地机器人包括拖布结构、地刷结构、中扫结构以及升降控制结构。",
        invention_content="通过升降控制结构在拖地模式和刷地模式之间控制中扫结构收纳或伸出。",
        invention_point="通过升降控制结构使具备拖地功能的扫地机器人在不同清洁模式下切换中扫结构位置",
        primary_product_object="具备拖地功能的扫地机器人",
        search_product_objects=["扫地机器人", "拖地扫地机器人"],
    )
    required, optional, excluded, _ = _fallback_features(state)
    required_texts = {feature["text"] for feature in required}

    assert "拖地" in required_texts, f"missing required feature 拖地: {required}"
    assert "升降" in required_texts, f"missing required feature 升降: {required}"
    assert "中扫" in {feature["text"] for feature in optional}, f"missing optional feature 中扫: {optional}"
    assert "安装位" in excluded, f"missing excluded generic term 安装位: {excluded}"

    skeleton = _build_required_feature_keywords(
        required,
        state.primary_product_object,
        state.search_product_objects,
        [],
    )
    context_extensions = _build_context_extension_keywords(
        ["宠物毛发"],
        [],
        state.primary_product_object,
        state.search_product_objects,
        [],
    )
    noisy_model_keywords = [
        {
            "keyword_text": "懒人扫地机器人",
            "keyword_type": "scenario_based",
            "combination_pattern": "人群型",
            "confidence": 0.8,
        },
        {
            "keyword_text": "中扫升降扫地机器人",
            "keyword_type": "invention_based",
            "combination_pattern": "特征组合型",
            "confidence": 0.86,
        },
    ]
    final_keywords = _apply_guardrails(
        skeleton + noisy_model_keywords + context_extensions,
        required_features=required,
        excluded_generic_terms=excluded,
    )
    keyword_texts = [item["keyword_text"] for item in final_keywords]

    expected = {
        "扫地机器人",
        "拖地",
        "升降",
        "拖地扫地机器人",
        "升降扫地机器人",
        "拖地升降扫地机器人",
        "中扫升降扫地机器人",
        "宠物毛发扫地机器人",
    }
    missing = expected.difference(keyword_texts)
    assert not missing, f"missing expected keywords {missing}; got {keyword_texts}"
    assert "懒人扫地机器人" not in keyword_texts, f"unconstrained audience keyword leaked: {keyword_texts}"

    print("required keyword strategy ok")
    print(keyword_texts)


if __name__ == "__main__":
    main()
