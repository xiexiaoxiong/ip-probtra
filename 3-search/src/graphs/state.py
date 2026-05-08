"""
商品检索模块 - 状态定义（使用Coze工作流搜索）
"""
import os
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field


# ==================== 图输入输出定义 ====================

class GraphInput(BaseModel):
    """工作流的输入"""
    patent_record_id: int = Field(..., description="专利解析主记录ID")
    analysis_session_id: str = Field(default="", description="分析会话ID")
    input_keywords: Optional[List[str]] = Field(default=None, description="搜索关键词列表（可选，如不提供则从表格读取）")


class GraphOutput(BaseModel):
    """工作流的输出"""
    product_dataset_id: str = Field(..., description="本次检索数据集唯一标识")
    search_run_id: int = Field(default=0, description="商品检索运行记录ID")
    total_products_count: int = Field(default=0, description="检索到的商品总数")
    is_complete: bool = Field(default=True, description="检索是否完整")
    error_message: str = Field(default="", description="错误信息")


# ==================== 全局状态定义 ====================

class GlobalState(BaseModel):
    """全局状态定义"""
    # 输入参数
    patent_record_id: int = Field(default=0, description="专利解析主记录ID")
    analysis_session_id: str = Field(default="", description="分析会话ID")
    input_keywords: Optional[List[str]] = Field(default=None, description="用户输入的关键词")

    # 关键词数据
    keywords: List[str] = Field(default=[], description="关键词列表")

    # Coze 搜索结果
    products: List[Dict[str, Any]] = Field(default=[], description="商品列表")
    total_products_count: int = Field(default=0, description="商品总数")
    coze_raw_response: Optional[str] = Field(default=None, description="Coze工作流原始响应")

    # 检索元信息
    product_dataset_id: str = Field(default="", description="数据集ID")
    retrieval_start_time: str = Field(default="", description="检索开始时间")
    successful_keywords_count: int = Field(default=0, description="成功检索的关键词数量")
    failed_keywords_count: int = Field(default=0, description="失败的关键词数量")
    is_complete: bool = Field(default=True, description="检索是否完整")

    # 结果存储
    search_run_id: int = Field(default=0, description="商品检索运行记录ID")
    error_message: str = Field(default="", description="错误信息")


# ==================== 节点输入输出定义 ====================

class GetKeywordsInput(BaseModel):
    """获取关键词节点的输入"""
    patent_record_id: int = Field(..., description="专利解析主记录ID")
    analysis_session_id: str = Field(default="", description="分析会话ID")
    input_keywords: Optional[List[str]] = Field(default=None, description="用户输入的关键词")


class GetKeywordsOutput(BaseModel):
    """获取关键词节点的输出"""
    keywords: List[str] = Field(default=[], description="关键词列表")
    error_message: str = Field(default="", description="错误信息")


class CozeSearchInput(BaseModel):
    """Coze搜索节点的输入"""
    keywords: List[str] = Field(default=[], description="关键词列表")


class CozeSearchOutput(BaseModel):
    """Coze搜索节点的输出"""
    products: List[Dict[str, Any]] = Field(default=[], description="商品列表")
    total_products_count: int = Field(default=0, description="商品总数")
    successful_keywords_count: int = Field(default=0, description="成功检索的关键词数量")
    failed_keywords_count: int = Field(default=0, description="失败的关键词数量")
    is_complete: bool = Field(default=True, description="检索是否完整")
    error_message: str = Field(default="", description="错误信息")


class SaveResultsInput(BaseModel):
    """保存结果节点的输入"""
    patent_record_id: int = Field(..., description="专利解析主记录ID")
    analysis_session_id: str = Field(default="", description="分析会话ID")
    products: List[Dict[str, Any]] = Field(default=[], description="商品列表")
    product_dataset_id: str = Field(default="", description="数据集ID")
    retrieval_start_time: str = Field(default="", description="检索开始时间")
    successful_keywords_count: int = Field(default=0, description="成功检索的关键词数量")
    failed_keywords_count: int = Field(default=0, description="失败的关键词数量")
    is_complete: bool = Field(default=True, description="检索是否完整")
    error_message: str = Field(default="", description="错误信息")


