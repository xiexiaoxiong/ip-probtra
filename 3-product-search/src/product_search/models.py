from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


DEFAULT_PLATFORMS = ["1688", "jd", "taobao", "tmall", "pdd", "suning"]


class ProductSearchInput(BaseModel):
    patent_record_id: int = Field(..., description="专利解析主记录ID")
    analysis_session_id: str = Field(default="", description="分析会话ID")
    input_keywords: list[str] | None = Field(default=None, description="显式关键词；null 表示读取 keyword_records")
    platforms: list[str] = Field(default_factory=lambda: list(DEFAULT_PLATFORMS))
    max_keywords: int = Field(default=8, ge=1, le=50)
    max_candidates_per_keyword: int = Field(default=6, ge=1, le=30)
    max_detail_candidates: int = Field(default=12, ge=1, le=100)
    max_products: int = Field(default=30, ge=1, le=200)
    request_timeout_seconds: int | None = Field(default=None, ge=5, le=120)
    serp_url_limit: int | None = Field(default=None, ge=1, le=10)
    persist: bool = Field(default=True, description="是否写入 product_detail_search_* 表")


class SearchQueryPlan(BaseModel):
    keyword: str
    original_keyword: str = ""
    platform: str
    query: str
    serp_url: str


class CandidateLink(BaseModel):
    keyword: str
    original_keyword: str = ""
    platform: str
    candidate_url: str
    title: str = ""
    snippet: str = ""
    source: str = ""
    rank: int = 0


class FetchResult(BaseModel):
    ok: bool
    url: str
    final_url: str
    status_code: int = 0
    html: str = ""
    provider: str = ""
    error_message: str = ""


class ParsedProductPage(BaseModel):
    product_id: str = ""
    platform: str = ""
    product_name: str = ""
    product_url: str = ""
    final_url: str = ""
    price: str = ""
    sales: str = ""
    brand: str = ""
    manufacturer: str = ""
    description: str = ""
    detail_text: str = ""
    picture: list[str] = Field(default_factory=list)
    raw_payload: dict[str, Any] = Field(default_factory=dict)


class QualityDecision(BaseModel):
    accepted: bool
    score: int
    status: Literal["accepted", "rejected", "error"]
    reasons: list[str] = Field(default_factory=list)
    flags: dict[str, Any] = Field(default_factory=dict)


class ProductResult(BaseModel):
    product_id: str = ""
    platform: str = ""
    product_name: str = ""
    product_url: str = ""
    final_url: str = ""
    price: str = ""
    sales: str = ""
    brand: str = ""
    manufacturer: str = ""
    description: str = ""
    detail_text: str = ""
    picture: list[str] = Field(default_factory=list)
    matched_keywords: list[str] = Field(default_factory=list)
    quality_score: int = 0
    quality_flags: dict[str, Any] = Field(default_factory=dict)
    raw_payload: dict[str, Any] = Field(default_factory=dict)


class CandidateDiagnostic(BaseModel):
    keyword: str
    platform: str
    candidate_url: str
    final_url: str = ""
    title: str = ""
    status: str = "queued"
    rejection_reason: str = ""
    quality_score: int = 0
    raw_payload: dict[str, Any] = Field(default_factory=dict)


class ProductSearchOutput(BaseModel):
    product_dataset_id: str
    product_detail_search_run_id: int = 0
    patent_record_id: int
    analysis_session_id: str = ""
    keywords: list[str] = Field(default_factory=list)
    total_candidate_links_count: int = 0
    accepted_products_count: int = 0
    rejected_candidates_count: int = 0
    is_complete: bool = True
    error_message: str = ""
    products: list[ProductResult] = Field(default_factory=list)
    candidates_preview: list[CandidateDiagnostic] = Field(default_factory=list)
