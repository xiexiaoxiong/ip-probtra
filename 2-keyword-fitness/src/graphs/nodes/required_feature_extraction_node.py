"""
必要检索特征识别节点
职责：从独立权利要求和说明书上下文中识别主检索骨架需要覆盖的必要特征
"""
import ast
import json
import os
import re
from typing import Any

from jinja2 import Template
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langgraph.runtime import Runtime

from coze_coding_dev_sdk import LLMClient
from coze_coding_utils.runtime_ctx.context import Context
from graphs.state import RequiredFeatureExtractionInput, RequiredFeatureExtractionOutput


GENERIC_TERMS = [
    "本体",
    "壳体",
    "外壳",
    "底部",
    "安装位",
    "吸尘口",
    "中扫吸尘口",
    "轮子",
    "滚轮",
    "连接件",
    "固定件",
    "支架",
    "控制装置",
    "驱动装置",
]

FEATURE_SUFFIXES = [
    "控制结构",
    "控制装置",
    "控制机构",
    "控制",
    "结构",
    "装置",
    "机构",
    "组件",
    "部件",
    "模式",
    "功能",
    "单元",
    "模块",
]

FEATURE_PREFIXES = [
    "可",
    "自动",
    "自适应",
    "智能",
]


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", "", str(text or ""))


def _feature_key(feature: dict[str, Any]) -> str:
    return _normalize_text(str(feature.get("text") or feature.get("keyword") or "")).lower()


def _extract_text_content(content: object) -> str:
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        text_parts: list[str] = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                text_parts.append(item.get("text", ""))
            elif isinstance(item, str):
                text_parts.append(item)
        return " ".join(text_parts).strip()
    return str(content).strip()


def _extract_jsonish(text: str) -> Any:
    cleaned = re.sub(r"```(?:json)?", "", str(text or ""), flags=re.IGNORECASE).replace("```", "")
    for pattern in (r"\{[\s\S]*\}", r"\[[\s\S]*\]"):
        match = re.search(pattern, cleaned)
        if not match:
            continue
        candidate = match.group(0)
        for parser in (json.loads, ast.literal_eval):
            try:
                return parser(candidate)
            except Exception:
                continue
    return None


def _coerce_feature(item: object, default_type: str) -> dict[str, Any] | None:
    if isinstance(item, str):
        text = item.strip()
        if not text:
            return None
        return {
            "text": text,
            "type": default_type,
            "reason": "模型输出",
            "source": "模型输出",
            "confidence": 0.75,
        }
    if not isinstance(item, dict):
        return None
    text = str(item.get("text") or item.get("feature") or item.get("keyword") or "").strip()
    if not text:
        return None
    confidence_raw = item.get("confidence") or item.get("confidence_score") or 0.8
    try:
        confidence = float(confidence_raw)
    except Exception:
        confidence = 0.8
    return {
        "text": text,
        "type": str(item.get("type") or default_type),
        "reason": str(item.get("reason") or item.get("why_required") or ""),
        "source": str(item.get("source") or item.get("source_location") or ""),
        "confidence": confidence,
    }


def _coerce_result(parsed: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str], str]:
    if isinstance(parsed, list):
        parsed = {"required_features": parsed}
    if not isinstance(parsed, dict):
        return [], [], [], ""

    required = [
        feature
        for item in parsed.get("required_features", []) or []
        if (feature := _coerce_feature(item, "REQUIRED_FEATURE"))
    ]
    optional = [
        feature
        for item in parsed.get("optional_features", []) or []
        if (feature := _coerce_feature(item, "OPTIONAL_FEATURE"))
    ]
    excluded = [
        str(term).strip()
        for term in parsed.get("excluded_generic_terms", []) or []
        if str(term).strip()
    ]
    log = str(parsed.get("analysis_log") or parsed.get("required_feature_log") or "").strip()
    required, optional = _canonicalize_features(required, optional)
    return required, optional, excluded, log


def _is_generic_or_object_term(text: str) -> bool:
    normalized = _normalize_text(text)
    if not normalized:
        return True
    if normalized in {_normalize_text(term) for term in GENERIC_TERMS}:
        return True
    if any(marker in normalized for marker in ["机器人", "设备", "系统", "装置", "产品", "器材"]) and len(normalized) >= 4:
        return True
    return False


def _strip_feature_affixes(text: str) -> str:
    cleaned = _normalize_text(text)
    cleaned = re.sub(r"^所述", "", cleaned)
    cleaned = re.sub(r"^(以及|及|和|与|并|该|所述)", "", cleaned)
    cleaned = re.sub(r"(以及|及|和|与).*$", "", cleaned)
    for prefix in FEATURE_PREFIXES:
        if cleaned.startswith(prefix) and len(cleaned) > len(prefix) + 1:
            cleaned = cleaned[len(prefix):]
            break
    changed = True
    while changed:
        changed = False
        for suffix in FEATURE_SUFFIXES:
            if cleaned.endswith(suffix) and len(cleaned) > len(suffix) + 1:
                cleaned = cleaned[: -len(suffix)]
                changed = True
                break
    return cleaned.strip()


