import logging
import datetime
from typing import Any, Dict, List

from langchain_core.runnables import RunnableConfig
from langgraph.runtime import Runtime
from coze_coding_utils.runtime_ctx.context import Context

from graphs.state import WriteResultsInput, WriteResultsOutput

logger = logging.getLogger(__name__)


def _build_summary(all_results: List[Dict[str, Any]]) -> str:
    total_products = len(all_results)
    total_features = 0
    green_products = 0
    yellow_products = 0
    red_products = 0
    mismatch_zeroed_claims = 0
    match_units = 0
    uncertain_units = 0
    mismatch_units = 0
    product_scores: List[float] = []

    for result in all_results:
        score = float(result.get("product_similarity_score", 0) or 0)
        product_scores.append(score)
        if score > 70:
            green_products += 1
        elif score >= 30:
            yellow_products += 1
        else:
            red_products += 1
        for claim_score in result.get("claim_scores", []):
            if isinstance(claim_score, dict) and bool(claim_score.get("zeroed_by_mismatch")):
                mismatch_zeroed_claims += 1
        for feature in result.get("features", []):
            if not isinstance(feature, dict):
                continue
            total_features += 1
            for unit in feature.get("token_units", []):
                if not isinstance(unit, dict):
                    continue
                status = str(unit.get("status", ""))
                if status == "match":
                    match_units += 1
                elif status == "mismatch":
                    mismatch_units += 1
                else:
                    uncertain_units += 1

    avg_product_score = round(sum(product_scores) / len(product_scores), 1) if product_scores else 0
    max_product_score = max(product_scores, default=0)

    return (
        f"完成 {total_products} 个商品比对；"
        f"特征总数 {total_features}；"
        f"绿色商品={green_products}；黄色商品={yellow_products}；红色商品={red_products}；"
        f"claim归零次数={mismatch_zeroed_claims}；"
        f"单元匹配={match_units}；单元待确认={uncertain_units}；单元不相同={mismatch_units}；"
        f"商品平均分={avg_product_score}；商品最高分={max_product_score}"
    )


def write_feishu_results_node(
    state: WriteResultsInput,
    config: RunnableConfig,
    runtime: Runtime[Context]
) -> WriteResultsOutput:
    """
    title: 写入比对结果
    desc: 将比对结果写入Postgres
    integrations: Postgres数据库
    """
    try:
        from storage.database.db import get_engine, get_session
        from storage.database.shared.model import Base, ClaimCompareResult, ClaimCompareRun

        engine = get_engine()
        Base.metadata.create_all(
            bind=engine,
            tables=[ClaimCompareRun.__table__, ClaimCompareResult.__table__],
        )

        all_results = state.all_comparison_results
        summary = _build_summary(all_results) if all_results else "无比对结果可写入"
        session = get_session()
        try:
            compare_run_id = int(state.claim_compare_run_id or 0)
            compare_run = None
            if compare_run_id > 0:
                compare_run = session.get(ClaimCompareRun, compare_run_id)
            if compare_run is None and state.run_id:
                compare_run = session.query(ClaimCompareRun).filter(ClaimCompareRun.run_id == state.run_id).one_or_none()
            if compare_run is None:
                compare_run = ClaimCompareRun(
                    patent_record_id=state.patent_record_id,
                    analysis_session_id=state.analysis_session_id or None,
                    run_id=state.run_id or None,
                    status="running",
                    started_at=datetime.datetime.now(datetime.timezone.utc),
                )
                session.add(compare_run)
                session.flush()
            compare_run_id = int(compare_run.id)
            compare_run.result_summary = summary
            compare_run.product_count = len(all_results)
            compare_run.status = "completed"
            compare_run.error_message = None
            compare_run.finished_at = datetime.datetime.now(datetime.timezone.utc)
            compare_run.updated_at = datetime.datetime.now(datetime.timezone.utc)

            session.query(ClaimCompareResult).filter(
                ClaimCompareResult.claim_compare_run_id == compare_run_id
            ).delete(synchronize_session=False)

            rows: List[ClaimCompareResult] = []
            for result in all_results:
                product_id = result.get("product_id") or result.get("id")
                product_name = result.get("product_name", "")
                for feature in result.get("features", []):
                    if not isinstance(feature, dict):
                        continue
                    # 提取 evidence_images
                    evidence_images = feature.get("evidence_images", [])
                    if not isinstance(evidence_images, list):
                        evidence_images = []
                    
                    rows.append(
                        ClaimCompareResult(
                            claim_compare_run_id=compare_run_id,
                            patent_record_id=state.patent_record_id,
                            analysis_session_id=state.analysis_session_id or None,
                            product_id=str(product_id) if product_id is not None else None,
                            product_name=product_name,
                            claim_id=str(feature.get("claim_id", "")) or None,
                            feature_id=str(feature.get("feature_id", "")) or None,
                            feature_text=str(feature.get("feature_text", "")) or None,
                            evidence=str(feature.get("evidence", "")) or None,
                            comparison_result=None,
                            similarity_score=int(round(float(feature.get("similarity_score", 0) or 0))),
                            score_band=str(feature.get("score_band", "")) or None,
                            feature_full_score=float(feature.get("feature_full_score", 0.0) or 0.0),
                            feature_awarded_score=float(feature.get("feature_awarded_score", 0.0) or 0.0),
                            feature_effective_length=int(feature.get("feature_effective_length", 0) or 0),
                            matched_effective_length=int(feature.get("matched_effective_length", 0) or 0),
                            claim_total_effective_length=int(feature.get("claim_total_effective_length", 0) or 0),
                            zeroed_by_mismatch=bool(feature.get("zeroed_by_mismatch", False)),
                            reason=str(feature.get("reason", "")) or None,
                            reasoning_type=str(feature.get("reasoning_type", "")) or None,
                            evidence_images=evidence_images if evidence_images else None,
                            token_units=feature.get("token_units", None),
                            raw_payload=feature,
                        )
                    )

            if rows:
                session.add_all(rows)
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

        logger.info(
            "比对结果已写入数据库, patent_record_id=%s, claim_compare_run_id=%s, rows=%s",
            state.patent_record_id,
            compare_run_id,
            len(rows),
        )
        return WriteResultsOutput(
            claim_compare_run_id=compare_run_id,
            run_id=state.run_id,
            result_summary=summary,
            table_urls=[],
        )
    except Exception as error:
        logger.error("写入比对结果失败: %s", error, exc_info=True)
        return WriteResultsOutput(
            claim_compare_run_id=int(state.claim_compare_run_id or 0),
            run_id=state.run_id,
            result_summary=f"写入比对结果失败: {error}",
            table_urls=[],
        )
