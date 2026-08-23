from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


DEFAULT_PLATFORMS = ["TB", "TM", "XY", "1688", "JD", "PDD", "XHS", "DY"]


class AndunSearchInput(BaseModel):
    patent_record_id: int = Field(default=0, ge=0)
    analysis_session_id: str = ""
    input_keywords: list[str] | None = None
    task_name: str = ""
    platforms: list[str] = Field(default_factory=lambda: list(DEFAULT_PLATFORMS))
    max_keywords: int = Field(default=3, ge=1, le=20)
    page_size: int = Field(default=100, ge=1, le=100)
    max_pages: int = Field(default=10, ge=1, le=100)
    max_products: int = Field(default=300, ge=1, le=5000)
    poll_interval_seconds: int | None = Field(default=None, ge=2, le=120)
    max_wait_seconds: int | None = Field(default=None, ge=30, le=14400)
    persist: bool = True


class AndunProduct(BaseModel):
    task_id: str = ""
    trade_no: str = ""
    platform: str = ""
    platform_name: str = ""
    product_front_page: str = ""
    title: str = ""
    price: str = ""
    monthly_sales: int = 0
    product_url: str = ""
    store_name: str = ""
    store_url: str = ""
    shopkeeper_id: str = ""
    create_time: str = ""
    raw_payload: dict[str, Any] = Field(default_factory=dict)


class ApiCallMetric(BaseModel):
    method: str
    elapsed_ms: int
    http_status: int
    code: int
    message: str = ""
    attempt_count: int = 1
    retry_errors: list[str] = Field(default_factory=list)


class AndunSearchOutput(BaseModel):
    andun_search_run_id: int
    product_dataset_id: str
    patent_record_id: int
    analysis_session_id: str = ""
    andun_task_id: str = ""
    status: str
    remote_status: int | None = None
    keywords: list[str] = Field(default_factory=list)
    platforms: list[str] = Field(default_factory=list)
    product_count: int = 0
    submit_elapsed_ms: int = 0
    total_elapsed_ms: int = 0
    error_message: str = ""
    api_calls: list[ApiCallMetric] = Field(default_factory=list)
    products: list[AndunProduct] = Field(default_factory=list)


class AsyncRunAccepted(BaseModel):
    andun_search_run_id: int
    product_dataset_id: str
    status: str = "queued"
