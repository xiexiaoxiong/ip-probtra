import json
import logging
import os
import socket
import ssl
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import urlparse

from langchain_core.messages import AIMessage

logger = logging.getLogger(__name__)

_LLM_COOLDOWN_UNTIL = 0.0
_LLM_COOLDOWN_REASON = ""


def _cooldown_seconds_for_error(error: Exception | None) -> float:
    detail = str(error or "")
    lower = detail.lower()
    if not detail:
        return 0.0
    is_rate_limit = "HTTP 429" in detail or "速率限制" in detail
    is_timeout = "timed out" in lower or "timeout" in lower
    if not is_rate_limit and not is_timeout:
        return 0.0
    env_key = "LOCAL_LLM_FAILURE_COOLDOWN_SECONDS" if is_rate_limit else "LOCAL_LLM_TIMEOUT_COOLDOWN_SECONDS"
    default_seconds = "300" if is_rate_limit else "0"
    try:
        return max(0.0, float(os.getenv(env_key, default_seconds) or default_seconds))
    except ValueError:
        return float(default_seconds)


def _raise_if_llm_in_cooldown() -> None:
    if _LLM_COOLDOWN_UNTIL <= 0:
        return
    remaining = _LLM_COOLDOWN_UNTIL - time.monotonic()
    if remaining <= 0:
        return
    reason = _LLM_COOLDOWN_REASON or "recent transient failure"
    raise RuntimeError(f"LLM provider cooling down for {int(remaining)}s after {reason}")


def _record_llm_failure_for_cooldown(error: Exception | None) -> None:
    global _LLM_COOLDOWN_UNTIL, _LLM_COOLDOWN_REASON
    seconds = _cooldown_seconds_for_error(error)
    if seconds <= 0:
        return
    _LLM_COOLDOWN_UNTIL = max(_LLM_COOLDOWN_UNTIL, time.monotonic() + seconds)
    _LLM_COOLDOWN_REASON = str(error or "transient failure").strip()[:180]


def bootstrap_local_env() -> None:
    try:
        from dotenv import load_dotenv
    except Exception:
        load_dotenv = None

    env_candidates = [
        Path(__file__).resolve().parents[3] / "IP-protral" / ".env.local",
        Path(__file__).resolve().parents[2] / ".env.local",
        Path.cwd() / ".env.local",
    ]
    if load_dotenv:
        for env_path in env_candidates:
            if env_path.exists():
                load_dotenv(env_path, override=False)

    if not os.getenv("PGDATABASE_URL") and os.getenv("DATABASE_URL"):
        os.environ["PGDATABASE_URL"] = os.getenv("DATABASE_URL", "")


def looks_like_vision_model_name(model_name: str) -> bool:
    lower = str(model_name or "").strip().lower()
    if not lower:
        return False
    if "vision" in lower:
        return True
    if not lower.startswith("glm-"):
        return False
    return lower.endswith("v") or any(segment.endswith("v") for segment in lower.split("-"))


def resolve_local_model_alias(model_name: Optional[str]) -> str:
    requested = str(model_name or "").strip()
    default_model = os.getenv("LOCAL_LLM_DEFAULT_MODEL", "glm-4.6v").strip() or "glm-4.6v"
    fast_model = os.getenv("LOCAL_LLM_FAST_MODEL", "glm-4.6v").strip() or default_model
    vision_model = os.getenv("LOCAL_LLM_VISION_MODEL", "glm-4.6v").strip() or default_model

    if not requested:
        return default_model

    lower = requested.lower()
    last_segment = lower.rsplit("-", 1)[-1]

    if "vision" in lower:
        return vision_model
    if looks_like_vision_model_name(requested):
        return requested
    if lower.startswith("glm-") and any(tag in lower for tag in ("plus", "air", "airx", "flash", "flashx")):
        return requested
    if lower.startswith("glm-5-0-"):
        return fast_model
    if lower.startswith("glm-") and "." in lower:
        return fast_model
    if lower.startswith("glm-") and not last_segment.isdigit():
        return requested
    if lower.startswith("doubao-"):
        if "pro" in lower:
            return default_model
        if "mini" in lower or "lite" in lower:
            return fast_model
        return default_model
    if "mini" in lower or "lite" in lower or "flash" in lower or "air" in lower:
        return fast_model
    return default_model


