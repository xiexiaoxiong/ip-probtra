"""
记录处理子图（循环处理每条记录）
所有节点展开在子图中，方便独立调试每个环节的 Prompt
"""
from langgraph.graph import StateGraph, END
from langchain_core.runnables import RunnableConfig
from langgraph.runtime import Runtime

from graphs.state import (
    GlobalState,
    RecordDispatchInput,
    RecordDispatchOutput,
    InputValidationInput,
    InputValidationOutput,
    ProductObjectExtractionInput,
    ProductObjectExtractionOutput,
    InventionPointExtractionInput,
    InventionPointExtractionOutput,
    KeywordExtractionInput,
    KeywordExtractionOutput,
    InventionPointRefinementInput,
    InventionPointRefinementOutput,
    KeywordFilteringInput,
    KeywordFilteringOutput,
    ScenarioAudienceInferenceInput,
    ScenarioAudienceInferenceOutput,
    KeywordCombinationInput,
    KeywordCombinationOutput,
    ResultAssemblyInput,
    ResultAssemblyOutput,
    ResultCollectInput,
    ResultCollectOutput
)

from graphs.nodes.record_dispatch_node import record_dispatch_node
from graphs.nodes.input_validation_node import input_validation_node
from graphs.nodes.product_object_extraction_node import product_object_extraction_node
from graphs.nodes.invention_point_extraction_node import invention_point_extraction_node
from graphs.nodes.keyword_extraction_node import keyword_extraction_node
from graphs.nodes.invention_point_refinement_node import invention_point_refinement_node
from graphs.nodes.keyword_filtering_node import keyword_filtering_node
from graphs.nodes.scenario_audience_inference_node import scenario_audience_inference_node
from graphs.nodes.keyword_combination_node import keyword_combination_node
from graphs.nodes.result_assembly_node import result_assembly_node
from graphs.nodes.result_collect_node import result_collect_node


# ==================== 包装节点 ====================

def record_dispatch_wrapper(
    state: RecordDispatchInput,
    config: RunnableConfig,
    runtime: Runtime
) -> RecordDispatchOutput:
    """记录分发"""
    return record_dispatch_node(state, config, runtime)


def input_validation_wrapper(
    state: InputValidationInput,
    config: RunnableConfig,
    runtime: Runtime
) -> InputValidationOutput:
    """输入验证"""
    return input_validation_node(state, config, runtime)


def product_object_extraction_wrapper(
    state: ProductObjectExtractionInput,
    config: RunnableConfig,
    runtime: Runtime
) -> ProductObjectExtractionOutput:
    """产品客体提取"""
    return product_object_extraction_node(state, config, runtime)


def invention_point_extraction_wrapper(
    state: InventionPointExtractionInput,
    config: RunnableConfig,
    runtime: Runtime
) -> InventionPointExtractionOutput:
    """发明点提炼"""
    return invention_point_extraction_node(state, config, runtime)


def keyword_extraction_wrapper(
    state: KeywordExtractionInput,
    config: RunnableConfig,
    runtime: Runtime
) -> KeywordExtractionOutput:
    """关键词提取"""
    return keyword_extraction_node(state, config, runtime)


def invention_point_refinement_wrapper(
    state: InventionPointRefinementInput,
    config: RunnableConfig,
    runtime: Runtime
) -> InventionPointRefinementOutput:
    """发明点特征词精炼"""
    return invention_point_refinement_node(state, config, runtime)


def keyword_filtering_wrapper(
    state: KeywordFilteringInput,
    config: RunnableConfig,
    runtime: Runtime
) -> KeywordFilteringOutput:
    """关键词筛选"""
    return keyword_filtering_node(state, config, runtime)


def scenario_audience_inference_wrapper(
    state: ScenarioAudienceInferenceInput,
    config: RunnableConfig,
    runtime: Runtime
) -> ScenarioAudienceInferenceOutput:
    """人群场景词推断"""
    return scenario_audience_inference_node(state, config, runtime)


def keyword_combination_wrapper(
    state: KeywordCombinationInput,
    config: RunnableConfig,
    runtime: Runtime
) -> KeywordCombinationOutput:
    """关键词组合"""
    return keyword_combination_node(state, config, runtime)


def result_assembly_wrapper(
    state: ResultAssemblyInput,
    config: RunnableConfig,
    runtime: Runtime
) -> ResultAssemblyOutput:
    """结果组装"""
    return result_assembly_node(state, config, runtime)


