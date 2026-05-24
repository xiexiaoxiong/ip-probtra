from sqlalchemy import Boolean, DateTime, Float, Integer, JSON, PrimaryKeyConstraint, Text, text
from typing import Optional
import datetime

from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

class Base(DeclarativeBase):
    pass


class PatentParseRecord(Base):
    __tablename__ = "patent_parse_records"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="patent_parse_records_pkey"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    task_id: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    patent_number: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    patent_holder: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    title: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    application_date: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    priority_date: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    specification: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    parse_errors: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(True), server_default=text("now()"))
    updated_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True), server_default=text("now()"))


class PatentClaim(Base):
    __tablename__ = "patent_claims"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="patent_claims_pkey"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    record_id: Mapped[int] = mapped_column(Integer, nullable=False)
    claim_id: Mapped[str] = mapped_column(Text, nullable=False)
    claim_type: Mapped[str] = mapped_column(Text, nullable=False)
    claim_text: Mapped[str] = mapped_column(Text, nullable=False)
    parent_claim_id: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    sentence_units: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(True), server_default=text("now()"))


class PatentFigure(Base):
    __tablename__ = "patent_figures"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="patent_figures_pkey"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    record_id: Mapped[int] = mapped_column(Integer, nullable=False)
    figure_id: Mapped[str] = mapped_column(Text, nullable=False)
    figure_url: Mapped[str] = mapped_column(Text, nullable=False)
    figure_description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    storage_key: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(True), server_default=text("now()"))


class SearchRun(Base):
    __tablename__ = "search_runs"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="search_runs_pkey"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    patent_record_id: Mapped[int] = mapped_column(Integer, nullable=False)
    analysis_session_id: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    product_dataset_id: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    retrieval_start_time: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    successful_keywords_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    failed_keywords_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    total_products_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    platforms_queried: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    is_complete: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(True), server_default=text("now()"))
    updated_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True), server_default=text("now()"))


class SearchProduct(Base):
    __tablename__ = "search_products"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="search_products_pkey"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    search_run_id: Mapped[int] = mapped_column(Integer, nullable=False)
    patent_record_id: Mapped[int] = mapped_column(Integer, nullable=False)
    analysis_session_id: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    product_id: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    product_name: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    product_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    product_source: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    price: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    brand: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    manufacturer: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    matched_keywords: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    picture: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    raw_payload: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(True), server_default=text("now()"))


class ClaimCompareRun(Base):
    __tablename__ = "claim_compare_runs"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="claim_compare_runs_pkey"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    patent_record_id: Mapped[int] = mapped_column(Integer, nullable=False)
    analysis_session_id: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    run_id: Mapped[Optional[str]] = mapped_column(Text, nullable=True, unique=True)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'queued'"))
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    result_summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    product_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    started_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True), nullable=True)
    finished_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True), nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(True), server_default=text("now()"))
    updated_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True), server_default=text("now()"))


class ClaimCompareResult(Base):
    __tablename__ = "claim_compare_results"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="claim_compare_results_pkey"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    claim_compare_run_id: Mapped[int] = mapped_column(Integer, nullable=False)
    patent_record_id: Mapped[int] = mapped_column(Integer, nullable=False)
    analysis_session_id: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    product_id: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    product_name: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    claim_id: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    feature_id: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    feature_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    evidence: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    comparison_result: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    reasoning_type: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    evidence_images: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    raw_payload: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(True), server_default=text("now()"))