def _canonical_feature_text(text: str) -> str:
    cleaned = _strip_feature_affixes(text)
    if not cleaned:
        return ""
    if _is_generic_or_object_term(cleaned):
        return ""
    if len(cleaned) > 6:
        match = re.search(r"([\u4e00-\u9fff]{2,4})(?:方式|过程|状态|动作|效果)?$", cleaned)
        if match:
            cleaned = match.group(1)
    if len(cleaned) < 2 or len(cleaned) > 6:
        return ""
    return cleaned


def _extract_feature_candidates(text: str) -> list[tuple[str, bool, str]]:
    normalized = _normalize_text(text)
    candidates: list[tuple[str, bool, str]] = []
    patterns = [
        (r"具备([\u4e00-\u9fff]{2,8})功能", True, "功能限定"),
        (r"具有([\u4e00-\u9fff]{2,8})功能", True, "功能限定"),
        (r"处于([\u4e00-\u9fff]{2,8})模式", False, "模式限定"),
        (r"([\u4e00-\u9fff]{2,8})控制(?:结构|装置|机构|组件)?", True, "控制限定"),
        (r"([\u4e00-\u9fff]{2,6}?)(?:结构|装置|机构|组件)", False, "结构限定"),
        (r"([\u4e00-\u9fff]{2,8})(?:移动|收纳|伸出|切换|锁定|解锁|调节|检测|拆装)", True, "动作限定"),
    ]
    for pattern, required_hint, source in patterns:
        for match in re.finditer(pattern, normalized):
            candidate = match.group(1).strip()
            if candidate:
                candidates.append((candidate, required_hint, source))
    return candidates


def _extract_claim_subject(claim_text: str) -> str:
    normalized = _normalize_text(claim_text)
    match = re.search(r"(?:^\d+[.、:：])?一种([^，。,；;:：]{2,40}?)(?:，?其特征在于|包括|包含)", normalized)
    return match.group(1).strip() if match else ""


def _split_claim_feature_phrases(claim_text: str) -> list[tuple[str, bool, str]]:
    normalized = re.sub(r"\s+", "", claim_text)
    candidates = _extract_feature_candidates(normalized)
    chunks = re.split(r"[；;。]|以及|和|与|、", normalized)
    for chunk in chunks:
        if not chunk or len(chunk) < 2:
            continue
        if any(marker in chunk for marker in ["其特征在于", "包括", "包含"]):
            chunk = re.split(r"其特征在于|包括|包含", chunk)[-1]
        if any(marker in chunk for marker in ["控制", "活动", "移动", "收纳", "伸出", "切换", "安装", "检测", "调节", "锁定", "拆装"]):
            candidates.append((chunk[:24], False, "权利要求片段"))
    return candidates[:8]


def _canonicalize_features(
    required: list[dict[str, Any]],
    optional: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    canonical_required: list[dict[str, Any]] = []
    canonical_optional: list[dict[str, Any]] = []
    required_seen: set[str] = set()
    optional_seen: set[str] = set()

    def add_feature(target: list[dict[str, Any]], seen: set[str], feature: dict[str, Any], text: str) -> None:
        key = _normalize_text(text).lower()
        if not key or key in seen:
            return
        seen.add(key)
        target.append({**feature, "text": text})

    for feature in required:
        raw_text = str(feature.get("text", ""))
        text = _canonical_feature_text(raw_text)
        if text and "模式" not in _normalize_text(raw_text):
            add_feature(canonical_required, required_seen, feature, text)
        elif text:
            add_feature(canonical_optional, optional_seen, {**feature, "type": "OPTIONAL_FEATURE"}, text)

    for feature in optional:
        text = _canonical_feature_text(str(feature.get("text", "")))
        if not text or _normalize_text(text).lower() in required_seen:
            continue
        add_feature(canonical_optional, optional_seen, feature, text)

    return canonical_required, canonical_optional


def _fallback_features(state: RequiredFeatureExtractionInput) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str], str]:
    claim_text = _normalize_text(state.claim_text)
    context_text = _normalize_text(
        "\n".join(
            [
                state.claim_text,
                state.abstract_text,
                state.invention_content,
                state.background_tech,
                state.dependent_claims_text,
                state.invention_point,
            ]
        )
    )

    required: list[dict[str, Any]] = []
    optional: list[dict[str, Any]] = []

    def add_required(text: str, reason: str, confidence: float = 0.86) -> None:
        required.append(
            {
                "text": text,
                "type": "REQUIRED_FEATURE",
                "reason": reason,
                "source": "规则兜底",
                "confidence": confidence,
            }
        )

    def add_optional(text: str, reason: str, confidence: float = 0.72) -> None:
        optional.append(
            {
                "text": text,
                "type": "OPTIONAL_FEATURE",
                "reason": reason,
                "source": "规则兜底",
                "confidence": confidence,
            }
        )

    subject = _extract_claim_subject(state.claim_text)
    subject_words = set(re.findall(r"[\u4e00-\u9fff]{2,4}", subject))
    for phrase, required_hint, source in _split_claim_feature_phrases(state.claim_text):
        text = _canonical_feature_text(phrase)
        if not text or text in subject_words:
            continue
        if required_hint and any(text in _normalize_text(field) for field in [state.invention_point, state.abstract_text, state.invention_content]):
            add_required(text, f"独立权利要求{source}且摘要/发明点中重复出现：{phrase}")
        else:
            add_optional(text, f"独立权利要求{source}的补充检索特征：{phrase}")

    excluded = [term for term in GENERIC_TERMS if term in context_text]
    required, optional = _canonicalize_features(required, optional)
    return required, optional, excluded, "规则兜底识别必要检索特征"


