from __future__ import annotations

from datetime import datetime, timezone
import uuid

from invalidity.report import ReportDataV1, build_report_data


def test_report_data_v1_is_top_level_and_row_field_allowlisted() -> None:
    investigation_id = uuid.uuid4()
    raw = {
        "investigation": {
            "id": investigation_id,
            "analysis_session_id": "analysis-report-v1",
            "environment": "test",
            "status": "partial",
            "pipeline_version": "pipeline-v1",
            "state_version": 4,
            "review_revision": 2,
            "source_snapshot": {"private_path": "/private/target.pdf"},
            "settings": {"api_key": "must-not-leak"},
            "created_at": datetime(2026, 7, 19, tzinfo=timezone.utc),
        },
        "claim_investigations": [
            {
                "id": uuid.uuid4(),
                "investigation_id": investigation_id,
                "claim_id": "1",
                "status": "partial",
                "critical_date": "2020-01-02",
                "critical_date_basis": "verified_priority_date",
                "source_snapshot": {"must_not": "leak"},
            }
        ],
        "artifacts": [
            {
                "id": uuid.uuid4(),
                "investigation_id": investigation_id,
                "artifact_type": "source_pdf",
                "uri": "/private/artifact.pdf",
                "sha256": "a" * 64,
                "byte_size": 12,
            }
        ],
        "module_runs": [
            {
                "id": uuid.uuid4(),
                "investigation_id": investigation_id,
                "module_code": "I4_S_SINGLE_REFERENCE",
                "contract_version": "v1",
                "status": "cancelled",
                "input_sha256": "c" * 64,
                "output_sha256": "d" * 64,
                "requested_mode": "live",
                "effective_mode": "live",
                "actual_provider": "google_patents",
                "network_used": True,
                "used_target_images": 2,
                "used_document_images": 3,
                "cancel_requested_at": "2026-07-19T01:00:00Z",
                "cancelled_at": "2026-07-19T01:00:01Z",
                "retry_of_module_run_id": uuid.uuid4(),
                "retry_reason": "人工确认后重试",
                "input_snapshot": {"secret": "must-not-leak"},
                "output_snapshot": {"secret": "must-not-leak"},
            }
        ],
        "gap_items": [
            {
                "id": uuid.uuid4(),
                "iteration_id": uuid.uuid4(),
                "claim_investigation_id": uuid.uuid4(),
                "limitation_id": None,
                "gap_type": "combination_gap",
                "gap_key": "combination-motivation",
                "gap_fingerprint": "b" * 64,
                "version_no": 2,
                "supersedes_id": uuid.uuid4(),
                "status": "closed",
                "description": "组合启示证据已经补齐",
                "search_objective": {"subtype": "combination_motivation"},
                "resolution_evidence": {"reason": "qualified_document_found"},
                "closed_reason": "qualified_document_found",
                "internal_worker_secret": "must-not-leak",
            }
        ],
        "document_versions": [
            {
                "id": uuid.uuid4(),
                "document_id": uuid.uuid4(),
                "version_no": 2,
                "content_sha256": "e" * 64,
                "content_artifact_id": uuid.uuid4(),
                "version_metadata": {"local_path": "/private/version.pdf"},
                "readable_document_audit": {
                    "schema_version": "readable-patent-v1",
                    "source_sha256": "e" * 64,
                    "analysis_ready": True,
                    "page_count": 1,
                    "processed_page_count": 1,
                    "failed_pages": [],
                    "section_count": 1,
                    "section_page_starts": [1],
                    "full_text_sha256": "f" * 64,
                    "compact_char_count": 300,
                },
            }
        ],
        "human_review_actions": [
            {
                "id": uuid.uuid4(),
                "investigation_id": investigation_id,
                "action_type": "evidence_import",
                "action_seq": 1,
                "status": "applied",
                "module_run_id": uuid.uuid4(),
                "job_id": uuid.uuid4(),
                "request_sha256": "f" * 64,
                "idempotency_key_sha256": "0" * 64,
            }
        ],
        "evidence_imports": [
            {
                "id": uuid.uuid4(),
                "investigation_id": investigation_id,
                "review_action_id": uuid.uuid4(),
                "document_id": uuid.uuid4(),
                "document_version_id": uuid.uuid4(),
                "document_source_id": uuid.uuid4(),
                "content_artifact_id": uuid.uuid4(),
                "declared_date_facts": {
                    "publication_date": "2019-01-02",
                    "authority": "CN",
                    "source_path": "/private/evidence.pdf",
                    "source_uri": "file:///private/evidence.pdf",
                    "request_sha256": "1" * 64,
                    "metadata": {"raw": "must-not-leak"},
                },
                "source_path": "/private/evidence.pdf",
                "source_uri": "file:///private/evidence.pdf",
                "request_sha256": "2" * 64,
                "idempotency_key_sha256": "3" * 64,
                "import_metadata": {"raw": "must-not-leak"},
            }
        ],
        "provider_debug": {"secret": "must-not-leak"},
    }
    report = build_report_data(
        raw,
        preview=True,
        generated_at=datetime(2026, 7, 19, tzinfo=timezone.utc),
    )

    validated = ReportDataV1.model_validate(report)
    assert validated.is_preview is True
    assert report["source_state_version"] == 4
    assert report["source_review_revision"] == 2
    assert "provider_debug" not in report
    assert "source_snapshot" not in report["investigation"]
    assert "settings" not in report["investigation"]
    assert "source_snapshot" not in report["claim_investigations"][0]
    assert "uri" not in report["artifacts"][0]
    assert report["gap_items"][0]["gap_key"] == "combination-motivation"
    assert report["gap_items"][0]["version_no"] == 2
    assert report["gap_items"][0]["closed_reason"] == "qualified_document_found"
    assert "internal_worker_secret" not in report["gap_items"][0]
    module_run = report["module_runs"][0]
    assert module_run["output_sha256"] == "d" * 64
    assert module_run["requested_mode"] == "live"
    assert module_run["effective_mode"] == "live"
    assert module_run["actual_provider"] == "google_patents"
    assert module_run["network_used"] is True
    assert module_run["used_target_images"] == 2
    assert module_run["used_document_images"] == 3
    assert module_run["cancelled_at"] == "2026-07-19T01:00:01Z"
    assert "input_snapshot" not in module_run
    assert "output_snapshot" not in module_run
    assert report["document_versions"][0]["content_artifact_id"]
    assert report["document_versions"][0]["readable_document_audit"] == {
        "schema_version": "readable-patent-v1",
        "source_sha256": "e" * 64,
        "analysis_ready": True,
        "page_count": 1,
        "processed_page_count": 1,
        "failed_pages": [],
        "section_count": 1,
        "section_page_starts": [1],
        "full_text_sha256": "f" * 64,
        "compact_char_count": 300,
    }
    action = report["human_review_actions"][0]
    assert action["module_run_id"]
    assert action["job_id"]
    assert "request_sha256" not in action
    imported = report["evidence_imports"][0]
    assert imported["declared_date_facts"] == {
        "publication_date": "2019-01-02",
        "authority": "CN",
    }
    for private_field in (
        "source_path",
        "source_uri",
        "request_sha256",
        "idempotency_key_sha256",
        "import_metadata",
    ):
        assert private_field not in imported
    assert "must-not-leak" not in str(report)
    assert "/private/" not in str(report)


