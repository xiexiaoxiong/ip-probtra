"""
关键词组合节点
职责：以大模型为主生成最终检索关键词，代码仅做轻量护栏校验
"""
import json
import os
import re

from jinja2 import Template
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langgraph.runtime import Runtime

from coze_coding_dev_sdk import LLMClient
from coze_coding_utils.runtime_ctx.context import Context
from graphs.state import KeywordCombinationInput, KeywordCombinationOutput


LEGALISTIC_MARKERS = ["用于", "具备", "关于", "相关", "本体", "装置"]
ENTERPRISE_MARKERS = [
    "公司", "集团", "有限", "股份", "企业", "实业", "科技", "技术", "工业", "电子",
    "株式会社", "株式会社", "会社", "LLC", "INC", "CORP", "CORPORATION", "LTD", "LIMITED", "GMBH",
]
ENTERPRISE_SUFFIX_PATTERNS = [
    r"(股份)?有限公司$",
    r"有限责任公司$",
    r"股份公司$",
    r"集团有限公司$",
    r"集团公司$",
    r"科技有限公司$",
    r"技术有限公司$",
    r"电子有限公司$",
    r"实业有限公司$",
    r"公司$",
    r"集团$",
    r"企业$",
    r"株式会社$",
    r"会社$",
    r",?\s*INC\.?$",
    r",?\s*LLC$",
    r",?\s*LTD\.?$",
    r",?\s*LIMITED$",
    r",?\s*CORP\.?$",
    r",?\s*CORPORATION$",
    r",?\s*GMBH$",
]


def _normalize_keyword_text(text: str) -> str:
    return re.sub(r"\s+", "", str(text or "").strip().lower())


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


def _parse_combined_keywords(result_text: str) -> list[dict]:
    combined_keywords: list[dict] = []
    try:
        json_match = re.search(r"\[[\s\S]*\]", result_text)
        if json_match:
            result_list = json.loads(json_match.group(0))
            if isinstance(result_list, list):
                for item in result_list:
                    if isinstance(item, dict) and item.get("keyword_text"):
                        combined_keywords.append(
                            {
                                "keyword_text": item.get("keyword_text", ""),
                                "keyword_type": item.get("keyword_type", "unknown"),
                                "combination_pattern": item.get("combination_pattern", ""),
                                "confidence": item.get("confidence", 0.8),
                            }
                        )
                return combined_keywords
    except json.JSONDecodeError:
        pass

    lines = [line.strip() for line in result_text.split("\n") if line.strip()]
    for line in lines:
        combined_keywords.append(
            {
                "keyword_text": line,
                "keyword_type": "unknown",
                "combination_pattern": "text_extraction",
                "confidence": 0.6,
            }
        )
    return combined_keywords


def _is_enterprise_holder(patent_holder: str) -> bool:
    holder = str(patent_holder or "").strip()
    if not holder:
        return False
    upper_holder = holder.upper()
    return any(marker in holder or marker in upper_holder for marker in ENTERPRISE_MARKERS)


def _extract_holder_key_name(patent_holder: str) -> str:
    holder = str(patent_holder or "").strip()
    if not holder:
        return ""

    cleaned = holder.replace("（", "(").replace("）", ")")
    cleaned = re.sub(r"\(.*?\)", "", cleaned).strip()
    for pattern in ENTERPRISE_SUFFIX_PATTERNS:
        cleaned = re.sub(pattern, "", cleaned, flags=re.IGNORECASE).strip()

    cleaned = cleaned.strip(" ,，.。·")
    if not cleaned:
        return ""

    if re.search(r"[A-Za-z]", cleaned):
        tokens = re.split(r"[\s,，]+", cleaned)
        cleaned = tokens[0].strip() if tokens and tokens[0].strip() else cleaned

    cleaned = cleaned.strip(" ,，.。·")
    return cleaned[:12]


def _clean_object_text(text: str) -> str:
    cleaned = str(text or "").strip()
    if "扫地机器人" in cleaned:
        return "扫地机器人"
    cleaned = re.sub(r"^一种", "", cleaned)
    cleaned = re.sub(r"^(具备|具有).{1,12}?功能的", "", cleaned)
    cleaned = cleaned.strip(" ,，.。；;:：")
    return cleaned


def _select_holder_keyword_object(
    primary_object: str,
    search_objects: list[str],
    product_objects: list[str],
) -> str:
    candidates = [obj for obj in list(search_objects) + list(product_objects) + [primary_object] if str(obj).strip()]
    for obj in candidates:
        text = str(obj).strip()
        if not text:
            continue
        if any(marker in text for marker in LEGALISTIC_MARKERS):
            continue
        return _clean_object_text(text)
    return _clean_object_text(primary_object)


