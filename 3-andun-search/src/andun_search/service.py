from __future__ import annotations

import asyncio
import datetime as dt
import time
import uuid
from dataclasses import dataclass
from typing import Any

from andun_search.client import AndunApiError, AndunApiResult, AndunClient
from andun_search.config import Settings
from andun_search.db import (
    create_run,
    fetch_keywords,
    get_run_detail,
    insert_api_call,
    replace_products,
    update_run,
)
from andun_search.models import (
    AndunProduct,
    AndunSearchInput,
    AndunSearchOutput,
    ApiCallMetric,
)


REMOTE_STATUS_LABELS = {0: "collecting", 10: "organizing", 20: "completed"}
ALLOWED_PLATFORMS = {"TB", "TM", "XY", "1688", "JD", "PDD", "XHS", "DY"}


@dataclass(frozen=True)
class PreparedRun:
    run_id: int
    product_dataset_id: str
    keywords: list[str]
    platforms: list[str]


def _clean_platforms(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        platform = str(value or "").strip().upper()
        if platform in ALLOWED_PLATFORMS and platform not in result:
            result.append(platform)
    return result


def normalize_product(record: dict[str, Any]) -> AndunProduct:
    try:
        monthly_sales = int(record.get("monthlySales") or 0)
    except (TypeError, ValueError):
        monthly_sales = 0
    return AndunProduct(
        task_id=str(record.get("taskId") or ""),
        trade_no=str(record.get("tradeNo") or ""),
        platform=str(record.get("platform") or ""),
        platform_name=str(record.get("platformName") or ""),
        product_front_page=str(record.get("productFrontPage") or ""),
        title=str(record.get("title") or ""),
        price=str(record.get("price") if record.get("price") is not None else ""),
        monthly_sales=max(0, monthly_sales),
        product_url=str(record.get("productUrl") or ""),
        store_name=str(record.get("storeName") or ""),
        store_url=str(record.get("storeUrl") or ""),
        shopkeeper_id=str(record.get("shopkeeperId") or ""),
        create_time=str(record.get("createTime") or ""),
        raw_payload=dict(record),
    )


class AndunSearchService:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.client = AndunClient(settings)

    def prepare(self, payload: AndunSearchInput) -> PreparedRun:
        keywords = fetch_keywords(
            payload.patent_record_id,
            payload.analysis_session_id,
            payload.input_keywords,
            payload.max_keywords,
        )
        if not keywords:
            raise ValueError("没有可用于安盾检索的关键词")
        platforms = _clean_platforms(payload.platforms)
        if not platforms:
            raise ValueError("至少需要一个有效平台")
        product_dataset_id = f"andun_{uuid.uuid4().hex}"
        run_id = create_run(
            patent_record_id=payload.patent_record_id,
            analysis_session_id=payload.analysis_session_id,
            product_dataset_id=product_dataset_id,
            keywords=keywords,
            platforms=platforms,
            raw_payload={
                "input": payload.model_dump(),
                "api_env": self.settings.api_env,
                "api_base_url": self.settings.api_base_url,
            },
        )
        return PreparedRun(run_id, product_dataset_id, keywords, platforms)

    def _record_call(
        self,
        run_id: int,
        result: AndunApiResult,
        request_body: dict[str, Any],
    ) -> ApiCallMetric:
        metric = ApiCallMetric(
            method=result.method,
            elapsed_ms=result.elapsed_ms,
            http_status=result.http_status,
            code=result.code,
            message=result.message,
            attempt_count=result.attempt_count,
            retry_errors=list(result.retry_errors),
        )
        data = result.data if isinstance(result.data, dict) else {}
        summary = {
            "data_keys": sorted(data.keys()),
            "record_count": len(data.get("records", [])) if isinstance(data.get("records"), list) else None,
            "total": data.get("total"),
            "pages": data.get("pages"),
            "current": data.get("current"),
            "status": data.get("status"),
            "taskId": str(data.get("taskId") or ""),
            "attempt_count": result.attempt_count,
            "retry_errors": list(result.retry_errors),
        }
        insert_api_call(
            run_id=run_id,
            metric=metric,
            request_body=request_body,
            response_summary=summary,
        )
        return metric

    @staticmethod
    def _require_success(result: AndunApiResult) -> dict[str, Any]:
        if result.code != 0:
            raise AndunApiError(f"{result.method} 返回 code={result.code}: {result.message}")
        if not isinstance(result.data, dict):
            raise AndunApiError(f"{result.method} 成功响应缺少 data 对象")
        return result.data

    async def execute(
        self,
        payload: AndunSearchInput,
        prepared: PreparedRun,
        *,
        existing_task_id: str = "",
        elapsed_offset_ms: int = 0,
        existing_remote_status: int | None = None,
    ) -> AndunSearchOutput:
        started = time.perf_counter()
        api_calls: list[ApiCallMetric] = []
        task_id = existing_task_id.strip()
        remote_status: int | None = existing_remote_status
        products: list[AndunProduct] = []
        try:
            if not self.settings.credentials_configured:
                raise AndunApiError("尚未配置 ANDUN_APP_KEY 和 ANDUN_APP_SECRET")
            if task_id:
                update_run(
                    prepared.run_id,
                    andun_task_id=task_id,
                    status=REMOTE_STATUS_LABELS.get(remote_status or 0, "collecting"),
                    remote_status=remote_status,
                    error_message="",
                    finished_at=None,
                )
            else:
                update_run(prepared.run_id, status="submitting")
                task_name = payload.task_name.strip() or f"专利商品检索-{prepared.run_id}"
                add_body = {
                    "name": task_name,
                    "platforms": prepared.platforms,
                    "searchKeywords": prepared.keywords,
                }
                add_result = await self.client.create_task(
                    name=task_name,
                    keywords=prepared.keywords,
                    platforms=prepared.platforms,
                )
                api_calls.append(self._record_call(prepared.run_id, add_result, add_body))
                add_data = self._require_success(add_result)
                task_id = str(add_data.get("taskId") or "")
                if not task_id:
                    raise AndunApiError("创建任务成功响应缺少 taskId")
                update_run(
                    prepared.run_id,
                    andun_task_id=task_id,
                    status="collecting",
                    remote_status=0,
                    submit_elapsed_ms=add_result.elapsed_ms,
                )

            poll_interval = payload.poll_interval_seconds or self.settings.poll_interval_seconds
            max_wait = payload.max_wait_seconds or self.settings.max_wait_seconds
            poll_started = time.perf_counter()
            while True:
                if time.perf_counter() - poll_started > max_wait:
                    raise TimeoutError(f"安盾任务在 {max_wait} 秒内未完成")
                info_body = {"taskId": task_id}
                info_result = await self.client.task_info(task_id)
                api_calls.append(self._record_call(prepared.run_id, info_result, info_body))
                info_data = self._require_success(info_result)
                try:
                    remote_status = int(info_data.get("status"))
                except (TypeError, ValueError):
                    raise AndunApiError(f"任务状态无效: {info_data.get('status')}")
                local_status = REMOTE_STATUS_LABELS.get(remote_status, "collecting")
                update_run(prepared.run_id, status=local_status, remote_status=remote_status)
                if remote_status == 20:
                    break
                await asyncio.sleep(poll_interval)

            seen: set[str] = set()
            for current in range(1, payload.max_pages + 1):
                list_body = {"current": current, "size": payload.page_size, "taskId": task_id}
                list_result = await self.client.goods_list(task_id, current, payload.page_size)
                api_calls.append(self._record_call(prepared.run_id, list_result, list_body))
                list_data = self._require_success(list_result)
                records = list_data.get("records", [])
                if not isinstance(records, list):
                    raise AndunApiError("商品列表 records 不是数组")
                for record in records:
                    if not isinstance(record, dict):
                        continue
                    product = normalize_product(record)
                    key = product.product_url or product.trade_no
                    if not key or key in seen:
                        continue
                    seen.add(key)
                    products.append(product)
                    if len(products) >= payload.max_products:
                        break
                if len(products) >= payload.max_products:
                    break
                try:
                    pages = int(list_data.get("pages") or 1)
                except (TypeError, ValueError):
                    pages = 1
                if current >= pages or not records:
                    break

            if payload.persist:
                replace_products(
                    run_id=prepared.run_id,
                    patent_record_id=payload.patent_record_id,
                    analysis_session_id=payload.analysis_session_id,
                    products=products,
                )
            total_elapsed_ms = elapsed_offset_ms + round((time.perf_counter() - started) * 1000)
            update_run(
                prepared.run_id,
                status="completed",
                remote_status=remote_status,
                product_count=len(products),
                total_elapsed_ms=total_elapsed_ms,
                error_message="",
                finished_at=dt.datetime.now(dt.timezone.utc),
            )
            return AndunSearchOutput(
                andun_search_run_id=prepared.run_id,
                product_dataset_id=prepared.product_dataset_id,
                patent_record_id=payload.patent_record_id,
                analysis_session_id=payload.analysis_session_id,
                andun_task_id=task_id,
                status="completed",
                remote_status=remote_status,
                keywords=prepared.keywords,
                platforms=prepared.platforms,
                product_count=len(products),
                submit_elapsed_ms=api_calls[0].elapsed_ms if not existing_task_id and api_calls else 0,
                total_elapsed_ms=total_elapsed_ms,
                api_calls=api_calls,
                products=products,
            )
        except Exception as error:
            total_elapsed_ms = elapsed_offset_ms + round((time.perf_counter() - started) * 1000)
            status = "timeout" if isinstance(error, TimeoutError) else "error"
            update_run(
                prepared.run_id,
                andun_task_id=task_id or None,
                status=status,
                remote_status=remote_status,
                product_count=len(products),
                total_elapsed_ms=total_elapsed_ms,
                error_message=str(error),
                finished_at=dt.datetime.now(dt.timezone.utc),
            )
            return AndunSearchOutput(
                andun_search_run_id=prepared.run_id,
                product_dataset_id=prepared.product_dataset_id,
                patent_record_id=payload.patent_record_id,
                analysis_session_id=payload.analysis_session_id,
                andun_task_id=task_id,
                status=status,
                remote_status=remote_status,
                keywords=prepared.keywords,
                platforms=prepared.platforms,
                product_count=len(products),
                submit_elapsed_ms=api_calls[0].elapsed_ms if not existing_task_id and api_calls else 0,
                total_elapsed_ms=total_elapsed_ms,
                error_message=str(error),
                api_calls=api_calls,
                products=products,
            )
        finally:
            await self.client.aclose()

    def get_run(self, run_id: int) -> dict[str, Any] | None:
        return get_run_detail(run_id)
