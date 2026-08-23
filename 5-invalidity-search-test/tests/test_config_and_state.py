from pathlib import Path

import pytest

from invalidity.config import ConfigurationError, ParserSettings, Settings
from invalidity.contracts import ClaimStatus
from invalidity.state_machine import (
    InvalidTransition,
    RoundProgress,
    aggregate_single_document_novelty,
    assert_transition,
    next_round_or_stop,
)


def valid_test_environment(tmp_path: Path) -> dict[str, str]:
    return {
        "INVALIDITY_ENV": "test",
        "INVALIDITY_PORT": "5209",
        "INVALIDITY_DATABASE_URL": "postgresql://localhost/example",
        "INVALIDITY_DATABASE_SCHEMA": "invalidity_test",
        "INVALIDITY_ARTIFACT_ROOT": str(tmp_path / "invalidity" / "test"),
        "INVALIDITY_ALLOWED_SOURCE_ROOTS": str(tmp_path / ".data" / "uploads" / "test"),
        "INVALIDITY_TEST_API_URL": "http://127.0.0.1:5209",
        "INVALIDITY_TEST_API_TOKEN": "test-api-token-with-enough-entropy",
        "INVALIDITY_TEST_PARSER_TOKEN": "test-parser-token-with-enough-entropy",
        "INVALIDITY_TEST_MODULE1_API_URL": "http://127.0.0.1:5201/run",
        "INVALIDITY_TEST_PATENT_PROVIDER": "google_patents",
        "INVALIDITY_TEST_NPL_PROVIDER": "arxiv",
        "INVALIDITY_TEST_LLM_BASE_URL": "https://example.invalid/v4",
        "INVALIDITY_TEST_LLM_API_KEY": "test-key",
        "INVALIDITY_TEST_LLM_MODEL": "glm-4.6v",
    }


def valid_parser_environment(tmp_path: Path) -> dict[str, str]:
    return {
        "INVALIDITY_ENV": "test",
        "INVALIDITY_PARSER_PORT": "5201",
        "INVALIDITY_ARTIFACT_ROOT": str(tmp_path / "invalidity" / "test"),
        "INVALIDITY_ALLOWED_SOURCE_ROOTS": str(tmp_path / ".data" / "uploads" / "test"),
        "INVALIDITY_TEST_PARSER_TOKEN": "test-parser-token-with-enough-entropy",
    }


def test_parser_configuration_requires_only_its_minimum_trust_boundary(
    tmp_path: Path,
) -> None:
    configured = ParserSettings.from_environment(
        valid_parser_environment(tmp_path), load_dotenv_files=False
    )
    assert configured.environment == "test"
    assert configured.parser_port == 5201
    assert configured.parser_api_token == "test-parser-token-with-enough-entropy"
    assert not hasattr(configured, "database_url")
    assert not hasattr(configured, "api_token")
    assert not hasattr(configured, "llm_api_key")


def test_parser_configuration_is_fail_closed(tmp_path: Path) -> None:
    env = valid_parser_environment(tmp_path)
    del env["INVALIDITY_TEST_PARSER_TOKEN"]
    with pytest.raises(ConfigurationError, match="INVALIDITY_TEST_PARSER_TOKEN"):
        ParserSettings.from_environment(env, load_dotenv_files=False)

    env = valid_parser_environment(tmp_path)
    env["INVALIDITY_ENV"] = "prod"
    with pytest.raises(ConfigurationError, match="test"):
        ParserSettings.from_environment(env, load_dotenv_files=False)


def test_api_and_parser_tokens_must_be_independent(tmp_path: Path) -> None:
    env = valid_test_environment(tmp_path)
    env["INVALIDITY_TEST_PARSER_TOKEN"] = env["INVALIDITY_TEST_API_TOKEN"]
    with pytest.raises(ConfigurationError, match="必须与.*不同"):
        Settings.from_environment(env, load_dotenv_files=False)


