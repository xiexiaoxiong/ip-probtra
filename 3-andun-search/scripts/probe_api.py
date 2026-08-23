#!/usr/bin/env python3
"""对安盾任务状态接口做只读连通性和时延测试，不输出凭据。"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from andun_search.client import AndunClient  # noqa: E402
from andun_search.config import get_settings  # noqa: E402


async def probe(task_id: str, *, goods_list: bool = False, size: int = 20) -> int:
    settings = get_settings()
    if not settings.credentials_configured:
        print(json.dumps({"ok": False, "error": "ANDUN_APP_KEY/ANDUN_APP_SECRET 未配置"}, ensure_ascii=False))
        return 2
    client = AndunClient(settings)
    try:
        result = (
            await client.goods_list(task_id, current=1, size=size)
            if goods_list
            else await client.task_info(task_id)
        )
    finally:
        await client.aclose()
    data = result.data if isinstance(result.data, dict) else {}
    records = data.get("records") if isinstance(data.get("records"), list) else []
    samples = []
    for record in records[:3]:
        if not isinstance(record, dict):
            continue
        samples.append(
            {
                "platform": record.get("platform"),
                "title": record.get("title"),
                "price": record.get("price"),
                "has_front_page": bool(record.get("productFrontPage")),
                "has_product_url": bool(record.get("productUrl")),
            }
        )
    print(
        json.dumps(
            {
                "ok": result.code == 0,
                "api_env": settings.api_env,
                "method": result.method,
                "http_status": result.http_status,
                "code": result.code,
                "message": result.message,
                "elapsed_ms": result.elapsed_ms,
                "attempt_count": result.attempt_count,
                "retry_errors": list(result.retry_errors),
                "data_keys": sorted(data.keys()),
                "remote_status": data.get("status"),
                "records_count": len(records),
                "total": data.get("total"),
                "pages": data.get("pages"),
                "current": data.get("current"),
                "samples": samples,
            },
            ensure_ascii=False,
        )
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task-id", default="1851920933728370690")
    parser.add_argument("--goods-list", action="store_true", help="只读探测第一页商品数据")
    parser.add_argument("--size", type=int, default=20, choices=range(1, 101), metavar="1..100")
    args = parser.parse_args()
    return asyncio.run(probe(args.task_id, goods_list=args.goods_list, size=args.size))


if __name__ == "__main__":
    raise SystemExit(main())
