"""
关键词生成模块主图编排
"""
from typing import List
from langgraph.graph import StateGraph, END
from langchain_core.runnables import RunnableConfig
from langgraph.runtime import Runtime
from coze_coding_utils.runtime_ctx.context import Context

from graphs.state import (
    GlobalState,
    GraphInput,
    GraphOutput,
    FeishuDataLoaderInput,
    FeishuDataLoaderOutput,
    RecordProcessLoopInput,
    RecordProcessLoopOutput,
    KeywordWriterInput,
    KeywordWriterOutput
)

from graphs.nodes.feishu_data_loader_node import feishu_data_loader_node
from graphs.nodes.keyword_writer_node import keyword_writer_node
from graphs.loop_graph import record_process_subgraph


# ==================== 包装节点 ====================


def feishu_data_loader_wrapper(
    state: FeishuDataLoaderInput,
    config: RunnableConfig,
    runtime: Runtime[Context]
) -> FeishuDataLoaderOutput:
    """
    title: 数据库数据加载
    desc: 从Postgres加载专利主记录、权利要求和附图，并转换为原有关键词流程可消费的结构
    integrations: Postgres数据库
    """
    return feishu_data_loader_node(state, config, runtime)


def record_process_loop_node(
    state: RecordProcessLoopInput,
    config: RunnableConfig,
    runtime: Runtime[Context]
) -> RecordProcessLoopOutput:
    """
    title: 记录循环处理
    desc: 循环处理每条专利记录，展开的子环节包括：记录分发→输入验证→产品客体提取→发明点提炼→关键词提取→同义词生成→结果组装→结果收集
    integrations: 大语言模型
    """
    ctx = runtime.context
    
    # 准备子图输入
    total_records = len(state.integrated_patent_records)
    subgraph_input = {
        "integrated_patent_records": state.integrated_patent_records,
        "field_mapping": state.field_mapping,
        "current_record_index": state.current_record_index,
        "all_keywords": state.all_keywords,
        "total_records": total_records,
        "has_more_records": state.current_record_index < total_records
    }
    
    # 计算所需的递归限制
    # 每条记录大约需要 12 个节点步骤（record_dispatch → input_validation → product_object_extraction → invention_point_extraction → keyword_extraction → invention_point_refinement → keyword_filtering → scenario_audience_inference → keyword_combination → result_assembly → result_collect → 循环判断）
    # 加上初始步骤和结束步骤，设置足够的缓冲
    nodes_per_record = 14
    buffer = 50
    required_limit = max(200, total_records * nodes_per_record + buffer)
    
    # 更新配置，增加递归限制
    subgraph_config = dict(config) if config else {}
    subgraph_config["recursion_limit"] = required_limit
    
    # 调用子图处理所有记录
    result = record_process_subgraph.invoke(
        subgraph_input,
        config=subgraph_config
    )
    
    # 返回更新后的状态
    return RecordProcessLoopOutput(
        all_keywords=result.get("all_keywords", []),
        processed_count=result.get("current_record_index", 0)
    )


def keyword_writer_wrapper(
    state: KeywordWriterInput,
    config: RunnableConfig,
    runtime: Runtime[Context]
) -> KeywordWriterOutput:
    """
    title: 关键词写入
    desc: 将所有关键词写入Postgres
    integrations: Postgres数据库
    """
    result = keyword_writer_node(state, config, runtime)
    return result


# ==================== 条件判断函数 ====================


def should_continue_after_load(state: GlobalState) -> str:
    """加载后路由"""
    if state.exception_type and state.exception_type != "":
        return "异常结束"
    if not state.integrated_patent_records or len(state.integrated_patent_records) == 0:
        return "无数据"
    return "开始处理"


# ==================== 主图编排 ====================

builder = StateGraph(
    GlobalState,
    input_schema=GraphInput,
    output_schema=GraphOutput
)

# 添加节点
builder.add_node("feishu_data_loader", feishu_data_loader_wrapper)
builder.add_node(
    "record_process_loop",
    record_process_loop_node,
    metadata={"type": "looparray"}
)
builder.add_node("keyword_writer", keyword_writer_wrapper)

# 设置入口点
builder.set_entry_point("feishu_data_loader")

# 加载后路由
builder.add_conditional_edges(
    source="feishu_data_loader",
    path=should_continue_after_load,
    path_map={
        "开始处理": "record_process_loop",
        "无数据": END,
        "异常结束": END
    }
)

# 循环处理 → 写入
builder.add_edge("record_process_loop", "keyword_writer")

# 写入 → 结束
builder.add_edge("keyword_writer", END)

# 编译图
main_graph = builder.compile()
