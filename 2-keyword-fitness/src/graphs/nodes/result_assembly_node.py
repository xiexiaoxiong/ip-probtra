"""
结果组装节点
职责：组装最终的关键词列表
"""
import uuid
from datetime import datetime
from langchain_core.runnables import RunnableConfig
from langgraph.runtime import Runtime
from coze_coding_utils.runtime_ctx.context import Context
from graphs.state import ResultAssemblyInput, ResultAssemblyOutput


def result_assembly_node(
    state: ResultAssemblyInput,
    config: RunnableConfig,
    runtime: Runtime[Context]
) -> ResultAssemblyOutput:
    """
    title: 结果组装
    desc: 将组合后的关键词列表组装成最终的关键词记录
    """
    ctx = runtime.context
    
    keywords = []
    created_at = datetime.now().isoformat()
    
    # 处理组合后的关键词
    for keyword in state.combined_keywords:
        keyword_id = f"KW-{uuid.uuid4().hex[:8]}"
        
        # 根据关键词类型设置字段
        keyword_type = keyword.get("keyword_type", "unknown")
        if keyword_type == "holder_based":
            keyword_type_display = "HOLDER_BASED"  # 权利要求人+同款+客体
        elif keyword_type == "invention_based":
            keyword_type_display = "INVENTION_BASED"  # 核心发明点+客体
        else:
            keyword_type_display = "COMBINED"
        
        keyword_item = {
            "keyword_id": keyword_id,
            "claim_id": state.claim_id,
            "keyword_text": keyword.get("keyword_text", ""),
            "keyword_type": keyword_type_display,
            "source_location": keyword.get("combination_pattern", ""),
            "generation_method": "KEYWORD_COMBINATION",
            "confidence_score": keyword.get("confidence", 0.8),
            "created_at": created_at
        }
        keywords.append(keyword_item)
    
    # 如果没有任何关键词，记录异常
    if len(keywords) == 0:
        return ResultAssemblyOutput(
            keywords=[{
                "keyword_id": f"KW-{uuid.uuid4().hex[:8]}",
                "claim_id": state.claim_id,
                "keyword_text": "",
                "keyword_type": "EMPTY",
                "source_location": "",
                "generation_method": "NONE",
                "confidence_score": None,
                "created_at": created_at
            }]
        )
    
    return ResultAssemblyOutput(keywords=keywords)
