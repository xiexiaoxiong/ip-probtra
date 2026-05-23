"""
专利解析模块状态定义

本模块职责：将原始专利文本转化为结构化专利数据
严格禁止：专利解读、保护范围分析、法律判断
"""
from typing import Literal, Optional, List, Dict, Any
from pydantic import BaseModel, Field
from utils.file.file import File


# ==================== 基础数据结构定义 ====================

class Claim(BaseModel):
    """单条权利要求"""
    claim_id: str = Field(..., description="权利要求编号，如'1', '2', '2-1'")
    claim_type: Literal["INDEPENDENT", "DEPENDENT"] = Field(
        ..., description="权利要求类型：独立权利要求或从属权利要求"
    )
    claim_text: str = Field(..., description="权利要求完整原文，逐字保留")
    parent_claim_id: Optional[str] = Field(
        default=None, description="从属权利要求的父权利要求编号（仅DEPENDENT类型）"
    )
    sentence_units: List[str] = Field(
        default=[], description="句子/从句级别的结构单元列表"
    )


class SpecificationSection(BaseModel):
    """说明书章节"""
    section_name: str = Field(..., description="章节名称")
    section_text: str = Field(..., description="章节原文内容")
    start_position: int = Field(default=0, description="在原文中的起始位置")
    end_position: int = Field(default=0, description="在原文中的结束位置")


class PatentMetadata(BaseModel):
    """专利元数据"""
    patent_holder: Optional[str] = Field(default=None, description="专利权人")
    patent_number: Optional[str] = Field(default=None, description="专利号")
    application_date: Optional[str] = Field(default=None, description="专利申请日期")
    priority_date: Optional[str] = Field(default=None, description="优先权日期")
    title: Optional[str] = Field(default=None, description="专利标题")


class ParseError(BaseModel):
    """解析错误信息"""
    error_type: str = Field(..., description="错误类型")
    error_message: str = Field(..., description="错误详细描述")
    affected_section: Optional[str] = Field(default=None, description="受影响的章节")
    is_recoverable: bool = Field(default=False, description="是否可恢复继续解析")


class PatentFigure(BaseModel):
    """专利附图"""
    figure_id: str = Field(..., description="附图编号，如'图1', '图2'")
    figure_url: str = Field(..., description="附图访问URL")
    figure_description: str = Field(default="", description="附图说明文字")
    storage_key: Optional[str] = Field(default=None, description="对象存储的key")
    file_path: Optional[str] = Field(default=None, description="附图本地文件路径")
    mime_type: Optional[str] = Field(default=None, description="附图MIME类型")
    file_size: Optional[int] = Field(default=None, description="附图文件大小（字节）")
    file_sha256: Optional[str] = Field(default=None, description="附图文件SHA256")


# ==================== 全局状态定义 ====================

class GlobalState(BaseModel):
    """全局状态：存储整个解析过程中的中间结果"""
    # 原始输入
    patent_file: File = Field(..., description="专利文档文件对象")
    task_id: str = Field(..., description="任务ID，用于追踪和重放")
    
    # 解析中间结果
    raw_text: str = Field(default="", description="从文档中提取的原始文本")
    file_format: str = Field(default="", description="文件格式：pdf/txt/html")
    specification_sections: List[SpecificationSection] = Field(
        default=[], description="说明书各章节内容"
    )
    claims_section_text: str = Field(default="", description="权利要求书原文")
    claims_list: List[Claim] = Field(default=[], description="解析后的权利要求列表")
    figures_list: List[PatentFigure] = Field(default=[], description="附图列表")
    patent_metadata: PatentMetadata = Field(
        default_factory=PatentMetadata, description="专利元数据"
    )
    
    # 数据库记录
    db_record_id: Optional[int] = Field(default=None, description="数据库记录ID")
    
    # 飞书多维表格
    feishu_app_token: Optional[str] = Field(default=None, description="飞书多维表格的app_token")
    feishu_url: Optional[str] = Field(default=None, description="飞书多维表格的访问URL")
    feishu_patent_record_id: Optional[str] = Field(default=None, description="飞书专利主表记录ID")
    
    # 异常处理
    read_error: Optional[ParseError] = Field(default=None, description="文件读取错误")
    identify_errors: List[ParseError] = Field(
        default=[], description="结构识别错误列表"
    )
    claims_errors: List[ParseError] = Field(
        default=[], description="权利要求解析错误列表"
    )
    figure_errors: List[ParseError] = Field(
        default=[], description="附图提取错误列表"
    )
    parse_errors: List[ParseError] = Field(
        default=[], description="所有解析错误列表"
    )
    has_critical_error: bool = Field(
        default=False, description="是否存在致命错误导致无法继续解析"
    )
    
    # 处理状态
    parse_stage: str = Field(
        default="initialized", description="解析阶段：initialized/file_read/structure_identified/claims_parsed/completed"
    )


