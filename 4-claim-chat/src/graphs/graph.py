from langgraph.graph import StateGraph, END
from graphs.state import (
    GlobalState,
    GraphInput,
    GraphOutput
)
from graphs.nodes.parse_and_fetch_node import parse_and_fetch_node
from graphs.nodes.decompose_claim_node import decompose_claim_node
from graphs.nodes.compare_products_loop_node import compare_products_loop_node
from graphs.nodes.write_feishu_results_node import write_feishu_results_node


# ============ 主图编排 ============

builder = StateGraph(GlobalState, input_schema=GraphInput, output_schema=GraphOutput)

# 添加节点
builder.add_node("parse_and_fetch", parse_and_fetch_node)
builder.add_node("decompose_claim", decompose_claim_node, metadata={"type": "agent", "llm_cfg": "config/decompose_claim_llm_cfg.json"})
builder.add_node("compare_products_loop", compare_products_loop_node, metadata={"type": "looparray"})
builder.add_node("write_feishu_results", write_feishu_results_node)

# 设置入口点
builder.set_entry_point("parse_and_fetch")

# 添加边
builder.add_edge("parse_and_fetch", "decompose_claim")
builder.add_edge("decompose_claim", "compare_products_loop")
builder.add_edge("compare_products_loop", "write_feishu_results")
builder.add_edge("write_feishu_results", END)

# 编译主图
main_graph = builder.compile()
