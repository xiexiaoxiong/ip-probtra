from typing import Literal, Optional, List, Dict, Any
from pydantic import BaseModel, Field


# ============ 中间数据类型 ============

class IndependentClaim(BaseModel):
    """独立权利要求"""
    claim_id: str = Field(..., description="权利要求编号，如1、7、10")
    claim_text: str = Field(..., description="权利要求全文")


class FeatureItem(BaseModel):
    """技术特征单元"""
    feature_id: str = Field(..., description="技术特征编号，如1A、1B、7A")
    feature_text: str = Field(..., description="权利要求技术特征原文")
    claim_id: str = Field(default="", description="所属独立权利要求编号")


class ProductInfo(BaseModel):
    """商品信息"""
    name: str = Field(default="", description="商品名称")
    description: str = Field(default="", description="商品文字描述")
    images: List[str] = Field(default=[], description="商品图片URL列表")
    raw_data: Dict[str, Any] = Field(default={}, description="飞书原始记录数据")


class FeatureAnalysisItem(BaseModel):
    """LLM对单个特征的分析结果"""
    feature_id: str = Field(..., description="技术特征编号")
    evidence: str = Field(default="", description="商品文字或图片中的证据引用")
    reason: str = Field(default="", description="作出分析结论的解释")
    reasoning_type: str = Field(..., description="推理类型枚举")
    evidence_images: List[str] = Field(default=[], description="能体现该特征的商品图片URL列表")


class FeatureComparisonItem(BaseModel):
    """单个特征的比对结果"""
    feature_id: str = Field(..., description="技术特征编号")
    feature_text: str = Field(..., description="权利要求技术特征原文")
    evidence: str = Field(default="", description="商品文字或图片中的证据引用")
    comparison_result: str = Field(..., description="比对结果：MATCH/NO_MATCH/UNCERTAIN")
    reason: str = Field(default="", description="作出比对结论的解释")
    reasoning_type: str = Field(..., description="推理类型枚举")
    claim_id: str = Field(default="", description="所属独立权利要求编号")
    evidence_images: List[str] = Field(default=[], description="能体现该特征的商品图片URL列表")


class ProductComparisonResult(BaseModel):
    """单个商品的比对结果（包含多个独立权利要求的比对）"""
    product_name: str = Field(..., description="商品名称")
    features: List[FeatureComparisonItem] = Field(default=[], description="所有独立权利要求的特征比对结果列表")


# ============ 全局状态 ============

class GlobalState(BaseModel):
    """全局状态定义"""
    patent_record_id: int = Field(default=0, description="专利解析主记录ID")
    analysis_session_id: str = Field(default="", description="分析会话ID")
    run_id: str = Field(default="", description="模块4任务运行ID")
    feishu_url: str = Field(default="", description="飞书多维表格URL")
    app_token: str = Field(default="", description="飞书多维表格app_token")
    table_id: str = Field(default="", description="飞书多维表格table_id")
    independent_claims: List[Dict[str, str]] = Field(default=[], description="独立权利要求列表 [{claim_id, claim_text}]")
    specification_text: str = Field(default="", description="专利说明书全文（用于理解权利要求）")
    specification_images: List[str] = Field(default=[], description="说明书附图URL列表")
    features: List[Dict[str, str]] = Field(default=[], description="所有独立权利要求拆解后的技术特征列表（含claim_id）")
    products: List[Dict[str, Any]] = Field(default=[], description="商品信息列表")
    all_comparison_results: List[Dict[str, Any]] = Field(default=[], description="所有商品的比对结果")
    error_status: str = Field(default="", description="异常状态信息")
    claim_compare_run_id: int = Field(default=0, description="比对运行记录ID")
    table_urls: List[str] = Field(default=[], description="创建的飞书子表格URL列表")


# ============ 图输入输出 ============

class GraphInput(BaseModel):
    """工作流输入"""
    patent_record_id: int = Field(..., description="专利解析主记录ID")
    analysis_session_id: str = Field(default="", description="分析会话ID")
    run_id: str = Field(default="", description="模块4任务运行ID")
    claim_compare_run_id: int = Field(default=0, description="预创建的比对运行记录ID")


class GraphOutput(BaseModel):
    """工作流输出"""
    claim_compare_run_id: int = Field(default=0, description="比对运行记录ID")
    run_id: str = Field(default="", description="模块4任务运行ID")
    all_comparison_results: List[Dict[str, Any]] = Field(default=[], description="所有商品的比对结果")
    result_summary: str = Field(default="", description="结果摘要")
    table_urls: List[str] = Field(default=[], description="创建的飞书子表格URL列表")


