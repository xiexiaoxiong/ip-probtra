import logging
from typing import List, Dict, Any
from langchain_core.runnables import RunnableConfig
from langgraph.runtime import Runtime
from coze_coding_utils.runtime_ctx.context import Context
from graphs.state import ApplyRulesInput, ApplyRulesOutput

logger = logging.getLogger(__name__)

# 推理类型到比对结果的映射规则
MATCH_REASONING_TYPES: set = {
    "文字直接公开",
    "从图片中看出",
    "结合文字和图片毫无疑义得出",
    "根据功能推导得出"
}

NO_MATCH_REASONING_TYPES: set = {
    "可判断不具有"
}

UNCERTAIN_REASONING_TYPE: str = "相关信息缺失"

# 明确缺失/不相同的关键词指示器（用于"相关信息缺失"类型的二次判断）
ABSENCE_INDICATORS: List[str] = [
    "明确不包含",
    "明确不具有",
    "明确缺失",
    "不相同",
    "与该特征不同",
    "明确不存在",
    "明确没有",
    "与该特征相反"
]


def _determine_comparison_result(reasoning_type: str, reason: str) -> str:
    """
    基于确定性规则判定比对结果。
    
    三级映射：
    - 匹配类（MATCH）：文字直接公开 / 从图片中看出 / 结合文字和图片毫无疑义得出 / 根据功能推导得出
    - 不匹配类（NO_MATCH）：可判断不具有
    - 不确定类（UNCERTAIN）：相关信息缺失（除非reason含明确缺失指示词→NO_MATCH）
    """
    if reasoning_type in MATCH_REASONING_TYPES:
        return "MATCH"

    if reasoning_type in NO_MATCH_REASONING_TYPES:
        return "NO_MATCH"

    if reasoning_type == UNCERTAIN_REASONING_TYPE:
        for indicator in ABSENCE_INDICATORS:
            if indicator in reason:
                return "NO_MATCH"
        return "UNCERTAIN"

    logger.warning(f"未知的reasoning_type: {reasoning_type}，默认为UNCERTAIN")
    return "UNCERTAIN"


def apply_rules_node(
    state: ApplyRulesInput,
    config: RunnableConfig,
    runtime: Runtime[Context]
) -> ApplyRulesOutput:
    """
    title: 应用比对规则
    desc: 基于确定性规则，根据LLM输出的reasoning_type判定每个技术特征的MATCH/NO_MATCH/UNCERTAIN结果
    """
    raw_analysis: List[Dict[str, Any]] = state.raw_analysis
    product_name: str = state.product_name
    features: List[Dict[str, str]] = state.features

    # 构建 feature_id -> feature_text / claim_id 的映射
    feature_text_map: Dict[str, str] = {}
    feature_claim_map: Dict[str, str] = {}
    for feat in features:
        fid: str = str(feat.get("feature_id", ""))
        ftext: str = str(feat.get("feature_text", ""))
        claim_id: str = str(feat.get("claim_id", ""))
        if fid:
            feature_text_map[fid] = ftext
            feature_claim_map[fid] = claim_id

    # 对每个分析结果应用规则
    comparison_features: List[Dict[str, Any]] = []
    for item in raw_analysis:
        fid = str(item.get("feature_id", ""))
        reasoning_type: str = str(item.get("reasoning_type", UNCERTAIN_REASONING_TYPE))
        reason: str = str(item.get("reason", ""))
        evidence: str = str(item.get("evidence", ""))
        evidence_images: List[str] = item.get("evidence_images", []) if isinstance(item.get("evidence_images"), list) else []

        comparison_result: str = _determine_comparison_result(reasoning_type, reason)

        feature_text: str = feature_text_map.get(fid, "")
        claim_id: str = feature_claim_map.get(fid, "")

        comparison_features.append({
            "feature_id": fid,
            "feature_text": feature_text,
            "evidence": evidence,
            "comparison_result": comparison_result,
            "reason": reason,
            "reasoning_type": reasoning_type,
            "claim_id": claim_id,
            "evidence_images": evidence_images
        })

    # 构建该商品的完整比对结果
    result: Dict[str, Any] = {
        "product_name": product_name,
        "features": comparison_features
    }

    return ApplyRulesOutput(comparison_result=result)
