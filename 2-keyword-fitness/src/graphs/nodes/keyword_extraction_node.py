"""
关键词提取节点
职责：从权利要求原文中提取核心术语
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
from graphs.state import KeywordExtractionInput, KeywordExtractionOutput


def keyword_extraction_node(
    state: KeywordExtractionInput,
    config: RunnableConfig,
    runtime: Runtime[Context]
) -> KeywordExtractionOutput:
    """
    title: 关键词提取
    desc: 从权利要求原文中提取名词性、技术性词汇作为核心术语
    integrations: 大语言模型
    """
    ctx = runtime.context
    
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
        "claim_text": state.claim_text,
        "invention_point": state.invention_point,
        "patent_holder": state.patent_holder
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
    
    # 解析 JSON 格式的关键词列表
    core_terms = []
    try:
        # 尝试提取 JSON 部分
        json_match = re.search(r'\[.*\]', result_text, re.DOTALL)
        if json_match:
            json_str = json_match.group(0)
            core_terms = json.loads(json_str)
        else:
            # 如果没有 JSON 格式，按行分割
            lines = [line.strip() for line in result_text.split('\n') if line.strip()]
            for i, line in enumerate(lines):
                # 去除序号
                term = re.sub(r'^\d+[\.\、\s]+', '', line)
                if term:
                    core_terms.append({
                        "text": term,
                        "source": "权利要求原文",
                        "type": "CORE_TERM"
                    })
    except json.JSONDecodeError:
        # JSON 解析失败，按行处理
        lines = [line.strip() for line in result_text.split('\n') if line.strip()]
        for line in lines:
            term = re.sub(r'^\d+[\.\、\s]+', '', line)
            if term:
                core_terms.append({
                    "text": term,
                    "source": "权利要求原文",
                    "type": "CORE_TERM"
                })
    
    return KeywordExtractionOutput(core_terms=core_terms)