def should_use_bigmodel_direct_route(request_url: str) -> bool:
    force_flag = (os.getenv("LOCAL_LLM_FORCE_DIRECT_ROUTE") or "").strip().lower()
    if force_flag in {"1", "true", "yes", "on"}:
        return True

    disable_flag = (os.getenv("LOCAL_LLM_DISABLE_DIRECT_ROUTE") or "").strip().lower()
    if disable_flag in {"1", "true", "yes", "on"}:
        return False

    route_mode = (os.getenv("LOCAL_LLM_DIRECT_ROUTE_MODE") or "off").strip().lower()
    if route_mode not in {"auto", "1", "true", "yes", "on"}:
        return False

    host = (urlparse(request_url).hostname or "").strip().lower()
    if host != "open.bigmodel.cn":
        return False

    try:
        resolved = socket.gethostbyname(host)
    except Exception:
        return True
    return resolved.startswith("198.18.")


def invoke_bigmodel_via_direct_route(
    request_url: str,
    api_key: str,
    payload: Dict[str, Any],
    timeout_seconds: float,
) -> Dict[str, Any]:
    parsed = urlparse(request_url)
    host = parsed.hostname or "open.bigmodel.cn"
    port = parsed.port or 443
    interface = (os.getenv("LOCAL_LLM_DIRECT_INTERFACE") or "en0").strip() or "en0"
    ip_candidates = [
        item.strip()
        for item in (
            os.getenv("LOCAL_LLM_BIGMODEL_DIRECT_IPS")
            or "122.10.144.213,156.59.96.30,129.227.65.212"
        ).split(",")
        if item.strip()
    ]
    if not ip_candidates:
        raise RuntimeError("未配置可用的智谱直连IP")

    request_body = json.dumps(payload, ensure_ascii=False)
    errors: list[str] = []

    for ip in ip_candidates:
        command = [
            "curl",
            "-sS",
            "--fail-with-body",
            "--interface",
            interface,
            "--connect-timeout",
            "20",
            "--max-time",
            str(int(timeout_seconds)),
            "--connect-to",
            f"{host}:{port}:{ip}:{port}",
            "-X",
            "POST",
            request_url,
            "-H",
            f"Authorization: Bearer {api_key}",
            "-H",
            "Content-Type: application/json",
            "--data-binary",
            request_body,
        ]
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        if result.returncode == 0:
            return json.loads(result.stdout or "{}")

        detail = (result.stderr or result.stdout).strip().replace("\n", " ")[:300]
        errors.append(f"{ip}: {detail}")

    raise RuntimeError("智谱直连兜底失败: " + " | ".join(errors))


def convert_message_content_for_openai(content: Any) -> Any:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return str(content)

    base_url = (os.getenv("LOCAL_LLM_BASE_URL") or os.getenv("OPENAI_BASE_URL") or "").lower()

    has_image = False
    for item in content:
        if isinstance(item, dict) and item.get("type") == "image_url":
            has_image = True
            break

    if "bigmodel.cn" in base_url and not has_image:
        text_parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                if item.strip():
                    text_parts.append(item)
                continue
            if isinstance(item, dict):
                item_type = item.get("type")
                if item_type == "text":
                    text = str(item.get("text", "")).strip()
                    if text:
                        text_parts.append(text)
                    continue
                if "text" in item:
                    text = str(item.get("text", "")).strip()
                    if text:
                        text_parts.append(text)
                    continue
            text = str(item).strip()
            if text:
                text_parts.append(text)

        return "\n".join(text_parts).strip()

    converted: list[Any] = []
    for item in content:
        if isinstance(item, str):
            converted.append({"type": "text", "text": item})
            continue
        if not isinstance(item, dict):
            converted.append({"type": "text", "text": str(item)})
            continue

        item_type = item.get("type")
        if item_type == "text":
            converted.append({"type": "text", "text": str(item.get("text", ""))})
        elif item_type == "image_url":
            image_url = item.get("image_url")
            if isinstance(image_url, str):
                converted.append({"type": "image_url", "image_url": {"url": image_url}})
            elif isinstance(image_url, dict):
                converted.append({"type": "image_url", "image_url": image_url})
        else:
            converted.append(item)

    return converted


