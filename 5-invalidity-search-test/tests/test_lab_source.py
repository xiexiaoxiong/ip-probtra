from __future__ import annotations

import hashlib
import uuid
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
import httpx
from fastapi.testclient import TestClient

import invalidity.lab as lab_module
import invalidity.main as main_module
import invalidity.source_snapshot as source_snapshot_module
import invalidity.workflow as workflow_module
from invalidity.config import Settings
from invalidity.contracts import ClaimSnapshot, CreateInvestigationRequest, PatentSnapshot
from invalidity.lab import LAB_MODULE_CODES, build_module_lab_handlers
from invalidity.main import create_app
from invalidity.report import public_json
from invalidity.source_snapshot import (
    SourceFreezer,
    SourceSnapshotError,
    ensure_patent_snapshot,
)
from invalidity.worker import JobContext

from test_api_worker import API_TOKEN, AUTH_HEADERS, PARSER_TOKEN, MemoryRepository


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        environment="test",
        service_port=5209,
        api_base_url="http://127.0.0.1:5209",
        api_token=API_TOKEN,
        database_url="postgresql://user:secret@localhost/invalidity",
        database_schema="invalidity_test",
        artifact_root=tmp_path / "invalidity" / "test",
        allowed_source_roots=(tmp_path.resolve(),),
        module1_api_url="http://127.0.0.1:5201/run",
        patent_provider="google_patents",
        npl_provider="arxiv",
        llm_base_url="https://example.invalid/v4",
        llm_api_key="test-secret",
        llm_model="glm-4.6v",
        module1_api_token=PARSER_TOKEN,
        module1_auth_mode="bearer",
        parser_api_token=PARSER_TOKEN,
        worker_poll_seconds=1,
        job_lease_seconds=60,
        parser_port=5201,
    )


def test_record_images_prefers_bounded_local_snapshots_over_protected_urls() -> None:
    local_pages = [f"/tmp/page-{index}.png" for index in range(1, 6)]
    images = lab_module._record_images(
        {
            "image_urls": [
                "https://ops.epo.org/protected/fullimage?Range=1",
                "https://ops.epo.org/protected/fullimage?Range=2",
                *local_pages,
            ],
            "artifacts": [
                {
                    "kind": "rendered_page",
                    "mime_type": "image/png",
                    "uri": local_pages[0],
                }
            ],
        }
    )
    assert images == local_pages[:4]


def test_record_images_keeps_remote_fallback_when_no_snapshot_exists() -> None:
    remote = [
        "https://public.example.invalid/page-1.png",
        "https://public.example.invalid/page-2.png",
    ]
    assert lab_module._record_images({"image_urls": remote}) == remote


def test_create_freezes_local_source_bytes_before_queue(tmp_path: Path) -> None:
    repository = MemoryRepository()
    source = tmp_path / "target.pdf"
    original = b"%PDF-1.4\nimmutable target bytes\n"
    source.write_bytes(original)
    application = create_app(
        settings=_settings(tmp_path),
        repository=repository,
        handler_registry={},
        start_worker=False,
    )

    body = {
        "analysis_session_id": "source-freeze-session",
        "source_path": str(source),
        "provider_mode": "live",
        "idempotency_key": "source-freeze-key",
    }
    with TestClient(application, headers=AUTH_HEADERS) as client:
        created = client.post("/v1/investigations", json=body)
        assert created.status_code == 201
        investigation_id = created.json()["investigation_id"]

        source.write_bytes(b"%PDF-1.4\nmutated after create\n")
        replay = client.post("/v1/investigations", json=body)
        assert replay.status_code == 200
        assert replay.json()["investigation_id"] == investigation_id

    row = repository.get_investigation(investigation_id)
    assert row is not None
    frozen = row["source_snapshot"]["frozen_source"]
    frozen_path = Path(frozen["uri"])
    assert frozen_path.read_bytes() == original
    assert frozen["sha256"] == hashlib.sha256(original).hexdigest()
    assert frozen["frozen_before_queue"] is True
    assert str(frozen_path).startswith(str(tmp_path / "invalidity" / "test"))


