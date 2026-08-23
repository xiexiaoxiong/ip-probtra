from __future__ import annotations

import inspect
import hashlib
import json
import os
import uuid
from copy import deepcopy
from datetime import date, datetime, timezone

import pytest
from sqlalchemy import text

import invalidity.db as db_module
from invalidity.db import (
    LeaseLostError,
    Repository,
    RepositoryConfigurationError,
    RepositoryConflictError,
    TABLE_NAMES,
    build_claim_job_sql,
    build_ddl_statements,
    build_recover_expired_jobs_sql,
    module_run_audit_values,
    plan_gap_frontier,
)


@pytest.mark.parametrize(
    ("environment", "schema"),
    (("test", "invalidity_test"), ("prod", "invalidity_prod")),
)
def test_repository_accepts_only_the_two_explicit_environment_pairs(
    environment: str, schema: str
) -> None:
    repository = Repository(
        "postgresql+psycopg2://user:secret@localhost/invalidity",
        schema,
        environment,
    )
    assert repository.environment == environment
    assert repository.schema == schema
    assert repository._engine is None  # construction is configuration-only


@pytest.mark.parametrize(
    ("environment", "schema"),
    (
        ("test", "invalidity_prod"),
        ("prod", "invalidity_test"),
        ("test", "public"),
        ("test", "invalidity_test; DROP SCHEMA public"),
        ("development", "invalidity_test"),
        ("", "invalidity_test"),
    ),
)
def test_repository_rejects_environment_schema_mismatch(
    environment: str, schema: str
) -> None:
    with pytest.raises(RepositoryConfigurationError):
        Repository(
            "postgresql://user:secret@localhost/invalidity",
            schema,
            environment,
        )


@pytest.mark.parametrize(
    "database_url",
    (
        "sqlite+pysqlite:///:memory:",
        "sqlite:///invalidity.db",
        "mysql+pymysql://localhost/invalidity",
        "",
        "not-a-database-url",
    ),
)
def test_repository_has_no_non_postgresql_fallback(database_url: str) -> None:
    with pytest.raises(RepositoryConfigurationError, match="PostgreSQL|无效"):
        Repository(database_url, "invalidity_test", "test")


def test_ddl_contains_every_required_schema_qualified_table() -> None:
    ddl = "\n".join(build_ddl_statements("invalidity_test", "test"))
    assert "CREATE SCHEMA IF NOT EXISTS invalidity_test" in ddl
    for table_name in TABLE_NAMES:
        assert f"CREATE TABLE IF NOT EXISTS invalidity_test.{table_name}" in ddl
    assert "invalidity_prod." not in ddl
    assert "public." not in ddl


def test_investigation_and_claim_inputs_are_jsonb_snapshots() -> None:
    ddl = "\n".join(build_ddl_statements("invalidity_test", "test"))
    assert "source_snapshot JSONB NOT NULL" in ddl
    assert "source_snapshot_sha256 CHAR(64) NOT NULL" in ddl
    assert "input_snapshot JSONB NOT NULL" in ddl
    assert "input_sha256 CHAR(64) NOT NULL" in ddl
    assert "limitation_snapshot JSONB NOT NULL" in ddl
    assert "workflow_state JSONB NOT NULL" in ddl
    assert "error_metadata JSONB NOT NULL" in ddl
    assert "result_summary JSONB NOT NULL" in ddl
    assert "critical_date DATE" in ddl
    assert "target_publication_date DATE" in ddl
    assert "critical_date_basis TEXT" in ddl


def test_module5_patent_text_cache_has_exact_publication_index() -> None:
    ddl = "\n".join(build_ddl_statements("invalidity_test", "test"))
    assert "module_runs_patent_fetch_cache_idx" in ddl
    assert "output_snapshot #>> '{output,publication_number}'" in ddl
    source = inspect.getsource(Repository.find_reusable_patent_fetches)
    assert "module_code = 'I3_FETCH'" in source
    assert "COALESCE(" in source
    assert "output_snapshot #>>" in source
    assert "output,effective_mode" in source
    assert "publication_number" in source
    assert "output_snapshot #>> '{output,title}'" not in source
    assert "output_snapshot #>> '{output,family_key}'" not in source


def test_review_continuation_and_report_snapshots_are_immutable_audit_tables() -> None:
    ddl = "\n".join(build_ddl_statements("invalidity_test", "test"))
    for table_name in (
        "critical_date_confirmations",
        "continuation_batches",
        "report_snapshots",
    ):
        assert f"CREATE TABLE IF NOT EXISTS invalidity_test.{table_name}" in ddl
    assert "idempotency_key_sha256 CHAR(64) NOT NULL" in ddl
    assert "request_sha256 CHAR(64) NOT NULL" in ddl
    assert "report_sha256 CHAR(64) NOT NULL" in ddl
    assert "source_review_revision INTEGER NOT NULL" in ddl
    assert "report_snapshots_source_version_uidx" in ddl
    assert "source_state_version, source_review_revision" in ddl


def test_human_derived_origin_constraints_are_validated_after_creation() -> None:
    ddl = "\n".join(build_ddl_statements("invalidity_test", "test"))
    for constraint in (
        "closest_prior_art_origin_check",
        "gap_items_origin_check",
        "combinations_origin_check",
    ):
        assert f"ADD CONSTRAINT {constraint}" in ddl
        assert f"VALIDATE CONSTRAINT {constraint}" in ddl


def test_gap_items_are_versioned_frontier_rows_not_iteration_upserts() -> None:
    ddl = "\n".join(build_ddl_statements("invalidity_test", "test"))
    for fragment in (
        "gap_key TEXT NOT NULL",
        "gap_fingerprint CHAR(64) NOT NULL",
        "version_no INTEGER NOT NULL",
        "supersedes_id UUID",
        "closed_reason TEXT",
        "ALTER COLUMN limitation_id DROP NOT NULL",
        "gap_items_iteration_key_uidx",
        "gap_items_lineage_version_uidx",
    ):
        assert fragment in ddl
    assert "UNIQUE (iteration_id, limitation_id, gap_type)" not in ddl


def test_gap_frontier_keeps_multiple_combination_gaps_and_stable_identity() -> None:
    first = plan_gap_frontier(
        iteration_no=1,
        open_gaps=[
            {
                "gap_type": "combination_gap",
                "description": "缺少相关技术问题证据",
                "search_objective": {
                    "subtype": "technical_problem",
                    "search_anchor": "相关技术问题 + D1 技术主题",
                },
            },
            {
                "gap_type": "combination_gap",
                "description": "缺少组合动机证据",
                "search_objective": {
                    "subtype": "combination_motivation",
                    "search_anchor": "组合启示 + D1 技术主题 + 区别特征",
                },
            },
        ],
    )

    assert len(first["rows"]) == 2
    assert len({row["gap_key"] for row in first["rows"]}) == 2
    assert all(row["limitation_id"] is None for row in first["rows"])
    assert all(row["version_no"] == 1 for row in first["rows"])
    repeated = plan_gap_frontier(
        iteration_no=1,
        open_gaps=[
            {
                "gap_type": row["gap_type"],
                "description": row["description"],
                "search_objective": row["search_objective"],
            }
            for row in first["rows"]
        ],
    )
    assert repeated["frontier_fingerprint"] == first["frontier_fingerprint"]


def test_gap_frontier_appends_versions_and_closes_only_disappeared_open_gaps() -> None:
    round_one = plan_gap_frontier(
        iteration_no=1,
        open_gaps=[
            {
                "gap_id": "gap-feature-a",
                "kind": "feature_gap",
                "feature_id": "F1",
                "search_anchor": "主题 + 特征 A",
                "rationale": "D1 未披露 A",
            },
            {
                "gap_id": "gap-motivation",
                "kind": "combination_gap",
                "subtype": "combination_motivation",
                "search_anchor": "主题 + 组合启示",
                "rationale": "缺少组合动机",
            },
        ],
    )
    history = [
        {**row, "id": uuid.uuid4()}
        for row in round_one["rows"]
    ]
    unchanged_history = deepcopy(history)
    resolving_document_id = uuid.uuid4()
    round_two = plan_gap_frontier(
        iteration_no=2,
        previous_gap_items=history,
        open_gaps=[
            {
                "gap_id": "gap-motivation",
                "kind": "combination_gap",
                "subtype": "combination_motivation",
                "search_anchor": "主题 + 组合启示",
                "rationale": "缺少组合动机",
            },
            {
                "gap_id": "gap-effect",
                "kind": "combination_gap",
                "subtype": "technical_effect",
                "search_anchor": "区别特征 + 技术效果",
                "rationale": "缺少可预期效果证据",
            },
        ],
        closed_gap_resolutions={
            "gap-feature-a": {
                "reason": "qualified_document_disclosed_feature",
                "resolved_by_document_id": resolving_document_id,
                "resolution_evidence": {"document_role": "D2"},
            }
        },
    )

    by_key = {row["gap_key"]: row for row in round_two["rows"]}
    assert by_key["gap-motivation"]["status"] == "open"
    assert by_key["gap-motivation"]["version_no"] == 2
    assert by_key["gap-effect"]["status"] == "open"
    assert by_key["gap-effect"]["version_no"] == 1
    closed = by_key["gap-feature-a"]
    assert closed["status"] == "closed"
    assert closed["version_no"] == 2
    assert closed["closed_reason"] == "qualified_document_disclosed_feature"
    assert closed["resolved_by_document_id"] == resolving_document_id
    assert closed["resolution_evidence"]["iteration_no"] == 2
    assert closed["resolution_evidence"]["document_role"] == "D2"
    assert round_two["delta"]["closed_gap_keys"] == ["gap-feature-a"]
    assert history == unchanged_history