def _merge_features(
    model_required: list[dict[str, Any]],
    model_optional: list[dict[str, Any]],
    model_excluded: list[str],
    fallback_required: list[dict[str, Any]],
    fallback_optional: list[dict[str, Any]],
    fallback_excluded: list[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    required: list[dict[str, Any]] = []
    seen_required: set[str] = set()
    fallback_optional_keys = {_feature_key(feature) for feature in fallback_optional}
    for feature in fallback_required + model_required:
        key = _feature_key(feature)
        if not key or key in seen_required:
            continue
        if feature in model_required and key in fallback_optional_keys:
            continue
        seen_required.add(key)
        required.append(feature)

    optional: list[dict[str, Any]] = []
    seen_optional = set(seen_required)
    for feature in fallback_optional + model_optional:
        key = _feature_key(feature)
        if not key or key in seen_optional:
            continue
        seen_optional.add(key)
        optional.append(feature)

    excluded: list[str] = []
    seen_excluded: set[str] = set()
    for term in list(model_excluded) + list(fallback_excluded):
        normalized = _normalize_text(term)
        if not normalized or normalized in seen_excluded:
            continue
        seen_excluded.add(normalized)
        excluded.append(str(term).strip())

    return required[:6], optional[:8], excluded[:20]


def required_feature_extraction_node(
    state: RequiredFeatureExtractionInput,
    config: RunnableConfig,
    runtime: Runtime[Context],
) -> RequiredFeatureExtractionOutput:
    """
    title: 必要检索特征识别
    desc: 识别必须进入关键词骨架的主特征，并排除主客体天然包含的泛词
    integrations: 大语言模型
    """
    fallback_required, fallback_optional, fallback_excluded, fallback_log = _fallback_features(state)
    model_required: list[dict[str, Any]] = []
    model_optional: list[dict[str, Any]] = []
    model_excluded: list[str] = []
    model_log = ""

    try:
        cfg_file = os.path.join(os.getenv("COZE_WORKSPACE_PATH"), config["metadata"]["llm_cfg"])
        with open(cfg_file, "r", encoding="utf-8") as fd:
            _cfg = json.load(fd)

        llm_config = _cfg.get("config", {})
        sp = _cfg.get("sp", "")
        up = _cfg.get("up", "")

        user_prompt = Template(up).render(
            {
                "claim_text": state.claim_text,
                "abstract_text": state.abstract_text,
                "invention_content": state.invention_content,
                "background_tech": state.background_tech,
                "dependent_claims_text": state.dependent_claims_text,
                "invention_point": state.invention_point,
                "primary_product_object": state.primary_product_object or "未识别",
                "search_product_objects": "、".join(state.search_product_objects) if state.search_product_objects else "无",
            }
        )

        client = LLMClient(ctx=runtime.context)
        response = client.invoke(
            messages=[SystemMessage(content=sp), HumanMessage(content=user_prompt)],
            model=llm_config.get("model", "glm-5-0-260211"),
            temperature=llm_config.get("temperature", 0.2),
            max_completion_tokens=llm_config.get("max_completion_tokens", 2500),
        )

        parsed = _extract_jsonish(_extract_text_content(response.content))
        model_required, model_optional, model_excluded, model_log = _coerce_result(parsed)
    except Exception as error:
        model_log = f"LLM必要特征识别失败，使用规则兜底: {error}"

    required, optional, excluded = _merge_features(
        model_required,
        model_optional,
        model_excluded,
        fallback_required,
        fallback_optional,
        fallback_excluded,
    )

    logs = [log for log in [model_log, fallback_log] if log]
    return RequiredFeatureExtractionOutput(
        required_features=required,
        optional_features=optional,
        excluded_generic_terms=excluded,
        required_feature_log="；".join(logs),
    )
