import json
import os
import subprocess
from pathlib import Path

import httpx
import pytest

import invalidity.llm as llm_module
from invalidity.llm import MultimodalModelError, VisionLLMClient


def test_llm_rejects_text_only_model() -> None:
    with pytest.raises(MultimodalModelError, match="glm-4.6v"):
        VisionLLMClient(base_url="https://example.test/v4", api_key="x", model="glm-4-flash")


def test_llm_rejects_non_positive_total_deadline() -> None:
    with pytest.raises(MultimodalModelError, match="总截止时间"):
        VisionLLMClient(
            base_url="https://example.test/v4",
            api_key="x",
            model="glm-4.6v",
            timeout_seconds=0,
        )


def test_comparison_requires_both_target_and_document_images(tmp_path: Path) -> None:
    target = tmp_path / "target.png"
    target.write_bytes(b"fake")
    client = VisionLLMClient(
        base_url="https://example.test/v4", api_key="x", model="glm-4.6v"
    )
    with pytest.raises(MultimodalModelError, match="对比文件图像"):
        client.invoke_json(
            prompt="compare",
            target_images=[target],
            require_document_image=True,
        )


def test_payload_keeps_text_and_both_image_groups(tmp_path: Path) -> None:
    target = tmp_path / "target.png"
    document = tmp_path / "document.png"
    target.write_bytes(b"target")
    document.write_bytes(b"document")
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "model": "glm-4.6v",
                "choices": [{"message": {"content": '{"ok":true}'}}],
            },
        )

    client = VisionLLMClient(
        base_url="https://example.test/v4",
        api_key="x",
        model="glm-4.6v",
        transport=httpx.MockTransport(handler),
    )
    result = client.invoke_json(
        prompt="compare",
        target_images=[target],
        document_images=[document],
        require_document_image=True,
    )
    content = captured["messages"][1]["content"]
    assert sum(item["type"] == "image_url" for item in content) == 2
    assert captured["thinking"] == {"type": "disabled"}
    assert result.target_image_count == 1
    assert result.document_image_count == 1