def result_collect_wrapper(
    state: ResultCollectInput,
    config: RunnableConfig,
    runtime: Runtime
) -> ResultCollectOutput:
    """结果收集"""
    return result_collect_node(state, config, runtime)


# ==================== 条件判断函数 ====================

def should_continue_after_dispatch(state: GlobalState) -> str:
    """分发后路由"""
    # 如果 has_more_records 为 False，说明已经没有更多记录了
    if not state.has_more_records:
        return "子图结束"
    # 如果当前索引已经超出范围，也结束
    if state.current_record_index >= state.total_records:
        return "子图结束"
    return "继续验证"


def should_continue_after_validation(state: GlobalState) -> str:
    """验证后路由"""
    if not state.is_valid:
        return "跳过该记录"
    return "继续提取产品客体"


def should_continue_loop(state: GlobalState) -> str:
    """循环判断"""
    if state.has_more_records:
        return "继续循环"
    return "子图结束"


# ==================== 子图编排 ====================

sub_builder = StateGraph(GlobalState)

# 添加节点（所有环节展开）
sub_builder.add_node("record_dispatch", record_dispatch_wrapper)
sub_builder.add_node("input_validation", input_validation_wrapper)
sub_builder.add_node(
    "product_object_extraction",
    product_object_extraction_wrapper,
    metadata={"type": "agent", "llm_cfg": "config/product_object_extraction_llm_cfg.json"}
)
sub_builder.add_node(
    "invention_point_extraction",
    invention_point_extraction_wrapper,
    metadata={"type": "agent", "llm_cfg": "config/invention_point_extraction_llm_cfg.json"}
)
sub_builder.add_node(
    "keyword_extraction",
    keyword_extraction_wrapper,
    metadata={"type": "agent", "llm_cfg": "config/keyword_extraction_llm_cfg.json"}
)
sub_builder.add_node(
    "invention_point_refinement",
    invention_point_refinement_wrapper,
    metadata={"type": "agent", "llm_cfg": "config/invention_point_refinement_llm_cfg.json"}
)
sub_builder.add_node(
    "keyword_filtering",
    keyword_filtering_wrapper,
    metadata={"type": "agent", "llm_cfg": "config/keyword_filtering_llm_cfg.json"}
)
sub_builder.add_node(
    "scenario_audience_inference",
    scenario_audience_inference_wrapper,
    metadata={"type": "agent", "llm_cfg": "config/scenario_audience_inference_llm_cfg.json"}
)
sub_builder.add_node(
    "keyword_combination",
    keyword_combination_wrapper,
    metadata={"type": "agent", "llm_cfg": "config/keyword_combination_llm_cfg.json"}
)
sub_builder.add_node("result_assembly", result_assembly_wrapper)
sub_builder.add_node("result_collect", result_collect_wrapper)

# 设置入口点
sub_builder.set_entry_point("record_dispatch")

# 分发后路由
sub_builder.add_conditional_edges(
    source="record_dispatch",
    path=should_continue_after_dispatch,
    path_map={
        "继续验证": "input_validation",
        "子图结束": END
    }
)

# 验证后路由
sub_builder.add_conditional_edges(
    source="input_validation",
    path=should_continue_after_validation,
    path_map={
        "继续提取产品客体": "product_object_extraction",
        "跳过该记录": "result_collect"
    }
)

# 产品客体提取 → 发明点提炼
sub_builder.add_edge("product_object_extraction", "invention_point_extraction")

# 发明点提炼 → 关键词提取
sub_builder.add_edge("invention_point_extraction", "keyword_extraction")

# 关键词提取 → 发明点特征词精炼
sub_builder.add_edge("keyword_extraction", "invention_point_refinement")

# 发明点特征词精炼 → 关键词筛选
sub_builder.add_edge("invention_point_refinement", "keyword_filtering")

# 关键词筛选 → 人群场景词推断
sub_builder.add_edge("keyword_filtering", "scenario_audience_inference")

# 人群场景词推断 → 关键词组合
sub_builder.add_edge("scenario_audience_inference", "keyword_combination")

# 关键词组合 → 结果组装
sub_builder.add_edge("keyword_combination", "result_assembly")

# 结果组装 → 结果收集
sub_builder.add_edge("result_assembly", "result_collect")

# 循环判断
sub_builder.add_conditional_edges(
    source="result_collect",
    path=should_continue_loop,
    path_map={
        "继续循环": "record_dispatch",
        "子图结束": END
    }
)

# 编译子图
record_process_subgraph = sub_builder.compile()
