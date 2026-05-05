"""
关键词写入节点
职责：将关键词结果写入 Postgres
"""
from datetime import datetime

from langchain_core.runnables import RunnableConfig
from langgraph.runtime import Runtime
from coze_coding_utils.runtime_ctx.context import Context

from graphs.state import KeywordWriterInput, KeywordWriterOutput


def keyword_writer_node(
    state: KeywordWriterInput,
    config: RunnableConfig,
    runtime: Runtime[Context]
) -> KeywordWriterOutput:
    """
    title: 关键词写入
    desc: 将生成的关键词结果保存到Postgres
    integrations: Postgres数据库
    """
    if not state.all_keywords:
        return KeywordWriterOutput(
            patent_record_id=state.patent_record_id,
            keyword_run_id=0,
            keywords_count=0,
            exception_type="NO_KEYWORDS_TO_WRITE",
            exception_message="没有关键词需要写入",
        )

    try:
        from storage.database.db import get_engine, get_session
        from storage.database.shared.model import Base, KeywordRecord, KeywordRun

        engine = get_engine()
        Base.metadata.create_all(
            bind=engine,
            tables=[KeywordRun.__table__, KeywordRecord.__table__],
        )

        session = get_session()
        try:
            valid_keywords = [
                keyword for keyword in state.all_keywords if str(keyword.get("keyword_text", "")).strip()
            ]
            keyword_run = KeywordRun(
                patent_record_id=state.patent_record_id,
                analysis_session_id=state.analysis_session_id or None,
                workflow_variant="home_appliances",
                keywords_count=len(valid_keywords),
            )
            session.add(keyword_run)
            session.flush()
            keyword_run_id = int(keyword_run.id)

            created_at = datetime.now()
            session.add_all(
                [
                    KeywordRecord(
                        keyword_run_id=keyword_run_id,
                        patent_record_id=state.patent_record_id,
                        analysis_session_id=state.analysis_session_id or None,
                        keyword_id=keyword.get("keyword_id"),
                        claim_id=keyword.get("claim_id"),
                        keyword_text=str(keyword.get("keyword_text", "")).strip(),
                        keyword_type=keyword.get("keyword_type"),
                        source_location=keyword.get("source_location"),
                        generation_method=keyword.get("generation_method"),
                        confidence_score=keyword.get("confidence_score"),
                        raw_payload=keyword,
                        created_at=created_at,
                    )
                    for keyword in valid_keywords
                ]
            )
            session.commit()
            keywords_count = len(valid_keywords)
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

        return KeywordWriterOutput(
            patent_record_id=state.patent_record_id,
            keyword_run_id=keyword_run_id,
            keywords_count=keywords_count,
            exception_type="",
            exception_message="",
        )
    except Exception as error:
        return KeywordWriterOutput(
            patent_record_id=state.patent_record_id,
            keyword_run_id=0,
            keywords_count=0,
            exception_type="KEYWORD_WRITE_ERROR",
            exception_message=f"写入关键词时发生错误: {error}",
        )