def _build_holder_based_keyword(
    patent_holder: str,
    primary_object: str,
    search_objects: list[str],
    product_objects: list[str],
) -> dict | None:
    if not _is_enterprise_holder(patent_holder):
        return None

    holder_key_name = _extract_holder_key_name(patent_holder)
    object_text = _select_holder_keyword_object(primary_object, search_objects, product_objects)
    if not holder_key_name or not object_text:
        return None

    return {
        "keyword_text": f"{holder_key_name}同款{object_text}",
        "keyword_type": "holder_based",
        "combination_pattern": "品牌同款型-补充生成",
        "confidence": 0.88,
    }


def _has_repeated_fragment(keyword_text: str) -> bool:
    normalized = _normalize_keyword_text(keyword_text)
    if not normalized:
        return False
    return bool(re.search(r"(.{2,8})\1", normalized))


def _violates_quality_guardrail(keyword_text: str, allow_short: bool = False) -> bool:
    cleaned = str(keyword_text or "").strip()
    normalized = _normalize_keyword_text(cleaned)
    if not normalized:
        return True
    min_length = 2 if allow_short else 3
    if len(normalized) < min_length or len(normalized) > 18:
        return True
    if any(sep in cleaned for sep in ["、", "，", ",", "/"]):
        return True
    if any(marker in cleaned for marker in LEGALISTIC_MARKERS) and len(normalized) > 10:
        return True
    if _has_repeated_fragment(cleaned):
        return True
    return False


def _feature_texts(features: list[dict]) -> list[str]:
    texts: list[str] = []
    for feature in features:
        if isinstance(feature, dict):
            text = str(feature.get("text", "")).strip()
        else:
            text = str(feature).strip()
        if text:
            texts.append(text)
    return texts


def _keyword_contains_any(keyword_text: str, terms: list[str]) -> bool:
    normalized = _normalize_keyword_text(keyword_text)
    return any(_normalize_keyword_text(term) in normalized for term in terms if term)


def _build_required_feature_keywords(
    required_features: list[dict],
    primary_object: str,
    search_objects: list[str],
    product_objects: list[str],
) -> list[dict]:
    object_text = _select_holder_keyword_object(primary_object, search_objects, product_objects)
    required_texts = _feature_texts(required_features)
    keywords: list[dict] = []

    if object_text:
        keywords.append(
            {
                "keyword_text": object_text,
                "keyword_type": "object_base",
                "combination_pattern": "主客体基础词",
                "confidence": 0.91,
            }
        )

    for text in required_texts:
        keywords.append(
            {
                "keyword_text": text,
                "keyword_type": "required_feature",
                "combination_pattern": "必要特征基础词",
                "confidence": 0.9,
            }
        )
        if object_text and _normalize_keyword_text(text) not in _normalize_keyword_text(object_text):
            keywords.append(
                {
                    "keyword_text": f"{text}{object_text}",
                    "keyword_type": "required_feature",
                    "combination_pattern": "必要特征+主客体",
                    "confidence": 0.91,
                }
            )

    if object_text and len(required_texts) >= 2:
        combined = "".join(required_texts[:3])
        keywords.append(
            {
                "keyword_text": f"{combined}{object_text}",
                "keyword_type": "required_feature",
                "combination_pattern": "多必要特征+主客体",
                "confidence": 0.93,
            }
        )

    return keywords


def _build_context_extension_keywords(
    scenario_words: list[str],
    audience_words: list[str],
    primary_object: str,
    search_objects: list[str],
    product_objects: list[str],
) -> list[dict]:
    object_text = _select_holder_keyword_object(primary_object, search_objects, product_objects)
    if not object_text:
        return []

    keywords: list[dict] = []
    for word in list(scenario_words)[:2]:
        text = str(word or "").strip()
        if not text:
            continue
        keywords.append(
            {
                "keyword_text": f"{text}{object_text}",
                "keyword_type": "scenario_extension",
                "combination_pattern": "场景扩展型-低优先级",
                "confidence": 0.68,
            }
        )
    for word in list(audience_words)[:2]:
        text = str(word or "").strip()
        if not text:
            continue
        keywords.append(
            {
                "keyword_text": f"{text}{object_text}",
                "keyword_type": "audience_extension",
                "combination_pattern": "人群扩展型-低优先级",
                "confidence": 0.64,
            }
        )
    return keywords


