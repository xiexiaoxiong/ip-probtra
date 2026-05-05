"""
关键词筛选节点
职责：筛选关键词，排除宽泛、无法体现核心发明点的、以及纯专利书面语的关键词
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
    
    try:
        # 提取 JSON 内容
        json_match = re.search(r'\{[\s\S]*\}', result_text)
        if json_match:
            result_data = json.loads(json_match.group(0))
            
            # 解析筛选后的核心术语
            if "filtered_core_terms" in result_data:
                for item in result_data["filtered_core_terms"]:
                    if isinstance(item, dict) and item.get("text"):
                        filtered_core_terms.append({
                            "text": item.get("text", ""),
                            "category": item.get("category", "unknown"),
                            "source_location": item.get("source_location", ""),
                            "confidence": item.get("confidence", 0.8)
                        })
            
            # 获取筛选日志
            filter_log = result_data.get("filter_log", "")
        
    except json.JSONDecodeError as e:
        filter_log = f"解析筛选结果失败: {str(e)}"
    except Exception as e:
        filter_log = f"处理筛选结果时发生错误: {str(e)}"
    
    return KeywordFilteringOutput(
        filtered_core_terms=filtered_core_terms,
        filter_log=filter_log
    )