# ============ 节点出入参定义 ============

class ParseAndFetchInput(BaseModel):
    """解析URL并获取飞书数据节点的输入"""
    patent_record_id: int = Field(..., description="专利解析主记录ID")
    analysis_session_id: str = Field(default="", description="分析会话ID")
    run_id: str = Field(default="", description="模块4任务运行ID")
    claim_compare_run_id: int = Field(default=0, description="预创建的比对运行记录ID")


class ParseAndFetchOutput(BaseModel):
    """解析URL并获取飞书数据节点的输出"""
    patent_record_id: int = Field(default=0, description="专利解析主记录ID")
    run_id: str = Field(default="", description="模块4任务运行ID")
    claim_compare_run_id: int = Field(default=0, description="比对运行记录ID")
    app_token: str = Field(..., description="飞书多维表格app_token")
    table_id: str = Field(default="", description="飞书多维表格table_id")
    independent_claims: List[Dict[str, str]] = Field(default=[], description="独立权利要求列表")
    specification_text: str = Field(default="", description="专利说明书全文")
    specification_images: List[str] = Field(default=[], description="说明书附图URL列表")
    products: List[Dict[str, Any]] = Field(default=[], description="商品信息列表")
    error_status: str = Field(default="", description="异常状态信息")


class DecomposeClaimInput(BaseModel):
    """权利要求拆解节点的输入"""
    independent_claims: List[Dict[str, str]] = Field(..., description="独立权利要求列表")
    specification_text: str = Field(default="", description="专利说明书全文，用于辅助理解权利要求")
    specification_images: List[str] = Field(default=[], description="说明书附图URL列表")


class DecomposeClaimOutput(BaseModel):
    """权利要求拆解节点的输出"""
    features: List[Dict[str, str]] = Field(..., description="拆解后的技术特征列表（含claim_id）")


class CompareLoopInput(BaseModel):
    """商品比对循环节点的输入"""
    features: List[Dict[str, str]] = Field(..., description="技术特征列表（含claim_id）")
    products: List[Dict[str, Any]] = Field(..., description="商品信息列表")
    specification_text: str = Field(default="", description="专利说明书全文，传递给子图用于辅助理解技术特征")


class CompareLoopOutput(BaseModel):
    """商品比对循环节点的输出"""
    all_comparison_results: List[Dict[str, Any]] = Field(..., description="所有商品的比对结果")


class WriteResultsInput(BaseModel):
    """写入飞书结果节点的输入"""
    patent_record_id: int = Field(..., description="专利解析主记录ID")
    analysis_session_id: str = Field(default="", description="分析会话ID")
    run_id: str = Field(default="", description="模块4任务运行ID")
    claim_compare_run_id: int = Field(default=0, description="预创建的比对运行记录ID")
    all_comparison_results: List[Dict[str, Any]] = Field(..., description="所有商品的比对结果")


class WriteResultsOutput(BaseModel):
    """写入飞书结果节点的输出"""
    claim_compare_run_id: int = Field(default=0, description="比对运行记录ID")
    run_id: str = Field(default="", description="模块4任务运行ID")
    result_summary: str = Field(..., description="结果摘要")
    table_urls: List[str] = Field(default=[], description="创建的飞书子表格URL列表")


# ============ 子图节点出入参 ============

class AnalyzeFeaturesInput(BaseModel):
    """特征分析节点（子图）的输入"""
    features: List[Dict[str, str]] = Field(..., description="技术特征列表（含claim_id）")
    product_data: Dict[str, Any] = Field(..., description="单个商品数据")
    specification_text: str = Field(default="", description="专利说明书全文，用于辅助理解技术特征含义")


class AnalyzeFeaturesOutput(BaseModel):
    """特征分析节点（子图）的输出"""
    raw_analysis: List[Dict[str, Any]] = Field(..., description="LLM原始分析结果列表")
    product_name: str = Field(default="", description="商品名称")


class ApplyRulesInput(BaseModel):
    """规则应用节点（子图）的输入"""
    raw_analysis: List[Dict[str, Any]] = Field(..., description="LLM原始分析结果列表")
    product_name: str = Field(default="", description="商品名称")
    features: List[Dict[str, str]] = Field(..., description="技术特征列表（用于补全feature_text和claim_id）")


class ApplyRulesOutput(BaseModel):
    """规则应用节点（子图）的输出"""
    comparison_result: Dict[str, Any] = Field(..., description="单个商品的比对结果")