def test_llm_transport_budgets_are_configurable_and_auditable(
    tmp_path: Path,
) -> None:
    env = valid_test_environment(tmp_path)
    env.update(
        {
            "INVALIDITY_LLM_TIMEOUT_SECONDS": "420",
            "INVALIDITY_LLM_DIRECT_ATTEMPT_TIMEOUT_SECONDS": "150",
            "INVALIDITY_LLM_DIRECT_PROBE_TIMEOUT_SECONDS": "4",
            "INVALIDITY_I2_LLM_TIMEOUT_SECONDS": "480",
            "INVALIDITY_I2_LLM_DIRECT_ATTEMPT_TIMEOUT_SECONDS": "175",
        }
    )

    configured = Settings.from_environment(env, load_dotenv_files=False)

    assert configured.llm_timeout_seconds == 420
    assert configured.llm_direct_attempt_timeout_seconds == 150
    assert configured.llm_direct_probe_timeout_seconds == 4
    assert configured.llm_i2_timeout_seconds == 480
    assert configured.llm_i2_direct_attempt_timeout_seconds == 175
    summary = configured.isolation_summary()
    assert summary["llm_timeout_seconds"] == 420
    assert summary["llm_direct_attempt_timeout_seconds"] == 150
    assert summary["llm_direct_probe_timeout_seconds"] == 4
    assert summary["llm_i2_timeout_seconds"] == 480
    assert summary["llm_i2_direct_attempt_timeout_seconds"] == 175


def test_worker_concurrency_defaults_to_two_and_is_bounded(
    tmp_path: Path,
) -> None:
    env = valid_test_environment(tmp_path)
    configured = Settings.from_environment(env, load_dotenv_files=False)
    assert configured.worker_concurrency == 2
    assert configured.isolation_summary()["worker_concurrency"] == 2

    env["INVALIDITY_WORKER_CONCURRENCY"] = "4"
    assert (
        Settings.from_environment(env, load_dotenv_files=False).worker_concurrency
        == 4
    )

    env["INVALIDITY_WORKER_CONCURRENCY"] = "5"
    with pytest.raises(ConfigurationError, match=r"1\.\.4"):
        Settings.from_environment(env, load_dotenv_files=False)


def test_http_run_forwards_epo_credentials_and_llm_transport_budgets() -> None:
    script = (
        Path(__file__).resolve().parents[1] / "scripts" / "http_run.sh"
    ).read_text(encoding="utf-8")

    for required_name in (
        '${PREFIX}_EPO_OPS_KEY',
        '${PREFIX}_EPO_OPS_SECRET',
        "INVALIDITY_LLM_TIMEOUT_SECONDS",
        "INVALIDITY_LLM_DIRECT_ATTEMPT_TIMEOUT_SECONDS",
        "INVALIDITY_LLM_DIRECT_PROBE_TIMEOUT_SECONDS",
        "INVALIDITY_I2_LLM_TIMEOUT_SECONDS",
        "INVALIDITY_I2_LLM_DIRECT_ATTEMPT_TIMEOUT_SECONDS",
        "INVALIDITY_WORKER_CONCURRENCY",
    ):
        assert required_name in script


def test_test_environment_is_fail_closed(tmp_path: Path) -> None:
    env = valid_test_environment(tmp_path)
    del env["INVALIDITY_TEST_MODULE1_API_URL"]
    with pytest.raises(ConfigurationError, match="INVALIDITY_TEST_MODULE1_API_URL"):
        Settings.from_environment(env, load_dotenv_files=False)


def test_api_token_and_source_roots_are_required(tmp_path: Path) -> None:
    env = valid_test_environment(tmp_path)
    del env["INVALIDITY_TEST_API_TOKEN"]
    with pytest.raises(ConfigurationError, match="INVALIDITY_TEST_API_TOKEN"):
        Settings.from_environment(env, load_dotenv_files=False)

    env = valid_test_environment(tmp_path)
    del env["INVALIDITY_ALLOWED_SOURCE_ROOTS"]
    with pytest.raises(ConfigurationError, match="INVALIDITY_ALLOWED_SOURCE_ROOTS"):
        Settings.from_environment(env, load_dotenv_files=False)

    env = valid_test_environment(tmp_path)
    del env["INVALIDITY_TEST_PARSER_TOKEN"]
    with pytest.raises(ConfigurationError, match="INVALIDITY_TEST_PARSER_TOKEN"):
        Settings.from_environment(env, load_dotenv_files=False)


