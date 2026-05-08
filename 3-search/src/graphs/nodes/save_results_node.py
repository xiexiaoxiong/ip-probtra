"""
保存结果节点
职责：将商品检索结果写入 Postgres
"""
import logging

from langchain_core.runnables import RunnableConfig
from langgraph.runtime import Runtime
from coze_coding_utils.runtime_ctx.context import Context

from graphs.state import SaveResultsInput, SaveResultsOutput

logger = logging.getLogger(__name__)


def save_results_node(
    state: SaveResultsInput,
    config: RunnableConfig,
    runtime: Runtime[Context]
) -> SaveResultsOutput:
    """
    title: 保存结果
    desc: 将商品数据保存到Postgres
    integrations: Postgres数据库
    """
    try:
        from storage.database.db import get_engine, get_session
        from storage.database.shared.model import Base, SearchProduct, SearchRun

        engine = get_engine()
        Base.metadata.create_all(
            bind=engine,
            tables=[SearchRun.__table__, SearchProduct.__table__],
        )

        session = get_session()
        try:
            search_run = SearchRun(
                patent_record_id=state.patent_record_id,
                analysis_session_id=state.analysis_session_id or None,
                product_dataset_id=state.product_dataset_id,
                retrieval_start_time=state.retrieval_start_time,
                successful_keywords_count=state.successful_keywords_count,
                failed_keywords_count=state.failed_keywords_count,
                total_products_count=len(state.products),
                platforms_queried=["Coze工作流"],
                is_complete=state.is_complete,
                error_message=state.error_message or None,
            )
            session.add(search_run)
            session.flush()
            search_run_id = int(search_run.id)

            session.add_all(
                [
                    SearchProduct(
                        search_run_id=search_run_id,
                        patent_record_id=state.patent_record_id,
                        analysis_session_id=state.analysis_session_id or None,
                        product_id=str(product.get("product_id", "")) or None,
                        product_name=product.get("product_name"),
                        product_url=product.get("product_url"),
                        product_source=product.get("product_source"),
                        price=str(product.get("price", "")) if product.get("price") is not None else None,
                        brand=product.get("brand"),
                        manufacturer=product.get("manufacturer"),
                        matched_keywords=product.get("matched_keywords"),
                        description=product.get("description"),
                        picture=product.get("picture") if isinstance(product.get("picture"), list) else [],
                        raw_payload=product.get("raw_payload") if isinstance(product.get("raw_payload"), dict) else product,
                    )
                    for product in state.products
                ]
            )

            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

        logger.info(
            "商品检索结果已写入数据库, patent_record_id=%s, search_run_id=%s, products=%s",
            state.patent_record_id,
            search_run_id,
            len(state.products),
        )
        return SaveResultsOutput(
            search_run_id=search_run_id,
            product_dataset_id=state.product_dataset_id,
            total_products_count=len(state.products),
            is_complete=state.is_complete,
            error_message=state.error_message or "",
        )
    except Exception as error:
        logger.error("保存商品结果失败: %s", error, exc_info=True)
        return SaveResultsOutput(
            search_run_id=0,
            product_dataset_id=state.product_dataset_id,
            total_products_count=len(state.products),
            is_complete=False,
            error_message=f"保存结果失败: {error}",
        )
