"""
关键词生成模块状态定义
"""
from typing import Literal, Optional, List
from pydantic import BaseModel, Field
from datetime import datetime


# ==================== 全局状态定义 ====================

class GlobalState(BaseModel):
    """全局状态定义"""
    analysis_session_id: str = Field(default="", description="分析会话ID")
    patent_record_id: int = Field(default=0, description="专利解析主记录ID")

    # 飞书多维表格相关
    feishu_url: str = Field(default="", description="飞书多维表格链接")
    app_token: str = Field(default="", description="飞书多维表格 App Token")
    table_id: str = Field(default="", description="飞书数据表 ID")
    keywords_table_id: str = Field(default="", description="关键词结果表 ID")
    keyword_run_id: int = Field(default=0, description="关键词运行记录ID")
    
    # 三表数据
    all_tables_info: dict = Field(default={}, description="所有数据表的完整信息（表名、字段、数据）")
    integrated_patent_records: List[dict] = Field(default=[], description="整合后的专利记录列表（包含权利要求和附图）")
    
    # 批量处理相关
    field_mapping: dict = Field(default={}, description="字段映射关系")
    all_keywords: List[dict] = Field(default=[], description="所有记录的关键词汇总")
    
    # ===== 循环状态（新增）=====
    current_record_index: int = Field(default=0, description="当前处理的记录索引")
    current_record: dict = Field(default={}, description="当前正在处理的记录")
    current_claim_index: int = Field(default=0, description="当前处理的权利要求索引")
    total_records: int = Field(default=0, description="总记录数")
    has_more_records: bool = Field(default=True, description="是否还有更多记录需要处理")
    is_valid: bool = Field(default=True, description="当前记录是否有效")
    
    # 当前处理的记录数据
    claim_id: str = Field(default="", description="权利要求编号")
    claim_type: Literal["INDEPENDENT", "DEPENDENT"] = Field(default="INDEPENDENT", description="权利要求类型")
    claim_text: str = Field(default="", description="权利要求原文")
    background_tech: str = Field(default="", description="背景技术")
    technical_field: str = Field(default="", description="技术领域")
    invention_content: str = Field(default="", description="发明或实用新型内容")
    description_figures: str = Field(default="", description="说明书附图描述")
    patent_holder: str = Field(default="", description="专利权人")
    patent_number: str = Field(default="", description="专利号")
    application_date: str = Field(default="", description="申请日期或优先权日期")
    
    # 处理过程数据
    primary_product_object: str = Field(default="", description="主客体，优先锚定权利要求1的主对象")
    search_product_objects: List[str] = Field(default=[], description="检索落地客体列表，仅用于补充检索，不替代主客体")
    product_object: List[str] = Field(default=[], description="兼容旧流程的产品客体列表，首项优先为主客体")
    invention_point: str = Field(default="", description="发明点描述")
    invention_point_source: str = Field(default="", description="发明点来源位置")
    core_terms: List[dict] = Field(default=[], description="精炼后的核心术语列表（已包含不同说法的同义表述）")
    
    # 筛选、推断和组合后的数据
    filtered_core_terms: List[dict] = Field(default=[], description="筛选后的核心术语列表")
    scenario_words: List[str] = Field(default=[], description="场景词列表（如：家用、静音、省空间）")
    audience_words: List[str] = Field(default=[], description="人群词列表（如：老人、康复、孕妇）")
    combined_keywords: List[dict] = Field(default=[], description="组合后的关键词列表")
    
    # 输出数据
    keywords: List[dict] = Field(default=[], description="关键词列表")
    exception_type: str = Field(default="", description="异常类型")
    exception_message: str = Field(default="", description="异常消息")


# ==================== 图输入输出定义 ====================

class GraphInput(BaseModel):
    """工作流的输入"""
    patent_record_id: int = Field(..., description="专利解析主记录ID")
    analysis_session_id: str = Field(default="", description="分析会话ID")


class GraphOutput(BaseModel):
    """工作流的输出"""
    patent_record_id: int = Field(default=0, description="专利解析主记录ID")
    keyword_run_id: int = Field(default=0, description="关键词运行记录ID")
    keywords_count: int = Field(default=0, description="生成的关键词数量")
    exception_type: str = Field(default="", description="异常类型")
    exception_message: str = Field(default="", description="异常消息")