def test_missing_local_source_fails_without_creating_investigation(
    tmp_path: Path,
) -> None:
    repository = MemoryRepository()
    application = create_app(
        settings=_settings(tmp_path),
        repository=repository,
        handler_registry={},
        start_worker=False,
    )
    with TestClient(application, headers=AUTH_HEADERS) as client:
        response = client.post(
            "/v1/investigations",
            json={
                "analysis_session_id": "missing-source-session",
                "source_path": str(tmp_path / "missing.pdf"),
                "provider_mode": "live",
                "idempotency_key": "missing-source-key",
            },
        )
    assert response.status_code == 422
    assert response.json()["code"] == "SOURCE_FILE_NOT_FOUND"
    assert repository.investigations == {}


def test_local_source_must_stay_inside_allowed_root_after_symlink_resolution(
    tmp_path: Path,
) -> None:
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    outside = tmp_path / "outside.pdf"
    outside.write_bytes(b"%PDF-1.4\noutside\n")
    link = uploads / "escape.pdf"
    link.symlink_to(outside)
    configured = replace(
        _settings(tmp_path),
        allowed_source_roots=(uploads.resolve(),),
    )
    request = CreateInvestigationRequest(
        analysis_session_id="source-path-escape",
        source_path=str(link),
        provider_mode="live",
        idempotency_key="source-path-escape-key",
    )
    with pytest.raises(SourceSnapshotError) as raised:
        SourceFreezer(configured).freeze(
            request=request,
            investigation_id=uuid.uuid4(),
            source_snapshot={},
        )
    assert raised.value.code == "SOURCE_PATH_NOT_ALLOWED"


def test_remote_source_rejects_private_redirect_before_second_request(
    tmp_path: Path,
) -> None:
    calls: list[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(
            302,
            headers={"location": "http://127.0.0.1/private.pdf"},
            request=request,
        )

    request = CreateInvestigationRequest(
        analysis_session_id="private-redirect",
        source_url="https://public.example/source.pdf",
        provider_mode="live",
        idempotency_key="private-redirect-key",
    )
    with pytest.raises(SourceSnapshotError) as raised:
        SourceFreezer(
            _settings(tmp_path),
            transport=httpx.MockTransport(respond),
            resolver=lambda _host: ["93.184.216.34"],
        ).freeze(
            request=request,
            investigation_id=uuid.uuid4(),
            source_snapshot={},
        )
    assert raised.value.code == "SOURCE_URL_BLOCKED"
    assert len(calls) == 1


def test_remote_source_enforces_byte_and_mime_limits(tmp_path: Path) -> None:
    def too_large(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "application/pdf"},
            content=b"%PDF-1.4\n" + b"x" * 32,
            request=request,
        )

    configured = replace(_settings(tmp_path), source_max_bytes=16)
    request = CreateInvestigationRequest(
        analysis_session_id="source-size-limit",
        source_url="https://public.example/source.pdf",
        provider_mode="live",
        idempotency_key="source-size-limit-key",
    )
    with pytest.raises(SourceSnapshotError) as raised:
        SourceFreezer(
            configured,
            transport=httpx.MockTransport(too_large),
            resolver=lambda _host: ["93.184.216.34"],
        ).freeze(
            request=request,
            investigation_id=uuid.uuid4(),
            source_snapshot={},
        )
    assert raised.value.code == "SOURCE_TOO_LARGE"

    def bad_mime(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "application/x-executable"},
            content=b"not-a-patent-document",
            request=request,
        )

    with pytest.raises(SourceSnapshotError) as raised:
        SourceFreezer(
            _settings(tmp_path),
            transport=httpx.MockTransport(bad_mime),
            resolver=lambda _host: ["93.184.216.34"],
        ).freeze(
            request=request,
            investigation_id=uuid.uuid4(),
            source_snapshot={},
        )
    assert raised.value.code == "SOURCE_MIME_NOT_ALLOWED"


