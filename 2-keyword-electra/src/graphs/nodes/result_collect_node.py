"""
结果收集节点
职责：收集当前记录处理的关键词结果，并更新索引以处理下一条记录
"""
from typing import List
from langchain_core.runnables import RunnableConfig
from langgraph.runtime import Runtime
from coze_coding_utils.runtime_ctx.context import Context
from graphs.state import ResultCollectInput, ResultCollectOutput


def result_collect_node(
    state: ResultCollectInput,
    config: RunnableConfig,
    runtime: Runtime[Context]
) -> ResultCollectOutput:
    """
    title: 结果收集
    desc: 收集当前记录的关键词，更新索引，判断是否继续循环
    """
    ctx = runtime.context
    
    current_keywords = state.keywords
    all_keywords = state.all_keywords or []
    record_index = state.current_record_index
    claim_index = state.current_claim_index
    total_records = state.total_records
    
    # 将当前关键词添加到总列表
    new_all_keywords = all_keywords + current_keywords
    
    # 计算下一个索引
    # 注意：这里简化处理，每次处理一条权利要求后直接跳到下一条记录
    # 如果需要处理同一记录的多条权利要求，需要更复杂的逻辑
    next_record_index = record_index + 1
    next_claim_index = 0  # 重置权利要求索引
    
    # 判断是否还有更多记录
    has_more = next_record_index < total_records
    
    return ResultCollectOutput(
        all_keywords=new_all_keywords,
        current_record_index=next_record_index,
        current_claim_index=next_claim_index,
        has_more_records=has_more
    )