def flatten_message_content_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return str(content)

    text_parts: list[str] = []
    for item in content:
        if isinstance(item, str):
            if item.strip():
                text_parts.append(item.strip())
            continue
        if isinstance(item, dict):
            item_type = item.get("type")
            if item_type == "text" or "text" in item:
                text_value = str(item.get("text", "")).strip()
                if text_value:
                    text_parts.append(text_value)
                continue
            if item_type == "image_url":
                image_url = item.get("image_url")
                if isinstance(image_url, dict):
                    image_url = image_url.get("url")
                image_url_text = str(image_url or "").strip()
                if image_url_text:
                    text_parts.append(f"图片链接: {image_url_text}")
                continue
        text_value = str(item).strip()
        if text_value:
            text_parts.append(text_value)
    return "\n".join(text_parts).strip()


def convert_message_role_for_openai(message: Any) -> str:
    role = str(getattr(message, "type", "user") or "user").lower()
    if role == "human":
        return "user"
    if role == "ai":
        return "assistant"
    return role


def payload_uses_image_inputs(payload: Dict[str, Any]) -> bool:
    for message in payload.get("messages", []):
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for item in content:
            if isinstance(item, dict) and item.get("type") == "image_url":
                return True
    return False


def extract_response_message_content(response: Dict[str, Any] | None) -> Any:
    if not isinstance(response, dict):
        return ""
    choices = response.get("choices")
    if not isinstance(choices, list) or not choices:
        return ""
    first_choice = choices[0]
    if not isinstance(first_choice, dict):
        return ""
    message = first_choice.get("message") or {}
    if not isinstance(message, dict):
        return ""
    return message.get("content", "")


def response_content_is_empty(response: Dict[str, Any] | None) -> bool:
    content = extract_response_message_content(response)
    if isinstance(content, str):
        return not content.strip()
    if isinstance(content, list):
        return not flatten_message_content_to_text(content).strip()
    if content is None:
        return True
    return not str(content).strip()


def build_empty_response_error(response: Dict[str, Any] | None) -> RuntimeError:
    choice: Dict[str, Any] = {}
    if isinstance(response, dict):
        choices = response.get("choices")
        if isinstance(choices, list) and choices and isinstance(choices[0], dict):
            choice = choices[0]
    finish_reason = str(choice.get("finish_reason", "") or "unknown")
    response_model = str((response or {}).get("model", "") or "unknown")
    response_id = str((response or {}).get("id", "") or "unknown")
    return RuntimeError(
        f"LLM返回空内容: model={response_model}, finish_reason={finish_reason}, id={response_id}"
    )


def should_prefer_fallback_for_text(payload: Dict[str, Any]) -> bool:
    provider = (os.getenv("LOCAL_LLM_TEXT_PROVIDER") or "").strip().lower()
    return provider == "fallback" and not payload_uses_image_inputs(payload)


def should_prefer_fallback_for_vision(payload: Dict[str, Any]) -> bool:
    provider = (os.getenv("LOCAL_LLM_VISION_PROVIDER") or "").strip().lower()
    return provider == "fallback" and payload_uses_image_inputs(payload)