def test_unknown_report_contract_version_is_rejected() -> None:
    try:
        ReportDataV1.model_validate(
            {
                "contract_version": "v2",
                "report_kind": "invalidity_evidence_data",
                "source_state_version": 0,
                "source_review_revision": 0,
                "generated_at": "2026-07-19T00:00:00Z",
                "is_preview": True,
                "investigation": {},
            }
        )
    except ValueError:
        pass
    else:
        raise AssertionError("未知 report contract_version 不应被接受")


def test_report_materialises_d1_top_five_large_claim_chart() -> None:
    raw = {
        "investigation": {
            "id": "INV1",
            "environment": "test",
            "status": "search_budget_exhausted",
            "pipeline_version": "test",
            "state_version": 1,
            "source_snapshot": {
                "patent_snapshot": {
                    "patent_number": "CN1",
                    "claims": [
                        {
                            "claim_id": "1",
                            "claim_type": "INDEPENDENT",
                            "claim_text": "独立权利要求1",
                        }
                    ],
                }
            },
        },
        "claim_investigations": [
            {"id": "CI1", "claim_id": "1", "status": "search_budget_exhausted"}
        ],
        "claim_limitations": [
            {
                "id": "L1",
                "claim_investigation_id": "CI1",
                "feature_key": "F1",
                "sequence_no": 1,
                "limitation_text": "特征一",
            }
        ],
        "documents": [
            {"id": "D1", "investigation_id": "INV1", "title": "D1"},
            {"id": "D2", "investigation_id": "INV1", "title": "D2"},
        ],
        "feature_disclosures": [
            {
                "id": "FD1",
                "claim_investigation_id": "CI1",
                "limitation_id": "L1",
                "document_id": "D1",
                "disclosure_status": "explicit",
                "excerpt": "D1 原文",
                "locator": "段1",
                "analysis": {"reasoning": "直接披露"},
            },
            {
                "id": "FD2",
                "claim_investigation_id": "CI1",
                "limitation_id": "L1",
                "document_id": "D2",
                "disclosure_status": "not_disclosed",
                "excerpt": "",
                "locator": "全文复核",
                "analysis": {"reasoning": "未披露"},
            },
        ],
        "closest_prior_art_versions": [
            {
                "id": "CPA1",
                "claim_investigation_id": "CI1",
                "iteration_id": "IT1",
                "document_id": "D1",
                "version_no": 1,
                "is_current": True,
                "rationale": {
                    "large_claim_chart_repository_document_ids": ["D1", "D2"],
                    "distinguishing_features": [{"feature_id": "F2"}],
                },
            }
        ],
    }

    report = build_report_data(raw, preview=True)

    assert len(report["large_claim_charts"]) == 1
    chart = report["large_claim_charts"][0]
    assert chart["d1_document_id"] == "D1"
    assert chart["document_ids"] == ["D1", "D2"]
    assert chart["feature_rows"][0]["cells"][0]["excerpt"] == "D1 原文"
    assert chart["feature_rows"][0]["cells"][1]["disclosure_status"] == "not_disclosed"