def test_environment_rejects_opposite_prefix_and_wrong_service_port(
    tmp_path: Path,
) -> None:
    env = valid_test_environment(tmp_path)
    env["INVALIDITY_PROD_API_TOKEN"] = "must-not-be-visible-to-test"
    with pytest.raises(ConfigurationError, match="INVALIDITY_PROD_"):
        Settings.from_environment(env, load_dotenv_files=False)

    env = valid_test_environment(tmp_path)
    env["INVALIDITY_PORT"] = "5109"
    with pytest.raises(ConfigurationError, match="5209"):
        Settings.from_environment(env, load_dotenv_files=False)


def test_epo_ops_requires_complete_current_environment_credentials(
    tmp_path: Path,
) -> None:
    env = valid_test_environment(tmp_path)
    env["INVALIDITY_TEST_PATENT_PROVIDER"] = "epo_ops"
    with pytest.raises(ConfigurationError, match="EPO_OPS_KEY"):
        Settings.from_environment(env, load_dotenv_files=False)

    env["INVALIDITY_TEST_EPO_OPS_KEY"] = "test-epo-consumer-key"
    with pytest.raises(ConfigurationError, match="必须同时配置"):
        Settings.from_environment(env, load_dotenv_files=False)

    env["INVALIDITY_TEST_EPO_OPS_SECRET"] = "test-epo-consumer-secret"
    configured = Settings.from_environment(env, load_dotenv_files=False)
    assert configured.patent_provider == "epo_ops"
    assert configured.epo_ops_consumer_key == "test-epo-consumer-key"
    assert configured.epo_ops_consumer_secret == "test-epo-consumer-secret"
    summary = configured.isolation_summary()
    assert "epo_ops_consumer_key" not in summary
    assert "epo_ops_consumer_secret" not in summary


def test_patsnap_requires_environment_key_and_exact_rest_contract(
    tmp_path: Path,
) -> None:
    env = valid_test_environment(tmp_path)
    env["INVALIDITY_TEST_PATENT_PROVIDER"] = "patsnap"
    with pytest.raises(ConfigurationError, match="PATSNAP_API_KEY"):
        Settings.from_environment(env, load_dotenv_files=False)

    env["INVALIDITY_TEST_PATSNAP_API_KEY"] = "not-a-current-rest-key"
    with pytest.raises(ConfigurationError, match="sk- 前缀"):
        Settings.from_environment(env, load_dotenv_files=False)

    env["INVALIDITY_TEST_PATSNAP_API_KEY"] = "sk-test-patsnap-key-000001"
    env["INVALIDITY_TEST_PATSNAP_BASE_URL"] = "https://connect.zhihuiya.com"
    env["INVALIDITY_TEST_PATSNAP_COUNT_PATH"] = (
        "/search/patent/query-search-count/v2"
    )
    env["INVALIDITY_TEST_PATSNAP_SEARCH_PATH"] = (
        "/search/patent/query-search-patent/v2"
    )
    with pytest.raises(ConfigurationError, match="EPO_OPS_KEY"):
        Settings.from_environment(env, load_dotenv_files=False)

    env["INVALIDITY_TEST_EPO_OPS_KEY"] = "test-epo-consumer-key"
    env["INVALIDITY_TEST_EPO_OPS_SECRET"] = "test-epo-consumer-secret"
    configured = Settings.from_environment(env, load_dotenv_files=False)
    assert configured.patent_provider == "patsnap"
    assert configured.patsnap_base_url == "https://connect.zhihuiya.com"
    assert configured.patsnap_count_path.endswith("/v2")
    assert configured.patsnap_search_path.endswith("/v2")
    assert "patsnap_api_key" not in configured.isolation_summary()

    env["INVALIDITY_TEST_PATSNAP_BASE_URL"] = "https://example.com"
    with pytest.raises(ConfigurationError, match="必须严格等于"):
        Settings.from_environment(env, load_dotenv_files=False)

    env["INVALIDITY_TEST_PATSNAP_BASE_URL"] = "https://connect.zhihuiya.com"
    env["INVALIDITY_TEST_PATSNAP_SEARCH_PATH"] = "/search/patent/unknown"
    with pytest.raises(ConfigurationError, match="路径白名单"):
        Settings.from_environment(env, load_dotenv_files=False)