def resolve_fallback_model_alias(model_name: str) -> str:
    fallback_default = os.getenv("LOCAL_LLM_FALLBACK_DEFAULT_MODEL", "deepseek-v4-pro").strip() or "deepseek-v4-pro"
    fallback_fast = os.getenv("LOCAL_LLM_FALLBACK_FAST_MODEL", "deepseek-v4-flash").strip() or fallback_default
    fallback_vision = (os.getenv("LOCAL_LLM_FALLBACK_VISION_MODEL") or "").strip()

    requested = str(model_name or "").strip().lower()
    if "vision" in requested or looks_like_vision_model_name(requested):
        return fallback_vision
    if requested.startswith("glm-5-0-") or any(tag in requested for tag in ("mini", "lite", "flash", "air")):
        return fallback_fast
    return fallback_default


def invoke_openai_compatible_via_urllib(
    request_url: str,
    api_key: str,
    payload: Dict[str, Any],
    timeout_seconds: float,
    max_attempts: int,
    retry_interval: float,
) -> Dict[str, Any]:
    request_body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    ssl_context = ssl.create_default_context()
    if hasattr(ssl, "TLSVersion"):
        ssl_context.minimum_version = ssl.TLSVersion.TLSv1_2

    last_error: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        request = urllib.request.Request(
            request_url,
            data=request_body,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout_seconds, context=ssl_context) as http_response:
                raw_response = http_response.read().decode("utf-8")
            return json.loads(raw_response or "{}")
        except urllib.error.HTTPError as error:
            body = error.read().decode("utf-8", errors="ignore")
            last_error = RuntimeError(f"HTTP {error.code}: {(body or str(error.reason)).strip()[:1000]}")
            if 400 <= error.code < 500 and error.code != 429:
                break
        except Exception as error:
            last_error = error

        if attempt < max_attempts:
            time.sleep(retry_interval * attempt)

    detail = str(last_error).strip()[:1000] if last_error else "未知错误"
    raise RuntimeError(detail)


