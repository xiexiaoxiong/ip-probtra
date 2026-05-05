from sqlalchemy import DateTime, Float, Integer, JSON, PrimaryKeyConstraint, Text, text
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
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), server_default=text("now()")
    )
    updated_at: Mapped[Optional[datetime.datetime]] = mapped_column(
        DateTime(True), server_default=text("now()")
    )


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
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), server_default=text("now()")
    )


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
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), server_default=text("now()")
    )


class KeywordRun(Base):
    __tablename__ = "keyword_runs"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="keyword_runs_pkey"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    patent_record_id: Mapped[int] = mapped_column(Integer, nullable=False)
    analysis_session_id: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    workflow_variant: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    keywords_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), server_default=text("now()")
    )
    updated_at: Mapped[Optional[datetime.datetime]] = mapped_column(
        DateTime(True), server_default=text("now()")
    )


class KeywordRecord(Base):
    __tablename__ = "keyword_records"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="keyword_records_pkey"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    keyword_run_id: Mapped[int] = mapped_column(Integer, nullable=False)
    patent_record_id: Mapped[int] = mapped_column(Integer, nullable=False)
    analysis_session_id: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    keyword_id: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    claim_id: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    keyword_text: Mapped[str] = mapped_column(Text, nullable=False)
    keyword_type: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    source_location: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    generation_method: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    confidence_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    raw_payload: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), server_default=text("now()")
    )
