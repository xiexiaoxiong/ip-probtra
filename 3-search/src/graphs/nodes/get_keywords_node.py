"""
获取关键词节点
职责：从 Postgres 的 keyword_records 读取关键词
"""
import logging
from typing import List

from langchain_core.runnables import RunnableConfig
from langgraph.runtime import Runtime
from coze_coding_utils.runtime_ctx.context import Context

from graphs.state import GetKeywordsInput, GetKeywordsOutput

logger = logging.getLogger(__name__)


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
        if state.input_keywords:
            keywords = [keyword.strip() for keyword in state.input_keywords if keyword and keyword.strip()]
            return GetKeywordsOutput(
                keywords=list(dict.fromkeys(keywords)),
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

        keywords: List[str] = []
        for row in rows:
            keyword_text = (row.keyword_text or "").strip()
            if keyword_text:
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
        return GetKeywordsOutput(keywords=keywords, error_message="")
    except Exception as error:
        logger.error("获取关键词失败: %s", error, exc_info=True)
        return GetKeywordsOutput(
            keywords=[],
            error_message=f"获取关键词失败: {error}",
        )
