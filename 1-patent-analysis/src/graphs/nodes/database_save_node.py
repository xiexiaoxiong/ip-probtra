"""
数据库保存节点
职责：将专利解析结果持久化到数据库
"""
import logging
from typing import List, Dict, Any
from langchain_core.runnables import RunnableConfig
from langgraph.runtime import Runtime
from coze_coding_utils.runtime_ctx.context import Context
from sqlalchemy import text

from graphs.state import (
    DatabaseSaveInput,
    DatabaseSaveOutput,
    Claim,
    PatentFigure,
    PatentMetadata,
    SpecificationSection,
    ParseError
)

logger = logging.getLogger(__name__)


def _ensure_patent_figure_columns(engine: Any) -> None:
    statements = [
        "ALTER TABLE patent_figures ADD COLUMN IF NOT EXISTS file_path TEXT",
        "ALTER TABLE patent_figures ADD COLUMN IF NOT EXISTS mime_type TEXT",
        "ALTER TABLE patent_figures ADD COLUMN IF NOT EXISTS file_size INTEGER",
        "ALTER TABLE patent_figures ADD COLUMN IF NOT EXISTS file_sha256 TEXT",
    ]
    with engine.begin() as conn:
        for statement in statements:
            conn.execute(text(statement))


def database_save_node(
    state: DatabaseSaveInput, config: RunnableConfig, runtime: Runtime[Context]
) -> DatabaseSaveOutput:
    """
    title: 数据库保存
    desc: 将专利解析结果持久化到Postgres数据库
    integrations: Postgres数据库
    """
    ctx = runtime.context
    
    claims_list: List[Claim] = state.claims_list
    specification_sections: List[SpecificationSection] = state.specification_sections
    figures_list: List[PatentFigure] = state.figures_list
    metadata: PatentMetadata = state.patent_metadata
    task_id: str = state.task_id
    
    # 收集所有错误
    all_errors: List[ParseError] = []
    if state.read_error:
        all_errors.append(state.read_error)
    all_errors.extend(state.identify_errors)
    all_errors.extend(state.claims_errors)
    all_errors.extend(state.figure_errors)
    
    # 将说明书章节转换为字典格式
    specification_dict: Dict[str, str] = {}
    for section in specification_sections:
        specification_dict[section.section_name] = section.section_text
    
    try:
        from storage.database.db import get_engine, get_session
        from storage.database.shared.model import (
            Base,
            PatentClaim as PatentClaimModel,
            PatentFigure as PatentFigureModel,
            PatentParseRecord,
        )

        engine = get_engine()
        Base.metadata.create_all(
            bind=engine,
            tables=[
                PatentParseRecord.__table__,
                PatentClaimModel.__table__,
                PatentFigureModel.__table__,
            ],
        )
        _ensure_patent_figure_columns(engine)

        session = get_session()
        try:
            record = (
                session.query(PatentParseRecord)
                .filter(PatentParseRecord.task_id == task_id)
                .one_or_none()
            )

            if record is None:
                record = PatentParseRecord(task_id=task_id)
                session.add(record)

            record.patent_number = metadata.patent_number
            record.patent_holder = metadata.patent_holder
            record.title = metadata.title
            record.application_date = metadata.application_date
            record.priority_date = metadata.priority_date
            record.specification = specification_dict
            record.parse_errors = [err.model_dump() for err in all_errors]

            session.flush()
            record_id = record.id
            logger.info(f"主记录保存成功，ID: {record_id}")

            session.query(PatentClaimModel).filter(
                PatentClaimModel.record_id == record_id
            ).delete()
            session.query(PatentFigureModel).filter(
                PatentFigureModel.record_id == record_id
            ).delete()

            if claims_list:
                session.add_all(
                    [
                        PatentClaimModel(
                            record_id=record_id,
                            claim_id=claim.claim_id,
                            claim_type=claim.claim_type,
                            claim_text=claim.claim_text,
                            parent_claim_id=claim.parent_claim_id,
                            sentence_units=claim.sentence_units,
                        )
                        for claim in claims_list
                    ]
                )
                logger.info(f"权利要求保存成功，共 {len(claims_list)} 条")

            if figures_list:
                session.add_all(
                    [
                        PatentFigureModel(
                            record_id=record_id,
                            figure_id=figure.figure_id,
                            figure_url=figure.figure_url,
                            figure_description=figure.figure_description,
                            storage_key=figure.storage_key,
                            file_path=figure.file_path,
                            mime_type=figure.mime_type,
                            file_size=figure.file_size,
                            file_sha256=figure.file_sha256,
                        )
                        for figure in figures_list
                    ]
                )
                logger.info(f"附图保存成功，共 {len(figures_list)} 张")

            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
        
        return DatabaseSaveOutput(
            db_record_id=record_id,
            save_success=True,
            save_error=None
        )
        
    except Exception as e:
        logger.error(f"数据库保存失败: {str(e)}", exc_info=True)
        
        return DatabaseSaveOutput(
            db_record_id=-1,
            save_success=False,
            save_error=ParseError(
                error_type="DATABASE_SAVE_ERROR",
                error_message=f"数据库保存失败: {str(e)}",
                is_recoverable=False
            )
        )