# ==================== 节点输入输出定义 ====================

class FeishuUrlParserInput(BaseModel):
    """飞书链接解析节点的输入"""
    feishu_url: str = Field(..., description="飞书多维表格链接")


class FeishuUrlParserOutput(BaseModel):
    """飞书链接解析节点的输出"""
    app_token: str = Field(..., description="飞书多维表格 App Token")
    exception_type: str = Field(default="", description="异常类型")
    exception_message: str = Field(default="", description="异常消息")


class FeishuDataLoaderInput(BaseModel):
    """数据库数据加载节点的输入"""
    patent_record_id: int = Field(..., description="专利解析主记录ID")
    analysis_session_id: str = Field(default="", description="分析会话ID")


class TableInfo(BaseModel):
    """单个数据表的信息"""
    table_id: str = Field(..., description="数据表 ID")
    table_name: str = Field(..., description="数据表名称")
    fields: List[dict] = Field(default=[], description="字段列表，包含 field_id, field_name, type 等")
    sample_records: List[dict] = Field(default=[], description="样本记录（前几条数据）")


class FeishuDataLoaderOutput(BaseModel):
    """数据库数据加载节点的输出"""
    all_tables_info: dict = Field(default={}, description="所有数据表的完整信息，key 为 table_id")
    tables: List[TableInfo] = Field(default=[], description="数据表列表")
    integrated_patent_records: List[dict] = Field(default=[], description="整合后的专利记录列表")
    field_mapping: dict = Field(default={}, description="字段映射关系")
    exception_type: str = Field(default="", description="异常类型")
    exception_message: str = Field(default="", description="异常消息")


class DataIntegrationInput(BaseModel):
    """数据整合节点的输入（使用大模型）"""
    app_token: str = Field(..., description="飞书多维表格 App Token")
    all_tables_info: dict = Field(..., description="所有数据表的完整信息")


class DataIntegrationOutput(BaseModel):
    """数据整合节点的输出"""
    integrated_patent_records: List[dict] = Field(default=[], description="整合后的专利记录列表")
    field_mapping: dict = Field(default={}, description="字段映射关系，key 为标准字段名，value 为飞书字段名")
    table_identification: dict = Field(default={}, description="表识别结果，如 {'data_table': 'table_id_1', 'claim_table': 'table_id_2', 'figure_table': 'table_id_3'}")
    exception_type: str = Field(default="", description="异常类型")
    exception_message: str = Field(default="", description="异常消息")


# ==================== 循环控制节点输入输出 ====================

class RecordDispatchInput(BaseModel):
    """记录分发节点的输入"""
    integrated_patent_records: List[dict] = Field(default=[], description="整合后的专利记录列表")
    current_record_index: int = Field(default=0, description="当前处理的记录索引")
    current_claim_index: int = Field(default=0, description="当前处理的权利要求索引")
    field_mapping: dict = Field(default={}, description="字段映射关系")


class RecordDispatchOutput(BaseModel):
    """记录分发节点的输出"""
    current_record: dict = Field(default={}, description="当前正在处理的记录")
    current_record_index: int = Field(default=0, description="当前处理的记录索引")
    current_claim_index: int = Field(default=0, description="当前处理的权利要求索引")
    total_records: int = Field(default=0, description="总记录数")
    claim_id: str = Field(default="", description="权利要求编号")
    claim_type: str = Field(default="INDEPENDENT", description="权利要求类型")
    claim_text: str = Field(default="", description="权利要求原文")
    background_tech: str = Field(default="", description="背景技术")
    technical_field: str = Field(default="", description="技术领域")
    invention_content: str = Field(default="", description="发明或实用新型内容")
    description_figures: str = Field(default="", description="说明书附图描述")
    patent_holder: str = Field(default="", description="专利权人")
    patent_number: str = Field(default="", description="专利号")
    application_date: str = Field(default="", description="申请日期或优先权日期")
    has_more_records: bool = Field(default=True, description="是否还有更多记录")
    exception_type: str = Field(default="", description="异常类型")
    exception_message: str = Field(default="", description="异常消息")