def _apply_guardrails(
    combined_keywords: list[dict],
    required_features: list[dict] | None = None,
    excluded_generic_terms: list[str] | None = None,
) -> list[dict]:
    processed: list[dict] = []
    seen: set[str] = set()
    required_texts = _feature_texts(required_features or [])
    excluded_norms = {_normalize_keyword_text(term) for term in (excluded_generic_terms or []) if term}

    for item in combined_keywords:
        keyword_text = str(item.get("keyword_text", "")).strip()
        keyword_type = str(item.get("keyword_type", "")).lower()
        pattern = str(item.get("combination_pattern", "")).lower()
        allow_short = keyword_type in {"required_feature", "object_base"}
        if _violates_quality_guardrail(keyword_text, allow_short=allow_short):
            continue

        normalized = _normalize_keyword_text(keyword_text)
        if normalized in excluded_norms:
            continue
        if any(term and term in normalized and normalized != term for term in excluded_norms):
            if keyword_type not in {"required_feature", "holder_based"}:
                continue
        if keyword_type in {"scenario_extension", "audience_extension"}:
            pass
        elif required_texts and ("人群" in pattern or "场景" in pattern or keyword_type in {"audience_based", "scenario_based"}):
            if not _keyword_contains_any(keyword_text, required_texts):
                continue
        if normalized in seen:
            continue
        seen.add(normalized)
        processed.append({**item, "keyword_text": keyword_text})

    return processed


def keyword_combination_node(
    state: KeywordCombinationInput,
    config: RunnableConfig,
    runtime: Runtime[Context]
) -> KeywordCombinationOutput:
    """
    title: 关键词组合
    desc: 以大语言模型为主组合关键词，代码仅负责轻量护栏校验
    integrations: 大语言模型
    """
    ctx = runtime.context

    if not state.filtered_core_terms:
        return KeywordCombinationOutput(combined_keywords=[])

    cfg_file = os.path.join(os.getenv("COZE_WORKSPACE_PATH"), config['metadata']['llm_cfg'])
    with open(cfg_file, 'r', encoding='utf-8') as fd:
        _cfg = json.load(fd)

    llm_config = _cfg.get("config", {})
    sp = _cfg.get("sp", "")
    up = _cfg.get("up", "")

    core_term_texts = [term.get("text", "") for term in state.filtered_core_terms if term.get("text")]

    up_tpl = Template(up)
    user_prompt = up_tpl.render({
        "core_terms": "、".join(core_term_texts) if core_term_texts else "无",
        "primary_product_object": state.primary_product_object or "未识别",
        "search_product_objects": "、".join(state.search_product_objects) if state.search_product_objects else "无",
        "product_object": "、".join(state.product_object) if state.product_object else "未识别",
        "patent_holder": state.patent_holder or "未知",
        "invention_point": state.invention_point or "未识别",
        "required_features": json.dumps(state.required_features, ensure_ascii=False),
        "optional_features": json.dumps(state.optional_features, ensure_ascii=False),
        "excluded_generic_terms": "、".join(state.excluded_generic_terms) if state.excluded_generic_terms else "无",
        "scenario_words": "、".join(state.scenario_words) if state.scenario_words else "无",
        "audience_words": "、".join(state.audience_words) if state.audience_words else "无",
    })

    client = LLMClient(ctx=ctx)
    response = client.invoke(
        messages=[
            SystemMessage(content=sp),
            HumanMessage(content=user_prompt),
        ],
        model=llm_config.get("model", "glm-5-0-260211"),
        temperature=llm_config.get("temperature", 0.3),
        max_completion_tokens=llm_config.get("max_completion_tokens", 3000),
    )

    result_text = _extract_text_content(response.content)
    parsed_keywords = _parse_combined_keywords(result_text)
    required_feature_keywords = _build_required_feature_keywords(
        state.required_features,
        state.primary_product_object,
        state.search_product_objects,
        state.product_object,
    )
    context_extension_keywords = _build_context_extension_keywords(
        state.scenario_words,
        state.audience_words,
        state.primary_product_object,
        state.search_product_objects,
        state.product_object,
    )
    parsed_keywords = required_feature_keywords + parsed_keywords + context_extension_keywords
    holder_based_keyword = _build_holder_based_keyword(
        state.patent_holder,
        state.primary_product_object,
        state.search_product_objects,
        state.product_object,
    )
    if holder_based_keyword:
        parsed_keywords.insert(0, holder_based_keyword)
    combined_keywords = _apply_guardrails(
        parsed_keywords,
        required_features=state.required_features,
        excluded_generic_terms=state.excluded_generic_terms,
    )

    return KeywordCombinationOutput(combined_keywords=combined_keywords)
