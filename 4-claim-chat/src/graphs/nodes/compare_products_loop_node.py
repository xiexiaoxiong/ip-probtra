import logging
from typing import List, Dict, Any
from concurrent.futures import ThreadPoolExecutor, as_completed
from langchain_core.runnables import RunnableConfig
from langgraph.runtime import Runtime
from coze_coding_utils.runtime_ctx.context import Context
from graphs.state import CompareLoopInput, CompareLoopOutput
from graphs.loop_graph import product_comparison_graph

logger = logging.getLogger(__name__)

# 并行线程数上限
MAX_PARALLEL_WORKERS: int = 10


def _score_band(score: float) -> str:
    if score > 70:
        return "明确相同"
    if score >= 30:
        return "中等相似"
    return "明确不相同" if score <= 0 else "低相似"


def _feature_key(feature: Dict[str, Any]) -> tuple[str, str, str]:
    feature_id = str(feature.get("feature_id", "")).strip()
    claim_id = str(feature.get("claim_id", "")).strip()
    feature_text = str(feature.get("feature_text", "")).strip()
    return (claim_id, feature_id, feature_text)


def _dedupe_features(features: List[Dict[str, str]]) -> List[Dict[str, str]]:
    """
    对当前 run 的技术特征去重。

    目标不是跨分析历史去重，而是避免同一轮模块4里把重复特征反复送给模型，
    从而导致同一商品页重复显示并放大 token 消耗。
    """
    deduped: List[Dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for feature in features:
        key = _feature_key(feature)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(feature)
    return deduped


def _compare_single_product(
    idx: int,
    product: Dict[str, Any],
    features: List[Dict[str, str]],
    specification_text: str
) -> Dict[str, Any]:
    """
    对单个商品执行特征比对（在子线程中运行）。
    所有独立权利要求的特征一起传入子图。
    """
    product_name: str = str(product.get("name", f"商品{idx + 1}"))
    logger.info(f"开始比对商品 {idx + 1}: {product_name}")

    # 构造子图输入
    sub_input: Dict[str, Any] = {
        "features": features,
        "product_data": product,
        "raw_analysis": [],
        "reviewed_analysis": [],
        "product_name": "",
        "comparison_result": {},
        "specification_text": specification_text
    }

    try:
        sub_result = product_comparison_graph.invoke(sub_input)
        comp_result: Dict[str, Any] = sub_result.get("comparison_result", {})
        if comp_result:
            comp_result["product_id"] = product.get("id")
        logger.info(f"商品 '{product_name}' 比对完成")
        return {"index": idx, "result": comp_result, "error": None}
    except Exception as e:
        logger.error(f"处理商品 '{product_name}' 时出错: {e}")
        # 异常处理：为该商品生成可兼容的新结构兜底结果
        error_features: List[Dict[str, Any]] = []
        claim_scores_map: Dict[str, Dict[str, Any]] = {}
        for feat in features:
            claim_id = str(feat.get("claim_id", ""))
            fallback_score = 0.0
            error_features.append({
                "feature_id": str(feat.get("feature_id", "")),
                "feature_text": str(feat.get("feature_text", "")),
                "evidence": "",
                "similarity_score": fallback_score,
                "score_band": _score_band(fallback_score),
                "reason": f"处理过程异常: {e}",
                "reasoning_type": "相关信息缺失",
                "claim_id": claim_id,
                "evidence_images": [],
                "score_rationale": "处理过程异常，按低相似兜底",
                "token_units": [],
                "feature_full_score": 0.0,
                "feature_awarded_score": 0.0,
                "feature_effective_length": int(feat.get("effective_length", 0) or 0),
                "matched_effective_length": 0,
                "claim_total_effective_length": 0,
                "zeroed_by_mismatch": False,
            })
            claim_scores_map[claim_id] = {
                "claim_id": claim_id,
                "similarity_score": fallback_score,
                "score_band": _score_band(fallback_score),
                "claim_total_effective_length": 0,
                "claim_matched_effective_length": 0,
                "zeroed_by_mismatch": False,
            }
        claim_scores = [
            claim_scores_map[claim_id]
            for claim_id in sorted(claim_scores_map)
        ]
        product_similarity_score = 0.0 if any(item.get("zeroed_by_mismatch") for item in claim_scores) else min(
            sum(float(item["similarity_score"]) for item in claim_scores),
            100.0,
        )
        error_result: Dict[str, Any] = {
            "product_id": product.get("id"),
            "product_name": product_name,
            "features": error_features,
            "claim_scores": claim_scores,
            "product_similarity_score": product_similarity_score,
            "product_score_band": _score_band(product_similarity_score),
        }
        return {"index": idx, "result": error_result, "error": str(e)}


def compare_products_loop_node(
    state: CompareLoopInput,
    config: RunnableConfig,
    runtime: Runtime[Context]
) -> CompareLoopOutput:
    """
    title: 并行比对商品技术特征
    desc: 使用线程池并行比对所有商品，每个商品独立调用子图完成特征分析+规则判定（包含所有独立权利要求的特征），最终汇总所有比对结果
    """
    raw_features: List[Dict[str, str]] = state.features
    features: List[Dict[str, str]] = _dedupe_features(raw_features)
    products: List[Dict[str, Any]] = state.products
    specification_text: str = state.specification_text

    if not features:
        logger.warning("技术特征列表为空，无法进行比对")
        return CompareLoopOutput(all_comparison_results=[])

    if not products:
        logger.warning("商品列表为空，无法进行比对")
        return CompareLoopOutput(all_comparison_results=[])

    # 统计独立权利要求数量
    claim_ids: set = set(str(f.get("claim_id", "")) for f in features)
    total: int = len(products)
    worker_count: int = min(total, MAX_PARALLEL_WORKERS)
    duplicate_count = max(0, len(raw_features) - len(features))
    logger.info(
        f"开始并行比对 {total} 个商品，{len(claim_ids)} 个独立权利要求，"
        f"技术特征 {len(features)} 个"
        + (f"（已去重 {duplicate_count} 个重复特征）" if duplicate_count else "")
        + f"，并发线程数: {worker_count}"
    )

    ordered_results: List[Dict[str, Any]] = [{}] * total

    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        future_to_idx: Dict[Any, int] = {}
        for idx, product in enumerate(products):
            future = executor.submit(_compare_single_product, idx, product, features, specification_text)
            future_to_idx[future] = idx

        completed: int = 0
        for future in as_completed(future_to_idx):
            completed += 1
            try:
                task_result: Dict[str, Any] = future.result()
                idx_key: int = int(task_result.get("index", 0))
                comp_result: Dict[str, Any] = task_result.get("result", {})
                if comp_result:
                    ordered_results[idx_key] = comp_result
                if task_result.get("error"):
                    logger.warning(f"商品 {idx_key + 1} 比对有异常: {task_result['error']}")
            except Exception as e:
                idx_val: int = future_to_idx[future]
                logger.error(f"获取商品 {idx_val + 1} 比对结果时出错: {e}")

            if completed % 10 == 0 or completed == total:
                logger.info(f"比对进度: {completed}/{total}")

    all_results: List[Dict[str, Any]] = [r for r in ordered_results if r]

    logger.info(f"全部比对完成，成功 {len(all_results)}/{total} 个商品")
    return CompareLoopOutput(all_comparison_results=all_results)