# ==================== 图输入输出定义 ====================

class GraphInput(BaseModel):
    """工作流输入"""
    patent_file: File = Field(..., description="专利文档文件（支持PDF/TXT/HTML）")
    task_id: str = Field(..., description="任务ID，用于追踪和日志关联")


class GraphOutput(BaseModel):
    """工作流输出"""
    claims: List[Claim] = Field(default=[], description="权利要求拆解结果")
    specification: Dict[str, str] = Field(
        default={}, description="说明书各章节内容，key为章节名，value为原文"
    )
    figures: List[PatentFigure] = Field(
        default=[], description="专利附图列表，包含图片URL"
    )
    metadata: PatentMetadata = Field(
        default_factory=PatentMetadata, description="专利元数据"
    )
    errors: List[ParseError] = Field(
        default=[], description="解析错误列表（如有）"
    )
    task_id: str = Field(..., description="任务ID，用于结果关联")
    db_record_id: Optional[int] = Field(
        default=None, description="数据库保存后的记录ID"
    )
    feishu_app_token: Optional[str] = Field(
        default=None, description="飞书多维表格的app_token"
    )
    feishu_url: Optional[str] = Field(
        default=None, description="飞书多维表格的访问URL"
    )


# ==================== 节点输入输出定义 ====================

# 1. 文件读取节点
class FileReadInput(BaseModel):
    """文件读取节点输入"""
    patent_file: File = Field(..., description="专利文档文件")


class FileReadOutput(BaseModel):
    """文件读取节点输出"""
    raw_text: str = Field(..., description="提取的原始文本内容")
    file_format: str = Field(..., description="文件格式：pdf/txt/html")
    read_error: Optional[ParseError] = Field(
        default=None, description="读取错误（如有）"
    )


# 2. 文档结构识别节点
class StructureIdentifyInput(BaseModel):
    """文档结构识别节点输入"""
    raw_text: str = Field(..., description="原始文本内容")


class StructureIdentifyOutput(BaseModel):
    """文档结构识别节点输出"""
    specification_sections: List[SpecificationSection] = Field(
        default=[], description="识别出的说明书各章节"
    )
    patent_metadata: PatentMetadata = Field(
        default_factory=PatentMetadata, description="提取的专利元数据"
    )
    claims_section_text: str = Field(
        default="", description="权利要求书的原始文本段落"
    )
    identify_errors: List[ParseError] = Field(
        default=[], description="识别过程中的错误列表"
    )


# 3. 权利要求解析节点
class ClaimsParseInput(BaseModel):
    """权利要求解析节点输入"""
    claims_section_text: str = Field(..., description="权利要求书原文")


class ClaimsParseOutput(BaseModel):
    """权利要求解析节点输出"""
    claims_list: List[Claim] = Field(default=[], description="解析后的权利要求列表")
    claims_errors: List[ParseError] = Field(
        default=[], description="解析错误列表"
    )


# 4. 结构化输出节点
class StructuredOutputInput(BaseModel):
    """结构化输出节点输入"""
    claims_list: List[Claim] = Field(default=[], description="权利要求列表")
    specification_sections: List[SpecificationSection] = Field(
        default=[], description="说明书章节列表"
    )
    figures_list: List[PatentFigure] = Field(
        default=[], description="附图列表"
    )
    patent_metadata: PatentMetadata = Field(
        default_factory=PatentMetadata, description="专利元数据"
    )
    identify_errors: List[ParseError] = Field(default=[], description="结构识别错误")
    claims_errors: List[ParseError] = Field(default=[], description="权利要求解析错误")
    figure_errors: List[ParseError] = Field(default=[], description="附图提取错误")
    read_error: Optional[ParseError] = Field(default=None, description="文件读取错误")
    task_id: str = Field(..., description="任务ID")
    db_record_id: Optional[int] = Field(default=None, description="数据库记录ID")
    feishu_app_token: Optional[str] = Field(default=None, description="飞书多维表格的app_token")
    feishu_url: Optional[str] = Field(default=None, description="飞书多维表格的访问URL")