def test_source_url_is_frozen_and_public_view_redacts_signed_query(
    tmp_path: Path,
) -> None:
    content = b"%PDF-1.4\nremote immutable bytes\n"

    def respond(request: httpx.Request) -> httpx.Response:
        assert request.url.params["token"] == "source-secret"
        return httpx.Response(
            200,
            headers={"content-type": "application/pdf"},
            content=content,
            request=request,
        )

    request = CreateInvestigationRequest(
        analysis_session_id="remote-source-session",
        source_url="https://example.invalid/target.pdf?token=source-secret&version=1",
        provider_mode="live",
        idempotency_key="remote-source-key",
    )
    snapshot = SourceFreezer(
        _settings(tmp_path),
        transport=httpx.MockTransport(respond),
        resolver=lambda _host: ["93.184.216.34"],
    ).freeze(
        request=request,
        investigation_id=uuid.uuid4(),
        source_snapshot={"request": request.model_dump(mode="json")},
    )
    frozen = snapshot["frozen_source"]
    assert Path(frozen["uri"]).read_bytes() == content
    assert frozen["sha256"] == hashlib.sha256(content).hexdigest()
    public = public_json(snapshot)
    assert "source-secret" not in str(public)
    assert "%5BREDACTED%5D" in str(public)


def test_create_copies_patent_record_and_images_at_creation(
    tmp_path: Path, monkeypatch: Any
) -> None:
    source_image = tmp_path / "mutable-module1-image.png"
    original_image = b"\x89PNG\r\n\x1a\nimmutable-image"
    source_image.write_bytes(original_image)
    snapshots = 0

    class FakeModule1Adapter:
        def __init__(self, **_kwargs: Any) -> None:
            pass

        def snapshot(self, **_kwargs: Any) -> PatentSnapshot:
            nonlocal snapshots
            snapshots += 1
            return PatentSnapshot(
                source_sha256="a" * 64,
                source_uri="legacy-record:77",
                patent_record_id=77,
                patent_number="CN123A",
                claims=[
                    ClaimSnapshot(
                        claim_id="1",
                        claim_type="INDEPENDENT",
                        claim_text="一种光学装置",
                    )
                ],
                figures=[{"file_path": str(source_image)}],
            )

        def ensure_target_images(self, *_args: Any, **_kwargs: Any) -> list[str]:
            return [str(source_image)]

    monkeypatch.setattr(
        source_snapshot_module, "Module1Adapter", FakeModule1Adapter
    )
    repository = MemoryRepository()
    application = create_app(
        settings=_settings(tmp_path),
        repository=repository,
        handler_registry={},
        start_worker=False,
    )
    body = {
        "analysis_session_id": "record-freeze-session",
        "patent_record_id": 77,
        "provider_mode": "live",
        "idempotency_key": "record-freeze-key",
    }
    with TestClient(application, headers=AUTH_HEADERS) as client:
        created = client.post("/v1/investigations", json=body)
        assert created.status_code == 201
        replay = client.post("/v1/investigations", json=body)
        assert replay.status_code == 200
    assert snapshots == 1

    row = repository.get_investigation(created.json()["investigation_id"])
    assert row is not None
    snapshot = row["source_snapshot"]
    assert snapshot["snapshot_state"] == "patent_snapshot_frozen"
    assert snapshot["patent_snapshot"]["patent_record_id"] == 77
    assert snapshot["frozen_source"]["record_snapshot_frozen"] is True
    assert len(snapshot["patent_figure_artifacts"]) == 1
    frozen_patent_figure = Path(
        snapshot["patent_snapshot"]["figures"][0]["file_path"]
    )
    frozen_image = Path(snapshot["target_images"][0])
    source_image.write_bytes(b"mutated")
    assert frozen_patent_figure.read_bytes() == original_image
    assert frozen_image.read_bytes() == original_image