def test_human_gap_frontier_is_superseded_by_next_system_round() -> None:
    gap = {
        "gap_id": "gap-feature-a",
        "kind": "feature_gap",
        "feature_id": "F1",
        "search_anchor": "主题 + 特征 A",
        "rationale": "尚未覆盖 A",
    }
    system_v1 = plan_gap_frontier(iteration_no=1, open_gaps=[gap])
    system_v1_row = {**system_v1["rows"][0], "id": uuid.uuid4()}
    human_v2 = plan_gap_frontier(
        iteration_no=None,
        review_revision=1,
        open_gaps=[{**gap, "rationale": "人工复核后仍未覆盖 A"}],
        previous_gap_items=[system_v1_row],
    )
    human_v2_row = {**human_v2["rows"][0], "id": uuid.uuid4()}
    system_v3 = plan_gap_frontier(
        iteration_no=2,
        open_gaps=[gap],
        previous_gap_items=[system_v1_row, human_v2_row],
    )

    assert human_v2_row["version_no"] == 2
    assert human_v2_row["supersedes_id"] == system_v1_row["id"]
    assert system_v3["rows"][0]["version_no"] == 3
    assert system_v3["rows"][0]["supersedes_id"] == human_v2_row["id"]

    ddl = "\n".join(build_ddl_statements("invalidity_test", "test"))
    assert "review.completed_at" in ddl or "review.completed_at" in inspect.getsource(
        Repository.reconcile_gap_frontier
    )
    assert "<= :iteration_created_at" in inspect.getsource(
        Repository.reconcile_gap_frontier
    )


def test_workflow_repository_exposes_audited_domain_operations() -> None:
    expected_methods = {
        "init_schema",
        "get_investigation_by_session",
        "update_investigation",
        "merge_investigation_workflow_state",
        "get_claim_investigation",
        "list_claim_investigations",
        "update_claim_status",
        "mark_claim_terminal",
        "insert_claim_limitations",
        "list_claim_limitations",
        "get_module_run",
        "get_module_run_by_job",
        "list_module_runs",
        "find_reusable_patent_fetches",
        "get_module_lab_run",
        "recover_expired_jobs",
        "update_iteration",
        "upsert_query",
        "update_query_execution",
        "finish_module_run",
        "upsert_document",
        "upsert_document_source",
        "upsert_document_qualification",
        "insert_feature_disclosures",
        "insert_closest_prior_art_version",
        "reconcile_gap_frontier",
        "insert_gap_items",
        "insert_combination",
        "insert_artifact",
        "get_report_data",
        "confirm_critical_date",
        "get_latest_critical_date_confirmation",
        "create_continuation_batch_and_enqueue",
        "get_continuation_batch",
        "update_continuation_batch",
        "create_report_snapshot",
        "get_report_snapshot",
        "get_claim_iteration",
    }
    assert not (expected_methods - set(dir(Repository)))
    assert not hasattr(Repository, "execute")
    assert not hasattr(Repository, "generic_upsert")


def test_confirmation_and_continuation_are_atomic_repository_operations() -> None:
    confirmation = inspect.getsource(Repository.confirm_critical_date)
    assert "with self.transaction() as connection" in confirmation
    assert "state_version = state_version + 1" in confirmation
    assert "critical_date_confirmations" in confirmation
    assert "self._insert_event(" in confirmation

    continuation = inspect.getsource(Repository.create_continuation_batch_and_enqueue)
    assert "with self.transaction() as connection" in continuation
    assert "self._insert_module_run(" in continuation
    assert "self._insert_job(" in continuation
    assert "continuation_batches" in continuation
    assert "self._insert_event(" in continuation
    assert "_CONTINUABLE_CLAIM_STATUSES" in continuation
    assert "_SUCCESSFUL_CLAIM_STATUSES" in continuation
    assert "claims_with_open_gaps" in continuation
    assert "没有未解决的 open gap" in continuation

    gap_reconcile = inspect.getsource(Repository.reconcile_gap_frontier)
    assert "with self.transaction() as connection" in gap_reconcile
    assert "gap_frontier" in gap_reconcile
    assert "ON CONFLICT" not in gap_reconcile
    assert "UPDATE {self.schema}.gap_items" not in gap_reconcile


def test_job_table_has_durable_retry_lease_and_heartbeat_state() -> None:
    ddl = "\n".join(build_ddl_statements("invalidity_test", "test"))
    for contract_fragment in (
        "available_at TIMESTAMPTZ NOT NULL",
        "attempt_count INTEGER NOT NULL",
        "max_attempts INTEGER NOT NULL",
        "lease_owner TEXT",
        "lease_token UUID",
        "lease_expires_at TIMESTAMPTZ",
        "heartbeat_at TIMESTAMPTZ",
        "cancel_requested_at TIMESTAMPTZ",
        "last_error JSONB",
    ):
        assert contract_fragment in ddl


