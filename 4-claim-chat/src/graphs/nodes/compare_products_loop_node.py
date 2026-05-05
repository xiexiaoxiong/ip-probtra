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
        # 异常处理：为该商品生成 UNCERTAIN 结果
        error_features: List[Dict[str, Any]] = []
        for feat in features:
            error_features.append({
                "feature_id": str(feat.get("feature_id", "")),
                "feature_text": str(feat.get("feature_text", "")),
                "evidence": "",
                "comparison_result": "UNCERTAIN",
                "reason": f"处理过程异常: {e}",
                "reasoning_type": "相关信息缺失",
                "claim_id": str(feat.get("claim_id", ""))
            })
        error_result: Dict[str, Any] = {
            "product_id": product.get("id"),
            "product_name": product_name,
            "features": error_features
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
    features: List[Dict[str, str]] = state.features
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
    logger.info(f"开始并行比对 {total} 个商品，{len(claim_ids)} 个独立权利要求，并发线程数: {worker_count}")

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