def test_promoted_release_accepts_only_exact_prod_mapping(tmp_path: Path) -> None:
    env = {
        "INVALIDITY_ENV": "prod",
        "INVALIDITY_PORT": "5109",
        "INVALIDITY_DATABASE_URL": "postgresql://localhost/example",
        "INVALIDITY_DATABASE_SCHEMA": "invalidity_prod",
        "INVALIDITY_ARTIFACT_ROOT": str(tmp_path / "invalidity" / "prod"),
        "INVALIDITY_ALLOWED_SOURCE_ROOTS": str(tmp_path / ".data" / "uploads" / "prod"),
        "INVALIDITY_PROD_API_URL": "http://127.0.0.1:5109",
        "INVALIDITY_PROD_API_TOKEN": "prod-api-token-with-enough-entropy",
        "INVALIDITY_PROD_MODULE1_API_URL": "http://127.0.0.1:5101/run",
        "INVALIDITY_PROD_MODULE1_AUTH_MODE": "legacy_unauthenticated",
        "INVALIDITY_PROD_PATENT_PROVIDER": "google_patents",
        "INVALIDITY_PROD_NPL_PROVIDER": "arxiv",
        "INVALIDITY_PROD_LLM_BASE_URL": "https://example.invalid/v4",
        "INVALIDITY_PROD_LLM_API_KEY": "prod-key",
        "INVALIDITY_PROD_LLM_MODEL": "glm-4.6v",
    }
    configured = Settings.from_environment(env, load_dotenv_files=False)
    assert configured.environment == "prod"
    assert configured.service_port == 5109
    assert configured.database_schema == "invalidity_prod"
    assert configured.module1_api_url == "http://127.0.0.1:5101/run"
    assert configured.module1_auth_mode == "legacy_unauthenticated"
    assert configured.module1_api_token is None


def test_prod_module1_auth_compatibility_is_explicit(tmp_path: Path) -> None:
    env = valid_test_environment(tmp_path)
    env.update(
        {
            "INVALIDITY_ENV": "prod",
            "INVALIDITY_PORT": "5109",
            "INVALIDITY_DATABASE_SCHEMA": "invalidity_prod",
            "INVALIDITY_ARTIFACT_ROOT": str(tmp_path / "invalidity" / "prod"),
            "INVALIDITY_ALLOWED_SOURCE_ROOTS": str(
                tmp_path / ".data" / "uploads" / "prod"
            ),
            "INVALIDITY_PROD_API_URL": "http://127.0.0.1:5109",
            "INVALIDITY_PROD_API_TOKEN": "prod-api-token-with-enough-entropy",
            "INVALIDITY_PROD_MODULE1_API_URL": "http://127.0.0.1:5101/run",
            "INVALIDITY_PROD_PATENT_PROVIDER": "google_patents",
            "INVALIDITY_PROD_NPL_PROVIDER": "arxiv",
            "INVALIDITY_PROD_LLM_BASE_URL": "https://example.invalid/v4",
            "INVALIDITY_PROD_LLM_API_KEY": "prod-key",
            "INVALIDITY_PROD_LLM_MODEL": "glm-4.6v",
        }
    )
    for name in tuple(env):
        if name.startswith("INVALIDITY_TEST_"):
            del env[name]
    with pytest.raises(ConfigurationError, match="MODULE1_AUTH_MODE"):
        Settings.from_environment(env, load_dotenv_files=False)

    env["INVALIDITY_PROD_MODULE1_API_TOKEN"] = "prod-module1-token-with-enough-entropy"
    configured = Settings.from_environment(env, load_dotenv_files=False)
    assert configured.module1_auth_mode == "bearer"
    assert configured.module1_api_token == env["INVALIDITY_PROD_MODULE1_API_TOKEN"]


