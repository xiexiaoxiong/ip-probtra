"""
产品客体提取节点
职责：提取主客体与检索落地客体，兼顾发明点锚定和电商检索可落地性
"""
import os
import json
import re
from jinja2 import Template
from langchain_core.runnables import RunnableConfig
from langgraph.runtime import Runtime
from coze_coding_utils.runtime_ctx.context import Context
from coze_coding_dev_sdk import LLMClient
from langchain_core.messages import SystemMessage, HumanMessage
from graphs.state import ProductObjectExtractionInput, ProductObjectExtractionOutput


def product_object_extraction_node(
    state: ProductObjectExtractionInput,
    config: RunnableConfig,
    runtime: Runtime[Context]
) -> ProductObjectExtractionOutput:
    """
    title: 产品客体提取
    desc: 提取主客体和检索落地客体；主客体优先锚定专利对象，补充客体仅用于检索展开
    integrations: 大语言模型
    """
    ctx = runtime.context
    
    if not any(str(value or "").strip() for value in [state.claim_text, state.technical_field, state.invention_content]):
        return ProductObjectExtractionOutput(
            primary_product_object="",
            search_product_objects=[],
            product_object=[],
        )
    
    # 读取 LLM 配置
    cfg_file = os.path.join(os.getenv("COZE_WORKSPACE_PATH"), config['metadata']['llm_cfg'])
    with open(cfg_file, 'r', encoding='utf-8') as fd:
        _cfg = json.load(fd)
    
    llm_config = _cfg.get("config", {})
    sp = _cfg.get("sp", "")
    up = _cfg.get("up", "")
    
    # 使用 Jinja2 渲染提示词
    up_tpl = Template(up)
    user_prompt = up_tpl.render({
        "claim_text": state.claim_text if state.claim_text else "（未提供）",
        "technical_field": state.technical_field,
        "invention_content": state.invention_content if state.invention_content else "（未提供）"
    })
    
    # 初始化 LLM 客户端
    client = LLMClient(ctx=ctx)
    
    # 调用 LLM
    messages = [
        SystemMessage(content=sp),
        HumanMessage(content=user_prompt)
    ]
    
    response = client.invoke(
        messages=messages,
        model=llm_config.get("model", "glm-5-0-260211"),
        temperature=llm_config.get("temperature", 0.2),
        max_completion_tokens=llm_config.get("max_completion_tokens", 800)
    )
    
    # 提取文本内容
    content = response.content
    raw_text = ""
    if isinstance(content, str):
        raw_text = content.strip()
    elif isinstance(content, list):
        text_parts = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                text_parts.append(item.get("text", ""))
            elif isinstance(item, str):
                text_parts.append(item)
        raw_text = " ".join(text_parts).strip()
    else:
        raw_text = str(content).strip()
    
    primary_product_object, search_product_objects = _parse_product_objects(raw_text)
    search_product_objects = _filter_grounded_objects(
        search_product_objects,
        [state.claim_text, state.technical_field, state.invention_content],
        primary_product_object,
    )
    combined_objects = _merge_product_objects(primary_product_object, search_product_objects)

    return ProductObjectExtractionOutput(
        primary_product_object=primary_product_object,
        search_product_objects=search_product_objects,
        product_object=combined_objects,
    )


def _parse_product_objects(raw_text: str) -> tuple[str, list[str]]:
    """从 LLM 返回文本中解析主客体与检索落地客体"""
    json_match = re.search(r'\{[\s\S]*\}', raw_text)
    if json_match:
        try:
            result = json.loads(json_match.group(0))
            if isinstance(result, dict):
                primary = str(result.get("primary_product_object", "")).strip()
                raw_search_objects = result.get("search_product_objects", [])
                if not isinstance(raw_search_objects, list):
                    raw_search_objects = []
                search_objects = [
                    str(item).strip()
                    for item in raw_search_objects
                    if isinstance(item, str) and item.strip()
                ]
                return primary, _dedupe_strings(search_objects, primary)
        except json.JSONDecodeError:
            pass

    json_array_match = re.search(r'\[[\s\S]*\]', raw_text)
    if json_array_match:
        try:
            result = json.loads(json_array_match.group(0))
            if isinstance(result, list):
                objects = [str(item).strip() for item in result if isinstance(item, str) and item.strip()]
                primary = objects[0] if objects else ""
                return primary, _dedupe_strings(objects[1:], primary)
        except json.JSONDecodeError:
            pass

    cleaned = raw_text.strip()
    cleaned = re.sub(r'^[^：]*[：:]', '', cleaned).strip()
    parts = re.split(r'[,，、\n]', cleaned)
    objects = [p.strip().strip('"\'""''') for p in parts if p.strip()]
    primary = objects[0] if objects else ""
    return primary, _dedupe_strings(objects[1:], primary)


def _dedupe_strings(values: list[str], primary: str = "") -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        normalized = re.sub(r'\s+', '', value)
        if not normalized:
            continue
        if primary and normalized == re.sub(r'\s+', '', primary):
            continue
        if normalized in seen:
            continue
        seen.add(normalized)
        result.append(value)
    return result


def _merge_product_objects(primary_product_object: str, search_product_objects: list[str]) -> list[str]:
    merged: list[str] = []
    if primary_product_object:
        merged.append(primary_product_object)
    merged.extend(_dedupe_strings(search_product_objects, primary_product_object))
    return merged


def _filter_grounded_objects(values: list[str], contexts: list[str], primary: str) -> list[str]:
    joined_context = "".join(str(item or "") for item in contexts)
    normalized_context = re.sub(r"\s+", "", joined_context)
    grounded: list[str] = []
    for value in values:
        normalized_value = re.sub(r"\s+", "", value)
        if not normalized_value:
            continue
        if primary and normalized_value == re.sub(r"\s+", "", primary):
            continue
        if normalized_value in normalized_context:
            grounded.append(value)
    return _dedupe_strings(grounded, primary)