def test_empty_completion_is_retryable_and_keeps_finish_reason(tmp_path: Path) -> None:
    target = tmp_path / "target.png"
    target.write_bytes(b"target")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "model": "glm-4.6v",
                "choices": [
                    {
                        "finish_reason": "length",
                        "message": {"content": ""},
                    }
                ],
            },
        )

    client = VisionLLMClient(
        base_url="https://example.test/v4",
        api_key="x",
        model="glm-4.6v",
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(MultimodalModelError) as captured:
        client.invoke_json(prompt="compare", target_images=[target])

    assert captured.value.retryable is True
    assert captured.value.reason_code == "glm_empty_content"
    assert "finish_reason=length" in str(captured.value)


def test_invalid_json_completion_keeps_auditable_response_metadata(
    tmp_path: Path,
) -> None:
    target = tmp_path / "target.png"
    target.write_bytes(b"target")
    raw_content = '{"features":[{"temp_id":"f1"}'

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "model": "glm-4.6v",
                "choices": [
                    {
                        "finish_reason": "length",
                        "message": {"content": raw_content},
                    }
                ],
            },
        )

    client = VisionLLMClient(
        base_url="https://example.test/v4",
        api_key="x",
        model="glm-4.6v",
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(MultimodalModelError) as captured:
        client.invoke_json(prompt="profile", target_images=[target])

    error = captured.value
    assert error.reason_code == "glm_output_truncated"
    assert error.finish_reason == "length"
    assert error.raw_content == raw_content
    assert error.model == "glm-4.6v"
    assert f"response_chars={len(raw_content)}" in str(error)


def test_direct_curl_keeps_bearer_and_payload_out_of_process_argv(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "glm-super-secret"
    prompt = "confidential patent prompt with full claim text"
    captured: dict = {}

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if "--head" in command:
            captured.setdefault("probes", []).append(command)
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        pass_fds = kwargs["pass_fds"]
        assert isinstance(pass_fds, tuple)
        captured["headers"] = os.read(pass_fds[0], 16384).decode("utf-8")
        captured["command"] = command
        captured["input"] = kwargs["input"]
        captured["env"] = kwargs["env"]
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps(
                {
                    "model": "glm-4.6v",
                    "choices": [{"message": {"content": '{"ok":true}'}}],
                }
            ),
            stderr="",
        )

    monkeypatch.setenv("LOCAL_LLM_BIGMODEL_DIRECT_IPS", "203.0.113.10")
    monkeypatch.setattr(llm_module.subprocess, "run", fake_run)
    client = VisionLLMClient(
        base_url="https://open.bigmodel.cn/api/paas/v4",
        api_key=secret,
        model="glm-4.6v",
        timeout_seconds=10,
    )

    response = client._post_via_curl({"messages": [{"content": prompt}]})

    argv = "\x00".join(captured["command"])
    assert captured["command"][:2] == ["/usr/bin/curl", "--disable"]
    assert len(captured["probes"]) == 1
    assert secret not in "\x00".join(captured["probes"][0])
    assert prompt not in "\x00".join(captured["probes"][0])
    assert secret not in argv
    assert prompt not in argv
    assert "Authorization: Bearer" not in argv
    assert "--data-binary\x00@-" in argv
    assert captured["headers"] == (
        f"Authorization: Bearer {secret}\nContent-Type: application/json\n"
    )
    assert prompt in captured["input"]
    assert secret not in captured["env"].values()
    assert response["choices"][0]["message"]["content"] == '{"ok":true}'


def test_known_tun_route_uses_working_hostname_before_direct_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict = {"direct_calls": 0}
    client = VisionLLMClient(
        base_url="https://open.bigmodel.cn/api/paas/v4",
        api_key="secret",
        model="glm-4.6v",
        timeout_seconds=10,
    )

    class FakeClient:
        def __init__(self, **kwargs: object) -> None:
            captured["client_kwargs"] = kwargs

        def __enter__(self) -> "FakeClient":
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def post(self, url: str, **kwargs: object) -> httpx.Response:
            captured["url"] = url
            captured["post_kwargs"] = kwargs
            return httpx.Response(
                200,
                request=httpx.Request("POST", url),
                json={"choices": []},
            )

    def fake_direct(*_args: object, **_kwargs: object) -> dict:
        captured["direct_calls"] += 1
        return {"should_not": "run"}

    monkeypatch.setattr(client, "_needs_direct_bigmodel_route", lambda: True)
    monkeypatch.setattr(client, "_post_via_curl", fake_direct)
    monkeypatch.setattr(llm_module.httpx, "Client", FakeClient)

    result = client._post({"request": "body"})

    assert result == {"choices": []}
    assert captured["direct_calls"] == 0
    assert captured["post_kwargs"]["json"] == {"request": "body"}
    timeout = captured["client_kwargs"]["timeout"]
    assert 9.5 < timeout.read <= 10


def test_known_tun_route_caps_hostname_wait_before_direct_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict = {"direct_calls": 0}
    client = VisionLLMClient(
        base_url="https://open.bigmodel.cn/api/paas/v4",
        api_key="secret",
        model="glm-4.6v",
        timeout_seconds=180,
    )

    class FakeClient:
        def __init__(self, **kwargs: object) -> None:
            captured["timeout"] = kwargs["timeout"]

        def __enter__(self) -> "FakeClient":
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def post(self, url: str, **_kwargs: object) -> httpx.Response:
            return httpx.Response(
                200,
                request=httpx.Request("POST", url),
                json={"choices": []},
            )

    monkeypatch.setattr(client, "_needs_direct_bigmodel_route", lambda: True)
    monkeypatch.setattr(llm_module.httpx, "Client", FakeClient)

    client._post({"request": "body"})

    assert 11.5 < captured["timeout"].connect <= 12
    assert 179.5 < captured["timeout"].read <= 180


def test_known_tun_route_falls_back_to_direct_only_after_transport_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict = {}
    client = VisionLLMClient(
        base_url="https://open.bigmodel.cn/api/paas/v4",
        api_key="secret",
        model="glm-4.6v",
        timeout_seconds=10,
    )

    class FailingClient:
        def __init__(self, **_kwargs: object) -> None:
            pass

        def __enter__(self) -> "FailingClient":
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def post(self, url: str, **_kwargs: object) -> httpx.Response:
            raise httpx.ConnectError(
                "unreachable hostname route",
                request=httpx.Request("POST", url),
            )

    def fake_direct(payload: dict, *, deadline: float) -> dict:
        captured["payload"] = payload
        captured["deadline"] = deadline
        return {"choices": []}

    monkeypatch.setattr(client, "_needs_direct_bigmodel_route", lambda: True)
    monkeypatch.setattr(client, "_post_via_curl", fake_direct)
    monkeypatch.setattr(llm_module.httpx, "Client", FailingClient)

    result = client._post({"request": "body"})

    assert result == {"choices": []}
    assert captured["payload"] == {"request": "body"}
    assert captured["deadline"] > llm_module.time.monotonic()


def test_direct_curl_retries_share_one_total_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = [100.0]
    attempt_timeouts: list[float] = []

    def fake_monotonic() -> float:
        return clock[0]

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if "--head" in command:
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        pass_fds = kwargs["pass_fds"]
        assert isinstance(pass_fds, tuple)
        os.read(pass_fds[0], 16384)
        attempt_timeouts.append(float(kwargs["timeout"]))
        clock[0] += 2.0
        return subprocess.CompletedProcess(command, 7, stdout="", stderr="network error")

    monkeypatch.setattr(llm_module.time, "monotonic", fake_monotonic)
    monkeypatch.setattr(llm_module.subprocess, "run", fake_run)
    monkeypatch.setenv(
        "LOCAL_LLM_BIGMODEL_DIRECT_IPS",
        "203.0.113.10,203.0.113.11,203.0.113.12",
    )
    client = VisionLLMClient(
        base_url="https://open.bigmodel.cn/api/paas/v4",
        api_key="safe-secret",
        model="glm-4.6v",
        timeout_seconds=6,
    )

    with pytest.raises(MultimodalModelError) as exc_info:
        client._post_via_curl({"messages": [{"content": "claim"}]})

    assert attempt_timeouts == pytest.approx([5.95, 3.95, 1.95])
    assert clock[0] == 106.0
    message = str(exc_info.value)
    assert "203.0.113.10:request_exit=7" in message
    assert "203.0.113.11:request_exit=7" in message
    assert "203.0.113.12:request_exit=7" in message
    assert "network error" not in message
    assert "safe-secret" not in message


def test_direct_curl_skips_failed_probe_before_sending_patent_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    probe_ips: list[str] = []
    post_ips: list[str] = []

    def routed_ip(command: list[str]) -> str:
        mapping = command[command.index("--connect-to") + 1]
        return mapping.split(":")[2]

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        ip = routed_ip(command)
        if "--head" in command:
            probe_ips.append(ip)
            return subprocess.CompletedProcess(
                command,
                28 if ip == "203.0.113.10" else 0,
                stdout="",
                stderr="",
            )
        post_ips.append(ip)
        pass_fds = kwargs["pass_fds"]
        assert isinstance(pass_fds, tuple)
        os.read(pass_fds[0], 16384)
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps(
                {
                    "model": "glm-4.6v",
                    "choices": [{"message": {"content": '{"ok":true}'}}],
                }
            ),
            stderr="",
        )

    monkeypatch.setattr(llm_module.subprocess, "run", fake_run)
    monkeypatch.setenv(
        "LOCAL_LLM_BIGMODEL_DIRECT_IPS",
        "203.0.113.10,203.0.113.11",
    )
    client = VisionLLMClient(
        base_url="https://open.bigmodel.cn/api/paas/v4",
        api_key="safe-secret",
        model="glm-4.6v",
        timeout_seconds=30,
        direct_probe_timeout_seconds=2,
    )

    response = client._post_via_curl(
        {"messages": [{"content": "confidential patent body"}]}
    )

    assert probe_ips == ["203.0.113.10", "203.0.113.11"]
    assert post_ips == ["203.0.113.11"]
    assert response["choices"][0]["message"]["content"] == '{"ok":true}'


