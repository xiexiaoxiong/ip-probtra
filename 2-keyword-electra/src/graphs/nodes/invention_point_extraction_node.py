"""
发明点提炼节点
职责：从说明书和权利要求中提炼发明点
"""
import os
import json
from jinja2 import Template
from langchain_core.runnables import RunnableConfig
from langgraph.runtime import Runtime
from coze_coding_utils.runtime_ctx.context import Context
from coze_coding_dev_sdk import LLMClient
from langchain_core.messages import SystemMessage, HumanMessage
from graphs.state import InventionPointExtractionInput, InventionPointExtractionOutput


def invention_point_extraction_node(
    state: InventionPointExtractionInput,
    config: RunnableConfig,
    runtime: Runtime[Context]
) -> InventionPointExtractionOutput:
    """
    title: 发明点提炼
    desc: 从说明书内容中总结发明点，并标注来源位置
    integrations: 大语言模型
    """
    ctx = runtime.context
    
    # 空值防护：权利要求文本和发明内容都为空时，返回默认值
    has_claim_text = bool(state.claim_text and state.claim_text.strip())
    has_invention_content = bool(state.invention_content and state.invention_content.strip())
    
    if not has_claim_text and not has_invention_content:
        return InventionPointExtractionOutput(
            invention_point="",
            invention_point_source="",
            innovation_direction=""
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
        "claim_text": state.claim_text if has_claim_text else "（未提供权利要求文本，请从发明内容中提炼）",
        "invention_content": state.invention_content if has_invention_content else "（未提供）",
        "description_figures": state.description_figures if state.description_figures else "（未提供）"
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
    
    # 解析发明点、来源和创新方向（预期格式：发明点内容|来源位置|创新方向）
    # 从右向左分割，最多分3段，避免发明点内容中的|被误切割
    parts = result_text.rsplit("|", 2)
    invention_point = parts[0].strip() if len(parts) > 0 else result_text
    invention_point_source = parts[1].strip() if len(parts) > 1 else "说明书内容"
    innovation_direction = parts[2].strip() if len(parts) > 2 else ""
    
    return InventionPointExtractionOutput(
        invention_point=invention_point,
        invention_point_source=invention_point_source,
        innovation_direction=innovation_direction
    )
