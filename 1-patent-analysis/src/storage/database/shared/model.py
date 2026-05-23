from coze_coding_dev_sdk.database import Base

from sqlalchemy import BigInteger, Boolean, Column, DateTime, Double, Integer, Numeric, PrimaryKeyConstraint, Table, Text, text, JSON
from sqlalchemy.dialects.postgresql import OID
from typing import Optional
import datetime

from sqlalchemy.orm import Mapped, mapped_column

class HealthCheck(Base):
    __tablename__ = 'health_check'
    __table_args__ = (
        PrimaryKeyConstraint('id', name='health_check_pkey'),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    updated_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True), server_default=text('now()'))


# ==================== 专利解析模块表结构 ====================

class PatentParseRecord(Base):
    """专利解析记录主表"""
    __tablename__ = 'patent_parse_records'
    __table_args__ = (
        PrimaryKeyConstraint('id', name='patent_parse_records_pkey'),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    task_id: Mapped[str] = mapped_column(Text, nullable=False, unique=True, comment="任务ID")
    patent_number: Mapped[Optional[str]] = mapped_column(Text, nullable=True, comment="专利号")
    patent_holder: Mapped[Optional[str]] = mapped_column(Text, nullable=True, comment="专利权人")
    title: Mapped[Optional[str]] = mapped_column(Text, nullable=True, comment="专利标题")
    application_date: Mapped[Optional[str]] = mapped_column(Text, nullable=True, comment="申请日期")
    priority_date: Mapped[Optional[str]] = mapped_column(Text, nullable=True, comment="优先权日期")
    specification: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True, comment="说明书章节内容")
    parse_errors: Mapped[Optional[list]] = mapped_column(JSON, nullable=True, comment="解析错误列表")
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), server_default=text('now()'), comment="创建时间"
    )
    updated_at: Mapped[Optional[datetime.datetime]] = mapped_column(
        DateTime(True), server_default=text('now()'), comment="更新时间"
    )


class PatentClaim(Base):
    """权利要求表"""
    __tablename__ = 'patent_claims'
    __table_args__ = (
        PrimaryKeyConstraint('id', name='patent_claims_pkey'),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    record_id: Mapped[int] = mapped_column(Integer, nullable=False, comment="关联的解析记录ID")
    claim_id: Mapped[str] = mapped_column(Text, nullable=False, comment="权利要求编号")
    claim_type: Mapped[str] = mapped_column(Text, nullable=False, comment="权利要求类型")
    claim_text: Mapped[str] = mapped_column(Text, nullable=False, comment="权利要求原文")
    parent_claim_id: Mapped[Optional[str]] = mapped_column(Text, nullable=True, comment="父权利要求编号")
    sentence_units: Mapped[Optional[list]] = mapped_column(JSON, nullable=True, comment="句子单元列表")
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), server_default=text('now()'), comment="创建时间"
    )


class PatentFigure(Base):
    """附图表"""
    __tablename__ = 'patent_figures'
    __table_args__ = (
        PrimaryKeyConstraint('id', name='patent_figures_pkey'),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    record_id: Mapped[int] = mapped_column(Integer, nullable=False, comment="关联的解析记录ID")
    figure_id: Mapped[str] = mapped_column(Text, nullable=False, comment="附图编号")
    figure_url: Mapped[str] = mapped_column(Text, nullable=False, comment="附图URL")
    figure_description: Mapped[Optional[str]] = mapped_column(Text, nullable=True, comment="附图说明")
    storage_key: Mapped[Optional[str]] = mapped_column(Text, nullable=True, comment="对象存储key")
    file_path: Mapped[Optional[str]] = mapped_column(Text, nullable=True, comment="附图本地文件路径")
    mime_type: Mapped[Optional[str]] = mapped_column(Text, nullable=True, comment="附图MIME类型")
    file_size: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, comment="附图文件大小")
    file_sha256: Mapped[Optional[str]] = mapped_column(Text, nullable=True, comment="附图文件SHA256")
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), server_default=text('now()'), comment="创建时间"
    )


t_pg_stat_statements = Table(
    'pg_stat_statements', Base.metadata,
    Column('userid', OID),
    Column('dbid', OID),
    Column('toplevel', Boolean),
    Column('queryid', BigInteger),
    Column('query', Text),
    Column('plans', BigInteger),
    Column('total_plan_time', Double(53)),
    Column('min_plan_time', Double(53)),
    Column('max_plan_time', Double(53)),
    Column('mean_plan_time', Double(53)),
    Column('stddev_plan_time', Double(53)),
    Column('calls', BigInteger),
    Column('total_exec_time', Double(53)),
    Column('min_exec_time', Double(53)),
    Column('max_exec_time', Double(53)),
    Column('mean_exec_time', Double(53)),
    Column('stddev_exec_time', Double(53)),
    Column('rows', BigInteger),
    Column('shared_blks_hit', BigInteger),
    Column('shared_blks_read', BigInteger),
    Column('shared_blks_dirtied', BigInteger),
    Column('shared_blks_written', BigInteger),
    Column('local_blks_hit', BigInteger),
    Column('local_blks_read', BigInteger),
    Column('local_blks_dirtied', BigInteger),
    Column('local_blks_written', BigInteger),
    Column('temp_blks_read', BigInteger),
    Column('temp_blks_written', BigInteger),
    Column('shared_blk_read_time', Double(53)),
    Column('shared_blk_write_time', Double(53)),
    Column('local_blk_read_time', Double(53)),
    Column('local_blk_write_time', Double(53)),
    Column('temp_blk_read_time', Double(53)),
    Column('temp_blk_write_time', Double(53)),
    Column('wal_records', BigInteger),
    Column('wal_fpi', BigInteger),
    Column('wal_bytes', Numeric),
    Column('jit_functions', BigInteger),
    Column('jit_generation_time', Double(53)),
    Column('jit_inlining_count', BigInteger),
    Column('jit_inlining_time', Double(53)),
    Column('jit_optimization_count', BigInteger),
    Column('jit_optimization_time', Double(53)),
    Column('jit_emission_count', BigInteger),
    Column('jit_emission_time', Double(53)),
    Column('jit_deform_count', BigInteger),
    Column('jit_deform_time', Double(53)),
    Column('stats_since', DateTime(True)),
    Column('minmax_stats_since', DateTime(True))
)


t_pg_stat_statements_info = Table(
    'pg_stat_statements_info', Base.metadata,
    Column('dealloc', BigInteger),
    Column('stats_reset', DateTime(True))
)