def test_direct_curl_caps_one_healthy_route_without_consuming_total_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    post_timeouts: list[float] = []

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if "--head" in command:
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        pass_fds = kwargs["pass_fds"]
        assert isinstance(pass_fds, tuple)
        os.read(pass_fds[0], 16384)
        post_timeouts.append(float(kwargs["timeout"]))
        return subprocess.CompletedProcess(command, 28, stdout="", stderr="")

    monkeypatch.setattr(llm_module.subprocess, "run", fake_run)
    monkeypatch.setenv("LOCAL_LLM_BIGMODEL_DIRECT_IPS", "203.0.113.10")
    client = VisionLLMClient(
        base_url="https://open.bigmodel.cn/api/paas/v4",
        api_key="safe-secret",
        model="glm-4.6v",
        timeout_seconds=600,
        direct_attempt_timeout_seconds=180,
    )

    with pytest.raises(MultimodalModelError, match="request_exit=28"):
        client._post_via_curl({"messages": [{"content": "claim"}]})

    assert post_timeouts == pytest.approx([180.0])


def test_direct_curl_honours_shorter_per_invocation_attempt_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    post_timeouts: list[float] = []

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if "--head" in command:
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        pass_fds = kwargs["pass_fds"]
        assert isinstance(pass_fds, tuple)
        os.read(pass_fds[0], 16384)
        post_timeouts.append(float(kwargs["timeout"]))
        return subprocess.CompletedProcess(command, 28, stdout="", stderr="")

    monkeypatch.setattr(llm_module.subprocess, "run", fake_run)
    monkeypatch.setenv("LOCAL_LLM_BIGMODEL_DIRECT_IPS", "203.0.113.10")
    client = VisionLLMClient(
        base_url="https://open.bigmodel.cn/api/paas/v4",
        api_key="safe-secret",
        model="glm-4.6v",
        timeout_seconds=600,
        direct_attempt_timeout_seconds=180,
    )

    with pytest.raises(MultimodalModelError, match="request_exit=28"):
        client._post_via_curl(
            {"messages": [{"content": "claim"}]},
            direct_attempt_timeout_seconds=55,
        )

    assert post_timeouts == pytest.approx([55.0])