class StructuredOutputOutput(BaseModel):
    """结构化输出节点输出"""
    final_output: GraphOutput = Field(..., description="最终的结构化输出结果")
    output_json: str = Field(..., description="JSON格式的输出字符串")


# 5. 异常处理节点（条件分支）
class ErrorCheckInput(BaseModel):
    """异常检查节点输入"""
    raw_text: str = Field(default="", description="原始文本")
    read_error: Optional[ParseError] = Field(default=None, description="文件读取错误")
    identify_errors: List[ParseError] = Field(default=[], description="结构识别错误")
    claims_errors: List[ParseError] = Field(default=[], description="权利要求解析错误")


class ErrorCheckOutput(BaseModel):
    """异常检查节点输出"""
    has_critical_error: bool = Field(..., description="是否存在致命错误")
    critical_errors: List[ParseError] = Field(
        default=[], description="致命错误列表"
    )
    recoverable_errors: List[ParseError] = Field(
        default=[], description="可恢复错误列表"
    )


# 6. 附图提取节点
class FigureExtractInput(BaseModel):
    """附图提取节点输入"""
    patent_file: File = Field(..., description="专利文档文件")
    task_id: str = Field(..., description="任务ID，用于附图本地落盘")
    specification_sections: List[SpecificationSection] = Field(
        default=[], description="说明书章节，用于提取附图说明"
    )


class FigureExtractOutput(BaseModel):
    """附图提取节点输出"""
    figures_list: List[PatentFigure] = Field(default=[], description="提取的附图列表")
    figure_errors: List[ParseError] = Field(
        default=[], description="提取过程中的错误列表"
    )


# 7. 数据库保存节点
class DatabaseSaveInput(BaseModel):
    """数据库保存节点输入"""
    claims_list: List[Claim] = Field(default=[], description="权利要求列表")
    specification_sections: List[SpecificationSection] = Field(
        default=[], description="说明书章节列表"
    )
    figures_list: List[PatentFigure] = Field(default=[], description="附图列表")
    patent_metadata: PatentMetadata = Field(default_factory=PatentMetadata, description="专利元数据")
    identify_errors: List[ParseError] = Field(default=[], description="结构识别错误")
    claims_errors: List[ParseError] = Field(default=[], description="权利要求解析错误")
    figure_errors: List[ParseError] = Field(default=[], description="附图提取错误")
    read_error: Optional[ParseError] = Field(default=None, description="文件读取错误")
    task_id: str = Field(..., description="任务ID")


class DatabaseSaveOutput(BaseModel):
    """数据库保存节点输出"""
    db_record_id: int = Field(..., description="数据库保存的记录ID")
    save_success: bool = Field(..., description="是否保存成功")
    save_error: Optional[ParseError] = Field(
        default=None, description="保存错误（如有）"
    )


# 8. 飞书多维表格保存节点
class FeishuSaveInput(BaseModel):
    """飞书多维表格保存节点输入"""
    claims_list: List[Claim] = Field(default=[], description="权利要求列表")
    specification_sections: List[SpecificationSection] = Field(
        default=[], description="说明书章节列表"
    )
    figures_list: List[PatentFigure] = Field(default=[], description="附图列表")
    patent_metadata: PatentMetadata = Field(default_factory=PatentMetadata, description="专利元数据")
    identify_errors: List[ParseError] = Field(default=[], description="结构识别错误")
    claims_errors: List[ParseError] = Field(default=[], description="权利要求解析错误")
    figure_errors: List[ParseError] = Field(default=[], description="附图提取错误")
    read_error: Optional[ParseError] = Field(default=None, description="文件读取错误")
    task_id: str = Field(..., description="任务ID")


class FeishuSaveOutput(BaseModel):
    """飞书多维表格保存节点输出"""
    feishu_app_token: str = Field(..., description="飞书多维表格的app_token")
    feishu_url: str = Field(..., description="飞书多维表格的访问URL")
    patent_record_id: str = Field(default="", description="专利主表记录ID")
    save_success: bool = Field(..., description="是否保存成功")
    save_error: Optional[ParseError] = Field(
        default=None, description="保存错误（如有）"
    )