def test_report_materialises_module11_narrative_and_top_ten_similarity_matrix() -> None:
    limitations = [
        {
            "id": f"L{index}",
            "claim_investigation_id": "CI1",
            "feature_key": f"F{index}",
            "sequence_no": index,
            "limitation_text": f"目标技术特征{index}",
        }
        for index in range(1, 4)
    ]
    documents = [
        {
            "id": f"D{index:02d}",
            "investigation_id": "INV1",
            "canonical_key": f"CN{index:02d}U",
            "title": f"对比文件{index:02d}",
        }
        for index in range(1, 12)
    ]
    statuses_by_document = {
        "D01": ["explicit", "necessarily_implicit", "not_disclosed"],
        "D02": ["explicit", "uncertain", "uncertain"],
        "D03": ["explicit", "not_disclosed", "not_disclosed"],
    }
    feature_disclosures = []
    for document in documents:
        document_id = document["id"]
        statuses = statuses_by_document.get(
            document_id,
            ["not_disclosed", "not_disclosed", "not_disclosed"],
        )
        for limitation, status in zip(limitations, statuses, strict=True):
            feature_disclosures.append(
                {
                    "id": f"FD-{document_id}-{limitation['id']}",
                    "claim_investigation_id": "CI1",
                    "limitation_id": limitation["id"],
                    "document_id": document_id,
                    "disclosure_status": status,
                    "excerpt": f"{document_id} 对 {limitation['id']} 的原文",
                    "locator": f"{document_id} 第1段",
                    "analysis": {"reasoning": f"{status} 的依据"},
                }
            )
    raw = {
        "investigation": {
            "id": "INV1",
            "environment": "test",
            "status": "search_budget_exhausted",
            "pipeline_version": "test",
            "state_version": 1,
            "source_snapshot": {
                "patent_snapshot": {
                    "patent_number": "CN-TARGET",
                    "claims": [
                        {
                            "claim_id": "1",
                            "claim_type": "INDEPENDENT",
                            "claim_text": "独立权利要求1",
                        }
                    ],
                }
            },
        },
        "claim_investigations": [
            {"id": "CI1", "claim_id": "1", "status": "search_budget_exhausted"}
        ],
        "claim_limitations": limitations,
        "documents": documents,
        "feature_disclosures": feature_disclosures,
        "closest_prior_art_versions": [
            {
                "id": "CPA1",
                "claim_investigation_id": "CI1",
                "iteration_id": "IT1",
                "document_id": "D01",
                "version_no": 1,
                "is_current": True,
                "rationale": {"large_claim_chart_repository_document_ids": ["D01"]},
            }
        ],
        "combinations": [
            {
                "id": "COMB1",
                "claim_investigation_id": "CI1",
                "iteration_id": "IT1",
                "closest_prior_art_version_id": "CPA1",
                "document_ids": ["D01", "D03"],
                "status": "evidence_complete",
                "coverage_complete": True,
                "motivation_status": "supported",
                "analysis": {
                    "closest_document_id": "D01",
                    "evidence_complete": True,
                    "conclusion_text": "现有证据已形成缺乏创造性的完整证据链（供律师复核）",
                    "distinguishing_feature_analysis": [
                        {
                            "feature_id": "F3",
                            "feature_text": "目标技术特征3",
                            "d1_disclosure_status": "not_disclosed",
                            "objective_technical_problem": "降低结构复杂度",
                            "supporting_document_ids": ["D03"],
                            "same_role_and_effect": {
                                "status": "supported",
                                "reasoning": "D03承担相同结构作用",
                            },
                            "technical_teaching": {
                                "status": "supported",
                                "reasoning": "D03明确给出替换启示",
                            },
                            "modification_motivation": {
                                "status": "supported",
                                "reasoning": "能够简化结构",
                            },
                            "modification_path": "将D03结构用于D01对应位置",
                            "teaching_away": {
                                "status": "absent",
                                "reasoning": "未发现相反记载",
                            },
                            "technical_effect": {
                                "status": "predictable",
                                "reasoning": "效果能够从原文预期",
                            },
                            "evidence_chain_complete": True,
                            "unresolved_reasons": [],
                        }
                    ],
                },
            }
        ],
    }

    report = build_report_data(raw, preview=True)

    narratives = report["inventive_step_narratives"]
    assert len(narratives) == 1
    assert narratives[0]["combination_id"] == "COMB1"
    narrative_text = "\n".join(narratives[0]["paragraphs"])
    assert "实际解决的技术问题为：降低结构复杂度" in narrative_text
    assert "现有技术是否给出具体技术启示" in narrative_text
    assert "反向教导" in narrative_text
    assert "现有证据已形成缺乏创造性的完整证据链" in narrative_text

    charts = report["similarity_claim_charts"]
    assert len(charts) == 1
    chart = charts[0]
    assert len(chart["ranked_documents"]) == 10
    assert [row["document_id"] for row in chart["ranked_documents"][:3]] == [
        "D01",
        "D03",
        "D02",
    ]
    assert "D11" not in chart["document_ids"]
    assert chart["ranked_documents"][0]["confirmed_disclosed_feature_count"] == 2
    assert chart["ranked_documents"][2]["confirmed_disclosed_feature_count"] == 1
    assert len(chart["feature_rows"]) == 3
    assert len(chart["feature_rows"][0]["cells"]) == 10


