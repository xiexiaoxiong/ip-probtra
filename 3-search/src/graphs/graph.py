"""
商品检索模块 - 主图编排（使用Coze工作流搜索）
"""
import uuid
import datetime
from langgraph.graph import StateGraph, END
from langchain_core.runnables import RunnableConfig
from langgraph.runtime import Runtime
from coze_coding_utils.runtime_ctx.context import Context

from graphs.state import (
    GlobalState,
    GraphInput,
    GraphOutput,
    GetKeywordsInput,
    CozeSearchInput,
    SaveResultsInput,
    EntryInput,
    EntryOutput,
    GetKeywordsWrapperInput,
    GetKeywordsWrapperOutput,
    CozeSearchWrapperInput,
    CozeSearchWrapperOutput,
    SaveResultsWrapperInput,
    SaveResultsWrapperOutput,
    ExitInput,
    ExitOutput
)

from graphs.nodes.get_keywords_node import get_keywords_node
from graphs.nodes.coze_search_node import coze_search_node
from graphs.nodes.save_results_node import save_results_node


def entry_node(
    state: EntryInput,
    config: RunnableConfig,
    runtime: Runtime[Context]
) -> EntryOutput:
    """
    title: 入口节点
    desc: 初始化全局状态，生成数据集ID和开始时间
    """
    product_dataset_id = f"DS_{datetime.datetime.now().strftime('%Y%m%d%H%M%S')}_{uuid.uuid4().hex[:8]}"
    retrieval_start_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    return EntryOutput(
        patent_record_id=state.patent_record_id,
        analysis_session_id=state.analysis_session_id,
        input_keywords=state.input_keywords,
        product_dataset_id=product_dataset_id,
        retrieval_start_time=retrieval_start_time
    )


def get_keywords_wrapper(
    state: GetKeywordsWrapperInput,
    config: RunnableConfig,
    runtime: Runtime[Context]
) -> GetKeywordsWrapperOutput:
    """
    title: 获取关键词
    desc: 从Postgres的keyword_records表读取关键词
    integrations: Postgres数据库
    """
    input_data = GetKeywordsInput(
        patent_record_id=state.patent_record_id,
        analysis_session_id=state.analysis_session_id,
        input_keywords=state.input_keywords
    )

    result = get_keywords_node(input_data, config, runtime)

    return GetKeywordsWrapperOutput(
        keywords=result.keywords,
        error_message=result.error_message
    )


def coze_search_wrapper(
    state: CozeSearchWrapperInput,
    config: RunnableConfig,
    runtime: Runtime[Context]
) -> CozeSearchWrapperOutput:
    """
    title: Coze工作流搜索商品
    desc: 调用Coze工作流API，传入关键词搜索商品
    integrations: Coze工作流API
    """
    input_data = CozeSearchInput(
        keywords=state.keywords
    )

    result = coze_search_node(input_data, config, runtime)

    return CozeSearchWrapperOutput(
        products=result.products,
        total_products_count=result.total_products_count,
        successful_keywords_count=result.successful_keywords_count,
        failed_keywords_count=result.failed_keywords_count,
        is_complete=result.is_complete,
        error_message=result.error_message
    )


def save_results_wrapper(
    state: SaveResultsWrapperInput,
    config: RunnableConfig,
    runtime: Runtime[Context]
) -> SaveResultsWrapperOutput:
    """
    title: 保存结果
    desc: 将商品数据保存到Postgres
    integrations: Postgres数据库
    """
    input_data = SaveResultsInput(
        patent_record_id=state.patent_record_id,
        analysis_session_id=state.analysis_session_id,
        products=state.products,
        product_dataset_id=state.product_dataset_id,
        retrieval_start_time=state.retrieval_start_time,
        successful_keywords_count=state.successful_keywords_count,
        failed_keywords_count=state.failed_keywords_count,
        is_complete=state.is_complete
    )

    result = save_results_node(input_data, config, runtime)

    return SaveResultsWrapperOutput(
        search_run_id=result.search_run_id,
        product_dataset_id=result.product_dataset_id,
        total_products_count=result.total_products_count,
        is_complete=result.is_complete,
        error_message=result.error_message
    )


def exit_node(
    state: ExitInput,
    config: RunnableConfig,
    runtime: Runtime[Context]
) -> ExitOutput:
    """
    title: 出口节点
    desc: 输出最终结果
    """
    return ExitOutput(
        search_run_id=state.search_run_id,
        product_dataset_id=state.product_dataset_id,
        total_products_count=state.total_products_count,
        is_complete=state.is_complete,
        error_message=state.error_message
    )


# 创建状态图
builder = StateGraph(GlobalState, input_schema=GraphInput, output_schema=GraphOutput)

# 添加节点
builder.add_node("entry", entry_node)
builder.add_node("get_keywords", get_keywords_wrapper)
builder.add_node("coze_search", coze_search_wrapper)
builder.add_node("save_results", save_results_wrapper)
builder.add_node("exit", exit_node)

# 设置入口点
builder.set_entry_point("entry")

# 添加边
builder.add_edge("entry", "get_keywords")
builder.add_edge("get_keywords", "coze_search")
builder.add_edge("coze_search", "save_results")
builder.add_edge("save_results", "exit")
builder.add_edge("exit", END)

# 编译图
main_graph = builder.compile()