def test_worker_rejects_tampered_frozen_source_before_module1(
    tmp_path: Path, monkeypatch: Any
) -> None:
    repository = MemoryRepository()
    source = tmp_path / "target.pdf"
    source.write_bytes(b"%PDF-1.4\noriginal\n")
    application = create_app(
        settings=_settings(tmp_path),
        repository=repository,
        handler_registry={},
        start_worker=False,
    )
    with TestClient(application, headers=AUTH_HEADERS) as client:
        created = client.post(
            "/v1/investigations",
            json={
                "analysis_session_id": "tampered-source-session",
                "source_path": str(source),
                "provider_mode": "live",
                "idempotency_key": "tampered-source-key",
            },
        )
    row = repository.get_investigation(created.json()["investigation_id"])
    assert row is not None
    Path(row["source_snapshot"]["frozen_source"]["uri"]).write_bytes(b"tampered")

    class MustNotRun:
        def __init__(self, **_kwargs: Any) -> None:
            raise AssertionError("Module1 must not run after integrity failure")

    monkeypatch.setattr(source_snapshot_module, "Module1Adapter", MustNotRun)
    with pytest.raises(SourceSnapshotError, match="SHA-256"):
        ensure_patent_snapshot(
            settings=_settings(tmp_path),
            repository=repository,
            investigation_id=created.json()["investigation_id"],
        )


def test_worker_cross_checks_module1_source_hash(
    tmp_path: Path, monkeypatch: Any
) -> None:
    repository = MemoryRepository()
    source = tmp_path / "target.pdf"
    source.write_bytes(b"%PDF-1.4\nsource hash input\n")
    application = create_app(
        settings=_settings(tmp_path),
        repository=repository,
        handler_registry={},
        start_worker=False,
    )
    with TestClient(application, headers=AUTH_HEADERS) as client:
        created = client.post(
            "/v1/investigations",
            json={
                "analysis_session_id": "module1-hash-session",
                "source_path": str(source),
                "provider_mode": "live",
                "idempotency_key": "module1-hash-key",
            },
        )

    adapter_tokens: list[str | None] = []

    class WrongHashModule1:
        def __init__(self, **kwargs: Any) -> None:
            adapter_tokens.append(kwargs.get("api_token"))

        def snapshot(self, **_kwargs: Any) -> PatentSnapshot:
            return PatentSnapshot(
                source_sha256="f" * 64,
                source_uri=str(source),
                claims=[
                    ClaimSnapshot(
                        claim_id="1",
                        claim_type="INDEPENDENT",
                        claim_text="一种装置",
                    )
                ],
            )

    monkeypatch.setattr(
        source_snapshot_module, "Module1Adapter", WrongHashModule1
    )
    with pytest.raises(SourceSnapshotError, match="哈希"):
        ensure_patent_snapshot(
            settings=_settings(tmp_path),
            repository=repository,
            investigation_id=created.json()["investigation_id"],
        )
    assert adapter_tokens == [PARSER_TOKEN]
    assert adapter_tokens != [API_TOKEN]


def test_full_investigation_rejects_non_live_provider_modes(tmp_path: Path) -> None:
    repository = MemoryRepository()
    application = create_app(
        settings=_settings(tmp_path),
        repository=repository,
        handler_registry={},
        start_worker=False,
    )
    with TestClient(application, headers=AUTH_HEADERS) as client:
        response = client.post(
            "/v1/investigations",
            json={
                "analysis_session_id": "fixture-i0-rejected",
                "source_path": str(tmp_path / "not-read.txt"),
                "provider_mode": "fixture",
                "idempotency_key": "fixture-i0-key",
            },
        )
    assert response.status_code == 422
    assert "只支持 live" in response.json()["detail"]
    assert repository.investigations == {}

    legacy = repository.create_investigation(
        analysis_session_id="legacy-fixture-i0",
        source_snapshot={"request": {"provider_mode": "fixture"}},
        pipeline_version="legacy",
    )
    with TestClient(application, headers=AUTH_HEADERS) as client:
        start = client.post(f"/v1/investigations/{legacy['id']}/start")
    assert start.status_code == 422
    assert repository.jobs == {}


