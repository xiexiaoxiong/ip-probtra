"""
权利要求解析节点
职责：拆分权利要求、标注独立/从属关系、句子级别拆解
LLM用途：辅助识别权利要求边界，不进行内容解释或总结
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
    ClaimsParseInput,
    ClaimsParseOutput,
    Claim,
    ParseError
)

logger = logging.getLogger(__name__)


def claims_parse_node(
    state: ClaimsParseInput, config: RunnableConfig, runtime: Runtime[Context]
) -> ClaimsParseOutput:
    """
    title: 权利要求解析
    desc: 拆分权利要求、标注独立/从属关系、进行句子级别的结构拆解
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
    
    claims_section_text: str = state.claims_section_text
    claims_list: List[Claim] = []
    parse_errors: List[ParseError] = []
    
    try:
        if not claims_section_text or len(claims_section_text.strip()) < 10:
            raise ValueError("权利要求书内容为空或过短")
        
        # 使用LLM辅助识别权利要求边界
        client = LLMClient(ctx=ctx)
        
        # 渲染提示词
        up_tpl = Template(up_template)
        user_prompt = up_tpl.render({"claims_text": claims_section_text})
        
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
        
        # 解析LLM返回的权利要求结构
        response_text = ""
        if isinstance(response.content, str):
            response_text = response.content
        elif isinstance(response.content, list):
            for item in response.content:
                if isinstance(item, dict) and item.get("type") == "text":
                    response_text += item.get("text", "")
        
        # 尝试解析JSON
        try:
            json_match = re.search(r'\{[\s\S]*\}', response_text)
            if json_match:
                claims_data: Dict[str, Any] = json.loads(json_match.group())
                
                claims_raw = claims_data.get("claims", [])
                for claim_dict in claims_raw:
                    if isinstance(claim_dict, dict):
                        claim = Claim(
                            claim_id=str(claim_dict.get("claim_id", "")),
                            claim_type=claim_dict.get("claim_type", "INDEPENDENT"),
                            claim_text=claim_dict.get("claim_text", ""),
                            parent_claim_id=claim_dict.get("parent_claim_id"),
                            sentence_units=claim_dict.get("sentence_units", [])
                        )
                        claims_list.append(claim)
                        
        except (json.JSONDecodeError, ValueError) as e:
            logger.warning(f"LLM返回结构解析失败，使用降级方案: {str(e)}")
            # 降级方案：使用正则规则
            claims_list = _fallback_claims_parse(claims_section_text, parse_errors)
        
        logger.info(f"成功解析权利要求，数量: {len(claims_list)}")
        
    except Exception as e:
        logger.error(f"权利要求解析失败: {str(e)}", exc_info=True)
        parse_errors.append(ParseError(
            error_type="CLAIMS_PARSE_ERROR",
            error_message=f"无法解析权利要求: {str(e)}",
            is_recoverable=False
        ))
        # 尝试降级方案
        claims_list = _fallback_claims_parse(claims_section_text, parse_errors)
    
    return ClaimsParseOutput(
        claims_list=claims_list,
        claims_errors=parse_errors
    )


def _fallback_claims_parse(
    claims_text: str,
    errors: List[ParseError]
) -> List[Claim]:
    """
    降级方案：使用正则规则解析权利要求
    """
    claims: List[Claim] = []
    
    try:
        # 匹配权利要求编号和内容
        # 常见格式：1. xxx, 2. xxx, 或权利要求1: xxx
        pattern = r'(?:权利要求\s*)?(\d+(?:\.\d+)?)\s*[.、:：]\s*([^权利要求\d]+?)(?=(?:权利要求\s*)?(?:\d+(?:\.\d+)?)[.、:：]|$)'
        
        matches = re.finditer(pattern, claims_text, re.MULTILINE)
        
        for match in matches:
            claim_id = match.group(1)
            claim_text = match.group(2).strip()
            
            # 判断是独立权利要求还是从属权利要求
            claim_type = "INDEPENDENT"
            parent_claim_id: Optional[str] = None
            
            # 从属权利要求特征：引用其他权利要求
            dependent_pattern = r'(?:根据|如|引用)\s*权利要求\s*(\d+)'
            dep_match = re.search(dependent_pattern, claim_text)
            if dep_match:
                claim_type = "DEPENDENT"
                parent_claim_id = dep_match.group(1)
            
            # 句子级别的拆分
            # 按句号、分号、逗号分段（保留原文）
            sentence_units = _split_into_sentences(claim_text)
            
            claims.append(Claim(
                claim_id=claim_id,
                claim_type=claim_type,
                claim_text=claim_text,
                parent_claim_id=parent_claim_id,
                sentence_units=sentence_units
            ))
        
        if not claims:
            errors.append(ParseError(
                error_type="CLAIMS_NUMBERING_ERROR",
                error_message="无法识别权利要求编号",
                is_recoverable=False
            ))
        else:
            logger.info(f"降级方案解析完成，权利要求数量: {len(claims)}")
            
    except Exception as e:
        logger.error(f"降级解析方案失败: {str(e)}", exc_info=True)
        errors.append(ParseError(
            error_type="FALLBACK_PARSE_ERROR",
            error_message=f"降级解析方案失败: {str(e)}",
            is_recoverable=False
        ))
    
    return claims


def _split_into_sentences(text: str) -> List[str]:
    """
    将权利要求文本拆分为句子/从句级别单元
    保留原文，不进行修改
    """
    # 中文分句：按句号、分号、逗号拆分
    # 保留分隔符在句子中
    sentences: List[str] = []
    
    # 按句号分段
    period_pattern = r'([^。]+。?)'
    for match in re.finditer(period_pattern, text):
        sentence = match.group(1).strip()
        if sentence:
            # 进一步按分号分段
            semicolon_parts = re.split(r'([^；]+；?)', sentence)
            for part in semicolon_parts:
                part = part.strip()
                if part and part not in ['；', '。']:
                    sentences.append(part)
    
    return sentences if sentences else [text]