class ResultCollectInput(BaseModel):
    """结果收集节点的输入"""
    keywords: List[dict] = Field(default=[], description="当前记录的关键词列表")
    all_keywords: List[dict] = Field(default=[], description="已收集的所有关键词")
    current_record_index: int = Field(default=0, description="当前处理的记录索引")
    current_claim_index: int = Field(default=0, description="当前处理的权利要求索引")
    total_records: int = Field(default=0, description="总记录数")


class ResultCollectOutput(BaseModel):
    """结果收集节点的输出"""
    all_keywords: List[dict] = Field(default=[], description="更新后的所有关键词")
    current_record_index: int = Field(default=0, description="下一个要处理的记录索引")
    current_claim_index: int = Field(default=0, description="下一个要处理的权利要求索引")
    has_more_records: bool = Field(default=True, description="是否还有更多记录")


class KeywordWriterInput(BaseModel):
    """关键词写入节点的输入"""
    patent_record_id: int = Field(..., description="专利解析主记录ID")
    analysis_session_id: str = Field(default="", description="分析会话ID")
    all_keywords: List[dict] = Field(default=[], description="关键词列表")


class KeywordWriterOutput(BaseModel):
    """关键词写入节点的输出"""
    patent_record_id: int = Field(default=0, description="专利解析主记录ID")
    keyword_run_id: int = Field(default=0, description="关键词运行记录ID")
    keywords_count: int = Field(default=0, description="写入的关键词数量")
    exception_type: str = Field(default="", description="异常类型")
    exception_message: str = Field(default="", description="异常消息")


class RecordProcessLoopInput(BaseModel):
    """记录循环处理节点的输入"""
    integrated_patent_records: List[dict] = Field(default=[], description="整合后的专利记录列表")
    field_mapping: dict = Field(default={}, description="字段映射关系")
    current_record_index: int = Field(default=0, description="当前处理的记录索引")
    all_keywords: List[dict] = Field(default=[], description="已收集的所有关键词")


class RecordProcessLoopOutput(BaseModel):
    """记录循环处理节点的输出"""
    all_keywords: List[dict] = Field(default=[], description="所有记录的关键词汇总")
    processed_count: int = Field(default=0, description="已处理的记录数")


# ==================== 子环节节点输入输出 ====================

class InputValidationInput(BaseModel):
    """输入验证节点的输入"""
    claim_id: str = Field(default="", description="权利要求编号")
    claim_text: str = Field(default="", description="权利要求原文")
    technical_field: str = Field(default="", description="技术领域")
    invention_content: str = Field(default="", description="发明或实用新型内容")
    background_tech: str = Field(default="", description="背景技术")


class InputValidationOutput(BaseModel):
    """输入验证节点的输出"""
    is_valid: bool = Field(..., description="输入是否有效")
    use_fallback_context: bool = Field(default=False, description="是否启用无权利要求降级路径")
    exception_type: str = Field(default="", description="异常类型")
    exception_message: str = Field(default="", description="异常消息")


class ProductObjectExtractionInput(BaseModel):
    """产品客体提取节点的输入"""
    claim_text: str = Field(default="", description="权利要求原文")
    technical_field: str = Field(default="", description="技术领域")
    invention_content: str = Field(default="", description="发明或实用新型内容（当技术领域过于宽泛时用于进一步明确客体）")


class ProductObjectExtractionOutput(BaseModel):
    """产品客体提取节点的输出"""
    primary_product_object: str = Field(default="", description="主客体")
    search_product_objects: List[str] = Field(default=[], description="检索落地客体列表")
    product_object: List[str] = Field(default=[], description="提取的产品客体列表（可能有多个客体，每个客体为具体产品名称）")


class InventionPointExtractionInput(BaseModel):
    """发明点提取节点的输入"""
    claim_text: str = Field(..., description="权利要求原文")
    invention_content: str = Field(default="", description="发明或实用新型内容")
    description_figures: str = Field(default="", description="说明书附图描述")


