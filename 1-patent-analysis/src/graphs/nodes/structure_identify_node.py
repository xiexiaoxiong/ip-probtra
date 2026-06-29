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
from utils.runtime_paths import resolve_project_path

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
    cfg_file = resolve_project_path(config["metadata"]["llm_cfg"])
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
        fallback_sections, fallback_claims_text, fallback_metadata = _fallback_structure_identify(raw_text, [])

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
            max_tokens=min(int(llm_config.get("max_tokens", 4096) or 4096), 4096),
            max_completion_tokens=min(int(llm_config.get("max_tokens", 4096) or 4096), 4096),
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
                claims_section_text = _normalize_claims_section_text(
                    str(structure_data.get("claims_section", "") or "")
                )
                
                # 提取元数据 - LLM提取的结果
                metadata_raw = structure_data.get("metadata", {})
                if metadata_raw:
                    patent_metadata = PatentMetadata(
                        patent_holder=metadata_raw.get("patent_holder"),
                        patent_number=metadata_raw.get("patent_number"),
                        application_date=metadata_raw.get("application_date"),
                        priority_date=metadata_raw.get("priority_date"),
                        title=metadata_raw.get("title"),
                        abstract=metadata_raw.get("abstract")
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
                if not patent_metadata.abstract and cn_metadata.abstract:
                    patent_metadata.abstract = cn_metadata.abstract
                if not patent_metadata.title:
                    patent_metadata.title = _infer_title_from_claims_text(claims_section_text)

                existing_section_names = {section.section_name for section in specification_sections}
                for fallback_section in fallback_sections:
                    if fallback_section.section_name not in existing_section_names:
                        specification_sections.append(fallback_section)
                claims_fallback_reason = _get_claims_section_fallback_reason(
                    llm_claims_text=claims_section_text,
                    fallback_claims_text=fallback_claims_text,
                )
                if claims_fallback_reason and fallback_claims_text:
                    logger.warning(
                        "LLM提取的权利要求书文本疑似不完整，改用正则降级结果: %s",
                        claims_fallback_reason,
                    )
                    claims_section_text = fallback_claims_text
                elif not claims_section_text and fallback_claims_text:
                    claims_section_text = fallback_claims_text
                if not patent_metadata.patent_number and fallback_metadata.patent_number:
                    patent_metadata.patent_number = fallback_metadata.patent_number
                if not patent_metadata.application_date and fallback_metadata.application_date:
                    patent_metadata.application_date = fallback_metadata.application_date
                if not patent_metadata.priority_date and fallback_metadata.priority_date:
                    patent_metadata.priority_date = fallback_metadata.priority_date
                if not patent_metadata.patent_holder and fallback_metadata.patent_holder:
                    patent_metadata.patent_holder = fallback_metadata.patent_holder
                if not patent_metadata.title and fallback_metadata.title:
                    patent_metadata.title = fallback_metadata.title
                    
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
    label_pat = lambda text: r'\s*'.join(re.escape(char) for char in text)
    
    # (21)申请号
    m21 = re.search(rf'\(21\)\s*{label_pat("申请号")}\s+([A-Z0-9\.\-\/\,]+)', raw_text)
    if m21:
        metadata.patent_number = m21.group(1).strip()
    
    # (22)申请日
    m22 = re.search(rf'\(22\)\s*{label_pat("申请日")}\s+(\d{{4}}[\.\/\-]\d{{2}}[\.\/\-]\d{{2}})', raw_text)
    if m22:
        metadata.application_date = m22.group(1).strip().replace('.', '-').replace('/', '-')
    
    # (30)优先权数据 - 格式: 优先权号 日期 国家代码
    m30 = re.search(rf'\(30\)\s*{label_pat("优先权数据")}\s*\n?\s*([^\n]+)', raw_text)
    if m30:
        priority_line = m30.group(1).strip()
        # 优先权行格式: "10-2016-0109359 2016.08.26 KR" 或 "62/057,001 2014.09.29 US"
        pdate_match = re.search(r'(\d{4}[\.\/\-]\d{2}[\.\/\-]\d{2})', priority_line)
        if pdate_match:
            metadata.priority_date = pdate_match.group(1).strip().replace('.', '-').replace('/', '-')
    
    # (54)名称 - 兼容“发明名称 / 实用新型名称 / 外观设计名称”等首页标记
    m54 = re.search(
        rf'\(54\)\s*(?:(?:{label_pat("发明")}|{label_pat("实用新型")}|{label_pat("外观设计")}))?\s*{label_pat("名称")}\s*\n?\s*([\s\S]*?)(?=\n\s*\(57\)|\n\s*{label_pat("摘要")})',
        raw_text,
    )
    if m54:
        title_text = m54.group(1).strip()
        # 合并跨行：移除换行符和多余空格
        title_text = re.sub(r'\s*\n\s*', '', title_text)
        metadata.title = title_text
    
    # (73)专利权人 - 可能有多行（多个专利权人）
    m73 = re.search(rf'\(73\)\s*{label_pat("专利权人")}\s+([\s\S]*?)(?=\n\s*{label_pat("地址")}|\n\s*\(72\)|\n\s*\(74\))', raw_text)
    if m73:
        holder_text = m73.group(1).strip()
        # 合并跨行
        holder_text = re.sub(r'\s*\n\s*', '', holder_text)
        metadata.patent_holder = holder_text

    # (57)摘要 - 首页摘要通常位于(57)标记后，到权利要求书/说明书或下一编号字段前结束
    m57 = re.search(
        rf'\(57\)\s*{label_pat("摘要")}\s*([\s\S]*?)(?=\n\s*(?:CN\s*\d{{6,}}\s*[A-Z]?\s*(?:{label_pat("权利要求书")}|{label_pat("说明书")})|{label_pat("权利要求书")}|{label_pat("说明书")}|\(\d{{2}}\))|$)',
        raw_text,
    )
    if m57:
        abstract_text = re.sub(r'\s*\n\s*', '', m57.group(1)).strip()
        abstract_text = re.sub(r'\s+', ' ', abstract_text).strip()
        if abstract_text:
            metadata.abstract = abstract_text
    
    logger.info(
        f"CN专利元数据提取: 专利号={metadata.patent_number}, "
        f"申请日={metadata.application_date}, 优先权日={metadata.priority_date}, "
        f"专利权人={metadata.patent_holder}, 标题={metadata.title}"
    )
    
    return metadata


def _infer_title_from_claims_text(claims_text: str) -> Optional[str]:
    """
    当首页(54)发明名称未被提取到时，从第一项权利要求主题兜底推断标题。

    只截取“一种/一种...，其特征在于”这类权利要求前序主题，不生成新摘要或法律判断。
    """
    if not claims_text:
        return None
    normalized = re.sub(r'\s+', '', claims_text)
    match = re.search(r'(一种[^，。,；;:：]{1,40}?)(?:，?其特征在于|包括|至少包括|，)', normalized)
    if match:
        return match.group(1)
    match = re.search(r'^\s*\d+[.、:：]\s*([^，。,；;:：]{2,40})', claims_text)
    if match:
        return re.sub(r'\s+', '', match.group(1)).strip()
    return None


def _extract_claim_ids_from_text(claims_text: str) -> List[str]:
    """
    从权利要求书文本中提取按行起始出现的权利要求编号。
    """
    if not claims_text:
        return []
    return re.findall(
        r'(?m)^\s*(?:权\s*利\s*要\s*求\s*)?(\d+(?:\.\d+)?)\s*[.、:：]',
        claims_text,
    )


def _normalize_claims_section_text(claims_text: str) -> str:
    """
    清洗权利要求书文本，只保留正文：
    1. 遇到“说明书”章节标题后立即截断；
    2. 去掉页眉页脚中的“权利要求书/页码/CN号”等噪音行。
    """
    text = (claims_text or "").strip()
    if not text:
        return ""

    end_match = re.search(
        r'(?m)^\s*(?:说\s*明\s*书\s*全\s*文|说\s*明\s*书|说明书全文|说明书)\s*$',
        text,
    )
    if end_match:
        text = text[:end_match.start()].strip()

    cleaned_lines: List[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            cleaned_lines.append("")
            continue
        if re.fullmatch(r'权\s*利\s*要\s*求\s*书', stripped):
            continue
        if re.fullmatch(r'\d+\s*/\s*\d+\s*页', stripped):
            continue
        if re.fullmatch(r'CN\s*\d+\s*[A-Z]?\s*\d*', stripped):
            continue
        if re.fullmatch(r'\d+', stripped):
            continue
        cleaned_lines.append(line)

    return "\n".join(cleaned_lines).strip()


def _extract_claims_section_from_raw_text(raw_text: str) -> str:
    """
    仅从权利要求书正文提取：
    - 优先从第一条权利要求（1.）开始；
    - 到“说明书”章节标题处结束；
    - 不从正文中的“根据权利要求X所述”位置起算。
    """
    text = (raw_text or "").strip()
    if not text:
        return ""

    start_match = re.search(r'(?m)^\s*1\s*[.、:：]\s*', text)
    if start_match:
        start_pos = start_match.start()
    else:
        title_match = re.search(
            r'(?m)^\s*(?:权\s*利\s*要\s*求\s*书|权利要求书)\s*$',
            text,
        )
        if not title_match:
            return ""
        start_pos = title_match.end()

    tail = text[start_pos:]
    end_match = re.search(
        r'(?m)^\s*(?:说\s*明\s*书\s*全\s*文|说\s*明\s*书|说明书全文|说明书)\s*$',
        tail,
    )
    end_pos = start_pos + end_match.start() if end_match else len(text)
    return _normalize_claims_section_text(text[start_pos:end_pos])


def _get_claims_section_fallback_reason(
    llm_claims_text: str,
    fallback_claims_text: str,
) -> Optional[str]:
    """
    判断是否应放弃LLM提取的权利要求书文本，改用正则降级结果。
    """
    llm_text = _normalize_claims_section_text(llm_claims_text)
    fallback_text = _normalize_claims_section_text(fallback_claims_text)

    if not fallback_text:
        return None
    if not llm_text:
        return "LLM未提取出权利要求书文本"

    llm_claim_ids = _extract_claim_ids_from_text(llm_text)
    fallback_claim_ids = _extract_claim_ids_from_text(fallback_text)

    if fallback_claim_ids:
        if "1" in fallback_claim_ids and "1" not in llm_claim_ids:
            return "LLM结果缺少权利要求1，而正则结果包含权利要求1"
        if "2" in fallback_claim_ids and "2" not in llm_claim_ids:
            return "LLM结果缺少权利要求2，而正则结果包含权利要求2"
        if llm_claim_ids and fallback_claim_ids[0] == "1" and llm_claim_ids[0] != "1":
            return f"LLM结果首条权利要求编号为{llm_claim_ids[0]}，而非1"
        if len(fallback_claim_ids) - len(llm_claim_ids) >= 2:
            return (
                f"LLM结果仅识别到{len(llm_claim_ids)}条权利要求，"
                f"正则结果识别到{len(fallback_claim_ids)}条"
            )

    if (
        len(fallback_text) >= 500
        and len(fallback_text) > len(llm_text) * 1.5
        and re.search(r'(?m)^\s*1\s*[.、:：]', fallback_text) is not None
    ):
        return "LLM结果长度明显短于正则结果，疑似被截断"

    return None


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
        heading_aliases = {
            "技术领域": [r"技术领域"],
            "背景技术": [r"背景技术"],
            "发明内容": [r"发明内容", r"发明概述", r"发明的内容"],
            "实用新型内容": [r"实用新型内容"],
            "附图说明": [r"附图说明", r"说明书附图"],
            "具体实施方式": [r"具体实施方式", r"具体实施例", r"实施方式"],
        }
        heading_regex = "|".join(
            alias
            for aliases in heading_aliases.values()
            for alias in aliases
        )
        heading_matches = list(re.finditer(
            rf"(?m)^\s*({heading_regex})\s*$",
            raw_text,
        ))

        for idx, match in enumerate(heading_matches):
            matched_title = match.group(1)
            section_name = next(
                (
                    canonical
                    for canonical, aliases in heading_aliases.items()
                    if any(re.fullmatch(alias, matched_title) for alias in aliases)
                ),
                matched_title,
            )
            content_start = match.end()
            content_end = heading_matches[idx + 1].start() if idx + 1 < len(heading_matches) else len(raw_text)
            section_text = raw_text[content_start:content_end].strip()
            if not section_text:
                continue

            sections.append(SpecificationSection(
                section_name=section_name,
                section_text=section_text,
                start_position=content_start,
                end_position=content_end,
            ))
        
        claims_text = _extract_claims_section_from_raw_text(raw_text)
            
        logger.info(f"降级方案识别完成，章节数量: {len(sections)}")
        
    except Exception as e:
        logger.error(f"降级识别方案失败: {str(e)}", exc_info=True)
        errors.append(ParseError(
            error_type="FALLBACK_IDENTIFY_ERROR",
            error_message=f"降级识别方案失败: {str(e)}",
            is_recoverable=False
        ))
    
    return sections, claims_text, metadata
