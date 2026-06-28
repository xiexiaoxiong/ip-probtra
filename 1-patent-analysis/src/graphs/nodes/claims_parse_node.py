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
from utils.runtime_paths import resolve_project_path

logger = logging.getLogger(__name__)

_DEPENDENT_PATTERN = re.compile(r'(?:根据|如|引用)\s*权\s*利\s*要\s*求\s*(\d+)')
_BROKEN_REFERENCE_PREFIX_PATTERN = re.compile(
    r'^\s*(?:权\s*利\s*要\s*求\s*)?(\d+)\s*所述的'
)


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
    cfg_file = resolve_project_path(config["metadata"]["llm_cfg"])
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
        claims_list = _parse_claims_with_retry(
            client=client,
            llm_config=llm_config,
            sp_template=sp_template,
            up_template=up_template,
            claims_section_text=claims_section_text,
        )
        
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


def _parse_claims_with_retry(
    client: LLMClient,
    llm_config: Dict[str, Any],
    sp_template: str,
    up_template: str,
    claims_section_text: str,
) -> List[Claim]:
    """
    先使用LLM解析；若结果违反关键校验规则，则带着失败原因重试一次。
    两次均失败后抛出异常，由上层回退到正则解析。
    """
    validation_feedback: List[str] = []

    for attempt in range(2):
        response_text = _invoke_claims_parse_llm(
            client=client,
            llm_config=llm_config,
            sp_template=sp_template,
            up_template=up_template,
            claims_section_text=claims_section_text,
            validation_feedback=validation_feedback,
        )
        claims_list = _parse_claims_from_response(response_text)
        validation_feedback = _validate_claims_parse_result(claims_list)

        if not validation_feedback:
            return claims_list

        logger.warning(
            "权利要求解析结果校验失败，第 %s 次识别存在问题: %s",
            attempt + 1,
            "；".join(validation_feedback),
        )

    raise ValueError(
        "LLM解析结果校验失败，已重试仍不满足规则: "
        + "；".join(validation_feedback)
    )


def _invoke_claims_parse_llm(
    client: LLMClient,
    llm_config: Dict[str, Any],
    sp_template: str,
    up_template: str,
    claims_section_text: str,
    validation_feedback: Optional[List[str]] = None,
) -> str:
    """
    调用LLM解析权利要求；若提供校验失败原因，则要求模型重新完整识别。
    """
    up_tpl = Template(up_template)
    user_prompt = up_tpl.render({"claims_text": claims_section_text})

    if validation_feedback:
        feedback_text = "\n".join(f"- {item}" for item in validation_feedback)
        user_prompt += (
            "\n\n上一次识别结果存在以下错误，请重新完整识别全部权利要求并严格修正：\n"
            f"{feedback_text}\n"
            "必须同时满足以下要求：\n"
            "1. 只要权利要求正文中出现“根据权利要求X所述”“如权利要求X所述”"
            "“引用权利要求X”等引用格式，该权利要求就必须标注为 DEPENDENT。\n"
            "2. 第一条独立权利要求的 claim_id 必须是 1。\n"
            "3. 不能把“权利要求X所述的...”识别成残缺文本，例如“1所述的...”这类文本一律视为错误。\n"
            "4. 任何 parent_claim_id 都必须指向实际存在的 claim_id。\n"
            "5. claim_text 必须逐字保留原文，不得遗漏、改写或错编号。\n"
            "请仅返回合法 JSON。"
        )

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

    response_text = ""
    if isinstance(response.content, str):
        response_text = response.content
    elif isinstance(response.content, list):
        for item in response.content:
            if isinstance(item, dict) and item.get("type") == "text":
                response_text += item.get("text", "")

    if not response_text.strip():
        raise ValueError("LLM未返回有效文本")

    return response_text


def _parse_claims_from_response(response_text: str) -> List[Claim]:
    """
    从LLM响应中抽取JSON并转换为 Claim 列表。
    """
    json_match = re.search(r'\{[\s\S]*\}', response_text)
    if not json_match:
        raise ValueError("LLM返回中未找到JSON对象")

    claims_data: Dict[str, Any] = json.loads(json_match.group())
    claims_raw = claims_data.get("claims", [])
    if not isinstance(claims_raw, list):
        raise ValueError("LLM返回的 claims 字段不是数组")

    claims_list: List[Claim] = []
    for claim_dict in claims_raw:
        if not isinstance(claim_dict, dict):
            continue
        claims_list.append(Claim(
            claim_id=str(claim_dict.get("claim_id", "")).strip(),
            claim_type=claim_dict.get("claim_type", "INDEPENDENT"),
            claim_text=str(claim_dict.get("claim_text", "")).strip(),
            parent_claim_id=_normalize_parent_claim_id(claim_dict.get("parent_claim_id")),
            sentence_units=_normalize_sentence_units(claim_dict.get("sentence_units", [])),
        ))

    if not claims_list:
        raise ValueError("LLM未解析出任何权利要求")

    return claims_list


