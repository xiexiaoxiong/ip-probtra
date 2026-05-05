from typing import List, Dict, Any
from pydantic import BaseModel, Field
from langgraph.graph import StateGraph, END
from graphs.nodes.analyze_features_node import analyze_features_node
from graphs.nodes.apply_rules_node import apply_rules_node


# ============ 子图状态定义 ============

class LoopState(BaseModel):
    """商品特征比对子图的状态"""
    features: List[Dict[str, str]] = Field(default=[], description="技术特征列表（含claim_id）")
    product_data: Dict[str, Any] = Field(default={}, description="单个商品数据")
    raw_analysis: List[Dict[str, Any]] = Field(default=[], description="LLM原始分析结果")
    product_name: str = Field(default="", description="商品名称")
    comparison_result: Dict[str, Any] = Field(default={}, description="比对结果")
    specification_text: str = Field(default="", description="专利说明书全文，用于辅助理解技术特征含义")


# ============ 子图编排 ============

builder = StateGraph(LoopState)

# 添加节点
builder.add_node("analyze_features", analyze_features_node, metadata={"type": "agent", "llm_cfg": "config/analyze_features_llm_cfg.json"})
builder.add_node("apply_rules", apply_rules_node)

# 设置入口点
builder.set_entry_point("analyze_features")

# 添加边：分析 → 规则判定 → 结束
builder.add_edge("analyze_features", "apply_rules")
builder.add_edge("apply_rules", END)

# 编译子图
product_comparison_graph = builder.compile()
