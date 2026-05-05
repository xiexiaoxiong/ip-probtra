"""
专利解析模块主图编排

工作流职责：
- 将原始专利文本转化为结构化专利数据
- 不对专利保护范围做任何实质性理解或判断

工作流节点：
1. file_read_node: 读取专利文档（PDF/TXT/HTML）
2. file_error_check: 检查文件读取错误
3. structure_identify_node: 识别文档结构（Agent节点，使用LLM）
4. claims_parse_node: 解析权利要求（Agent节点，使用LLM）
5. figure_extract_node: 提取附图并上传对象存储
6. database_save_node: 保存解析结果到数据库
7. feishu_save_node: 保存解析结果到飞书多维表格
8. structured_output_node: 生成结构化输出

严格禁止：
- 总结专利保护范围
- 提炼"发明点""创新点"
- 判断权利要求的技术效果
- 判断是否为功能性限定
- 合并或简化权利要求语言
"""
from langgraph.graph import StateGraph, END
from langchain_core.runnables import RunnableConfig
from langgraph.runtime import Runtime

from graphs.state import (
    GlobalState,
    GraphInput,
    GraphOutput,
    FileReadInput,
    ErrorCheckInput,
    ErrorCheckOutput,
    StructureIdentifyInput,
    ClaimsParseInput,
    FigureExtractInput,
    DatabaseSaveInput,
    StructuredOutputInput
)

from graphs.nodes.file_read_node import file_read_node
from graphs.nodes.error_check_node import error_check_node, should_continue_on_error
from graphs.nodes.structure_identify_node import structure_identify_node
from graphs.nodes.claims_parse_node import claims_parse_node
from graphs.nodes.figure_extract_node import figure_extract_node
from graphs.nodes.database_save_node import database_save_node
from graphs.nodes.structured_output_node import structured_output_node


# 创建状态图
builder = StateGraph(
    GlobalState,
    input_schema=GraphInput,
    output_schema=GraphOutput
)

# ==================== 添加节点 ====================

# 1. 文件读取节点
builder.add_node("file_read_node", file_read_node)

# 2. 文件读取错误检查节点（条件分支）
builder.add_node("file_error_check", error_check_node)

# 3. 文档结构识别节点（Agent节点，使用LLM）
builder.add_node(
    "structure_identify_node",
    structure_identify_node,
    metadata={
        "type": "agent",
        "llm_cfg": "config/structure_identify_llm_cfg.json"
    }
)

# 4. 权利要求解析节点（Agent节点，使用LLM）
builder.add_node(
    "claims_parse_node",
    claims_parse_node,
    metadata={
        "type": "agent",
        "llm_cfg": "config/claims_parse_llm_cfg.json"
    }
)

# 5. 附图提取节点
builder.add_node("figure_extract_node", figure_extract_node)

# 6. 数据库保存节点
builder.add_node("database_save_node", database_save_node)

# 7. 结构化输出节点
builder.add_node("structured_output_node", structured_output_node)


# ==================== 添加边 ====================

# 设置入口点
builder.set_entry_point("file_read_node")

# 文件读取 -> 错误检查
builder.add_edge("file_read_node", "file_error_check")

# 错误检查 -> 条件分支
# 如果有致命错误，结束工作流
# 如果没有致命错误，继续文档结构识别
builder.add_conditional_edges(
    source="file_error_check",
    path=should_continue_on_error,
    path_map={
        "致命错误": END,
        "继续解析": "structure_identify_node"
    }
)

# 文档结构识别 -> 并行执行权利要求解析和附图提取
builder.add_edge("structure_identify_node", "claims_parse_node")
builder.add_edge("structure_identify_node", "figure_extract_node")

# 并行分支汇聚 -> 数据库保存
# claims_parse_node 和 figure_extract_node 都完成后，执行数据库保存
builder.add_edge(["claims_parse_node", "figure_extract_node"], "database_save_node")

# 数据库保存 -> 结构化输出
builder.add_edge("database_save_node", "structured_output_node")

# 结构化输出 -> 结束
builder.add_edge("structured_output_node", END)


# ==================== 编译图 ====================
main_graph = builder.compile()
