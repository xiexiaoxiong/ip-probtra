"""
关键词筛选节点
职责：筛选关键词，排除宽泛、无法体现核心发明点的、以及纯专利书面语的关键词
"""
import os
import json
import re
import ast
from jinja2 import Template
from langchain_core.runnables import RunnableConfig
from langgraph.runtime import Runtime
from coze_coding_utils.runtime_ctx.context import Context
from coze_coding_dev_sdk import LLMClient
from langchain_core.messages import SystemMessage, HumanMessage
from graphs.state import KeywordFilteringInput, KeywordFilteringOutput


def keyword_filtering_node(
    state: KeywordFilteringInput,
    config: RunnableConfig,
    runtime: Runtime[Context]
) -> KeywordFilteringOutput:
    """
    title: 关键词筛选
    desc: 筛选关键词，排除宽泛、无法体现核心发明点的、以及纯专利书面语的关键词
    integrations: 大语言模型
    """
    ctx = runtime.context
    
    # 如果没有核心术语，返回空列表
    if not state.core_terms or len(state.core_terms) == 0:
        return KeywordFilteringOutput(
            filtered_core_terms=[],
            filter_log="无关键词需要筛选"
        )
    
    # 读取 LLM 配置
    cfg_file = os.path.join(os.getenv("COZE_WORKSPACE_PATH"), config['metadata']['llm_cfg'])
    with open(cfg_file, 'r', encoding='utf-8') as fd:
        _cfg = json.load(fd)
    
    llm_config = _cfg.get("config", {})
    sp = _cfg.get("sp", "")
    up = _cfg.get("up", "")
    
    # 准备关键词数据
    core_term_texts = [term.get("text", "") for term in state.core_terms if term.get("text")]
    
    # 使用 Jinja2 渲染提示词
    up_tpl = Template(up)
    user_prompt = up_tpl.render({
        "core_terms": "、".join(core_term_texts) if core_term_texts else "无",
        "invention_point": state.invention_point or "未提供"
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
        temperature=llm_config.get("temperature", 0.3),
        max_completion_tokens=llm_config.get("max_completion_tokens", 2000)
    )
    
    # 提取文本内容
    content = response.content
    if isinstance(content, str):
        result_text = content.strip()
    elif isinstance(content, list):
        text_parts = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                text_parts.append(item.get("text", ""))
            elif isinstance(item, str):
                text_parts.append(item)
        result_text = " ".join(text_parts).strip()
    else:
        result_text = str(content).strip()
    
    # 解析筛选结果
    filtered_core_terms = []
    filter_log = ""

    def _normalize_key(key: str) -> str:
        return re.sub(r'[\s\-]+', '_', str(key or '').strip().lower())

    def _extract_jsonish_blocks(text: str) -> list[str]:
        cleaned = re.sub(r"```(?:json)?", "", text, flags=re.IGNORECASE).replace("```", "")
        blocks: list[str] = []
        for pattern in (r'\{[\s\S]*\}', r'\[[\s\S]*\]'):
            match = re.search(pattern, cleaned)
            if match:
                blocks.append(match.group(0).strip())
        blocks.append(cleaned.strip())
        return [b for b in blocks if b]

    def _parse_jsonish(text: str):
        for candidate in _extract_jsonish_blocks(text):
            try:
                return json.loads(candidate)
            except Exception:
                pass
            try:
                return ast.literal_eval(candidate)
            except Exception:
                pass
            try:
                fixed = re.sub(r",\s*([}\]])", r"\1", candidate)
                return json.loads(fixed)
            except Exception:
                pass
        return None

    def _coerce_terms(obj) -> list[dict]:
        terms: list[dict] = []
        if obj is None:
            return terms

        if isinstance(obj, dict):
            normalized = {_normalize_key(k): v for k, v in obj.items()}
            for k in ("filtered_core_terms", "filtered_terms", "core_terms", "terms", "keywords"):
                if k in normalized:
                    obj = normalized[k]
                    break
            else:
                return terms

        if isinstance(obj, list):
            for item in obj:
                if isinstance(item, str):
                    text = item.strip()
                    if text:
                        terms.append(
                            {
                                "text": text,
                                "category": "unknown",
                                "source_location": "coerced",
                                "confidence": 0.7,
                            }
                        )
                    continue
                if isinstance(item, dict):
                    normalized_item = {_normalize_key(k): v for k, v in item.items()}
                    text = str(
                        normalized_item.get("text")
                        or normalized_item.get("term")
                        or normalized_item.get("keyword_text")
                        or normalized_item.get("keyword")
                        or ""
                    ).strip()
                    if not text:
                        continue
                    confidence_raw = (
                        normalized_item.get("confidence")
                        or normalized_item.get("confidence_score")
                        or normalized_item.get("score")
                        or 0.8
                    )
                    try:
                        confidence = float(confidence_raw)
                    except Exception:
                        confidence = 0.8
                    terms.append(
                        {
                            "text": text,
                            "category": str(normalized_item.get("category") or "unknown"),
                            "source_location": str(
                                normalized_item.get("source_location")
                                or normalized_item.get("source")
                                or normalized_item.get("reason")
                                or ""
                            ),
                            "confidence": confidence,
                        }
                    )
            return terms

        return terms
    
    try:
        parsed = _parse_jsonish(result_text)
        filtered_core_terms = _coerce_terms(parsed)
        if isinstance(parsed, dict):
            normalized = {_normalize_key(k): v for k, v in parsed.items()}
            log_val = normalized.get("filter_log") or normalized.get("log") or normalized.get("message") or ""
            if log_val:
                filter_log = str(log_val)
        
    except json.JSONDecodeError as e:
        filter_log = f"解析筛选结果失败: {str(e)}"
    except Exception as e:
        filter_log = f"处理筛选结果时发生错误: {str(e)}"

    if not filtered_core_terms and core_term_texts:
        filtered_core_terms = [
            {
                "text": term,
                "category": "unknown",
                "source_location": "fallback",
                "confidence": 0.6,
            }
            for term in core_term_texts[:12]
            if term
        ]
        if not filter_log:
            filter_log = "筛选结果为空，已回退为直接使用原始核心术语"
    
    return KeywordFilteringOutput(
        filtered_core_terms=filtered_core_terms,
        filter_log=filter_log
    )