def test_module_run_persists_cancel_retry_and_server_derived_audit_contract() -> None:
    ddl = "\n".join(build_ddl_statements("invalidity_test", "test"))
    for contract_fragment in (
        "output_sha256 CHAR(64)",
        "requested_mode TEXT",
        "effective_mode TEXT",
        "actual_provider TEXT",
        "network_used BOOLEAN",
        "used_target_images INTEGER",
        "used_document_images INTEGER",
        "retry_of_module_run_id UUID",
        "retry_reason TEXT",
        "cancel_reason TEXT",
        "cancelled_at TIMESTAMPTZ",
    ):
        assert contract_fragment in ddl
    assert "WHERE requested_mode IS NULL" in ddl

    for mode, provider, network_used in (
        ("fixture", "fixture", False),
        ("manual", "manual", False),
        ("live", "google_patents", True),
    ):
        output = {
            "status": "completed",
            "requested_mode": mode,
            "effective_mode": mode,
            "actual_provider": provider,
            "network_used": network_used,
            "model": "glm-4.6v" if mode == "live" else "fixture-no-model",
            "output_sha256": "client-supplied-digest-must-be-ignored",
            "output": {
                "used_target_images": 2 if mode == "live" else 0,
                "used_document_images": 3 if mode == "live" else 0,
            },
        }
        audit = module_run_audit_values(
            {"request": {"input_mode": mode}},
            output,
        )
        canonical = json.dumps(
            output,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        assert audit["requested_mode"] == mode
        assert audit["effective_mode"] == mode
        assert audit["actual_provider"] == provider
        assert audit["network_used"] is network_used
        assert audit["model_version"] == (
            "glm-4.6v" if mode == "live" else "fixture-no-model"
        )
        assert audit["used_target_images"] == (2 if mode == "live" else 0)
        assert audit["used_document_images"] == (3 if mode == "live" else 0)
        assert audit["output_sha256"] == hashlib.sha256(
            canonical.encode("utf-8")
        ).hexdigest()
        assert audit["output_sha256"] != output["output_sha256"]


def test_cancel_and_manual_retry_are_atomic_and_completion_is_cancel_safe() -> None:
    cancel_source = inspect.getsource(Repository.request_module_run_cancellation)
    assert "with self.transaction() as connection" in cancel_source
    assert "FOR UPDATE" in cancel_source
    assert "self._cancel_locked_job(" in cancel_source
    assert 'event_type="module_run.cancel_requested"' in cancel_source

    complete_source = inspect.getsource(Repository.complete_job)
    cancellation_check = complete_source.index(
        'locked.get("cancel_requested_at") is not None'
    )
    success_write = complete_source.index("SET status = 'succeeded'")
    assert cancellation_check < success_write
    assert "cancel_requested_at IS NULL" in complete_source
    assert 'event_type="job.succeeded"' in complete_source

    fail_source = inspect.getsource(Repository.fail_job)
    assert 'locked.get("cancel_requested_at") is not None' in fail_source
    assert "self._cancel_locked_job(" in fail_source
    assert "if not will_retry:" in fail_source
    assert "self._mark_human_review_action_terminal(" in fail_source

    request_cancel_source = inspect.getsource(
        Repository.request_module_run_cancellation
    )
    assert "self._mark_human_review_action_terminal(" in request_cancel_source

    recovery_source = inspect.getsource(
        Repository._recover_exhausted_expired_jobs
    )
    assert "self._mark_human_review_action_terminal(" in recovery_source

    retry_source = inspect.getsource(Repository.retry_module_run)
    assert "with self.transaction() as connection" in retry_source
    assert "retry_of_module_run_id" in retry_source
    assert 'event_type="module_run.retry_requested"' in retry_source
    assert 'event_type="module_run.retry_queued"' in retry_source
    assert "只有已失败或结构复核未完成的 I4-S run/job 才能人工重试" in retry_source


def test_human_review_i4s_persists_current_structural_analysis_fields() -> None:
    source = inspect.getsource(Repository.complete_human_review_apply)
    assert "prompt_version=I4S_PROMPT_VERSION" in source
    assert "rule_version=I4S_RULE_VERSION" in source
    for field in (
        "target_structural_role",
        "reference_structure_mapping",
        "mapping_basis",
        "structural_evidence",
        "integrated_structure_mapping",
        "structural_search_summary",
        "necessity_chain",
        "reasonable_alternatives_excluded",
        "alternative_path_analysis",
        "analysis_rule_version",
    ):
        assert field in source


def test_claim_job_uses_postgresql_skip_locked_and_recovers_expired_leases() -> None:
    sql = build_claim_job_sql("invalidity_test", "test")
    normalized = " ".join(sql.upper().split())
    assert "FOR UPDATE SKIP LOCKED" in normalized
    assert "STATUS = 'LEASED' AND LEASE_EXPIRES_AT <= NOW()" in normalized
    assert "ATTEMPT_COUNT = JOB.ATTEMPT_COUNT + 1" in normalized
    assert "LEASE_TOKEN = :LEASE_TOKEN" in normalized


def test_exhausted_expired_lease_is_failed_without_an_extra_attempt() -> None:
    sql = build_recover_expired_jobs_sql("invalidity_test", "test")
    normalized = " ".join(sql.upper().split())
    assert "FOR UPDATE SKIP LOCKED" in normalized
    assert "STATUS = 'LEASED'" in normalized
    assert "LEASE_EXPIRES_AT <= NOW()" in normalized
    assert "ATTEMPT_COUNT >= MAX_ATTEMPTS" in normalized
    assert "SET STATUS = 'FAILED'" in normalized
    assert "ATTEMPT_COUNT = JOB.ATTEMPT_COUNT + 1" not in normalized

    claim_source = inspect.getsource(Repository.claim_job)
    recovery_call = claim_source.index("self._recover_exhausted_expired_jobs(")
    lease_call = claim_source.index("build_claim_job_sql(")
    assert recovery_call < lease_call


def test_event_and_next_job_have_an_explicit_atomic_repository_operation() -> None:
    source = inspect.getsource(Repository.record_event_and_enqueue_job)
    assert "with self.transaction() as connection" in source
    assert "self._insert_job(" in source
    assert "self._insert_event(" in source

    transition_source = inspect.getsource(Repository.transition_claim_and_schedule)
    assert "state_version = state_version + 1" in transition_source
    assert "self._insert_module_run(" in transition_source
    assert "self._insert_job(" in transition_source
    assert "self._insert_event(" in transition_source


def test_terminal_i0_success_commits_before_i5_snapshot_job_is_visible() -> None:
    source = inspect.getsource(Repository.complete_job)
    succeeded_event = source.index('event_type="job.succeeded"')
    report_run = source.index('module_code="I5_REPORT"')
    report_job = source.index("report_job = self._insert_job(")
    report_event = source.index('event_type="report.queued"')
    assert succeeded_event < report_run < report_job < report_event
    for payload_field in (
        '"kind": "report_snapshot"',
        '"investigation_id": str(investigation["id"])',
        '"source_state_version": source_state_version',
        '"source_review_revision": source_review_revision',
        '"report_snapshot_id": str(report_snapshot_id)',
        '"contract_version": "v1"',
    ):
        assert payload_field in source


def test_terminal_i0_failure_and_shutdown_release_are_atomic_repository_operations() -> None:
    fail_source = inspect.getsource(Repository.fail_job)
    recovery_source = inspect.getsource(Repository._recover_exhausted_expired_jobs)
    finalizer_source = inspect.getsource(Repository._finalize_i0_terminal_failure)
    initialize_source = inspect.getsource(Repository.initialize)
    assert "self._finalize_i0_terminal_failure(" in fail_source
    assert "self._finalize_i0_terminal_failure(" in recovery_source
    for table_name in (
        "queries",
        "jobs",
        "module_runs",
        "iterations",
        "claim_investigations",
    ):
        assert f"{{self.schema}}.{table_name}" in finalizer_source
    assert "self._reconcile_terminal_i0_failures(" in initialize_source

    release_source = inspect.getsource(Repository.release_job_lease_for_shutdown)
    assert "with self.transaction() as connection" in release_source
    assert "lease_owner = :worker_id" in release_source
    assert "lease_token = :lease_token" in release_source
    assert "attempt_count = GREATEST(attempt_count - 1, 0)" in release_source
    assert 'event_type="job.requeued_for_worker_shutdown"' in release_source


@pytest.fixture
def postgres_repository(monkeypatch: pytest.MonkeyPatch) -> Repository:
    """Use an ephemeral schema so a live test worker cannot steal fixture jobs."""

    database_url = os.getenv("INVALIDITY_TEST_DATABASE_URL", "").strip()
    if not database_url:
        pytest.skip("未设置 INVALIDITY_TEST_DATABASE_URL，跳过真实 PostgreSQL 集成测试")
    schema = f"invalidity_contract_{uuid.uuid4().hex[:16]}"
    # Runtime configuration remains fail-closed to the two fixed schemas.  The
    # integration fixture temporarily replaces only the in-process test
    # mapping, preserving that production invariant while avoiding contention
    # with the already-running 5209 worker in invalidity_test.
    monkeypatch.setitem(db_module.SCHEMA_BY_ENVIRONMENT, "test", schema)
    repository = Repository(database_url, schema, "test")
    repository.initialize()
    try:
        yield repository
    finally:
        try:
            with repository.transaction() as connection:
                connection.execute(text(f"DROP SCHEMA IF EXISTS {schema} CASCADE"))
        finally:
            repository.close()


def test_real_postgresql_snapshot_transition_lease_and_completion(
    postgres_repository: Repository,
) -> None:
    repository = postgres_repository
    suffix = uuid.uuid4().hex
    investigation_id: uuid.UUID | None = None
    try:
        investigation = repository.create_investigation(
            analysis_session_id=f"db_contract_{suffix}",
            patent_record_id=f"patent_{suffix}",
            source_snapshot={
                "module1": {
                    "session": f"analysis_{suffix}",
                    "claims": [{"id": "1", "text": "一种测试装置"}],
                }
            },
            pipeline_version="test-contract-v1",
            settings={"max_rounds": 3},
            budgets={"queries": 10},
        )
        investigation_id = investigation["id"]
        assert investigation["environment"] == "test"
        assert investigation["source_snapshot"]["module1"]["claims"][0]["id"] == "1"
        assert repository.get_investigation_by_session(
            f"db_contract_{suffix}"
        )["id"] == investigation_id
        investigation = repository.merge_investigation_workflow_state(
            investigation_id,
            {"stage": "claim_setup"},
            expected_state_version=0,
            actor="integration-test",
        )
        assert investigation["workflow_state"] == {"stage": "claim_setup"}
        assert investigation["state_version"] == 1

        claim = repository.create_claim_investigation(
            investigation_id=investigation_id,
            claim_id="1",
            source_claim_text="一种测试装置",
            expanded_claim_text="一种测试装置，包括部件A",
            source_snapshot={"claim": "一种测试装置", "features": ["部件A"]},
        )
        limitations = repository.insert_claim_limitations(
            claim["id"],
            [
                {
                    "feature_key": "F1",
                    "sequence_no": 1,
                    "limitation_text": "包括部件A",
                }
            ],
            actor="integration-test",
        )
        assert limitations[0]["feature_key"] == "F1"
        assert repository.list_claim_limitations(claim["id"])[0]["id"] == limitations[0][
            "id"
        ]
        iteration = repository.create_iteration(
            claim_investigation_id=claim["id"],
            iteration_no=1,
            purpose="initial_search",
        )
        scheduled = repository.transition_claim_and_schedule(
            claim_investigation_id=claim["id"],
            expected_state_version=0,
            new_claim_status="searching",
            module_code="search.patent",
            contract_version="v1",
            input_snapshot={"features": ["部件A"]},
            idempotency_key=f"search:{suffix}:1",
            event_type="claim.search_scheduled",
            actor="integration-test",
            event_payload={"round": 1},
            job_payload={"query": "部件A"},
            iteration_id=iteration["id"],
            priority=2_000_000_000,
        )
        assert scheduled["claim"]["state_version"] == 1

        query = repository.upsert_query(
            iteration_id=iteration["id"],
            query_type="feature_combination",
            channel="patent",
            language="zh",
            expression='"测试装置" "部件A"',
            feature_ids=["F1"],
            date_filter={"before": "2020-01-01"},
            actor="integration-test",
        )
        assert query["status"] == "planned"
        document = repository.upsert_document(
            investigation_id=investigation_id,
            canonical_key="patent:CN000000A",
            document_type="patent",
            title="测试对比文件",
            evidence_level="retrieved_document",
            identifiers={"publication_number": "CN000000A"},
            dates={"publication_date": "2019-01-01"},
            content_sha256="a" * 64,
            content_mime_type="application/pdf",
            content_byte_size=1024,
            actor="integration-test",
        )
        assert document["document_version_id"]
        assert document["document_version_no"] == 1
        source = repository.upsert_document_source(
            document_id=document["id"],
            provider="fixture",
            source_url="https://example.invalid/CN000000A.pdf",
            retrieved_at=datetime(2026, 7, 19, tzinfo=timezone.utc),
            snapshot_uri="artifact://CN000000A.pdf",
            snapshot_sha256="a" * 64,
            actor="integration-test",
        )
        qualification = repository.upsert_document_qualification(
            document_id=document["id"],
            claim_investigation_id=claim["id"],
            limitation_id=limitations[0]["id"],
            critical_date=date(2020, 1, 1),
            publication_date=date(2019, 1, 1),
            public_availability_date=date(2019, 1, 1),
            eligibility_type="ordinary_prior_art",
            novelty_eligible=True,
            inventive_step_eligible=True,
            verification_status="verified",
            verification_reason="fixture date",
            date_rule_version="v1",
            actor="integration-test",
        )
        assert source["status"] == "retrieved"
        assert source["document_version_id"] == document["document_version_id"]
        assert qualification["novelty_eligible"] is True
        assert (
            qualification["document_version_id"]
            == document["document_version_id"]
        )
        repeated_query = repository.upsert_query(
            iteration_id=iteration["id"],
            query_type="feature_combination",
            channel="patent",
            language="zh",
            expression='"测试装置" "部件A"',
            feature_ids=["F1"],
            date_filter={"before": "2020-01-01"},
            status="executed",
            executed_at=datetime(2026, 7, 19, tzinfo=timezone.utc),
            actor="integration-test",
        )
        repeated_document = repository.upsert_document(
            investigation_id=investigation_id,
            canonical_key="patent:CN000000A",
            document_type="patent",
            title="测试对比文件（已核验）",
            evidence_level="qualified_evidence",
            metadata={"verified": True},
            actor="integration-test",
        )
        repeated_source = repository.upsert_document_source(
            document_id=document["id"],
            provider="fixture",
            source_url="https://example.invalid/CN000000A.pdf",
            retrieved_at=datetime(2026, 7, 19, tzinfo=timezone.utc),
            snapshot_uri="artifact://CN000000A.pdf",
            snapshot_sha256="a" * 64,
            status="verified",
            actor="integration-test",
        )
        repeated_qualification = repository.upsert_document_qualification(
            document_id=document["id"],
            claim_investigation_id=claim["id"],
            limitation_id=limitations[0]["id"],
            critical_date=date(2020, 1, 1),
            publication_date=date(2019, 1, 1),
            public_availability_date=date(2019, 1, 1),
            eligibility_type="ordinary_prior_art",
            novelty_eligible=True,
            inventive_step_eligible=True,
            verification_status="human_confirmed",
            verification_reason="fixture reviewed",
            date_rule_version="v1",
            human_confirmed=True,
            actor="integration-test",
        )
        assert repeated_query["id"] == query["id"]
        assert repeated_document["id"] == document["id"]
        assert repeated_document["evidence_level"] == "qualified_evidence"
        assert repeated_source["id"] == source["id"]
        assert repeated_qualification["id"] == qualification["id"]

        disclosures = repository.insert_feature_disclosures(
            module_run_id=scheduled["module_run"]["id"],
            claim_investigation_id=claim["id"],
            document_id=document["id"],
            document_source_id=source["id"],
            disclosures=[
                {
                    "limitation_id": limitations[0]["id"],
                    "disclosure_status": "disclosed",
                    "excerpt": "部件A",
                    "locator": {"paragraph": "[0010]"},
                }
            ],
            rule_version="v1",
            actor="integration-test",
        )
        assert disclosures[0]["disclosure_status"] == "disclosed"
        assert (
            disclosures[0]["document_version_id"]
            == document["document_version_id"]
        )
        selection = repository.insert_closest_prior_art_version(
            claim_investigation_id=claim["id"],
            iteration_id=iteration["id"],
            document_id=document["id"],
            metrics={"coverage": 1},
            rationale={"reason": "fixture"},
            selected_by="integration-test",
            actor="integration-test",
        )
        assert selection["document_version_id"] == document["document_version_id"]
        first_open_gaps = [
            {
                "gap_id": "combination-technical-problem",
                "gap_type": "combination_gap",
                "description": "需要相关技术问题证据",
                "search_objective": {
                    "subtype": "technical_problem",
                    "topic": "测试装置",
                    "search_anchor": "相关技术问题 + 测试装置",
                },
            },
            {
                "gap_id": "combination-motivation",
                "gap_type": "combination_gap",
                "description": "需要组合启示",
                "search_objective": {
                    "subtype": "combination_motivation",
                    "topic": "测试装置",
                    "feature": "部件A",
                    "search_anchor": "组合启示 + 测试装置 + 部件A",
                },
            },
        ]
        gaps = repository.reconcile_gap_frontier(
            iteration_id=iteration["id"],
            claim_investigation_id=claim["id"],
            source_module_run_id=scheduled["module_run"]["id"],
            open_gaps=first_open_gaps,
            actor="integration-test",
        )
        assert len(gaps) == 2
        assert {gap["gap_key"] for gap in gaps} == {
            "combination-technical-problem",
            "combination-motivation",
        }
        assert all(gap["status"] == "open" for gap in gaps)
        assert all(gap["limitation_id"] is None for gap in gaps)
        replayed_gaps = repository.reconcile_gap_frontier(
            iteration_id=iteration["id"],
            claim_investigation_id=claim["id"],
            source_module_run_id=scheduled["module_run"]["id"],
            open_gaps=first_open_gaps,
            actor="integration-test-replay",
        )
        assert [gap["id"] for gap in replayed_gaps] == [
            gap["id"] for gap in gaps
        ]
        with pytest.raises(
            RepositoryConflictError,
            match="gap frontier 已提交",
        ):
            repository.reconcile_gap_frontier(
                iteration_id=iteration["id"],
                claim_investigation_id=claim["id"],
                source_module_run_id=scheduled["module_run"]["id"],
                open_gaps=first_open_gaps[:1],
                actor="integration-test-conflicting-replay",
            )

        second_iteration = repository.create_iteration(
            claim_investigation_id=claim["id"],
            iteration_no=2,
            purpose="gap_search",
            trigger_reason="combination_gap",
            parent_iteration_id=iteration["id"],
        )
        second_run = repository.create_module_run(
            investigation_id=investigation_id,
            claim_investigation_id=claim["id"],
            iteration_id=second_iteration["id"],
            module_code="I4_COMBINATION",
            contract_version="v1",
            input_snapshot={"round": 2},
            idempotency_key=f"gap-frontier:{suffix}:2",
        )
        second_frontier = repository.reconcile_gap_frontier(
            iteration_id=second_iteration["id"],
            claim_investigation_id=claim["id"],
            source_module_run_id=second_run["id"],
            open_gaps=[first_open_gaps[1]],
            closed_gap_resolutions={
                "combination-technical-problem": {
                    "reason": "qualified_document_resolved_gap",
                    "resolved_by_document_id": document["id"],
                    "resolution_evidence": {"document_role": "D2"},
                }
            },
            actor="integration-test",
        )
        second_by_key = {gap["gap_key"]: gap for gap in second_frontier}
        assert second_by_key["combination-motivation"]["status"] == "open"
        assert second_by_key["combination-motivation"]["version_no"] == 2
        closed_gap = second_by_key["combination-technical-problem"]
        assert closed_gap["status"] == "closed"
        assert closed_gap["version_no"] == 2
        assert closed_gap["supersedes_id"] is not None
        assert closed_gap["closed_reason"] == "qualified_document_resolved_gap"
        assert closed_gap["resolved_by_document_id"] == document["id"]
        assert closed_gap["resolution_evidence"]["document_role"] == "D2"

        third_iteration = repository.create_iteration(
            claim_investigation_id=claim["id"],
            iteration_no=3,
            purpose="novelty_review",
            trigger_reason="single_reference_complete",
            parent_iteration_id=second_iteration["id"],
        )
        third_frontier = repository.reconcile_gap_frontier(
            iteration_id=third_iteration["id"],
            claim_investigation_id=claim["id"],
            open_gaps=[],
            closed_gap_resolutions={
                "combination-motivation": {
                    "reason": "single_reference_novelty_complete",
                    "resolved_by_document_id": document["id"],
                }
            },
            actor="integration-test",
        )
        assert len(third_frontier) == 1
        assert third_frontier[0]["gap_key"] == "combination-motivation"
        assert third_frontier[0]["status"] == "closed"
        assert third_frontier[0]["version_no"] == 3
        assert repository.reconcile_gap_frontier(
            iteration_id=third_iteration["id"],
            claim_investigation_id=claim["id"],
            open_gaps=[],
            closed_gap_resolutions={
                "combination-motivation": {
                    "reason": "single_reference_novelty_complete",
                    "resolved_by_document_id": document["id"],
                }
            },
            actor="integration-test-replay",
        )[0]["id"] == third_frontier[0]["id"]
        combination = repository.insert_combination(
            claim_investigation_id=claim["id"],
            iteration_id=iteration["id"],
            module_run_id=scheduled["module_run"]["id"],
            closest_prior_art_version_id=selection["id"],
            document_ids=[document["id"]],
            status="needs_more_evidence",
            coverage_complete=True,
            motivation_status="missing",
            analysis={"teaching": "not established"},
            actor="integration-test",
        )
        assert combination["motivation_status"] == "missing"
        artifact = repository.insert_artifact(
            investigation_id=investigation_id,
            module_run_id=scheduled["module_run"]["id"],
            artifact_type="source_pdf",
            uri="artifact://CN000000A.pdf",
            sha256="a" * 64,
            actor="integration-test",
        )
        assert artifact["artifact_type"] == "source_pdf"

        leased = repository.claim_job(worker_id=f"worker-{suffix}", lease_seconds=60)
        assert leased is not None
        assert leased["id"] == scheduled["job"]["id"]
        assert leased["attempt_count"] == 1
        assert leased["lease_token"] is not None

        heartbeat = repository.heartbeat_job(
            job_id=leased["id"],
            worker_id=f"worker-{suffix}",
            lease_token=leased["lease_token"],
            lease_seconds=60,
        )
        assert heartbeat["status"] == "leased"

        completed = repository.complete_job(
            job_id=leased["id"],
            worker_id=f"worker-{suffix}",
            lease_token=leased["lease_token"],
            output_snapshot={"documents": ["CN000000A"]},
        )
        assert completed["job"]["status"] == "succeeded"
        assert completed["module_run"]["output_snapshot"] == {
            "documents": ["CN000000A"]
        }
        report = repository.get_report_data(investigation_id)
        assert report is not None
        assert report["investigation"]["id"] == investigation_id
        assert len(report["claim_limitations"]) == 1
        assert len(report["documents"]) == 1
        assert len(report["feature_disclosures"]) == 1
        gap_history = report["gap_items"]
        assert len(gap_history) == 5
        assert [
            (gap["gap_key"], gap["version_no"], gap["status"])
            for gap in gap_history
        ] == [
            ("combination-motivation", 1, "open"),
            ("combination-motivation", 2, "open"),
            ("combination-motivation", 3, "closed"),
            ("combination-technical-problem", 1, "open"),
            ("combination-technical-problem", 2, "closed"),
        ]
        event_types = {
            event["event_type"] for event in repository.list_events(investigation_id)
        }
        assert {
            "investigation.workflow_state_merged",
            "claim.limitations_inserted",
            "claim.search_scheduled",
            "query.upserted",
            "document.upserted",
            "document.source_upserted",
            "document.qualification_upserted",
            "feature_disclosures.inserted",
            "closest_prior_art.selected",
            "gap_frontier.reconciled",
            "combination.upserted",
            "artifact.inserted",
            "job.succeeded",
        } <= event_types
        frontier_events = [
            event
            for event in repository.list_events(investigation_id)
            if event["event_type"] == "gap_frontier.reconciled"
        ]
        assert len(frontier_events) == 3
    finally:
        if investigation_id is not None:
            with repository.transaction() as connection:
                connection.execute(
                    text(
                        f"DELETE FROM {repository.schema}.investigations WHERE id = :id"
                    ),
                    {"id": investigation_id},
                )


def test_real_postgresql_continuation_is_cas_idempotent_and_preserves_gap_history(
    postgres_repository: Repository,
) -> None:
    repository = postgres_repository
    suffix = uuid.uuid4().hex
    investigation_id: uuid.UUID | None = None
    try:
        investigation = repository.create_investigation(
            analysis_session_id=f"continuation_contract_{suffix}",
            patent_record_id=f"patent_{suffix}",
            source_snapshot={"claims": [{"id": "1", "text": "测试权利要求"}]},
            pipeline_version="continuation-contract-v1",
            status="partial",
        )
        investigation_id = investigation["id"]
        continuable_claim = repository.create_claim_investigation(
            investigation_id=investigation_id,
            claim_id="1",
            source_claim_text="测试权利要求",
            expanded_claim_text="测试权利要求，包括部件A",
            source_snapshot={"features": ["部件A"]},
            status="partial",
        )
        successful_claim = repository.create_claim_investigation(
            investigation_id=investigation_id,
            claim_id="2",
            source_claim_text="另一项测试权利要求",
            expanded_claim_text="另一项测试权利要求，包括部件B",
            source_snapshot={"features": ["部件B"]},
            status="novelty_evidence_complete",
        )
        no_open_gap_claim = repository.create_claim_investigation(
            investigation_id=investigation_id,
            claim_id="3",
            source_claim_text="无待解决缺口的测试权利要求",
            expanded_claim_text="无待解决缺口的测试权利要求，包括部件C",
            source_snapshot={"features": ["部件C"]},
            status="partial",
        )
        iteration = repository.create_iteration(
            claim_investigation_id=continuable_claim["id"],
            iteration_no=1,
            purpose="initial_search",
            status="running",
        )
        repository.reconcile_gap_frontier(
            iteration_id=iteration["id"],
            claim_investigation_id=continuable_claim["id"],
            open_gaps=[
                {
                    "gap_key": "feature-gap-A",
                    "gap_type": "feature_gap",
                    "description": "尚未披露部件A",
                    "search_objective": {
                        "feature_id": "F1",
                        "search_anchor": "测试权利要求 + 部件A",
                    },
                }
            ],
            actor="integration-test",
        )
        repository.update_iteration(
            iteration["id"],
            status="partial",
            stop_reason="automatic_round_limit",
            actor="integration-test",
        )
        with repository.engine.connect() as connection:
            history_before = [
                dict(row)
                for row in connection.execute(
                    text(
                            f"""
                            SELECT * FROM {repository.schema}.gap_items
                        WHERE claim_investigation_id = :claim_id
                        ORDER BY gap_key, version_no
                        """
                    ),
                    {"claim_id": continuable_claim["id"]},
                ).mappings()
            ]

        command = {
            "investigation_id": investigation_id,
            "claim_investigation_ids": None,
            "max_additional_rounds": 2,
            "reason": "人工确认后继续解决未关闭 gap",
            "expected_state_version": 0,
            "idempotency_key_sha256": "b" * 64,
            "request_sha256": "c" * 64,
            "module_code": "I0_ORCHESTRATE",
            "contract_version": "v1",
            "actor": "integration-test",
        }
        first = repository.create_continuation_batch_and_enqueue(**command)
        assert first["idempotent_replay"] is False
        assert first["investigation"]["state_version"] == 1
        assert first["continuation_batch"]["round_plan"] == [
            {
                "claim_investigation_id": str(continuable_claim["id"]),
                "parent_iteration_id": str(iteration["id"]),
                "start_iteration_no": 2,
                "end_iteration_no": 3,
            }
        ]
        replay = repository.create_continuation_batch_and_enqueue(**command)
        assert replay["idempotent_replay"] is True
        assert replay["continuation_batch"]["id"] == first["continuation_batch"][
            "id"
        ]
        assert replay["module_run"]["id"] == first["module_run"]["id"]
        assert replay["job"]["id"] == first["job"]["id"]

        with pytest.raises(RepositoryConflictError, match="幂等键已用于不同请求"):
            repository.create_continuation_batch_and_enqueue(
                **{**command, "request_sha256": "d" * 64}
            )
        with pytest.raises(RepositoryConflictError, match="state_version 已变化"):
            repository.create_continuation_batch_and_enqueue(
                **{
                    **command,
                    "idempotency_key_sha256": "e" * 64,
                    "request_sha256": "f" * 64,
                }
            )
        with pytest.raises(RepositoryConflictError, match="没有未解决的 open gap"):
            repository.create_continuation_batch_and_enqueue(
                **{
                    **command,
                    "claim_investigation_ids": [no_open_gap_claim["id"]],
                    "expected_state_version": 1,
                    "idempotency_key_sha256": "0" * 64,
                    "request_sha256": "9" * 64,
                }
            )
        with pytest.raises(RepositoryConflictError, match="成功证据终态"):
            repository.create_continuation_batch_and_enqueue(
                **{
                    **command,
                    "claim_investigation_ids": [successful_claim["id"]],
                    "expected_state_version": 1,
                    "idempotency_key_sha256": "1" * 64,
                    "request_sha256": "2" * 64,
                }
            )

        with repository.engine.connect() as connection:
            history_after = [
                dict(row)
                for row in connection.execute(
                    text(
                            f"""
                            SELECT * FROM {repository.schema}.gap_items
                        WHERE claim_investigation_id = :claim_id
                        ORDER BY gap_key, version_no
                        """
                    ),
                    {"claim_id": continuable_claim["id"]},
                ).mappings()
            ]
            continuation_count = connection.execute(
                text(
                        f"""
                        SELECT count(*) FROM {repository.schema}.continuation_batches
                    WHERE investigation_id = :investigation_id
                    """
                ),
                {"investigation_id": investigation_id},
            ).scalar_one()
            continuation_job_count = connection.execute(
                text(
                        f"""
                        SELECT count(*) FROM {repository.schema}.jobs
                    WHERE module_run_id = :module_run_id
                    """
                ),
                {"module_run_id": first["module_run"]["id"]},
            ).scalar_one()
        assert history_after == history_before
        assert continuation_count == 1
        assert continuation_job_count == 1
    finally:
        if investigation_id is not None:
            with repository.transaction() as connection:
                connection.execute(
                    text(
                        f"DELETE FROM {repository.schema}.investigations WHERE id = :id"
                    ),
                    {"id": investigation_id},
                )


def test_real_postgresql_final_expired_lease_fails_once_without_overrun(
    postgres_repository: Repository,
) -> None:
    repository = postgres_repository
    suffix = uuid.uuid4().hex
    investigation_id: uuid.UUID | None = None
    try:
        investigation = repository.create_investigation(
            analysis_session_id=f"lease_exhaustion_{suffix}",
            patent_record_id=f"patent_{suffix}",
            source_snapshot={"claims": [{"id": "1", "text": "测试权利要求"}]},
            pipeline_version="lease-contract-v1",
            status="queued",
        )
        investigation_id = investigation["id"]
        claim = repository.create_claim_investigation(
            investigation_id=investigation_id,
            claim_id="1",
            source_claim_text="一种测试装置",
            expanded_claim_text="一种测试装置，包括部件A",
            source_snapshot={"features": ["部件A"]},
            status="single_reference_review",
        )
        iteration = repository.create_iteration(
            claim_investigation_id=claim["id"],
            iteration_no=1,
            purpose="initial_search",
            status="running",
        )
        query = repository.upsert_query(
            iteration_id=iteration["id"],
            query_type="full_claim",
            channel="ordinary_prior_art",
            expression="测试装置 部件A",
            date_filter={"published_before": "2020-01-02"},
            status="planned",
        )
        child_run = repository.create_module_run(
            investigation_id=investigation_id,
            claim_investigation_id=claim["id"],
            iteration_id=iteration["id"],
            module_code="I3_P_PATENT_SEARCH",
            contract_version="v1",
            input_snapshot={"query_id": str(query["id"])},
            idempotency_key=f"child-running:{suffix}",
            status="running",
        )
        module_run = repository.create_module_run(
            investigation_id=investigation_id,
            module_code="I0_ORCHESTRATE",
            contract_version="v1",
            input_snapshot={"investigation_id": str(investigation_id)},
            idempotency_key=f"lease-exhaustion:{suffix}",
            status="queued",
        )
        job = repository.enqueue_job(
            module_run_id=module_run["id"],
            payload={"kind": "investigation"},
            priority=2_147_483_647,
            max_attempts=2,
        )

        first_lease = repository.claim_job(
            worker_id=f"lease-worker-1-{suffix}",
            lease_seconds=60,
        )
        assert first_lease is not None
        assert first_lease["id"] == job["id"]
        assert first_lease["attempt_count"] == 1
        with repository.transaction() as connection:
            connection.execute(
                text(
                        f"""
                        UPDATE {repository.schema}.jobs
                    SET lease_expires_at = now() - interval '1 second'
                    WHERE id = :job_id
                    """
                ),
                {"job_id": job["id"]},
            )

        final_lease = repository.claim_job(
            worker_id=f"lease-worker-2-{suffix}",
            lease_seconds=60,
        )
        assert final_lease is not None
        assert final_lease["id"] == job["id"]
        assert final_lease["attempt_count"] == 2
        assert final_lease["attempt_count"] == final_lease["max_attempts"]
        with repository.transaction() as connection:
            connection.execute(
                text(
                        f"""
                        UPDATE {repository.schema}.jobs
                    SET lease_expires_at = now() - interval '1 second'
                    WHERE id = :job_id
                    """
                ),
                {"job_id": job["id"]},
            )

        with pytest.raises(LeaseLostError):
            repository.complete_job(
                job_id=job["id"],
                worker_id=f"lease-worker-2-{suffix}",
                lease_token=final_lease["lease_token"],
                output_snapshot={"status": "completed"},
            )

        # A normal worker poll performs terminal recovery before trying to
        # lease work.  It must not execute this job for a third time.
        assert (
            repository.claim_job(
                worker_id=f"lease-worker-3-{suffix}",
                lease_seconds=60,
            )
            is None
        )

        failed_job = repository.get_job(job["id"])
        failed_run = repository.get_module_run(module_run["id"])
        failed_investigation = repository.get_investigation(investigation_id)
        assert failed_job is not None
        assert failed_run is not None
        assert failed_investigation is not None
        assert failed_job["status"] == "failed"
        assert failed_job["attempt_count"] == 2
        assert failed_job["lease_owner"] is None
        assert failed_job["lease_token"] is None
        assert failed_job["last_error"]["code"] == (
            "LEASE_EXPIRED_ATTEMPTS_EXHAUSTED"
        )
        assert failed_run["status"] == "failed"
        assert failed_run["attempt_no"] == 2
        assert failed_run["output_snapshot"] is None
        assert failed_investigation["status"] == "failed"
        failed_claim = repository.get_claim_investigation(claim["id"])
        failed_iteration = repository.get_claim_iteration(claim["id"], 1)
        failed_child_run = repository.get_module_run(child_run["id"])
        assert failed_claim is not None and failed_claim["status"] == "failed"
        assert failed_claim["completed_at"] is not None
        assert failed_iteration is not None
        assert failed_iteration["status"] == "failed"
        assert failed_iteration["completed_at"] is not None
        assert failed_child_run is not None
        assert failed_child_run["status"] == "failed"
        with repository.engine.connect() as connection:
            failed_query = connection.execute(
                text(
                    f"SELECT * FROM {repository.schema}.queries WHERE id = :id"
                ),
                {"id": query["id"]},
            ).mappings().one()
        assert failed_query["status"] == "failed"
        assert failed_query["executed_at"] is not None

        # Recovery is idempotent and cannot emit another terminal event.
        assert not any(
            item["job"]["id"] == job["id"]
            for item in repository.recover_expired_jobs(
                actor=f"lease-reaper-{suffix}"
            )
        )
        events = repository.list_events(investigation_id)
        job_failures = [
            event
            for event in events
            if event["event_type"] == "job.failed"
            and event["payload"].get("job_id") == str(job["id"])
        ]
        investigation_failures = [
            event
            for event in events
            if event["event_type"] == "investigation.failed"
            and event["payload"].get("job_id") == str(job["id"])
        ]
        assert len(job_failures) == 1
        assert len(investigation_failures) == 1
        assert job_failures[0]["payload"]["attempt"] == 2
        assert not any(event["event_type"] == "job.succeeded" for event in events)
    finally:
        if investigation_id is not None:
            with repository.transaction() as connection:
                connection.execute(
                    text(
                        f"DELETE FROM {repository.schema}.investigations WHERE id = :id"
                    ),
                    {"id": investigation_id},
                )


def test_real_postgresql_cancel_requested_expired_lease_is_swept_atomically(
    postgres_repository: Repository,
) -> None:
    """取消请求的过期租约必须被普通 poll 清扫收口，不能永远卡在 leased。

    回归：`_mark_i0_investigation_cancelled` 的 UPDATE ... FROM 曾因未限定的
    `completed_at` 在两表同名列上触发 AmbiguousColumn，导致 claim_job 每次
     poll 都 ProgrammingError 回滚，取消请求永远落不了地（用户看到的"假停止"）。
    """

    repository = postgres_repository
    suffix = uuid.uuid4().hex
    investigation_id: uuid.UUID | None = None
    try:
        investigation = repository.create_investigation(
            analysis_session_id=f"cancel_sweep_{suffix}",
            patent_record_id=f"patent_{suffix}",
            source_snapshot={"claims": [{"id": "1", "text": "测试权利要求"}]},
            pipeline_version="cancel-sweep-contract-v1",
            status="running",
        )
        investigation_id = investigation["id"]
        claim = repository.create_claim_investigation(
            investigation_id=investigation_id,
            claim_id="1",
            source_claim_text="一种测试装置",
            expanded_claim_text="一种测试装置，包括部件A",
            source_snapshot={"features": ["部件A"]},
            status="gap_search",
        )
        iteration = repository.create_iteration(
            claim_investigation_id=claim["id"],
            iteration_no=1,
            purpose="initial_search",
            status="running",
        )
        module_run = repository.create_module_run(
            investigation_id=investigation_id,
            module_code="I0_ORCHESTRATE",
            contract_version="v1",
            input_snapshot={"investigation_id": str(investigation_id)},
            idempotency_key=f"cancel-sweep:{suffix}",
            status="queued",
        )
        job = repository.enqueue_job(
            module_run_id=module_run["id"],
            payload={"kind": "investigation"},
            priority=2_147_483_647,
            max_attempts=3,
        )

        leased = repository.claim_job(
            worker_id=f"cancel-sweep-worker-1-{suffix}",
            lease_seconds=60,
        )
        assert leased is not None and leased["id"] == job["id"]

        repository.request_module_run_cancellation(
            module_run["id"],
            reason="用户停止",
            actor="test",
        )
        with repository.transaction() as connection:
            connection.execute(
                text(
                    f"""
                    UPDATE {repository.schema}.jobs
                    SET lease_expires_at = now() - interval '1 second'
                    WHERE id = :job_id
                    """
                ),
                {"job_id": job["id"]},
            )

        # 普通 worker poll 必须先清扫取消请求，再尝试领取新工作；
        # 修复前这里每次都会 ProgrammingError 回滚。
        assert (
            repository.claim_job(
                worker_id=f"cancel-sweep-worker-2-{suffix}",
                lease_seconds=60,
            )
            is None
        )

        swept_job = repository.get_job(job["id"])
        swept_run = repository.get_module_run(module_run["id"])
        swept_investigation = repository.get_investigation(investigation_id)
        swept_claim = repository.get_claim_investigation(claim["id"])
        swept_iteration = repository.get_claim_iteration(claim["id"], 1)
        assert swept_job is not None
        assert swept_job["status"] == "cancelled"
        assert swept_job["cancelled_at"] is not None
        assert swept_job["lease_owner"] is None
        assert swept_run is not None and swept_run["status"] == "cancelled"
        assert swept_investigation is not None
        assert swept_investigation["status"] == "cancelled"
        assert swept_claim is not None and swept_claim["status"] == "cancelled"
        assert swept_iteration is not None
        assert swept_iteration["status"] == "cancelled"

        # 清扫幂等：后续 poll 不再报错也不重复产生事件。
        assert (
            repository.claim_job(
                worker_id=f"cancel-sweep-worker-3-{suffix}",
                lease_seconds=60,
            )
            is None
        )
        events = repository.list_events(investigation_id)
        cancels = [
            event
            for event in events
            if event["event_type"] == "job.cancelled"
            and event["payload"].get("job_id") == str(job["id"])
        ]
        assert len(cancels) == 1
    finally:
        if investigation_id is not None:
            with repository.transaction() as connection:
                connection.execute(
                    text(
                        f"DELETE FROM {repository.schema}.investigations WHERE id = :id"
                    ),
                    {"id": investigation_id},
                )


def test_real_postgresql_shutdown_release_is_immediately_reclaimable(
    postgres_repository: Repository,
) -> None:
    repository = postgres_repository
    suffix = uuid.uuid4().hex
    investigation_id: uuid.UUID | None = None
    try:
        investigation = repository.create_investigation(
            analysis_session_id=f"shutdown_release_{suffix}",
            patent_record_id=f"patent_{suffix}",
            source_snapshot={"claims": []},
            pipeline_version="shutdown-release-v1",
            status="queued",
        )
        investigation_id = investigation["id"]
        module_run = repository.create_module_run(
            investigation_id=investigation_id,
            module_code="I0_ORCHESTRATE",
            contract_version="v1",
            input_snapshot={"investigation_id": str(investigation_id)},
            idempotency_key=f"shutdown-release:{suffix}",
            status="queued",
        )
        job = repository.enqueue_job(
            module_run_id=module_run["id"],
            payload={"kind": "investigation"},
            priority=2_147_483_647,
            max_attempts=1,
        )
        first = repository.claim_job(
            worker_id=f"shutdown-old-{suffix}", lease_seconds=600
        )
        assert first is not None and first["id"] == job["id"]
        assert first["attempt_count"] == 1

        released = repository.release_job_lease_for_shutdown(
            job_id=first["id"],
            worker_id=f"shutdown-old-{suffix}",
            lease_token=first["lease_token"],
        )
        assert released["released"] is True
        assert released["job"]["status"] == "queued"
        assert released["job"]["attempt_count"] == 0
        assert released["job"]["lease_owner"] is None
        assert released["module_run"]["status"] == "queued"

        with pytest.raises(LeaseLostError):
            repository.complete_job(
                job_id=first["id"],
                worker_id=f"shutdown-old-{suffix}",
                lease_token=first["lease_token"],
                output_snapshot={"status": "completed"},
            )
        reclaimed = repository.claim_job(
            worker_id=f"shutdown-new-{suffix}", lease_seconds=60
        )
        assert reclaimed is not None and reclaimed["id"] == job["id"]
        assert reclaimed["attempt_count"] == 1
        assert reclaimed["lease_token"] != first["lease_token"]
        events = repository.list_events(investigation_id)
        assert sum(
            event["event_type"] == "job.requeued_for_worker_shutdown"
            for event in events
        ) == 1
    finally:
        if investigation_id is not None:
            with repository.transaction() as connection:
                connection.execute(
                    text(
                        f"DELETE FROM {repository.schema}.investigations WHERE id = :id"
                    ),
                    {"id": investigation_id},
                )


def test_real_postgresql_human_review_action_stays_open_for_retry_then_fails(
    postgres_repository: Repository,
) -> None:
    repository = postgres_repository
    suffix = uuid.uuid4().hex
    investigation_id: uuid.UUID | None = None
    try:
        investigation = repository.create_investigation(
            analysis_session_id=f"review_retry_{suffix}",
            patent_record_id=f"patent_{suffix}",
            source_snapshot={"claims": [{"id": "1"}]},
            pipeline_version="review-retry-v1",
            status="queued",
        )
        investigation_id = investigation["id"]
        claim = repository.create_claim_investigation(
            investigation_id=investigation_id,
            claim_id="1",
            source_claim_text="一种装置",
            expanded_claim_text="一种装置，包括部件A",
            source_snapshot={"features": ["部件A"]},
            status="needs_human_review",
        )
        module_run = repository.create_module_run(
            investigation_id=investigation_id,
            module_code="HUMAN_REVIEW_APPLY",
            contract_version="v1",
            input_snapshot={"kind": "human_review_apply"},
            idempotency_key=f"review-apply:{suffix}",
            status="queued",
        )
        job = repository.enqueue_job(
            module_run_id=module_run["id"],
            payload={"kind": "human_review_apply"},
            priority=2_147_483_647,
            max_attempts=2,
        )
        action_id = uuid.uuid4()
        with repository.transaction() as connection:
            connection.execute(
                text(
                    f"""
                    UPDATE {repository.schema}.investigations
                    SET state_version = 1, review_revision = 1,
                        review_recomputation_required = true
                    WHERE id = :investigation_id
                    """
                ),
                {"investigation_id": investigation_id},
            )
            connection.execute(
                text(
                    f"""
                    UPDATE {repository.schema}.claim_investigations
                    SET state_version = 1, review_revision = 1
                    WHERE id = :claim_id
                    """
                ),
                {"claim_id": claim["id"]},
            )
            connection.execute(
                text(
                    f"""
                    INSERT INTO {repository.schema}.human_review_actions (
                        id, investigation_id, action_type, action_seq,
                        target_snapshot, actor, reason, request_sha256,
                        idempotency_key_sha256,
                        base_investigation_state_version,
                        resulting_investigation_state_version,
                        base_claim_state_versions,
                        resulting_claim_state_versions,
                        base_review_revision, resulting_review_revision,
                        status, module_run_id, job_id,
                        invalidated_derivations, pending_recomputation,
                        started_at
                    ) VALUES (
                        :id, :investigation_id, 'evidence_import', 1,
                        CAST(:target_snapshot AS JSONB), 'user:test',
                        '测试可重试失败', :request_sha256,
                        :idempotency_key_sha256, 0, 1,
                        CAST(:base_claim_versions AS JSONB),
                        CAST(:result_claim_versions AS JSONB),
                        0, 1, 'running', :module_run_id, :job_id,
                        '["report"]'::jsonb, '["report"]'::jsonb, now()
                    )
                    """
                ),
                {
                    "id": action_id,
                    "investigation_id": investigation_id,
                    "target_snapshot": json.dumps(
                        {"claim_investigation_ids": [str(claim["id"])]}
                    ),
                    "request_sha256": "a" * 64,
                    "idempotency_key_sha256": "b" * 64,
                    "base_claim_versions": json.dumps({str(claim["id"]): 0}),
                    "result_claim_versions": json.dumps({str(claim["id"]): 1}),
                    "module_run_id": module_run["id"],
                    "job_id": job["id"],
                },
            )

        first = repository.claim_job(
            worker_id=f"review-worker-1-{suffix}", lease_seconds=60
        )
        assert first is not None and first["id"] == job["id"]
        retried = repository.fail_job(
            job_id=first["id"],
            worker_id=f"review-worker-1-{suffix}",
            lease_token=first["lease_token"],
            error_code="MODEL_TEMPORARY",
            error_message="模型临时不可用",
            retryable=True,
        )
        assert retried["will_retry"] is True
        with repository.engine.connect() as connection:
            action_after_retry = connection.execute(
                text(
                    f"SELECT * FROM {repository.schema}.human_review_actions WHERE id = :id"
                ),
                {"id": action_id},
            ).mappings().one()
        assert action_after_retry["status"] == "running"
        assert action_after_retry["completed_at"] is None

        second = repository.claim_job(
            worker_id=f"review-worker-2-{suffix}", lease_seconds=60
        )
        assert second is not None and second["id"] == job["id"]
        failed = repository.fail_job(
            job_id=second["id"],
            worker_id=f"review-worker-2-{suffix}",
            lease_token=second["lease_token"],
            error_code="MODEL_TEMPORARY",
            error_message="模型重试耗尽",
            retryable=True,
        )
        assert failed["will_retry"] is False
        with repository.engine.connect() as connection:
            action_after_failure = connection.execute(
                text(
                    f"SELECT * FROM {repository.schema}.human_review_actions WHERE id = :id"
                ),
                {"id": action_id},
            ).mappings().one()
        assert action_after_failure["status"] == "failed"
        assert action_after_failure["completed_at"] is not None
        assert action_after_failure["error_summary"]["code"] == "MODEL_TEMPORARY"
        current_investigation = repository.get_investigation(investigation_id)
        current_claim = repository.get_claim_investigation(claim["id"])
        assert current_investigation is not None
        assert current_investigation["status"] == "needs_human_review"
        assert current_investigation["review_recomputation_required"] is True
        assert current_claim is not None
        assert current_claim["status"] == "needs_human_review"
    finally:
        if investigation_id is not None:
            with repository.transaction() as connection:
                connection.execute(
                    text(
                        f"DELETE FROM {repository.schema}.investigations WHERE id = :id"
                    ),
                    {"id": investigation_id},
                )


def test_real_postgresql_reconciles_historical_failed_i0_descendants(
    postgres_repository: Repository,
) -> None:
    repository = postgres_repository
    suffix = uuid.uuid4().hex
    investigation_id: uuid.UUID | None = None
    try:
        investigation = repository.create_investigation(
            analysis_session_id=f"historical_i0_failure_{suffix}",
            patent_record_id=f"patent_{suffix}",
            source_snapshot={"claims": [{"id": "1"}]},
            pipeline_version="historical-reconcile-v1",
            status="failed",
        )
        investigation_id = investigation["id"]
        claim = repository.create_claim_investigation(
            investigation_id=investigation_id,
            claim_id="1",
            source_claim_text="一种装置",
            expanded_claim_text="一种装置，包括部件A",
            source_snapshot={"features": ["部件A"]},
            status="single_reference_review",
        )
        iteration = repository.create_iteration(
            claim_investigation_id=claim["id"],
            iteration_no=1,
            purpose="initial_search",
            status="running",
        )
        query = repository.upsert_query(
            iteration_id=iteration["id"],
            query_type="full_claim",
            channel="ordinary_prior_art",
            expression="一种装置 部件A",
            date_filter={"published_before": "2020-01-02"},
            status="planned",
        )
        repository.create_module_run(
            investigation_id=investigation_id,
            module_code="I0_ORCHESTRATE",
            contract_version="v1",
            input_snapshot={"investigation_id": str(investigation_id)},
            idempotency_key=f"historical-failed:{suffix}",
            status="failed",
        )

        reconciled = repository.reconcile_terminal_i0_failures(
            actor=f"historical-reconcile-{suffix}"
        )
        assert len(reconciled) == 1
        assert reconciled[0]["transitioned"] is False
        assert reconciled[0]["descendant_counts"] == {
            "queries": 1,
            "jobs": 0,
            "module_runs": 0,
            "iterations": 1,
            "claims": 1,
        }
        assert repository.get_claim_investigation(claim["id"])["status"] == "failed"
        assert repository.get_claim_iteration(claim["id"], 1)["status"] == "failed"
        with repository.engine.connect() as connection:
            query_status = connection.execute(
                text(f"SELECT status FROM {repository.schema}.queries WHERE id = :id"),
                {"id": query["id"]},
            ).scalar_one()
        assert query_status == "failed"
        assert repository.reconcile_terminal_i0_failures(
            actor=f"historical-reconcile-replay-{suffix}"
        ) == []
    finally:
        if investigation_id is not None:
            with repository.transaction() as connection:
                connection.execute(
                    text(
                        f"DELETE FROM {repository.schema}.investigations WHERE id = :id"
                    ),
                    {"id": investigation_id},
                )


def test_real_postgresql_review_context_exposes_top_level_identity(
    postgres_repository: Repository,
) -> None:
    """Portal 契约要求 review-context 顶层携带 investigation_id 与 status。"""

    repository = postgres_repository
    suffix = uuid.uuid4().hex
    investigation_id: uuid.UUID | None = None
    try:
        investigation = repository.create_investigation(
            analysis_session_id=f"db_contract_review_context_{suffix}",
            patent_record_id=f"patent_{suffix}",
            source_snapshot={
                "module1": {
                    "session": f"analysis_{suffix}",
                    "claims": [{"id": "1", "text": "一种测试装置"}],
                }
            },
            pipeline_version="test-contract-v1",
            settings={"max_rounds": 3},
            budgets={"queries": 10},
        )
        investigation_id = investigation["id"]
        context = repository.get_review_context(investigation_id)
        assert context is not None
        assert context["contract_version"] == "v1"
        assert context["investigation_id"] == str(investigation_id)
        assert context["status"] == str(investigation["status"])
        assert context["investigation"]["id"] == investigation_id
        assert context["investigation"]["status"] == investigation["status"]
        assert isinstance(context["state_version"], int)
        assert isinstance(context["review_revision"], int)
        assert isinstance(context["quiescent"], bool)
        assert isinstance(context["recomputation_required"], bool)
        for name in (
            "claims",
            "documents",
            "document_versions",
            "latest_qualifications",
            "date_fact_revisions",
            "current_gaps",
            "current_d1",
            "action_history",
        ):
            assert isinstance(context[name], list), name
    finally:
        if investigation_id is not None:
            with repository.transaction() as connection:
                connection.execute(
                    text(
                        f"DELETE FROM {repository.schema}.investigations WHERE id = :id"
                    ),
                    {"id": investigation_id},
                )