def test_dotenv_loading_requires_explicit_service_file() -> None:
    with pytest.raises(ConfigurationError, match="禁止自动扫描共享"):
        Settings.from_environment(load_dotenv_files=True)


def test_explicit_empty_environment_never_inherits_process_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("INVALIDITY_ENV", "test")
    monkeypatch.setenv("INVALIDITY_DATABASE_SCHEMA", "invalidity_test")
    with pytest.raises(ConfigurationError, match="INVALIDITY_ENV"):
        Settings.from_environment({}, load_dotenv_files=False)


def test_test_environment_rejects_prod_port(tmp_path: Path) -> None:
    env = valid_test_environment(tmp_path)
    env["INVALIDITY_TEST_MODULE1_API_URL"] = "http://127.0.0.1:5101/run"
    with pytest.raises(ConfigurationError, match="5201"):
        Settings.from_environment(env, load_dotenv_files=False)


def test_model_must_be_glm_46v(tmp_path: Path) -> None:
    env = valid_test_environment(tmp_path)
    env["INVALIDITY_TEST_LLM_MODEL"] = "glm-4-flash"
    with pytest.raises(ConfigurationError, match="glm-4.6v"):
        Settings.from_environment(env, load_dotenv_files=False)


def test_single_document_novelty_never_merges_documents() -> None:
    first = [{"feature_id": "f1", "status": "explicit", "confidence": 0.95}]
    second = [{"feature_id": "f2", "status": "explicit", "confidence": 0.95}]
    assert not aggregate_single_document_novelty(
        {"f1", "f2"}, first, eligible_for_novelty=True
    )
    assert not aggregate_single_document_novelty(
        {"f1", "f2"}, second, eligible_for_novelty=True
    )


def test_state_machine_rejects_terminal_transition() -> None:
    with pytest.raises(InvalidTransition):
        assert_transition(ClaimStatus.NOVELTY_EVIDENCE_COMPLETE, ClaimStatus.GAP_SEARCH)


@pytest.mark.parametrize(
    ("target",),
    [
        (ClaimStatus.GAP_SEARCH,),
        (ClaimStatus.EXHAUSTED,),
    ],
)
def test_single_reference_review_can_continue_or_stop_without_a_document(
    target: ClaimStatus,
) -> None:
    assert_transition(ClaimStatus.SINGLE_REFERENCE_REVIEW, target)


def test_uncertain_dependency_can_stop_before_date_analysis() -> None:
    assert_transition(ClaimStatus.QUEUED, ClaimStatus.NEEDS_HUMAN_REVIEW)


def test_date_evidence_review_can_pause_after_single_reference_stage() -> None:
    assert_transition(
        ClaimStatus.SINGLE_REFERENCE_REVIEW,
        ClaimStatus.NEEDS_HUMAN_REVIEW,
    )


def test_closest_prior_art_can_be_partial_after_provider_failure() -> None:
    assert_transition(ClaimStatus.CLOSEST_PRIOR_ART_SELECTED, ClaimStatus.PARTIAL)


def test_no_progress_consumes_a_gap_round_but_provider_failure_is_partial() -> None:
    stalled = RoundProgress(0, 2, 2, 1, 1, False, True)
    assert next_round_or_stop(
        current_round=1,
        max_rounds=3,
        progress=stalled,
        has_complete_inventive_combination=False,
    ) == ClaimStatus.GAP_SEARCH

    incomplete = RoundProgress(0, 2, 2, 1, 1, False, False)
    assert next_round_or_stop(
        current_round=1,
        max_rounds=3,
        progress=incomplete,
        has_complete_inventive_combination=False,
    ) == ClaimStatus.PARTIAL
