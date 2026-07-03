import argparse
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException

from product_search.config import bootstrap_local_env, get_settings
from product_search.db import ensure_tables, get_run_detail
from product_search.models import ProductSearchInput, ProductSearchOutput
from product_search.service import ProductSearchService


bootstrap_local_env()

app = FastAPI(title="Patent Product Detail Search", version="0.1.0")


@app.on_event("startup")
def _startup() -> None:
    ensure_tables()


@app.get("/health")
def health() -> dict[str, Any]:
    settings = get_settings()
    return {
        "status": "ok",
        "module": "3-product-search",
        "brightdata_api_key_configured": bool(settings.brightdata_api_key),
        "brightdata_serp_zone_configured": bool(settings.brightdata_serp_zone),
        "brightdata_unlocker_zone_configured": bool(settings.brightdata_unlocker_zone),
        "brightdata_auto_zone_discovery": bool(settings.brightdata_api_key),
        "direct_fetch_fallback": settings.allow_direct_fetch_fallback,
    }


@app.post("/run", response_model=ProductSearchOutput)
async def run_product_search(payload: ProductSearchInput) -> ProductSearchOutput:
    try:
        service = ProductSearchService(get_settings())
        return await service.run(payload)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/runs/{run_id}")
def read_run(run_id: int) -> dict[str, Any]:
    detail = get_run_detail(run_id)
    if not detail:
        raise HTTPException(status_code=404, detail=f"run not found: {run_id}")
    return detail


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("-m", "--mode", default="http", choices=["http"])
    parser.add_argument("-p", "--port", default=5107, type=int)
    args = parser.parse_args()
    uvicorn.run(app, host="127.0.0.1", port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
