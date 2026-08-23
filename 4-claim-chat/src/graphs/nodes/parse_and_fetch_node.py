import logging
from typing import Any, Dict, List

from langchain_core.runnables import RunnableConfig
from langgraph.runtime import Runtime
from coze_coding_utils.runtime_ctx.context import Context
from sqlalchemy import text

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


def _build_product_detail_description(row: Dict[str, Any]) -> str:
    parts = [
        str(row.get("description") or "").strip(),
        str(row.get("detail_text") or "").strip(),
    ]
    return "\n\n".join(part for part in parts if part)


def _load_product_detail_rows(session, patent_record_id: int, analysis_session_id: str) -> List[Dict[str, Any]]:
    """
    Read products produced by 3-product-search. Module4 still prefers the legacy
    search_products table; this fallback lets test/prototype flows compare the
    new product-detail module without copying rows into the old table.
    """
    sql = """
      WITH latest_run AS (
        SELECT id
        FROM product_detail_search_runs
        WHERE patent_record_id = :patent_record_id
          AND (:analysis_session_id = '' OR analysis_session_id = :analysis_session_id)
        ORDER BY created_at DESC, id DESC
        LIMIT 1
      )
      SELECT id, run_id, product_id, platform, product_name, product_url, final_url,
             price, sales, brand, manufacturer, matched_keywords, description,
             detail_text, picture, quality_score, quality_flags, raw_payload,
             created_at
      FROM product_detail_search_products
      WHERE run_id = (SELECT id FROM latest_run)
      ORDER BY id ASC
      LIMIT 30
    """
    try:
        rows = session.execute(
            text(sql),
            {
                "patent_record_id": patent_record_id,
                "analysis_session_id": analysis_session_id or "",
            },
        ).mappings().all()
    except Exception as error:
        logger.warning("读取 product_detail_search_products 失败，跳过新模块三兜底: %s", error)
        return []
    return [dict(row) for row in rows]


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
            product_detail_rows = []
            if not product_rows:
                product_detail_rows = _load_product_detail_rows(
                    session,
                    state.patent_record_id,
                    state.analysis_session_id,
                )
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
        if not products:
            for row in product_detail_rows:
                product_images = row.get("picture") if isinstance(row.get("picture"), list) else []
                stable_product_id = f"product-detail-{row.get('id')}"
                raw_payload = row.get("raw_payload") if isinstance(row.get("raw_payload"), dict) else {}
                quality_flags = row.get("quality_flags") if isinstance(row.get("quality_flags"), dict) else {}
                products.append(
                    {
                        "id": stable_product_id,
                        "name": row.get("product_name") or "",
                        "description": _build_product_detail_description(row),
                        "images": product_images,
                        "raw_data": {
                            **raw_payload,
                            "source_product_id": row.get("product_id"),
                            "source_product_detail_search_run_id": row.get("run_id"),
                            "source_product_detail_search_product_id": row.get("id"),
                            "source_product_detail_url": row.get("final_url") or row.get("product_url"),
                            "source_platform": row.get("platform"),
                            "quality_score": row.get("quality_score"),
                            "quality_flags": quality_flags,
                            "module3_product_detail_fallback": True,
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
