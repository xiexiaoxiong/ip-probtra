from __future__ import annotations

import importlib.util
from pathlib import Path

import httpx
import pytest

from invalidity.contracts import I4S_RULE_VERSION


ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "scripts/run_decision_i4s_blind.py"


def _load_runner():
    spec = importlib.util.spec_from_file_location("blind_i4s_runner", RUNNER)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_wait_recovers_from_a_brief_local_service_disconnect(monkeypatch) -> None:
    runner = _load_runner()
    calls = 0

    def fake_request(client, method, url, payload=None):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise httpx.ConnectError("temporary restart")
        return {
            "module_run": {
                "status": "succeeded",
                "output_snapshot": {"output": {"disclosures": [{"status": "uncertain"}]}},
            }
        }

    monkeypatch.setattr(runner, "_request", fake_request)
    output = runner._wait(
        object(),
        "http://127.0.0.1:5209",
        "run-1",
        timeout_seconds=0.1,
        poll_interval=0,
        transient_error_grace_seconds=0.05,
    )
    assert output["disclosures"][0]["status"] == "uncertain"
    assert calls == 2


def test_wait_default_execution_budget_matches_bounded_llm_budget() -> None:
    runner = _load_runner()
    assert runner._wait.__kwdefaults__["timeout_seconds"] == 420


def test_wait_stops_after_bounded_continuous_disconnect(monkeypatch) -> None:
    runner = _load_runner()

    def fake_request(client, method, url, payload=None):
        raise httpx.ConnectError("service unavailable")

    monkeypatch.setattr(runner, "_request", fake_request)
    with pytest.raises(RuntimeError, match="连续断开"):
        runner._wait(
            object(),
            "http://127.0.0.1:5209",
            "run-2",
            timeout_seconds=0.1,
            poll_interval=0.001,
            transient_error_grace_seconds=0.003,
        )


def test_wait_does_not_charge_queued_time_to_execution_timeout(monkeypatch) -> None:
    runner = _load_runner()
    calls = 0

    def fake_request(client, method, url, payload=None):
        nonlocal calls
        calls += 1
        if calls < 5:
            return {
                "module_run": {"status": "running"},
                "job": {"status": "queued"},
            }
        return {
            "module_run": {
                "status": "succeeded",
                "output_snapshot": {
                    "output": {"disclosures": [{"status": "uncertain"}]}
                },
            },
            "job": {"status": "succeeded"},
        }

    monkeypatch.setattr(runner, "_request", fake_request)
    output = runner._wait(
        object(),
        "http://127.0.0.1:5209",
        "run-queued",
        timeout_seconds=0.001,
        queue_timeout_seconds=0.1,
        poll_interval=0.002,
    )
    assert output["disclosures"][0]["status"] == "uncertain"
    assert calls == 5


def test_usable_comparison_rejects_failed_structural_review() -> None:
    runner = _load_runner()
    assert runner._usable_comparison(
        {
            "analysis_rule_version": I4S_RULE_VERSION,
            "structural_review_status": "model_error",
            "disclosures": [{"status": "explicit"}],
        }
    ) is False


def test_unusable_reason_exposes_rule_version_and_fails_fast() -> None:
    runner = _load_runner()
    value = {
        "analysis_rule_version": "structural-disclosure-old",
        "structural_review_status": "completed",
        "disclosures": [{"feature_id": "f-1", "status": "explicit"}],
    }

    assert runner._comparison_unusable_reason(value) == (
        f"rule_version_mismatch expected={I4S_RULE_VERSION} "
        "actual=structural-disclosure-old"
    )
    with pytest.raises(runner.RuleVersionMismatchError, match="rule_version_mismatch"):
        runner._raise_if_unusable(value, "run-version-drift")
    assert runner._usable_comparison(
        {
            "analysis_rule_version": I4S_RULE_VERSION,
            "structural_review_status": "not_needed",
            "disclosures": [{"status": "uncertain"}],
        }
    ) is True
    assert runner._usable_comparison(
        {
            "analysis_rule_version": I4S_RULE_VERSION,
            "structural_review_status": "not_needed",
            "disclosures": [{"status": "analysis_failed"}],
        }
    ) is False
    assert runner._usable_comparison(
        {
            "analysis_rule_version": "structural-disclosure-old",
            "structural_review_status": "not_needed",
            "disclosures": [{"status": "explicit"}],
        }
    ) is False


def test_document_selector_accepts_publication_number_or_pdf_name() -> None:
    runner = _load_runner()
    assert runner._normalise_document_selector("US 2005/0173559 A1.pdf").removesuffix(
        "PDF"
    ) == "US20050173559A1"
    assert runner._normalise_document_selector("CN203880100U") == "CN203880100U"


def test_pending_run_recovery_prefers_newest_unique_matching_analysis_input() -> None:
    runner = _load_runner()
    rows = [
        {
            "module_run_id": "old-run",
            "analysis_input_sha256": "input-a",
            "comparison": None,
            "attempt_error": "poll disconnected",
        },
        {
            "module_run_id": "other-document",
            "analysis_input_sha256": "input-b",
            "comparison": None,
        },
        {
            "module_run_id": "new-run",
            "analysis_input_sha256": "input-a",
            "comparison": None,
            "attempt_error": "service restart",
        },
        {
            "module_run_id": "old-run",
            "analysis_input_sha256": "input-a",
            "comparison": None,
        },
    ]

    assert runner._pending_run_ids(rows, "input-a") == ["old-run", "new-run"]
