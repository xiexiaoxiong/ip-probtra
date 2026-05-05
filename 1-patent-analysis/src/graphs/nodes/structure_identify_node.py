"""
文档结构识别节点
职责：识别说明书各部分（背景技术、技术领域、发明内容、权利要求书等）
LLM用途：辅助识别章节边界，不进行内容解释或总结
"""
import os
import json
import logging
import re
from typing import List, Dict, Any, Optional
from jinja2 import Template
from langchain_core.runnables import RunnableConfig
from langchain_core.messages import SystemMessage, HumanMessage
from langgraph.runtime import Runtime
from coze_coding_utils.runtime_ctx.context import Context
from coze_coding_dev_sdk import LLMClient

from graphs.state import (
    StructureIdentifyInput,
    StructureIdentifyOutput,
    SpecificationSection,
    PatentMetadata,
    ParseError
)

logger = logging.getLogger(__name__)


def structure_identify_node(
    state: StructureIdentifyInput, config: RunnableConfig, runtime: Runtime[Context]
) -> StructureIdentifyOutput:
    """
    title: 专利文档结构识别
    desc: 识别说明书的各个章节（背景技术、技术领域、发明内容、权利要求书等）并提取元数据
    integrations: 大语言模型
    """
    ctx = runtime.context
    
    # 读取LLM配置
    cfg_file = os.path.join(
        os.getenv("COZE_WORKSPACE_PATH", ""), 
        config["metadata"]["llm_cfg"]
    )
    with open(cfg_file, "r", encoding="utf-8") as f:
        llm_config_dict = json.load(f)
    
    llm_config = llm_config_dict.get("config", {})
    sp_template = llm_config_dict.get("sp", "")
    up_template = llm_config_dict.get("up", "")
    
    raw_text: str = state.raw_text
    specification_sections: List[SpecificationSection] = []
    patent_metadata = PatentMetadata()
    claims_section_text: str = ""
    identify_errors: List[ParseError] = []
    
    # 无论LLM是否成功，都先用正则提取CN专利元数据（括号编号规则）
    cn_metadata = _extract_cn_patent_metadata(raw_text)
    
    try:
        # 使用LLM识别文档结构
        # 注意：LLM仅用于识别边界，不进行内容解释
        client = LLMClient(ctx=ctx)
        
        # 渲染提示词
        up_tpl = Template(up_template)
        user_prompt = up_tpl.render({"raw_text": raw_text[:5000]})  # 限制长度避免超token
        
        messages = [
            SystemMessage(content=sp_template),
            HumanMessage(content=user_prompt)
        ]
        
        response = client.invoke(
            messages=messages,
            model=llm_config.get("model", "doubao-seed-1-6-251015"),
            temperature=llm_config.get("temperature", 0.1),
            max_completion_tokens=llm_config.get("max_completion_tokens", 32768)
        )
        
        # 解析LLM返回的结构信息
        response_text = ""
        if isinstance(response.content, str):
            response_text = response.content
        elif isinstance(response.content, list):
            for item in response.content:
                if isinstance(item, dict) and item.get("type") == "text":
                    response_text += item.get("text", "")
        
        # 尝试解析JSON结构
        try:
            # 提取JSON部分
            json_match = re.search(r'\{[\s\S]*\}', response_text)
            if json_match:
                structure_data: Dict[str, Any] = json.loads(json_match.group())
                
                # 提取说明书章节
                sections_raw = structure_data.get("sections", [])
                for sec in sections_raw:
                    if isinstance(sec, dict):
                        section_name = sec.get("name", "")
                        section_text = sec.get("text", "")
                        
                        # 在原文中定位章节位置
                        start_pos = raw_text.find(section_text) if section_text else 0
                        end_pos = start_pos + len(section_text) if section_text else 0
                        
                        specification_sections.append(SpecificationSection(
                            section_name=section_name,
                            section_text=section_text,
                            start_position=start_pos,
                            end_position=end_pos
                        ))
                
                # 提取权利要求书文本
                claims_section_text = structure_data.get("claims_section", "")
                
                # 提取元数据 - LLM提取的结果
                metadata_raw = structure_data.get("metadata", {})
                if metadata_raw:
                    patent_metadata = PatentMetadata(
                        patent_holder=metadata_raw.get("patent_holder"),
                        patent_number=metadata_raw.get("patent_number"),
                        application_date=metadata_raw.get("application_date"),
                        priority_date=metadata_raw.get("priority_date"),
                        title=metadata_raw.get("title")
                    )
                
                # 用CN专利正则提取结果填补LLM未识别的字段
                if not patent_metadata.patent_number and cn_metadata.patent_number:
                    patent_metadata.patent_number = cn_metadata.patent_number
                if not patent_metadata.application_date and cn_metadata.application_date:
                    patent_metadata.application_date = cn_metadata.application_date
                if not patent_metadata.priority_date and cn_metadata.priority_date:
                    patent_metadata.priority_date = cn_metadata.priority_date
                if not patent_metadata.patent_holder and cn_metadata.patent_holder:
                    patent_metadata.patent_holder = cn_metadata.patent_holder
                if not patent_metadata.title and cn_metadata.title:
                    patent_metadata.title = cn_metadata.title
                    
        except (json.JSONDecodeError, ValueError) as e:
            logger.warning(f"LLM返回结构解析失败，尝试使用正则规则: {str(e)}")
            # 降级方案：使用正则规则识别
            specification_sections, claims_section_text, patent_metadata = \
                _fallback_structure_identify(raw_text, identify_errors)
        
        logger.info(f"成功识别文档结构，章节数量: {len(specification_sections)}")
        
    except Exception as e:
        logger.error(f"文档结构识别失败: {str(e)}", exc_info=True)
        identify_errors.append(ParseError(
            error_type="STRUCTURE_IDENTIFY_ERROR",
            error_message=f"无法识别文档结构: {str(e)}",
            is_recoverable=False
        ))
        # 尝试降级方案
        specification_sections, claims_section_text, patent_metadata = \
            _fallback_structure_identify(raw_text, identify_errors)
    
    return StructureIdentifyOutput(
        specification_sections=specification_sections,
        patent_metadata=patent_metadata,
        claims_section_text=claims_section_text,
        identify_errors=identify_errors
    )