def test_ordinary_report_excludes_historical_dependent_claim_analysis() -> None:
    investigation_id = str(uuid.uuid4())
    report = build_report_data(
        {
            "investigation": {
                "id": investigation_id,
                "environment": "test",
                "status": "partial",
                "pipeline_version": "test",
                "state_version": 1,
                "source_snapshot": {
                    "patent_snapshot": {
                        "patent_number": "CN1",
                        "claims": [
                            {
                                "claim_id": "1",
                                "claim_type": "INDEPENDENT",
                                "claim_text": "独立权利要求1",
                            },
                            {
                                "claim_id": "2",
                                "claim_type": "DEPENDENT",
                                "claim_text": "从属权利要求2",
                                "parent_claim_ids": ["1"],
                            },
                        ],
                    }
                },
            },
            "claim_investigations": [
                {"id": "CI1", "claim_id": "1", "status": "partial"},
                {
                    "id": "CI2",
                    "claim_id": "2",
                    "status": "needs_human_review",
                },
            ],
            "claim_limitations": [
                {
                    "id": "L1",
                    "claim_investigation_id": "CI1",
                    "feature_key": "F1",
                    "limitation_text": "独立特征",
                },
                {
                    "id": "L2",
                    "claim_investigation_id": "CI2",
                    "feature_key": "F2",
                    "limitation_text": "从属特征",
                },
            ],
            "documents": [
                {
                    "id": "D1",
                    "investigation_id": investigation_id,
                    "title": "独立文献",
                },
                {
                    "id": "D2",
                    "investigation_id": investigation_id,
                    "title": "从属文献",
                },
            ],
            "feature_disclosures": [
                {
                    "id": "FD1",
                    "claim_investigation_id": "CI1",
                    "limitation_id": "L1",
                    "document_id": "D1",
                    "disclosure_status": "explicit",
                },
                {
                    "id": "FD2",
                    "claim_investigation_id": "CI2",
                    "limitation_id": "L2",
                    "document_id": "D2",
                    "disclosure_status": "explicit",
                },
            ],
        },
        preview=True,
    )

    assert [row["id"] for row in report["claim_investigations"]] == ["CI1"]
    assert [row["id"] for row in report["claim_limitations"]] == ["L1"]
    assert [row["id"] for row in report["documents"]] == ["D1"]
    assert [row["id"] for row in report["feature_disclosures"]] == ["FD1"]
    assert report["claim_scope"]["excluded_dependent_claim_ids"] == ["2"]


