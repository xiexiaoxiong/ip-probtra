import logging
from typing import Any, Dict, List

from langchain_core.runnables import RunnableConfig
from langgraph.runtime import Runtime
from coze_coding_utils.runtime_ctx.context import Context

from graphs.state import ParseAndFetchInput, ParseAndFetchOutput

logger = logging.getLogger(__name__)


def _build_product_description(row) -> str:
    description = str(row.description or "").strip()
    raw_payload = row.raw_payload if isinstance(row.raw_payload, dict) else {}
    enrichment = raw_payload.get("secondary_enrichment") if isinstance(raw_payload, dict) else {}
    supplement_text = ""
    if isinstance(enrichment, dict):
        supplement_text = str(enrichment.get("supplement_text") or "").strip()
    if supplement_text and supplement_text not in description:
        return f"{description}\n\n【二次检索补充资料】\n{supplement_text}".strip()
    return description


def parse_and_fetch_node(
    state: ParseAndFetchInput,
    config: RunnableConfig,
    runtime: Runtime[Context]
) -> ParseAndFetchOutput:
    """
    title: 获取专利与商品数据
    desc: 从Postgres读取独立权利要求、说明书、附图和检索到的商品
    integrations: Postgres数据库
    """
    try:
        from storage.database.db import get_engine, get_session
        from storage.database.shared.model import (
            Base,
            PatentClaim,
            PatentFigure,
            PatentParseRecord,
            SearchProduct,
        )

        engine = get_engine()
        Base.metadata.create_all(
            bind=engine,
            tables=[
                PatentParseRecord.__table__,
                PatentClaim.__table__,
                PatentFigure.__table__,
                SearchProduct.__table__,
            ],
        )

        session = get_session()
        try:
            patent_record = (
                session.query(PatentParseRecord)
                .filter(PatentParseRecord.id == state.patent_record_id)
                .one_or_none()
            )
            if patent_record is None:
                return ParseAndFetchOutput(
                    patent_record_id=state.patent_record_id,
                    run_id=state.run_id,
                    claim_compare_run_id=state.claim_compare_run_id,
                    app_token="",
                    table_id="",
                    independent_claims=[],
                    specification_text="",
                    specification_images=[],
                    products=[],
                    error_status=f"未找到专利记录: {state.patent_record_id}",
                )

            claim_rows = (
                session.query(PatentClaim)
                .filter(
                    PatentClaim.record_id == state.patent_record_id,
                    PatentClaim.claim_type == "INDEPENDENT",
                )
                .order_by(PatentClaim.id.asc())
                .all()
            )
            figure_rows = (
                session.query(PatentFigure)
                .filter(PatentFigure.record_id == state.patent_record_id)
                .order_by(PatentFigure.id.asc())
                .all()
            )
            product_query = session.query(SearchProduct).filter(
                SearchProduct.patent_record_id == state.patent_record_id
            )
            if state.analysis_session_id:
                product_query = product_query.filter(
                    SearchProduct.analysis_session_id == state.analysis_session_id
                )
            product_rows = product_query.order_by(SearchProduct.id.asc()).all()
        finally:
            session.close()

        independent_claims = [
            {"claim_id": row.claim_id, "claim_text": row.claim_text}
            for row in claim_rows
            if row.claim_text
        ]

        specification = patent_record.specification or {}
        specification_text = "\n\n".join(
            f"{key}\n{value}" for key, value in specification.items() if value
        )
        specification_images = [row.figure_url for row in figure_rows if row.figure_url]

        products: List[Dict[str, Any]] = []
        for row in product_rows:
            product_images = row.picture if isinstance(row.picture, list) else []
            # 使用 search_products.id 作为当前分析内的稳定唯一键，避免业务侧 product_id
            # 在不同商品之间重复，导致多个商品的比对结果被错误合并到同一商品页。
            stable_product_id = str(row.id)
            products.append(
                {
                    "id": stable_product_id,
                    "name": row.product_name or "",
                    "description": _build_product_description(row),
                    "images": product_images,
                    "raw_data": {
                        **(row.raw_payload or {}),
                        "source_product_id": row.product_id,
                    },
                }
            )

        return ParseAndFetchOutput(
            patent_record_id=state.patent_record_id,
            run_id=state.run_id,
            claim_compare_run_id=state.claim_compare_run_id,
            app_token="",
            table_id="",
            independent_claims=independent_claims,
            specification_text=specification_text,
            specification_images=specification_images,
            products=products,
            error_status="",
        )
    except Exception as error:
        logger.error("读取数据库比对输入失败: %s", error, exc_info=True)
        return ParseAndFetchOutput(
            patent_record_id=state.patent_record_id,
            run_id=state.run_id,
            claim_compare_run_id=state.claim_compare_run_id,
            app_token="",
            table_id="",
            independent_claims=[],
            specification_text="",
            specification_images=[],
            products=[],
            error_status=f"读取数据库比对输入失败: {error}",
        )