class InventionPointExtractionOutput(BaseModel):
    """发明点提取节点的输出"""
    invention_point: str = Field(default="", description="发明点描述")
    invention_point_source: str = Field(default="", description="发明点来源位置")


class KeywordExtractionInput(BaseModel):
    """关键词提取节点的输入"""
    claim_text: str = Field(..., description="权利要求原文")
    invention_point: str = Field(default="", description="发明点描述")
    patent_holder: str = Field(default="", description="专利权人")


class KeywordExtractionOutput(BaseModel):
    """关键词提取节点的输出"""
    core_terms: List[dict] = Field(default=[], description="核心术语列表")


class InventionPointRefinementInput(BaseModel):
    """发明点特征词精炼节点的输入"""
    core_terms: List[dict] = Field(default=[], description="初步提取的核心术语列表")
    invention_point: str = Field(default="", description="发明点描述")
    invention_content: str = Field(default="", description="发明或实用新型内容")
    claim_text: str = Field(default="", description="权利要求原文")
    background_tech: str = Field(default="", description="背景技术")
    primary_product_object: str = Field(default="", description="主客体")
    search_product_objects: List[str] = Field(default=[], description="检索落地客体列表")


class InventionPointRefinementOutput(BaseModel):
    """发明点特征词精炼节点的输出"""
    core_terms: List[dict] = Field(default=[], description="精炼后的核心术语列表（可能包含原术语和精炼后的特征词）")
    refinement_log: str = Field(default="", description="精炼日志（记录哪些术语被精炼及原因）")


class KeywordFilteringInput(BaseModel):
    """关键词筛选节点的输入"""
    core_terms: List[dict] = Field(default=[], description="核心术语列表")
    invention_point: str = Field(default="", description="发明点描述")


class KeywordFilteringOutput(BaseModel):
    """关键词筛选节点的输出"""
    filtered_core_terms: List[dict] = Field(default=[], description="筛选后的核心术语列表")
    filter_log: str = Field(default="", description="筛选日志")


class ScenarioAudienceInferenceInput(BaseModel):
    """人群场景词推断节点的输入"""
    invention_point: str = Field(default="", description="发明点描述")
    invention_content: str = Field(default="", description="发明或实用新型内容")
    claim_text: str = Field(default="", description="权利要求原文")
    primary_product_object: str = Field(default="", description="主客体")
    search_product_objects: List[str] = Field(default=[], description="检索落地客体列表")
    product_object: List[str] = Field(default=[], description="产品客体列表")
    filtered_core_terms: List[dict] = Field(default=[], description="筛选后的核心术语列表")


class ScenarioAudienceInferenceOutput(BaseModel):
    """人群场景词推断节点的输出"""
    scenario_words: List[str] = Field(default=[], description="场景词列表（如：家用、静音、省空间、户外）")
    audience_words: List[str] = Field(default=[], description="人群词列表（如：老人、康复、孕妇、儿童）")


class KeywordCombinationInput(BaseModel):
    """关键词组合节点的输入"""
    filtered_core_terms: List[dict] = Field(default=[], description="筛选后的核心术语列表")
    primary_product_object: str = Field(default="", description="主客体")
    search_product_objects: List[str] = Field(default=[], description="检索落地客体列表")
    product_object: List[str] = Field(default=[], description="产品客体列表")
    patent_holder: str = Field(default="", description="专利权人")
    invention_point: str = Field(default="", description="发明点描述")
    scenario_words: List[str] = Field(default=[], description="场景词列表")
    audience_words: List[str] = Field(default=[], description="人群词列表")


class KeywordCombinationOutput(BaseModel):
    """关键词组合节点的输出"""
    combined_keywords: List[dict] = Field(default=[], description="组合后的关键词列表（包含五种类型：品牌同款型/特征组合型/场景型/人群型/功能型）")


class ResultAssemblyInput(BaseModel):
    """结果组装节点的输入"""
    claim_id: str = Field(..., description="权利要求编号")
    combined_keywords: List[dict] = Field(default=[], description="组合后的关键词列表")


class ResultAssemblyOutput(BaseModel):
    """结果组装节点的输出"""
    keywords: List[dict] = Field(default=[], description="组装后的关键词列表")