def test_every_lab_module_has_deterministic_offline_fixture_handler(
    tmp_path: Path, monkeypatch: Any
) -> None:
    class NoNetworkProvider:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            raise AssertionError("fixture handler attempted network provider construction")

    monkeypatch.setattr(lab_module, "GooglePatentsProvider", NoNetworkProvider)
    monkeypatch.setattr(lab_module, "ArxivProvider", NoNetworkProvider)
    monkeypatch.setattr(lab_module, "VisionLLMClient", NoNetworkProvider)

    settings = _settings(tmp_path)
    repository = MemoryRepository()
    investigation = repository.create_investigation(
        analysis_session_id="lab-handler-session",
        source_snapshot={"fixture": True},
        pipeline_version="test",
    )
    handlers = build_module_lab_handlers(settings=settings, repository=repository)
    assert set(handlers) == set(LAB_MODULE_CODES)

    heartbeat_count = 0

    def heartbeat() -> dict[str, bool]:
        nonlocal heartbeat_count
        heartbeat_count += 1
        return {"ok": True}

    outputs: dict[str, dict[str, Any]] = {}
    for code in LAB_MODULE_CODES:
        context = JobContext(
            job={
                "id": uuid.uuid4(),
                "payload": {"investigation_id": str(investigation["id"])},
            },
            module_run={
                "id": uuid.uuid4(),
                "investigation_id": investigation["id"],
                "module_code": code,
                "input_snapshot": {
                    "request": {
                        "input_mode": "fixture",
                        "input": {"fixture": "default"},
                    }
                },
            },
            repository=repository,
            settings=settings,
            worker_id="fixture-worker",
            _heartbeat=heartbeat,
        )
        output = handlers[code](context)
        assert isinstance(output, dict)
        assert output["status"] == "completed"
        assert output["module_code"] == code
        assert output["input_mode"] == "fixture"
        outputs[code] = output

    assert heartbeat_count == len(LAB_MODULE_CODES) * 2
    assert outputs["I2_QUERY_PLAN"]["output"]["model"] == "fixture-no-model"
    assert outputs["I3_QUALIFY"]["output"]["stage"] == "qualified_evidence"
    assert outputs["I5_REPORT"]["output"]["report_kind"] == "invalidity_evidence_data"


def test_default_registry_prepares_snapshot_and_registers_all_lab_modules(
    tmp_path: Path, monkeypatch: Any
) -> None:
    calls: list[str] = []

    class FakeWorkflow:
        def execute_job(self, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
            calls.append("workflow")
            return {"status": "completed", "ok": True}

    monkeypatch.setattr(
        workflow_module,
        "build_workflow",
        lambda **_kwargs: FakeWorkflow(),
    )
    monkeypatch.setattr(
        main_module,
        "ensure_patent_snapshot",
        lambda **_kwargs: calls.append("snapshot") or {},
    )
    settings = _settings(tmp_path)
    repository = MemoryRepository()
    investigation = repository.create_investigation(
        analysis_session_id="default-registry-session",
        source_snapshot={"request": {"provider_mode": "live"}},
        pipeline_version="test",
    )
    application = create_app(
        settings=settings,
        repository=repository,
        start_worker=False,
    )
    with TestClient(application, headers=AUTH_HEADERS) as client:
        health = client.get("/health")
        assert health.status_code == 200
        assert "registered_module_handlers" not in health.json()
        registered = set(application.state.invalidity_runtime.worker.handlers)
        assert set(LAB_MODULE_CODES).issubset(registered)
        assert {"I0_ORCHESTRATE", "I0_INVALIDITY_WORKFLOW"}.issubset(registered)
        handler = application.state.invalidity_runtime.worker.handlers["I0_ORCHESTRATE"]
        context = JobContext(
            job={"payload": {"investigation_id": str(investigation["id"])}},
            module_run={"module_code": "I0_ORCHESTRATE"},
            repository=repository,
            settings=settings,
            worker_id="registry-test",
            _heartbeat=lambda: {},
        )
        assert handler(context)["status"] == "completed"
    assert calls == ["snapshot", "workflow"]