def invoke_local_llm(
    messages: list[Any],
    model: str,
    temperature: Optional[float] = None,
    frequency_penalty: Optional[float] = None,
    top_p: Optional[float] = None,
    max_tokens: Optional[int] = None,
    max_completion_tokens: Optional[int] = None,
) -> AIMessage:
    base_url = (os.getenv("LOCAL_LLM_BASE_URL") or os.getenv("OPENAI_BASE_URL") or "").rstrip("/")
    api_key = os.getenv("LOCAL_LLM_API_KEY") or os.getenv("OPENAI_API_KEY") or ""

    if not base_url:
        raise RuntimeError("本地模型调用失败: 缺少 LOCAL_LLM_BASE_URL")
    if not api_key:
        raise RuntimeError("本地模型调用失败: 缺少 LOCAL_LLM_API_KEY")
    _raise_if_llm_in_cooldown()

    resolved_model = resolve_local_model_alias(model)
    payload: Dict[str, Any] = {
        "model": resolved_model,
        "messages": [
            {
                "role": convert_message_role_for_openai(message),
                "content": convert_message_content_for_openai(getattr(message, "content", "")),
            }
            for message in messages
        ],
        "stream": False,
    }
    if payload_uses_image_inputs(payload):
        vision_model = resolve_local_model_alias(os.getenv("LOCAL_LLM_VISION_MODEL", "vision") or "vision")
        if vision_model and str(payload.get("model") or "") != vision_model:
            logger.info(
                "Switching local LLM request to vision model for image inputs: %s -> %s",
                payload.get("model"),
                vision_model,
            )
            payload["model"] = vision_model

    if temperature is not None:
        payload["temperature"] = temperature
    if frequency_penalty is not None:
        payload["frequency_penalty"] = frequency_penalty
    if top_p is not None:
        payload["top_p"] = top_p

    token_limit = max_tokens or max_completion_tokens
    if token_limit:
        model_name = str(payload.get("model") or "").lower()
        max_allowed: int | None = None
        if "glm-4-plus" in model_name:
            max_allowed = 4096
        elif "glm-4.6v" in model_name:
            max_allowed = 32768
        elif "glm-4.7" in model_name:
            max_allowed = 131072
        elif model_name.startswith("glm-5"):
            max_allowed = 131072
        elif model_name.startswith("glm-4.6"):
            max_allowed = 131072
        if max_allowed is not None and token_limit > max_allowed:
            token_limit = max_allowed
        payload["max_tokens"] = token_limit

    timeout_seconds = float(os.getenv("LOCAL_LLM_HTTP_TIMEOUT_SECONDS", "240") or "240")
    max_attempts = max(1, int(os.getenv("LOCAL_LLM_HTTP_RETRIES", "1") or "1"))
    retry_interval = float(os.getenv("LOCAL_LLM_HTTP_RETRY_INTERVAL_SECONDS", "2") or "2")
    request_url = f"{base_url}/chat/completions"

    last_error: Exception | None = None
    response: Dict[str, Any] | None = None
    fallback_base_url = (os.getenv("LOCAL_LLM_FALLBACK_BASE_URL") or "").rstrip("/")
    fallback_api_key = os.getenv("LOCAL_LLM_FALLBACK_API_KEY") or ""
    fallback_model = resolve_fallback_model_alias(model)

    if (
        (should_prefer_fallback_for_text(payload) or should_prefer_fallback_for_vision(payload))
        and fallback_base_url
        and fallback_api_key
        and fallback_model
    ):
        try:
            logger.info("Prefer fallback LLM request, model=%s", fallback_model)
            fallback_payload = dict(payload)
            fallback_payload["model"] = fallback_model
            response = invoke_openai_compatible_via_urllib(
                request_url=f"{fallback_base_url}/chat/completions",
                api_key=fallback_api_key,
                payload=fallback_payload,
                timeout_seconds=timeout_seconds,
                max_attempts=max_attempts,
                retry_interval=retry_interval,
            )
        except Exception as error:
            last_error = error

    if response is None and should_use_bigmodel_direct_route(request_url):
        try:
            response = invoke_bigmodel_via_direct_route(
                request_url=request_url,
                api_key=api_key,
                payload=payload,
                timeout_seconds=timeout_seconds,
            )
        except Exception as error:
            last_error = error

    if response is None:
        try:
            response = invoke_openai_compatible_via_urllib(
                request_url=request_url,
                api_key=api_key,
                payload=payload,
                timeout_seconds=timeout_seconds,
                max_attempts=max_attempts,
                retry_interval=retry_interval,
            )
        except Exception as error:
            last_error = error

    if response is not None and response_content_is_empty(response):
        last_error = build_empty_response_error(response)
        logger.warning("%s; will try fallback provider if configured", last_error)
        response = None

    if response is None and fallback_base_url and fallback_api_key and fallback_model:
        try:
            logger.warning("Primary LLM failed, fallback to secondary provider model=%s", fallback_model)
            fallback_payload = dict(payload)
            fallback_payload["model"] = fallback_model
            response = invoke_openai_compatible_via_urllib(
                request_url=f"{fallback_base_url}/chat/completions",
                api_key=fallback_api_key,
                payload=fallback_payload,
                timeout_seconds=timeout_seconds,
                max_attempts=max_attempts,
                retry_interval=retry_interval,
            )
        except Exception as error:
            last_error = error

    if response is not None and response_content_is_empty(response):
        last_error = build_empty_response_error(response)
        logger.warning("%s", last_error)
        response = None

    if response is None:
        _record_llm_failure_for_cooldown(last_error)
        detail = str(last_error).strip()[:1000] if last_error else "未知错误"
        raise RuntimeError(f"本地模型 HTTP 调用失败: {detail}")

    choice = (response.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    content = extract_response_message_content(response)

    return AIMessage(
        content=content,
        response_metadata={
            "id": response.get("id"),
            "model": response.get("model"),
            "usage": response.get("usage"),
        },
    )


bootstrap_local_env()
