"""
数据库数据加载节点
职责：从 Postgres 读取专利主记录、权利要求和附图，并转换为关键词流程可消费的结构
"""
from typing import Dict, List

from langchain_core.runnables import RunnableConfig
from langgraph.runtime import Runtime
from coze_coding_utils.runtime_ctx.context import Context

from graphs.state import FeishuDataLoaderInput, FeishuDataLoaderOutput, TableInfo


def _find_spec_section(specification: dict, keywords: List[str]) -> str:
    if not specification:
        return ""

    lowered = [(str(key), str(value)) for key, value in specification.items()]
    for key, value in lowered:
        key_lower = key.lower()
        if any(keyword in key_lower for keyword in keywords):
            return value

    return ""


def feishu_data_loader_node(
    state: FeishuDataLoaderInput,
    config: RunnableConfig,
    runtime: Runtime[Context]
) -> FeishuDataLoaderOutput:
    """
    title: 数据库数据加载
    desc: 从Postgres读取专利解析结果，生成与原关键词流程兼容的记录结构
    integrations: Postgres数据库
    """
    try:
        from storage.database.db import get_engine, get_session
        from storage.database.shared.model import (
            Base,
            PatentClaim,
            PatentFigure,
            PatentParseRecord,
        )

        engine = get_engine()
        Base.metadata.create_all(
            bind=engine,
            tables=[
                PatentParseRecord.__table__,
                PatentClaim.__table__,
                PatentFigure.__table__,
            ],
        )

        session = get_session()
        try:
            patent_record = (
                session.query(PatentParseRecord)
                .filter(PatentParseRecord.id == state.patent_record_id)
                .one_or_none()
            )
            if patent_record is None:
                return FeishuDataLoaderOutput(
                    all_tables_info={},
                    tables=[],
                    integrated_patent_records=[],
                    field_mapping={},
                    exception_type="PATENT_RECORD_NOT_FOUND",
                    exception_message=f"未找到专利记录: {state.patent_record_id}",
                )

            claims = (
                session.query(PatentClaim)
                .filter(PatentClaim.record_id == state.patent_record_id)
                .order_by(PatentClaim.id.asc())
                .all()
            )
            figures = (
                session.query(PatentFigure)
                .filter(PatentFigure.record_id == state.patent_record_id)
                .order_by(PatentFigure.id.asc())
                .all()
            )
        finally:
            session.close()

        specification = patent_record.specification or {}
        data_fields = {
            "patent_holder": patent_record.patent_holder or "",
            "patent_number": patent_record.patent_number or "",
            "application_date": patent_record.application_date or patent_record.priority_date or "",
            "technical_field": _find_spec_section(specification, ["技术领域", "technical field"]),
            "background_tech": _find_spec_section(specification, ["背景技术", "background"]),
            "invention_content": _find_spec_section(
                specification,
                ["发明内容", "实用新型内容", "发明概述", "summary"],
            ) or "\n".join(str(value) for value in specification.values()),
        }

        figure_records = [
            {
                "record_id": str(figure.id),
                "fields": {
                    "figure_id": figure.figure_id,
                    "figure_description": figure.figure_description or "",
                    "figure_url": figure.figure_url,
                },
            }
            for figure in figures
        ]

        integrated_patent_records: List[dict] = []
        primary_claim = None
        for claim in claims:
            if str(claim.claim_id).strip() == "1":
                primary_claim = claim
                break
        if primary_claim is None:
            for claim in claims:
                if claim.claim_type == "INDEPENDENT":
                    primary_claim = claim
                    break
        if primary_claim is None and claims:
            primary_claim = claims[0]

        if primary_claim is not None:
            integrated_patent_records.append(
                {
                    "data_record": {
                        "record_id": str(patent_record.id),
                        "fields": data_fields,
                    },
                    "claim_records": [
                        {
                            "record_id": str(primary_claim.id),
                            "fields": {
                                "claim_id": primary_claim.claim_id,
                                "claim_type": primary_claim.claim_type,
                                "claim_text": primary_claim.claim_text,
                            },
                        }
                    ],
                    "figure_records": figure_records,
                }
            )

        if not integrated_patent_records:
            integrated_patent_records.append(
                {
                    "data_record": {
                        "record_id": str(patent_record.id),
                        "fields": data_fields,
                    },
                    "claim_records": [],
                    "figure_records": figure_records,
                }
            )

        field_mapping = {
            "patent_holder": "patent_holder",
            "patent_number": "patent_number",
            "application_date": "application_date",
            "technical_field": "technical_field",
            "background_tech": "background_tech",
            "invention_content": "invention_content",
            "claim_id": "claim_id",
            "claim_type": "claim_type",
            "claim_text": "claim_text",
            "figure_description": "figure_description",
        }

        all_tables_info = {
            "patent_record": {
                "table_id": "patent_record",
                "table_name": "patent_parse_records",
                "fields": [{"field_name": key} for key in data_fields.keys()],
                "sample_records": [{"record_id": str(patent_record.id), "fields": data_fields}],
            },
            "claims": {
                "table_id": "claims",
                "table_name": "patent_claims",
                "fields": [{"field_name": key} for key in ["claim_id", "claim_type", "claim_text"]],
                "sample_records": [
                    record["claim_records"][0]
                    for record in integrated_patent_records[:5]
                    if record.get("claim_records")
                ],
            },
            "figures": {
                "table_id": "figures",
                "table_name": "patent_figures",
                "fields": [{"field_name": key} for key in ["figure_id", "figure_description", "figure_url"]],
                "sample_records": figure_records[:5],
            },
        }

        return FeishuDataLoaderOutput(
            all_tables_info=all_tables_info,
            tables=[
                TableInfo(
                    table_id=value["table_id"],
                    table_name=value["table_name"],
                    fields=value["fields"],
                    sample_records=value["sample_records"],
                )
                for value in all_tables_info.values()
            ],
            integrated_patent_records=integrated_patent_records,
            field_mapping=field_mapping,
            exception_type="",
            exception_message="",
        )
    except Exception as error:
        return FeishuDataLoaderOutput(
            all_tables_info={},
            tables=[],
            integrated_patent_records=[],
            field_mapping={},
            exception_type="DB_LOAD_ERROR",
            exception_message=f"加载数据库专利数据失败: {error}",
        )