def test_report_target_patent_exposes_readonly_module1_facts_and_figure_index() -> None:
    figure_sha = "f" * 64
    report = build_report_data(
        {
            "investigation": {
                "id": "INV-RO",
                "environment": "test",
                "status": "running",
                "pipeline_version": "test",
                "state_version": 1,
                "source_snapshot": {
                    "patent_snapshot": {
                        "patent_number": "CN217443913U",
                        "title": "无人值守监控系统",
                        "holder": "示例权利人",
                        "abstract": "摘要",
                        "application_number": "202123456789.0",
                        "application_date": "2021-12-07",
                        "priority_date": None,
                        "publication_date": "2022-09-16",
                        "grant_date": "2022-09-16",
                        "source_format": "pdf",
                        "page_count": 6,
                        "used_ocr": False,
                        "specification": {"技术领域": "本实用新型涉及……"},
                        "bibliographic_data": {"inventors": ["张三"]},
                        "source_uri": "/private/must-not-leak.pdf",
                        "page_texts": ["不得泄露分页全文"],
                        "figures": [
                            {
                                "figure_id": "摘要附图",
                                "figure_description": "摘要附图",
                                "page_number": 1,
                                "mime_type": "image/jpeg",
                                "file_size": 12,
                                "file_sha256": figure_sha,
                                "file_path": "/private/must-not-leak.jpg",
                            }
                        ],
                    },
                    "patent_figure_artifacts": [
                        {
                            "uri": "/private/figure-001.jpg",
                            "sha256": figure_sha,
                            "byte_size": 12,
                            "mime_type": "image/jpeg",
                            "artifact_type": "target_patent_figure_snapshot",
                        }
                    ],
                },
            },
            "claim_investigations": [],
        },
        preview=True,
    )

    target = report["target_patent"]
    assert target["application_number"] == "202123456789.0"
    assert target["grant_date"] == "2022-09-16"
    assert target["source_format"] == "pdf"
    assert target["page_count"] == 6
    assert target["specification"] == {"技术领域": "本实用新型涉及……"}
    assert target["bibliographic_data"] == {"inventors": ["张三"]}
    assert "source_uri" not in target
    assert "page_texts" not in target
    figure = target["figures"][0]
    assert figure["artifact_index"] == 0
    assert "file_path" not in figure