def _extract_cn_patent_metadata(raw_text: str) -> PatentMetadata:
    """从中国专利文档首页提取元数据
    
    中国专利文档首页使用括号编号标记各字段：
    - (21) 申请号
    - (22) 申请日
    - (30) 优先权数据（包含优先权号和优先权日期）
    - (54) 发明名称
    - (73) 专利权人
    """
    metadata = PatentMetadata()
    
    # (21)申请号
    m21 = re.search(r'\(21\)\s*申请号\s+([A-Z0-9\.\-\/\,]+)', raw_text)
    if m21:
        metadata.patent_number = m21.group(1).strip()
    
    # (22)申请日
    m22 = re.search(r'\(22\)\s*申请日\s+(\d{4}[\.\/\-]\d{2}[\.\/\-]\d{2})', raw_text)
    if m22:
        metadata.application_date = m22.group(1).strip().replace('.', '-').replace('/', '-')
    
    # (30)优先权数据 - 格式: 优先权号 日期 国家代码
    m30 = re.search(r'\(30\)\s*优先权数据\s*\n?\s*([^\n]+)', raw_text)
    if m30:
        priority_line = m30.group(1).strip()
        # 优先权行格式: "10-2016-0109359 2016.08.26 KR" 或 "62/057,001 2014.09.29 US"
        pdate_match = re.search(r'(\d{4}[\.\/\-]\d{2}[\.\/\-]\d{2})', priority_line)
        if pdate_match:
            metadata.priority_date = pdate_match.group(1).strip().replace('.', '-').replace('/', '-')
    
    # (54)发明名称 - 名称可能跨行，到(57)摘要之前结束
    m54 = re.search(r'\(54\)\s*发明名称\s*\n?\s*([\s\S]*?)(?=\n\s*\(57\)|\n\s*摘要)', raw_text)
    if m54:
        title_text = m54.group(1).strip()
        # 合并跨行：移除换行符和多余空格
        title_text = re.sub(r'\s*\n\s*', '', title_text)
        metadata.title = title_text
    
    # (73)专利权人 - 可能有多行（多个专利权人）
    m73 = re.search(r'\(73\)\s*专利权人\s+([\s\S]*?)(?=\n\s*地址|\n\s*\(72\)|\n\s*\(74\))', raw_text)
    if m73:
        holder_text = m73.group(1).strip()
        # 合并跨行
        holder_text = re.sub(r'\s*\n\s*', '', holder_text)
        metadata.patent_holder = holder_text
    
    logger.info(
        f"CN专利元数据提取: 专利号={metadata.patent_number}, "
        f"申请日={metadata.application_date}, 优先权日={metadata.priority_date}, "
        f"专利权人={metadata.patent_holder}, 标题={metadata.title}"
    )
    
    return metadata


def _fallback_structure_identify(
    raw_text: str, 
    errors: List[ParseError]
) -> tuple[List[SpecificationSection], str, PatentMetadata]:
    """
    降级方案：使用正则规则识别文档结构
    当LLM识别失败时使用
    """
    sections: List[SpecificationSection] = []
    claims_text: str = ""
    # 优先使用CN专利括号编号规则提取元数据
    metadata = _extract_cn_patent_metadata(raw_text)
    
    try:
        # 常见专利文档章节标题模式
        section_patterns = {
            "技术领域": r"技术领域[：:\s]*\n?([\s\S]*?)(?=\n\s*(?:背景技术|发明内容|权利要求|$))",
            "背景技术": r"背景技术[：:\s]*\n?([\s\S]*?)(?=\n\s*(?:发明内容|技术领域|权利要求|$))",
            "发明内容": r"发明内容[：:\s]*\n?([\s\S]*?)(?=\n\s*(?:附图说明|具体实施方式|权利要求|$))",
            "实用新型内容": r"实用新型内容[：:\s]*\n?([\s\S]*?)(?=\n\s*(?:附图说明|具体实施方式|权利要求|$))",
        }
        
        # 提取各章节
        for section_name, pattern in section_patterns.items():
            match = re.search(pattern, raw_text)
            if match:
                section_text = match.group(1).strip()
                start_pos = match.start(1)
                end_pos = match.end(1)
                
                sections.append(SpecificationSection(
                    section_name=section_name,
                    section_text=section_text,
                    start_position=start_pos,
                    end_position=end_pos
                ))
        
        # 提取权利要求书
        claims_pattern = r"权利要求书[：:\s]*\n?([\s\S]*?)(?=\n\s*(?:说明书|摘要|$))"
        claims_match = re.search(claims_pattern, raw_text)
        if claims_match:
            claims_text = claims_match.group(1).strip()
            
        logger.info(f"降级方案识别完成，章节数量: {len(sections)}")
        
    except Exception as e:
        logger.error(f"降级识别方案失败: {str(e)}", exc_info=True)
        errors.append(ParseError(
            error_type="FALLBACK_IDENTIFY_ERROR",
            error_message=f"降级识别方案失败: {str(e)}",
            is_recoverable=False
        ))
    
    return sections, claims_text, metadata