@pytest.mark.parametrize(
    ("status", "expected_retryable"),
    [(400, False), (413, False), (429, True), (503, True)],
)
def test_direct_curl_classifies_http_failures_without_exposing_body(
    monkeypatch: pytest.MonkeyPatch,
    status: int,
    expected_retryable: bool,
) -> None:
    body = {
        "error": {
            "code": "safe_provider_code",
            "message": "sensitive provider detail must stay private",
        }
    }

    def fake_run(
        command: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        if "--head" in command:
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        pass_fds = kwargs["pass_fds"]
        assert isinstance(pass_fds, tuple)
        os.read(pass_fds[0], 16384)
        stdout = (
            json.dumps(body)
            + f"\n{llm_module._CURL_HTTP_STATUS_MARKER}{status}"
        )
        return subprocess.CompletedProcess(command, 22, stdout=stdout, stderr="")

    monkeypatch.setattr(llm_module.subprocess, "run", fake_run)
    monkeypatch.setenv("LOCAL_LLM_BIGMODEL_DIRECT_IPS", "203.0.113.10")
    client = VisionLLMClient(
        base_url="https://open.bigmodel.cn/api/paas/v4",
        api_key="safe-secret",
        model="glm-4.6v",
        timeout_seconds=30,
    )

    with pytest.raises(MultimodalModelError) as exc_info:
        client._post_via_curl({"messages": [{"content": "claim"}]})

    error = exc_info.value
    assert error.retryable is expected_retryable
    assert error.http_status == status
    assert error.reason_code == "safe_provider_code"
    assert f"http_status={status}" in str(error)
    assert "error_code=safe_provider_code" in str(error)
    assert "sensitive provider detail" not in str(error)


def test_direct_curl_treats_observed_bigmodel_1210_as_batch_retryable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = {"error": {"code": "1210", "message": "must stay private"}}

    def fake_run(
        command: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        if "--head" in command:
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        pass_fds = kwargs["pass_fds"]
        assert isinstance(pass_fds, tuple)
        os.read(pass_fds[0], 16384)
        stdout = (
            json.dumps(body)
            + f"\n{llm_module._CURL_HTTP_STATUS_MARKER}400"
        )
        return subprocess.CompletedProcess(command, 22, stdout=stdout, stderr="")

    monkeypatch.setattr(llm_module.subprocess, "run", fake_run)
    monkeypatch.setenv("LOCAL_LLM_BIGMODEL_DIRECT_IPS", "203.0.113.10")
    client = VisionLLMClient(
        base_url="https://open.bigmodel.cn/api/paas/v4",
        api_key="safe-secret",
        model="glm-4.6v",
        timeout_seconds=30,
    )

    with pytest.raises(MultimodalModelError) as exc_info:
        client._post_via_curl({"messages": [{"content": "claim"}]})

    assert exc_info.value.retryable is True
    assert exc_info.value.reason_code == "1210"
    assert "must stay private" not in str(exc_info.value)
