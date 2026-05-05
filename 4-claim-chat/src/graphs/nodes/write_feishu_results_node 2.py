import re
import time
import logging
from typing import List, Dict, Any
from langchain_core.runnables import RunnableConfig
from langgraph.runtime import Runtime
from coze_coding_utils.runtime_ctx.context import Context
from graphs.state import WriteResultsInput, WriteResultsOutput
from tools.feishu_bitable import FeishuBitable

logger = logging.getLogger(__name__)

# 飞书写接口不支持并发，串行调用间隔（秒）
API_CALL_INTERVAL: float = 0.5
# 频率限制重试参数
MAX_RETRIES: int = 5
RETRY_BASE_DELAY: float = 2.0


def _safe_table_name(product_name: str) -> str:
    """
    生成安全的飞书表格名称。
    """
    name: str = product_name.strip()
    for ch in ["/", "\\", ":", "*", "?", '"', "<", ">", "|", "\n", "\r", "\t"]:
        name = name.replace(ch, "_")
    name = name.strip("_").strip()
    if not name:
        name = "未知商品"
    suffix: str = "_特征比对"
    max_main_len: int = 100 - len(suffix)
    if len(name) > max_main_len:
        name = name[:max_main_len]
    return name + suffix


def _extract_tenant_base_url(feishu_url: str) -> str:
    """
    从用户输入的飞书URL中提取租户域名前缀，构建正确的用户可访问 Base URL。
    """
    match = re.search(r"(https://[a-zA-Z0-9\-]+\.(?:feishu\.cn|larkoffice\.com))", feishu_url)
    if match:
        base: str = match.group(1)
        return f"{base}/base"
    return "https://feishu.cn/base"


def _call_with_retry(
    fn,
    *args,
    max_retries: int = MAX_RETRIES,
    base_delay: float = RETRY_BASE_DELAY,
    **kwargs
) -> dict:
    """带指数退避重试的飞书 API 调用。"""
    last_error: Exception = Exception("unknown")
    for attempt in range(max_retries):
        try:
            result: dict = fn(*args, **kwargs)
            return result
        except Exception as e:
            err_msg: str = str(e)
            is_rate_limit: bool = "99991400" in err_msg or "frequency limit" in err_msg.lower()
            is_server_error: bool = any(code in err_msg for code in ["99991403", "99991404", "timeout"])
            if is_rate_limit or is_server_error:
                delay: float = base_delay * (2 ** attempt)
                logger.warning(f"飞书 API 限流/服务错误，第 {attempt + 1}/{max_retries} 次重试，等待 {delay:.1f}s: {err_msg[:200]}")
                time.sleep(delay)
                last_error = e
                continue
            else:
                raise e
    raise last_error


