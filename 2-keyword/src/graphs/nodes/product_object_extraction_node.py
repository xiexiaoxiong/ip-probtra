"""
产品客体提取节点
职责：从技术领域中提取产品客体，支持多客体和说明书回溯逻辑
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
    desc: 从技术领域中识别产品客体；若技术领域过于宽泛则结合发明内容进一步明确，支持多个客体
    integrations: 大语言模型
    """
    ctx = runtime.context
    
    # 如果技术领域为空，返回空结果
    if not state.technical_field or state.technical_field.strip() == "":
        return ProductObjectExtractionOutput(product_object=[])
    
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
    
    # 解析 JSON 数组结果
    product_objects = _parse_product_objects(raw_text)
    
    return ProductObjectExtractionOutput(product_object=product_objects)


def _parse_product_objects(raw_text: str) -> list:
    """从 LLM 返回文本中解析产品客体列表"""
    # 尝试提取 JSON 数组
    json_match = re.search(r'\[.*?\]', raw_text, re.DOTALL)
    if json_match:
        json_str = json_match.group(0)
        try:
            result = json.loads(json_str)
            if isinstance(result, list):
                # 过滤空字符串和非字符串项
                return [str(item).strip() for item in result if isinstance(item, str) and item.strip()]
        except json.JSONDecodeError:
            pass
    
    # JSON 解析失败时，按逗号/顿号/换行分割
    cleaned = raw_text.strip()
    # 去掉可能的前缀说明
    cleaned = re.sub(r'^[^：]*[：:]', '', cleaned).strip()
    # 按常见分隔符拆分
    parts = re.split(r'[,，、\n]', cleaned)
    objects = [p.strip().strip('"\'""''') for p in parts if p.strip()]
    
    return objects
