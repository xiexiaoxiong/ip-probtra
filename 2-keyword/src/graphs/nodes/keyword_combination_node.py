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


def _has_repeated_fragment(keyword_text: str) -> bool:
    normalized = _normalize_keyword_text(keyword_text)
    if not normalized:
        return False
    return bool(re.search(r"(.{2,8})\1", normalized))


def _violates_quality_guardrail(keyword_text: str) -> bool:
    cleaned = str(keyword_text or "").strip()
    normalized = _normalize_keyword_text(cleaned)
    if not normalized:
        return True
    if len(normalized) < 3 or len(normalized) > 18:
        return True
    if any(sep in cleaned for sep in ["、", "，", ",", "/"]):
        return True
    if any(marker in cleaned for marker in LEGALISTIC_MARKERS) and len(normalized) > 10:
        return True
    if _has_repeated_fragment(cleaned):
        return True
    return False


def _apply_guardrails(
    combined_keywords: list[dict],
) -> list[dict]:
    processed: list[dict] = []
    seen: set[str] = set()

    for item in combined_keywords:
        keyword_text = str(item.get("keyword_text", "")).strip()
        if _violates_quality_guardrail(keyword_text):
            continue

        normalized = _normalize_keyword_text(keyword_text)
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
    combined_keywords = _apply_guardrails(parsed_keywords)

    return KeywordCombinationOutput(combined_keywords=combined_keywords)