class SaveResultsOutput(BaseModel):
    """保存结果节点的输出"""
    search_run_id: int = Field(default=0, description="商品检索运行记录ID")
    product_dataset_id: str = Field(default="", description="数据集ID")
    total_products_count: int = Field(default=0, description="商品总数")
    is_complete: bool = Field(default=True, description="检索是否完整")
    error_message: str = Field(default="", description="错误信息")


# ==================== 包装节点输入输出定义 ====================

class EntryInput(BaseModel):
    """入口节点的输入"""
    patent_record_id: int = Field(..., description="专利解析主记录ID")
    analysis_session_id: str = Field(default="", description="分析会话ID")
    input_keywords: Optional[List[str]] = Field(default=None, description="搜索关键词列表")


class EntryOutput(BaseModel):
    """入口节点的输出"""
    patent_record_id: int = Field(default=0, description="专利解析主记录ID")
    analysis_session_id: str = Field(default="", description="分析会话ID")
    input_keywords: Optional[List[str]] = Field(default=None, description="用户输入的关键词")
    product_dataset_id: str = Field(default="", description="数据集ID")
    retrieval_start_time: str = Field(default="", description="检索开始时间")


class GetKeywordsWrapperInput(BaseModel):
    """获取关键词包装节点的输入"""
    patent_record_id: int = Field(..., description="专利解析主记录ID")
    analysis_session_id: str = Field(default="", description="分析会话ID")
    input_keywords: Optional[List[str]] = Field(default=None, description="用户输入的关键词")


class GetKeywordsWrapperOutput(BaseModel):
    """获取关键词包装节点的输出"""
    keywords: List[str] = Field(default=[], description="关键词列表")
    error_message: str = Field(default="", description="错误信息")


class CozeSearchWrapperInput(BaseModel):
    """Coze搜索包装节点的输入"""
    keywords: List[str] = Field(default=[], description="关键词列表")


class CozeSearchWrapperOutput(BaseModel):
    """Coze搜索包装节点的输出"""
    products: List[Dict[str, Any]] = Field(default=[], description="商品列表")
    total_products_count: int = Field(default=0, description="商品总数")
    successful_keywords_count: int = Field(default=0, description="成功检索的关键词数量")
    failed_keywords_count: int = Field(default=0, description="失败的关键词数量")
    is_complete: bool = Field(default=True, description="检索是否完整")
    error_message: str = Field(default="", description="错误信息")


class SaveResultsWrapperInput(BaseModel):
    """保存结果包装节点的输入"""
    patent_record_id: int = Field(..., description="专利解析主记录ID")
    analysis_session_id: str = Field(default="", description="分析会话ID")
    products: List[Dict[str, Any]] = Field(default=[], description="商品列表")
    product_dataset_id: str = Field(default="", description="数据集ID")
    retrieval_start_time: str = Field(default="", description="检索开始时间")
    successful_keywords_count: int = Field(default=0, description="成功检索的关键词数量")
    failed_keywords_count: int = Field(default=0, description="失败的关键词数量")
    is_complete: bool = Field(default=True, description="检索是否完整")
    error_message: str = Field(default="", description="错误信息")


class SaveResultsWrapperOutput(BaseModel):
    """保存结果包装节点的输出"""
    search_run_id: int = Field(default=0, description="商品检索运行记录ID")
    product_dataset_id: str = Field(default="", description="数据集ID")
    total_products_count: int = Field(default=0, description="商品总数")
    is_complete: bool = Field(default=True, description="检索是否完整")
    error_message: str = Field(default="", description="错误信息")


class ExitInput(BaseModel):
    """出口节点的输入"""
    search_run_id: int = Field(default=0, description="商品检索运行记录ID")
    product_dataset_id: str = Field(default="", description="数据集ID")
    total_products_count: int = Field(default=0, description="商品总数")
    is_complete: bool = Field(default=True, description="检索是否完整")
    error_message: str = Field(default="", description="错误信息")


class ExitOutput(BaseModel):
    """出口节点的输出"""
    search_run_id: int = Field(default=0, description="商品检索运行记录ID")
    product_dataset_id: str = Field(default="", description="数据集ID")
    total_products_count: int = Field(default=0, description="检索到的商品总数")
    is_complete: bool = Field(default=True, description="检索是否完整")
    error_message: str = Field(default="", description="错误信息")
