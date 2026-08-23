"""
获取关键词节点
职责：从 Postgres 的 keyword_records 读取关键词
"""
import logging
import re
from typing import List

from langchain_core.runnables import RunnableConfig
from langgraph.runtime import Runtime
from coze_coding_utils.runtime_ctx.context import Context

from graphs.state import GetKeywordsInput, GetKeywordsOutput

logger = logging.getLogger(__name__)


def _normalize_for_containment(value: str) -> str:
    return re.sub(r"\s+", "", str(value or "")).lower()


def _contains_object_term(keyword: str, object_terms: List[str]) -> bool:
    normalized_keyword = _normalize_for_containment(keyword)
    return any(
        normalized_term in normalized_keyword
        for term in object_terms
        if (normalized_term := _normalize_for_containment(term))
    )


def get_keywords_node(
    state: GetKeywordsInput,
    config: RunnableConfig,
    runtime: Runtime[Context]
) -> GetKeywordsOutput:
    """
    title: 获取关键词
    desc: 从Postgres的keyword_records表读取关键词
    integrations: Postgres数据库
    """
    try:
        if state.input_keywords is not None:
            keywords = [keyword.strip() for keyword in state.input_keywords if keyword and keyword.strip()]
            object_terms = [term.strip() for term in (state.input_object_terms or []) if term and term.strip()]
            object_terms = list(dict.fromkeys(object_terms))
            if object_terms:
                keywords = [keyword for keyword in keywords if _contains_object_term(keyword, object_terms)]
            return GetKeywordsOutput(
                keywords=list(dict.fromkeys(keywords)),
                object_terms=object_terms,
                error_message="",
            )

        from storage.database.db import get_engine, get_session
        from storage.database.shared.model import Base, KeywordRecord

        engine = get_engine()
        Base.metadata.create_all(bind=engine, tables=[KeywordRecord.__table__])

        session = get_session()
        try:
            query = session.query(KeywordRecord).filter(
                KeywordRecord.patent_record_id == state.patent_record_id
            )
            if state.analysis_session_id:
                query = query.filter(KeywordRecord.analysis_session_id == state.analysis_session_id)
            rows = query.order_by(KeywordRecord.id.asc()).all()
        finally:
            session.close()

        object_terms: List[str] = []
        for row in rows:
            keyword_text = (row.keyword_text or "").strip()
            raw_payload = row.raw_payload if isinstance(row.raw_payload, dict) else {}
            raw_object_terms = raw_payload.get("object_terms", [])
            if isinstance(raw_object_terms, list):
                object_terms.extend(
                    str(term).strip()
                    for term in raw_object_terms
                    if isinstance(term, str) and term.strip()
                )
            if str(row.keyword_type or "").upper() == "OBJECT_BASE" and keyword_text:
                object_terms.append(keyword_text)

        object_terms = list(dict.fromkeys(object_terms))
        keywords: List[str] = []
        for row in rows:
            keyword_text = (row.keyword_text or "").strip()
            if not keyword_text or str(row.source_location or "") == "必要特征基础词":
                continue
            raw_payload = row.raw_payload if isinstance(row.raw_payload, dict) else {}
            if str(raw_payload.get("query_role", "executable_search")) != "executable_search":
                continue
            if str(raw_payload.get("guard_status", "passed")) != "passed":
                continue
            if object_terms and not _contains_object_term(keyword_text, object_terms):
                continue
            keywords.append(keyword_text)

        keywords = list(dict.fromkeys(keywords))
        if not keywords:
            return GetKeywordsOutput(
                keywords=[],
                error_message=f"未找到关键词记录: patent_record_id={state.patent_record_id}",
            )

        logger.info(
            "从数据库读取关键词成功, patent_record_id=%s, count=%s",
            state.patent_record_id,
            len(keywords),
        )
        return GetKeywordsOutput(keywords=keywords, object_terms=object_terms, error_message="")
    except Exception as error:
        logger.error("获取关键词失败: %s", error, exc_info=True)
        return GetKeywordsOutput(
            keywords=[],
            error_message=f"获取关键词失败: {error}",
        )