def write_feishu_results_node(
    state: WriteResultsInput,
    config: RunnableConfig,
    runtime: Runtime[Context]
) -> WriteResultsOutput:
    """
    title: 写入比对结果到飞书
    desc: 为每个商品在飞书多维表格中创建子表格，将所有独立权利要求的比对结果写入同一个子表格。串行调用飞书API，带指数退避重试。
    integrations: 飞书多维表格
    """
    ctx = runtime.context
    app_token: str = state.app_token
    all_results: List[Dict[str, Any]] = state.all_comparison_results
    feishu_url: str = state.feishu_url

    # 从原始URL提取租户域名
    tenant_base_url: str = _extract_tenant_base_url(feishu_url)
    logger.info(f"使用租户域名前缀: {tenant_base_url} (原始URL: {feishu_url[:80]}...)")

    if not app_token:
        return WriteResultsOutput(
            result_summary="错误：缺少飞书app_token，无法写入结果",
            table_urls=[]
        )

    if not all_results:
        return WriteResultsOutput(
            result_summary="无比对结果可写入",
            table_urls=[]
        )

    bitable = FeishuBitable()
    success_count: int = 0
    fail_count: int = 0
    table_urls: List[str] = []
    failed_products: List[str] = []

    # 定义表格字段：增加 claim_id 字段，支持多个独立权利要求的比对结果
    fields_def: List[Dict[str, Any]] = [
        {"field_name": "claim_id", "type": 1},
        {"field_name": "feature_id", "type": 1},
        {"field_name": "feature_text", "type": 1},
        {"field_name": "evidence", "type": 1},
        {
            "field_name": "comparison_result",
            "type": 3,
            "property": {
                "options": [
                    {"name": "MATCH", "color": 0},
                    {"name": "NO_MATCH", "color": 1},
                    {"name": "UNCERTAIN", "color": 2}
                ]
            }
        },
        {"field_name": "reason", "type": 1},
        {
            "field_name": "reasoning_type",
            "type": 3,
            "property": {
                "options": [
                    {"name": "文字直接公开", "color": 0},
                    {"name": "从图片中看出", "color": 1},
                    {"name": "结合文字和图片毫无疑义得出", "color": 2},
                    {"name": "根据功能推导得出", "color": 3},
                    {"name": "相关信息缺失", "color": 4}
                ]
            }
        }
    ]

    total: int = len(all_results)

    for idx, result in enumerate(all_results):
        product_name: str = str(result.get("product_name", f"商品{idx + 1}"))
        features: list = result.get("features", [])
        table_name: str = _safe_table_name(product_name)

        # ===== 1. 创建子表格 =====
        new_table_id: str = ""
        try:
            time.sleep(API_CALL_INTERVAL)
            create_resp: dict = _call_with_retry(
                bitable.create_table,
                app_token=app_token,
                table_name=table_name,
                fields=fields_def
            )
            new_table_id = str(create_resp.get("data", {}).get("table_id", ""))
            if not new_table_id:
                logger.error(f"创建子表格失败，未返回table_id: {create_resp}")
                fail_count += 1
                failed_products.append(product_name)
                continue
        except Exception as e:
            logger.error(f"创建子表格 '{table_name}' 失败: {e}")
            fail_count += 1
            failed_products.append(product_name)
            continue

        logger.info(f"[{idx + 1}/{total}] 创建子表格成功: {table_name} (id={new_table_id})")

        # ===== 2. 写入比对结果记录 =====
        if not features:
            success_count += 1
            table_url: str = f"{tenant_base_url}/{app_token}?table={new_table_id}"
            table_urls.append(table_url)
            continue

        records: List[Dict[str, Any]] = []
        for feat in features:
            if not isinstance(feat, dict):
                continue
            record_fields: Dict[str, Any] = {
                "claim_id": str(feat.get("claim_id", "")),
                "feature_id": str(feat.get("feature_id", "")),
                "feature_text": str(feat.get("feature_text", "")),
                "evidence": str(feat.get("evidence", "")),
                "comparison_result": str(feat.get("comparison_result", "UNCERTAIN")),
                "reason": str(feat.get("reason", "")),
                "reasoning_type": str(feat.get("reasoning_type", "相关信息缺失"))
            }
            records.append({"fields": record_fields})

        # 分批写入
        batch_size: int = 450
        write_ok: bool = True
        for i in range(0, len(records), batch_size):
            batch: List[Dict[str, Any]] = records[i:i + batch_size]
            try:
                time.sleep(API_CALL_INTERVAL)
                _call_with_retry(
                    bitable.add_records,
                    app_token=app_token,
                    table_id=new_table_id,
                    records=batch
                )
            except Exception as e:
                logger.error(f"写入记录到 '{table_name}' 失败: {e}")
                write_ok = False
                break

        if write_ok:
            success_count += 1
            table_url = f"{tenant_base_url}/{app_token}?table={new_table_id}"
            table_urls.append(table_url)
        else:
            fail_count += 1
            failed_products.append(product_name)

        if (idx + 1) % 10 == 0 or (idx + 1) == total:
            logger.info(f"写入进度: {idx + 1}/{total}, 成功: {success_count}, 失败: {fail_count}")

    # ===== 生成摘要 =====
    total_products: int = len(all_results)
    total_features: int = 0
    match_count: int = 0
    no_match_count: int = 0
    uncertain_count: int = 0
    claim_stats: Dict[str, Dict[str, int]] = {}

    for r in all_results:
        for f in r.get("features", []):
            if isinstance(f, dict):
                total_features += 1
                cr: str = str(f.get("comparison_result", ""))
                cid: str = str(f.get("claim_id", ""))

                # 按claim_id统计
                if cid not in claim_stats:
                    claim_stats[cid] = {"match": 0, "no_match": 0, "uncertain": 0}
                claim_stats[cid][cr.lower()] = claim_stats[cid].get(cr.lower(), 0) + 1

                if cr == "MATCH":
                    match_count += 1
                elif cr == "NO_MATCH":
                    no_match_count += 1
                elif cr == "UNCERTAIN":
                    uncertain_count += 1

    # 按权利要求编号排序输出统计
    claim_summary_parts: List[str] = []
    for cid in sorted(claim_stats.keys(), key=lambda x: int(x) if x.isdigit() else 999):
        stats = claim_stats[cid]
        claim_summary_parts.append(
            f"权利要求{cid}: MATCH={stats.get('match', 0)}, NO_MATCH={stats.get('no_match', 0)}, UNCERTAIN={stats.get('uncertain', 0)}"
        )

    summary: str = (
        f"比对完成。共处理{total_products}个商品，{total_features}个技术特征。"
        f"MATCH: {match_count}, NO_MATCH: {no_match_count}, UNCERTAIN: {uncertain_count}。"
        f"写入成功: {success_count}, 失败: {fail_count}。"
    )
    if claim_summary_parts:
        summary += " " + "; ".join(claim_summary_parts) + "。"
    if failed_products:
        failed_str: str = ", ".join(failed_products[:10])
        if len(failed_products) > 10:
            failed_str += f" 等{len(failed_products)}个"
        summary += f"失败商品: {failed_str}。"

    logger.info("=" * 60)
    logger.info(summary)
    logger.info("=" * 60)

    return WriteResultsOutput(result_summary=summary, table_urls=table_urls)