def _validate_claims_parse_result(claims_list: List[Claim]) -> List[str]:
    """
    校验LLM识别结果是否满足关键业务规则。
    """
    validation_errors: List[str] = []

    if not claims_list:
        return ["未识别出任何权利要求"]

    first_independent_claim: Optional[Claim] = None
    claim_ids = {
        claim.claim_id.strip()
        for claim in claims_list
        if claim.claim_id and claim.claim_id.strip()
    }

    for claim in claims_list:
        claim_text = claim.claim_text.strip()
        if not claim.claim_id:
            validation_errors.append("存在缺少 claim_id 的权利要求")
            continue
        if not claim_text:
            validation_errors.append(f"权利要求{claim.claim_id} 缺少 claim_text")
            continue

        broken_prefix_match = _BROKEN_REFERENCE_PREFIX_PATTERN.match(claim_text)
        if broken_prefix_match:
            referenced_claim_id = broken_prefix_match.group(1)
            validation_errors.append(
                f"权利要求{claim.claim_id} 以残缺引用格式开头（{referenced_claim_id}所述的...），疑似漏掉“根据权利要求”"
            )

        dep_match = _DEPENDENT_PATTERN.search(claim_text)
        if dep_match:
            expected_parent = dep_match.group(1)
            if claim.claim_type != "DEPENDENT":
                validation_errors.append(
                    f"权利要求{claim.claim_id} 含有引用格式，但未标注为 DEPENDENT"
                )
            if claim.parent_claim_id != expected_parent:
                validation_errors.append(
                    f"权利要求{claim.claim_id} 的 parent_claim_id 应为 {expected_parent}"
                )

        if claim.parent_claim_id and claim.parent_claim_id not in claim_ids:
            validation_errors.append(
                f"权利要求{claim.claim_id} 引用了不存在的父权利要求 {claim.parent_claim_id}"
            )

        if first_independent_claim is None and claim.claim_type == "INDEPENDENT":
            first_independent_claim = claim

    if first_independent_claim is None:
        validation_errors.append("未识别出独立权利要求")
    elif first_independent_claim.claim_id != "1":
        validation_errors.append(
            f"第一条独立权利要求编号应为1，当前为 {first_independent_claim.claim_id}"
        )

    return validation_errors


def _normalize_parent_claim_id(parent_claim_id: Any) -> Optional[str]:
    """
    统一清洗父权利要求编号。
    """
    if parent_claim_id is None:
        return None
    normalized = str(parent_claim_id).strip()
    return normalized or None


def _normalize_sentence_units(sentence_units: Any) -> List[str]:
    """
    将模型返回的句子单元统一转为字符串列表。
    """
    if isinstance(sentence_units, list):
        normalized = [str(item).strip() for item in sentence_units if str(item).strip()]
        return normalized
    return []


def _fallback_claims_parse(
    claims_text: str,
    errors: List[ParseError]
) -> List[Claim]:
    """
    降级方案：使用正则规则解析权利要求
    """
    claims: List[Claim] = []
    
    try:
        header_pattern = r'(?m)^\s*(?:权\s*利\s*要\s*求\s*)?(\d+(?:\.\d+)?)\s*[.、:：]\s*'
        headers = list(re.finditer(header_pattern, claims_text))

        if not headers:
            legacy_pattern = r'(?:权利要求\s*)?(\d+(?:\.\d+)?)\s*[.、:：]\s*(.+?)(?=(?:权利要求\s*)?(?:\d+(?:\.\d+)?)[.、:：]|$)'
            headers = list(re.finditer(legacy_pattern, claims_text, re.MULTILINE | re.DOTALL))

        for idx, match in enumerate(headers):
            claim_id = match.group(1)
            start_pos = match.end()
            end_pos = headers[idx + 1].start() if idx + 1 < len(headers) else len(claims_text)
            claim_text = claims_text[start_pos:end_pos].strip()
            
            # 判断是独立权利要求还是从属权利要求
            claim_type = "INDEPENDENT"
            parent_claim_id: Optional[str] = None
            
            # 从属权利要求特征：引用其他权利要求
            dep_match = _DEPENDENT_PATTERN.search(claim_text)
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
