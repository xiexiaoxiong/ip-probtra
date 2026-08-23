from __future__ import annotations

import argparse
import asyncio
import base64
import json
import logging
import os
import re
import shutil
import socket
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from PIL import Image
from langchain_core.messages import HumanMessage, SystemMessage
from playwright.async_api import BrowserContext, Page, async_playwright

logger = logging.getLogger(__name__)

DEFAULT_READY_SELECTORS = [
    "h1",
    "[class*='title']",
    "[class*='name']",
    "[data-testid*='title']",
    "[class*='price']",
    "main",
]

DEFAULT_CLOSE_SELECTORS = [
    "[aria-label='Close']",
    "[aria-label='close']",
    "[data-testid='close']",
    "[class*='close']",
    "[class*='modal-close']",
    "[class*='popup-close']",
    "[class*='dialog-close']",
]

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/136.0.0.0 Safari/537.36"
)

TEXT_SCORE_PATTERNS = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        "title",
        "name",
        "price",
        "subtitle",
        "summary",
        "intro",
        "detail",
        "spec",
        "feature",
        "desc",
    )
]

DETAIL_HINT_PATTERNS = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        "detail",
        "desc",
        "description",
        "graphic",
        "module",
        "introduce",
        "spec",
        "parameter",
        "详情",
        "描述",
        "介绍",
        "参数",
        "图文",
    )
]

NON_DETAIL_HINT_PATTERNS = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        "banner",
        "hero",
        "gallery",
        "thumb",
        "thumbnail",
        "main-image",
        "sku",
        "carousel",
        "slider",
        "nav",
        "header",
        "logo",
        "店铺",
        "横幅",
        "轮播",
        "主图",
    )
]

DETAIL_SECTION_SELECTORS = [
    "[class*='detail']",
    "[id*='detail']",
    "[class*='desc']",
    "[id*='desc']",
    "[class*='description']",
    "[class*='graphic']",
    "[class*='spec']",
    "[class*='parameter']",
]


@dataclass
class CaptureConfig:
    url: str
    output_dir: Path
    timeout_ms: int = 30_000
    viewport_width: int = 1440
    viewport_height: int = 1800
    locale: str = "zh-CN"
    timezone_id: str = "Asia/Shanghai"
    wait_selectors: list[str] = field(default_factory=lambda: list(DEFAULT_READY_SELECTORS))
    close_selectors: list[str] = field(default_factory=lambda: list(DEFAULT_CLOSE_SELECTORS))
    headless: bool = True
    enable_ocr: bool = True
    max_image_crops: int = 8
    ocr_languages: str = "chi_sim+eng"
    manual_login: bool = False
    user_data_dir: Path | None = None
    manual_login_wait_sec: int = 300
    enable_detail_image_llm_filter: bool = True
    detail_image_candidate_limit: int = 12
    detail_image_max_results: int = 6
    detail_image_min_confidence: float = 0.55
    vision_model: str = "glm-4.6v"
    enable_fullpage_region_llm: bool = True


def sanitize_filename(value: str, fallback: str = "capture") -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip()).strip("-._")
    return cleaned[:80] or fallback


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


def resolve_local_model_alias(model_name: str) -> str:
    requested = str(model_name or "").strip()
    default_model = os.getenv("LOCAL_LLM_DEFAULT_MODEL", "glm-4.6v").strip() or "glm-4.6v"
    fast_model = os.getenv("LOCAL_LLM_FAST_MODEL", "glm-4.6v").strip() or default_model
    vision_model = os.getenv("LOCAL_LLM_VISION_MODEL", "glm-4.6v").strip() or default_model

    if not requested:
        return default_model

    lower = requested.lower()
    last_segment = lower.rsplit("-", 1)[-1]
    if lower.startswith("glm-") and not last_segment.isdigit():
        return requested
    if "vision" in lower or lower.endswith("v"):
        return vision_model
    if lower.startswith("glm-5-0-"):
        return fast_model
    if "mini" in lower or "lite" in lower or "flash" in lower or "air" in lower:
        return fast_model
    return default_model


def convert_message_content_for_openai(content: Any) -> Any:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return str(content)

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


def convert_message_role_for_openai(message: Any) -> str:
    role = str(getattr(message, "type", "user") or "user").lower()
    if role == "human":
        return "user"
    if role == "ai":
        return "assistant"
    return role


def should_use_bigmodel_direct_route(request_url: str) -> bool:
    force_flag = (os.getenv("LOCAL_LLM_FORCE_DIRECT_ROUTE") or "").strip().lower()
    if force_flag in {"1", "true", "yes", "on"}:
        return True
    disable_flag = (os.getenv("LOCAL_LLM_DISABLE_DIRECT_ROUTE") or "").strip().lower()
    if disable_flag in {"1", "true", "yes", "on"}:
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
    payload: dict[str, Any],
    timeout_seconds: float,
) -> dict[str, Any]:
    host = urlparse(request_url).hostname or "open.bigmodel.cn"
    port = urlparse(request_url).port or 443
    interface = (os.getenv("LOCAL_LLM_DIRECT_INTERFACE") or "en0").strip() or "en0"
    ip_candidates = [
        item.strip()
        for item in (
            os.getenv("LOCAL_LLM_BIGMODEL_DIRECT_IPS") or "122.10.144.213,156.59.96.30,129.227.65.212"
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


def invoke_openai_compatible_via_urllib(
    request_url: str,
    api_key: str,
    payload: dict[str, Any],
    timeout_seconds: float,
    max_attempts: int,
    retry_interval: float,
) -> dict[str, Any]:
    request_body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    ssl_context = ssl.create_default_context()
    if hasattr(ssl, "TLSVersion"):
        ssl_context.minimum_version = ssl.TLSVersion.TLSv1_2

    last_error: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        request = urllib.request.Request(
            request_url,
            data=request_body,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout_seconds, context=ssl_context) as http_response:
                raw_response = http_response.read().decode("utf-8")
            return json.loads(raw_response or "{}")
        except urllib.error.HTTPError as error:
            body = error.read().decode("utf-8", errors="ignore")
            last_error = RuntimeError(f"HTTP {error.code}: {(body or str(error.reason)).strip()[:1000]}")
        except Exception as error:
            last_error = error
        if attempt < max_attempts:
            time.sleep(retry_interval * attempt)
    detail = str(last_error).strip()[:1000] if last_error else "未知错误"
    raise RuntimeError(detail)


def invoke_local_llm(messages: list[Any], model: str, max_completion_tokens: int = 4096) -> str:
    base_url = (
        os.getenv("COZE_INTEGRATION_MODEL_BASE_URL")
        or os.getenv("LOCAL_LLM_BASE_URL")
        or os.getenv("OPENAI_BASE_URL")
        or ""
    ).rstrip("/")
    api_key = (
        os.getenv("COZE_WORKLOAD_IDENTITY_API_KEY")
        or os.getenv("LOCAL_LLM_API_KEY")
        or os.getenv("OPENAI_API_KEY")
        or ""
    )
    if not base_url or not api_key:
        raise RuntimeError("缺少本地多模态模型环境变量")

    payload: dict[str, Any] = {
        "model": resolve_local_model_alias(model),
        "messages": [
            {
                "role": convert_message_role_for_openai(message),
                "content": convert_message_content_for_openai(getattr(message, "content", "")),
            }
            for message in messages
        ],
        "stream": False,
        "max_tokens": max_completion_tokens,
    }
    timeout_seconds = float(os.getenv("LOCAL_LLM_HTTP_TIMEOUT_SECONDS", "180") or "180")
    max_attempts = max(1, int(os.getenv("LOCAL_LLM_HTTP_RETRIES", "2") or "2"))
    retry_interval = float(os.getenv("LOCAL_LLM_HTTP_RETRY_INTERVAL_SECONDS", "2") or "2")
    request_url = f"{base_url}/chat/completions"

    if should_use_bigmodel_direct_route(request_url):
        response = invoke_bigmodel_via_direct_route(
            request_url=request_url,
            api_key=api_key,
            payload=payload,
            timeout_seconds=timeout_seconds,
        )
    else:
        response = invoke_openai_compatible_via_urllib(
            request_url=request_url,
            api_key=api_key,
            payload=payload,
            timeout_seconds=timeout_seconds,
            max_attempts=max_attempts,
            retry_interval=retry_interval,
        )
    choice = (response.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    return str(message.get("content", "") or "")


def dedupe_by(items: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    seen: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for item in items:
        item_key = str(item.get(key, "")).strip()
        if not item_key or item_key in seen:
            continue
        seen.add(item_key)
        deduped.append(item)
    return deduped


def score_text_block(block: dict[str, Any]) -> int:
    score = int(block.get("text_length", 0))
    tag_name = str(block.get("tag_name", "")).lower()
    class_name = str(block.get("class_name", ""))
    if tag_name in {"h1", "h2", "h3"}:
        score += 120
    if tag_name in {"strong", "b"}:
        score += 40
    if int(block.get("top", 0)) < 1200:
        score += 30
    if int(block.get("width", 0)) > 240:
        score += 20
    for pattern in TEXT_SCORE_PATTERNS:
        if pattern.search(class_name):
            score += 45
    return score


def normalize_text_blocks(blocks: list[dict[str, Any]], limit: int = 60) -> list[dict[str, Any]]:
    cleaned: list[dict[str, Any]] = []
    seen_text: set[str] = set()
    for block in blocks:
        text = re.sub(r"\s+", " ", str(block.get("text", "")).strip())
        if len(text) < 2 or text in seen_text:
            continue
        seen_text.add(text)
        block = dict(block)
        block["text"] = text
        block["score"] = score_text_block(block)
        cleaned.append(block)
    cleaned.sort(key=lambda item: (-int(item.get("score", 0)), int(item.get("top", 0))))
    return cleaned[:limit]


def normalize_image_candidates(candidates: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    cleaned: list[dict[str, Any]] = []
    for item in candidates:
        width = int(item.get("width", 0))
        height = int(item.get("height", 0))
        src = str(item.get("src", "")).strip()
        if not src or width < 80 or height < 80:
            continue
        score = (width * height) + max(0, 2000 - int(item.get("top", 0)))
        candidate = dict(item)
        candidate["score"] = score
        cleaned.append(candidate)
    cleaned.sort(key=lambda item: (-int(item.get("score", 0)), int(item.get("top", 0))))
    return dedupe_by(cleaned, "src")[:limit]


def score_detail_image_candidate(candidate: dict[str, Any]) -> int:
    width = int(candidate.get("width", 0))
    height = int(candidate.get("height", 0))
    top = int(candidate.get("top", 0))
    class_name = f"{candidate.get('class_name', '')} {candidate.get('id', '')}"
    score = width * height
    if width >= 500:
        score += 80_000
    if height >= 350:
        score += 60_000
    if top >= 800:
        score += 40_000
    if top >= 1400:
        score += 60_000
    if candidate.get("kind") == "background":
        score += 10_000
    for pattern in DETAIL_HINT_PATTERNS:
        if pattern.search(class_name):
            score += 90_000
    for pattern in NON_DETAIL_HINT_PATTERNS:
        if pattern.search(class_name):
            score -= 120_000
    return score


def select_detail_image_candidates(candidates: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    ranked: list[dict[str, Any]] = []
    for candidate in candidates:
        width = int(candidate.get("width", 0))
        height = int(candidate.get("height", 0))
        if width < 220 or height < 120:
            continue
        candidate = dict(candidate)
        candidate["detail_candidate_score"] = score_detail_image_candidate(candidate)
        ranked.append(candidate)
    ranked.sort(
        key=lambda item: (-int(item.get("detail_candidate_score", 0)), int(item.get("top", 0)))
    )
    return dedupe_by(ranked, "src")[:limit]


async def dismiss_overlays(page: Page, selectors: list[str]) -> list[str]:
    clicked: list[str] = []
    for selector in selectors:
        try:
            locator = page.locator(selector).first
            if await locator.is_visible(timeout=250):
                await locator.click(timeout=800)
                clicked.append(selector)
                await page.wait_for_timeout(250)
        except Exception:
            continue
    try:
        await page.keyboard.press("Escape")
    except Exception:
        pass
    return clicked


async def wait_for_any_selector(page: Page, selectors: list[str], timeout_ms: int) -> str | None:
    deadline = asyncio.get_running_loop().time() + (timeout_ms / 1000)
    while asyncio.get_running_loop().time() < deadline:
        for selector in selectors:
            try:
                locator = page.locator(selector).first
                if await locator.count() > 0 and await locator.is_visible(timeout=150):
                    return selector
            except Exception:
                continue
        await page.wait_for_timeout(250)
    return None


async def wait_for_height_stable(page: Page, stable_rounds: int = 3, interval_ms: int = 500) -> int:
    last_height = -1
    stable_count = 0
    current_height = 0
    for _ in range(18):
        current_height = await page.evaluate(
            "() => Math.max(document.body.scrollHeight, document.documentElement.scrollHeight)"
        )
        if abs(current_height - last_height) <= 4:
            stable_count += 1
        else:
            stable_count = 0
        if stable_count >= stable_rounds:
            return current_height
        last_height = current_height
        await page.wait_for_timeout(interval_ms)
    return current_height


async def ensure_images_loaded(page: Page) -> dict[str, int]:
    return await page.evaluate(
        """
        () => {
          const images = Array.from(document.images || []);
          let loaded = 0;
          for (const image of images) {
            if (image.complete && image.naturalWidth > 0) {
              loaded += 1;
            }
          }
          return { total: images.length, loaded };
        }
        """
    )


async def perform_human_like_scroll(page: Page) -> None:
    viewport = page.viewport_size or {"height": 1600}
    step = max(480, int(viewport["height"] * 0.7))
    total_height = await page.evaluate(
        "() => Math.max(document.body.scrollHeight, document.documentElement.scrollHeight)"
    )
    current = 0
    rounds = 0
    while current < total_height and rounds < 12:
        rounds += 1
        await page.mouse.move(180 + (rounds % 4) * 120, 220 + (rounds % 3) * 90)
        await page.mouse.wheel(0, step)
        await page.wait_for_timeout(500)
        current += step
        total_height = await page.evaluate(
            "() => Math.max(document.body.scrollHeight, document.documentElement.scrollHeight)"
        )
    await page.wait_for_timeout(900)
    await page.evaluate("() => window.scrollTo(0, 0)")
    await page.wait_for_timeout(500)


async def wait_for_page_ready(page: Page, config: CaptureConfig) -> dict[str, Any]:
    ready: dict[str, Any] = {
        "ready_state": await page.evaluate("() => document.readyState"),
        "matched_selector": None,
        "images": {},
        "scroll_height": 0,
        "dismissed_overlays": [],
    }
    await page.wait_for_load_state("domcontentloaded", timeout=config.timeout_ms)
    ready["dismissed_overlays"] = await dismiss_overlays(page, config.close_selectors)
    try:
        await page.wait_for_load_state("networkidle", timeout=min(config.timeout_ms, 12_000))
    except Exception:
        ready["networkidle_timeout"] = True
    await page.wait_for_timeout(1000)
    ready["matched_selector"] = await wait_for_any_selector(
        page, config.wait_selectors, timeout_ms=min(config.timeout_ms, 8_000)
    )
    await perform_human_like_scroll(page)
    ready["images"] = await ensure_images_loaded(page)
    ready["scroll_height"] = await wait_for_height_stable(page)
    ready["ready_state"] = await page.evaluate("() => document.readyState")
    return ready


async def extract_text_blocks(page: Page) -> list[dict[str, Any]]:
    raw_blocks: list[dict[str, Any]] = await page.evaluate(
        """
        () => {
          const selectors = [
            "h1", "h2", "h3", "h4", "p", "span", "li", "dt", "dd",
            "strong", "b", "td", "th", "a", "button", "div"
          ];
          const isVisible = (element) => {
            const style = window.getComputedStyle(element);
            const rect = element.getBoundingClientRect();
            return (
              style &&
              style.display !== "none" &&
              style.visibility !== "hidden" &&
              Number(style.opacity || "1") > 0.05 &&
              rect.width > 0 &&
              rect.height > 0
            );
          };
          const nodes = Array.from(document.querySelectorAll(selectors.join(",")));
          return nodes.map((element) => {
            const rect = element.getBoundingClientRect();
            const text = (element.innerText || element.textContent || "").trim();
            return {
              text,
              tag_name: element.tagName.toLowerCase(),
              class_name: element.className || "",
              top: Math.round(rect.top + window.scrollY),
              left: Math.round(rect.left + window.scrollX),
              width: Math.round(rect.width),
              height: Math.round(rect.height),
              text_length: text.length,
              visible: isVisible(element),
            };
          }).filter((item) => item.visible && item.text_length >= 2);
        }
        """
    )
    return normalize_text_blocks(raw_blocks)


async def extract_image_candidates(page: Page, limit: int) -> list[dict[str, Any]]:
    raw_candidates: list[dict[str, Any]] = await page.evaluate(
        """
        () => {
          const isVisible = (element) => {
            const style = window.getComputedStyle(element);
            const rect = element.getBoundingClientRect();
            return (
              style &&
              style.display !== "none" &&
              style.visibility !== "hidden" &&
              Number(style.opacity || "1") > 0.05 &&
              rect.width > 0 &&
              rect.height > 0
            );
          };
          const parseBackgroundImage = (value) => {
            if (!value || value === "none") return "";
            const match = /url\\(["']?(.*?)["']?\\)/.exec(value);
            return match ? match[1] : "";
          };
          const elements = Array.from(document.querySelectorAll("img, *"));
          const results = [];
          for (const element of elements) {
            if (!isVisible(element)) continue;
            const rect = element.getBoundingClientRect();
            const style = window.getComputedStyle(element);
            const isImg = element.tagName.toLowerCase() === "img";
            const parent = element.parentElement;
            const src = isImg
              ? (element.currentSrc || element.src || element.getAttribute("data-src") || "")
              : parseBackgroundImage(style.backgroundImage);
            if (!src) continue;
            results.push({
              kind: isImg ? "img" : "background",
              src,
              alt: isImg ? (element.alt || "") : "",
              id: element.id || "",
              class_name: element.className || "",
              parent_class_name: parent ? (parent.className || "") : "",
              top: Math.round(rect.top + window.scrollY),
              left: Math.round(rect.left + window.scrollX),
              width: Math.round(rect.width),
              height: Math.round(rect.height),
            });
          }
          return results;
        }
        """
    )
    return normalize_image_candidates(raw_candidates, limit=limit)


async def capture_detail_section_candidates(page: Page, output_dir: Path) -> list[dict[str, Any]]:
    screenshots_dir = output_dir / "screenshots"
    screenshots_dir.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, Any]] = []
    for index, selector in enumerate(DETAIL_SECTION_SELECTORS, start=1):
        try:
            locator = page.locator(selector).first
            if await locator.count() == 0 or not await locator.is_visible(timeout=300):
                continue
            box = await locator.bounding_box()
            if not box:
                continue
            crop_path = screenshots_dir / f"detail-section-{index:02d}.png"
            await locator.screenshot(path=str(crop_path))
            results.append(
                {
                    "kind": "detail_section",
                    "src": f"detail-section://{selector}",
                    "selector": selector,
                    "crop_path": str(crop_path),
                    "top": int(box.get("y", 0)),
                    "left": int(box.get("x", 0)),
                    "width": int(box.get("width", 0)),
                    "height": int(box.get("height", 0)),
                }
            )
        except Exception:
            continue
    return results


def save_text_snapshot(output_dir: Path, text_blocks: list[dict[str, Any]]) -> Path:
    text_dir = output_dir / "text"
    text_dir.mkdir(parents=True, exist_ok=True)
    file_path = text_dir / "visible-text.txt"
    file_path.write_text("\n\n".join(block["text"] for block in text_blocks), encoding="utf-8")
    return file_path


def run_tesseract_ocr(image_path: Path, languages: str) -> str | None:
    if shutil.which("tesseract") is None:
        return None
    command = [
        "tesseract",
        str(image_path),
        "stdout",
        "-l",
        languages,
        "--psm",
        "6",
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        return None
    text = re.sub(r"\s+", " ", result.stdout).strip()
    return text or None


def build_llm_review_image(image_path: Path) -> Path:
    review_path = image_path.with_name(f"{image_path.stem}-llm.jpg")
    with Image.open(image_path) as image:
        image = image.convert("RGB")
        max_side = max(image.size)
        if max_side > 1400:
            scale = 1400 / max_side
            resized = (
                max(1, int(image.size[0] * scale)),
                max(1, int(image.size[1] * scale)),
            )
            image = image.resize(resized)
        image.save(review_path, format="JPEG", quality=88)
    return review_path


def image_file_to_data_url(image_path: Path) -> str:
    mime_type = "image/jpeg" if image_path.suffix.lower() in {".jpg", ".jpeg"} else "image/png"
    encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


def extract_json_from_text(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if not stripped:
        return {}
    block_match = re.search(r"```(?:json)?\s*([\s\S]*?)```", stripped)
    if block_match:
        stripped = block_match.group(1).strip()
    try:
        parsed = json.loads(stripped)
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        pass
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            parsed = json.loads(stripped[start : end + 1])
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def extract_json_value_from_text(text: str) -> Any:
    stripped = text.strip()
    if not stripped:
        return None
    block_match = re.search(r"```(?:json)?\s*([\s\S]*?)```", stripped)
    if block_match:
        stripped = block_match.group(1).strip()
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        pass
    start_object = stripped.find("{")
    end_object = stripped.rfind("}")
    if start_object != -1 and end_object != -1 and end_object > start_object:
        try:
            return json.loads(stripped[start_object : end_object + 1])
        except json.JSONDecodeError:
            pass
    start_array = stripped.find("[")
    end_array = stripped.rfind("]")
    if start_array != -1 and end_array != -1 and end_array > start_array:
        try:
            return json.loads(stripped[start_array : end_array + 1])
        except json.JSONDecodeError:
            pass
    return None


def infer_detail_type_by_heuristic(candidate: dict[str, Any]) -> dict[str, Any]:
    width = int(candidate.get("width", 0))
    height = int(candidate.get("height", 0))
    top = int(candidate.get("top", 0))
    class_name = " ".join(
        str(candidate.get(key, "")).strip()
        for key in ("class_name", "parent_class_name", "id", "selector")
        if str(candidate.get(key, "")).strip()
    )
    looks_like_detail = False
    reason_parts: list[str] = []
    for pattern in NON_DETAIL_HINT_PATTERNS:
        if pattern.search(class_name):
            return {
                "is_product_detail": False,
                "confidence": 0.2,
                "category": "non_detail",
                "reason": "DOM 标识更像主图、横幅、缩略图或导航区域",
                "source": "heuristic",
            }
    if width >= 500 and height >= 200 and top >= 600:
        looks_like_detail = True
        reason_parts.append("位置和尺寸更像详情区内容")
    if height >= 600:
        looks_like_detail = True
        reason_parts.append("图片较长，接近图文详情图")
    for pattern in DETAIL_HINT_PATTERNS:
        if pattern.search(class_name):
            looks_like_detail = True
            reason_parts.append("DOM 标识包含 detail/desc/spec 等关键词")
            break
    category = "detail" if looks_like_detail else "other"
    confidence = 0.65 if looks_like_detail else 0.35
    return {
        "is_product_detail": looks_like_detail,
        "confidence": confidence,
        "category": category,
        "reason": "；".join(reason_parts) or "未命中详情图启发式规则",
        "source": "heuristic",
    }


def normalize_fullpage_regions(
    raw_regions: Any,
    review_size: tuple[int, int],
    original_size: tuple[int, int],
    min_confidence: float,
) -> list[dict[str, Any]]:
    if isinstance(raw_regions, dict):
        raw_regions = raw_regions.get("regions") or raw_regions.get("items") or raw_regions.get("data") or []
    if not isinstance(raw_regions, list):
        return []

    review_width, review_height = review_size
    original_width, original_height = original_size
    scale_x = original_width / max(1, review_width)
    scale_y = original_height / max(1, review_height)

    normalized: list[dict[str, Any]] = []
    seen_boxes: set[tuple[int, int, int, int]] = set()
    for item in raw_regions:
        if not isinstance(item, dict):
            continue
        try:
            x = max(0, int(float(item.get("x", 0))))
            y = max(0, int(float(item.get("y", 0))))
            width = max(1, int(float(item.get("width", 0))))
            height = max(1, int(float(item.get("height", 0))))
            confidence = float(item.get("confidence", 0.0) or 0.0)
        except Exception:
            continue

        if confidence < min_confidence:
            continue
        if width < 40 or height < 40:
            continue

        original_x = max(0, min(int(round(x * scale_x)), original_width - 1))
        original_y = max(0, min(int(round(y * scale_y)), original_height - 1))
        original_w = max(1, min(int(round(width * scale_x)), original_width - original_x))
        original_h = max(1, min(int(round(height * scale_y)), original_height - original_y))
        box_key = (original_x, original_y, original_w, original_h)
        if box_key in seen_boxes:
            continue
        seen_boxes.add(box_key)
        normalized.append(
            {
                "x": original_x,
                "y": original_y,
                "width": original_w,
                "height": original_h,
                "confidence": confidence,
                "reason": str(item.get("reason", "") or ""),
                "category": str(item.get("category", "detail") or "detail"),
                "source": "fullpage_llm",
            }
        )

    normalized.sort(key=lambda region: (region["y"], region["x"]))
    return normalized


def detect_detail_regions_from_full_page(
    full_page_image: Path,
    config: CaptureConfig,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    detection_meta: dict[str, Any] = {
        "used_llm": False,
        "llm_available": can_use_multimodal_detail_filter(),
        "errors": [],
        "review_image": None,
        "review_size": None,
    }
    if not config.enable_fullpage_region_llm or not detection_meta["llm_available"]:
        return [], detection_meta

    review_path = build_llm_review_image(full_page_image)
    detection_meta["review_image"] = str(review_path)
    with Image.open(full_page_image) as original_image:
        original_size = original_image.size
    with Image.open(review_path) as review_image:
        review_size = review_image.size
    detection_meta["review_size"] = {"width": review_size[0], "height": review_size[1]}

    prompt = (
        "你是电商商品页视觉定位助手。给你一张完整商品详情页截图，请识别其中真正属于“商品详情介绍图/详情图文模块”的区域。\n"
        "目标区域通常是：功能说明、参数说明、结构说明、材质说明、卖点图文、使用场景说明、长图详情模块。\n"
        "不要返回：主图、SKU 小图、店铺横幅、导航、评价、客服、店铺装修、页头页尾。\n"
        f"当前图片尺寸为 width={review_size[0]}, height={review_size[1]}。\n"
        "请返回 JSON，格式如下，不要解释：\n"
        '{"regions":[{"x":120,"y":900,"width":960,"height":420,"confidence":0.88,"category":"detail","reason":"参数说明图"}]}\n'
        "要求：\n"
        "1. x/y/width/height 使用当前这张输入图片的像素坐标。\n"
        "2. 只返回你有把握的详情图区域。\n"
        f"3. 最多返回 {config.detail_image_max_results} 个区域。\n"
        "4. 如果没有明确详情图，返回 {\"regions\":[]}。"
    )
    messages = [
        SystemMessage(content="你只输出合法 JSON。"),
        HumanMessage(
            content=[
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": image_file_to_data_url(review_path)}},
            ]
        ),
    ]
    try:
        raw_response = invoke_local_llm(messages=messages, model=config.vision_model, max_completion_tokens=1200)
        raw_regions = extract_json_value_from_text(raw_response)
        regions = normalize_fullpage_regions(
            raw_regions=raw_regions,
            review_size=review_size,
            original_size=original_size,
            min_confidence=config.detail_image_min_confidence,
        )
        detection_meta["used_llm"] = True
        detection_meta["region_count"] = len(regions)
        return regions, detection_meta
    except Exception as exc:
        detection_meta["errors"].append(str(exc)[:500])
        return [], detection_meta


def crop_detail_regions_from_full_page(
    full_page_image: Path,
    output_dir: Path,
    regions: list[dict[str, Any]],
    enable_ocr: bool,
    ocr_languages: str,
) -> list[dict[str, Any]]:
    screenshots_dir = output_dir / "screenshots"
    screenshots_dir.mkdir(parents=True, exist_ok=True)
    cropped: list[dict[str, Any]] = []
    with Image.open(full_page_image) as image:
        page_width, page_height = image.size
        for index, region in enumerate(regions, start=1):
            left = max(0, min(int(region["x"]), page_width - 1))
            top = max(0, min(int(region["y"]), page_height - 1))
            right = max(left + 1, min(left + int(region["width"]), page_width))
            bottom = max(top + 1, min(top + int(region["height"]), page_height))
            if right - left < 40 or bottom - top < 40:
                continue
            crop_path = screenshots_dir / f"detail-region-{index:02d}.png"
            image.crop((left, top, right, bottom)).save(crop_path)
            ocr_text = run_tesseract_ocr(crop_path, ocr_languages) if enable_ocr else None
            cropped.append(
                {
                    "kind": "fullpage_region",
                    "src": f"fullpage-region://{index}",
                    "crop_path": str(crop_path),
                    "left": left,
                    "top": top,
                    "width": right - left,
                    "height": bottom - top,
                    "ocr_text": ocr_text,
                    "detail_decision": {
                        "is_product_detail": True,
                        "confidence": float(region.get("confidence", 0.0) or 0.0),
                        "category": str(region.get("category", "detail") or "detail"),
                        "reason": str(region.get("reason", "") or ""),
                        "source": "fullpage_llm",
                    },
                }
            )
    return cropped


def crop_image_candidates(
    full_page_image: Path,
    output_dir: Path,
    candidates: list[dict[str, Any]],
    enable_ocr: bool,
    ocr_languages: str,
) -> list[dict[str, Any]]:
    screenshots_dir = output_dir / "screenshots"
    screenshots_dir.mkdir(parents=True, exist_ok=True)
    captured: list[dict[str, Any]] = []
    with Image.open(full_page_image) as image:
        page_width, page_height = image.size
        for index, candidate in enumerate(candidates, start=1):
            left = max(0, min(int(candidate["left"]), page_width))
            top = max(0, min(int(candidate["top"]), page_height))
            right = max(left + 1, min(left + int(candidate["width"]), page_width))
            bottom = max(top + 1, min(top + int(candidate["height"]), page_height))
            if right - left < 40 or bottom - top < 40:
                continue
            crop_path = screenshots_dir / f"image-{index:02d}.png"
            image.crop((left, top, right, bottom)).save(crop_path)
            ocr_text = run_tesseract_ocr(crop_path, ocr_languages) if enable_ocr else None
            captured.append(
                {
                    **candidate,
                    "crop_path": str(crop_path),
                    "ocr_text": ocr_text,
                }
            )
    return captured


def can_use_multimodal_detail_filter() -> bool:
    base_url = (
        os.getenv("COZE_INTEGRATION_MODEL_BASE_URL")
        or os.getenv("LOCAL_LLM_BASE_URL")
        or os.getenv("OPENAI_BASE_URL")
        or ""
    ).strip()
    api_key = (
        os.getenv("COZE_WORKLOAD_IDENTITY_API_KEY")
        or os.getenv("LOCAL_LLM_API_KEY")
        or os.getenv("OPENAI_API_KEY")
        or ""
    ).strip()
    return bool(base_url and api_key)


def classify_detail_image_with_llm(candidate: dict[str, Any], config: CaptureConfig) -> dict[str, Any]:
    crop_path = Path(str(candidate.get("crop_path", "")))
    if not crop_path.exists():
        return {
            "is_product_detail": False,
            "confidence": 0.0,
            "category": "missing",
            "reason": "候选图片文件不存在",
            "source": "llm",
        }

    review_path = build_llm_review_image(crop_path)
    prompt = (
        "你是电商商品页图片分类助手。请判断这张图是否属于“商品详情介绍图/商品详情页说明图”。\n"
        "商品详情图通常是用于展示功能、参数、结构、材质、使用场景、卖点说明的图文介绍。\n"
        "以下类型不要判为详情图：主图、SKU 小图、缩略图、店铺横幅、广告横幅、导航、评价截图、客服区域、店铺装修背景。\n"
        "只返回 JSON，不要解释，不要 markdown。格式如下：\n"
        '{"is_product_detail": true, "confidence": 0.86, "category": "detail", "reason": "..." }\n'
        f"补充上下文：DOM class/id/selector = {candidate.get('class_name', '')} | {candidate.get('id', '')} | {candidate.get('selector', '')}；"
        f"位置 top={candidate.get('top', 0)} width={candidate.get('width', 0)} height={candidate.get('height', 0)}。"
    )
    messages = [
        SystemMessage(content="你只输出合法 JSON。"),
        HumanMessage(
            content=[
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": image_file_to_data_url(review_path)}},
            ]
        ),
    ]
    raw_response = invoke_local_llm(messages=messages, model=config.vision_model, max_completion_tokens=800)
    parsed = extract_json_from_text(raw_response)
    if not parsed:
        raise RuntimeError(f"多模态返回无法解析: {raw_response[:300]}")
    return {
        "is_product_detail": bool(parsed.get("is_product_detail")),
        "confidence": float(parsed.get("confidence", 0.0) or 0.0),
        "category": str(parsed.get("category", "") or ""),
        "reason": str(parsed.get("reason", "") or ""),
        "source": "llm",
    }


def choose_detail_images(
    image_candidates: list[dict[str, Any]],
    detail_section_candidates: list[dict[str, Any]],
    config: CaptureConfig,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    selection_meta: dict[str, Any] = {
        "used_llm": False,
        "llm_available": can_use_multimodal_detail_filter(),
        "candidate_count": 0,
        "selected_count": 0,
        "errors": [],
    }
    ranked_candidates = select_detail_image_candidates(
        image_candidates + detail_section_candidates,
        limit=config.detail_image_candidate_limit,
    )
    selection_meta["candidate_count"] = len(ranked_candidates)
    selected: list[dict[str, Any]] = []

    for candidate in ranked_candidates:
        decision = infer_detail_type_by_heuristic(candidate)
        if config.enable_detail_image_llm_filter and selection_meta["llm_available"]:
            try:
                decision = classify_detail_image_with_llm(candidate, config)
                selection_meta["used_llm"] = True
            except Exception as exc:
                selection_meta["errors"].append(str(exc)[:300])

        enriched = dict(candidate)
        enriched["detail_decision"] = decision
        is_detail = bool(decision.get("is_product_detail"))
        confidence = float(decision.get("confidence", 0.0) or 0.0)
        if is_detail and confidence >= config.detail_image_min_confidence:
            selected.append(enriched)
        elif decision.get("source") == "heuristic" and is_detail:
            selected.append(enriched)

    if not selected:
        for candidate in ranked_candidates[: config.detail_image_max_results]:
            enriched = dict(candidate)
            enriched["detail_decision"] = infer_detail_type_by_heuristic(candidate)
            if enriched["detail_decision"]["is_product_detail"]:
                selected.append(enriched)

    selected.sort(key=lambda item: int(item.get("top", 0)))
    selected = selected[: config.detail_image_max_results]
    selection_meta["selected_count"] = len(selected)
    return selected, selection_meta


def infer_page_name(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme == "file":
        return sanitize_filename(Path(parsed.path).stem, fallback="local-page")
    return sanitize_filename(parsed.netloc or parsed.path or "product-page", fallback="product-page")


def default_user_data_dir(config: CaptureConfig) -> Path:
    host = urlparse(config.url).netloc or "local-page"
    return (config.output_dir / ".." / ".playwright-user-data" / sanitize_filename(host)).resolve()


def build_manual_login_notes(config: CaptureConfig) -> dict[str, Any]:
    return {
        "enabled": config.manual_login,
        "user_data_dir": str(config.user_data_dir) if config.user_data_dir else None,
        "wait_sec": config.manual_login_wait_sec,
    }


async def open_browser(config: CaptureConfig) -> tuple[Any, BrowserContext]:
    playwright = await async_playwright().start()
    if config.user_data_dir:
        context = await playwright.chromium.launch_persistent_context(
            user_data_dir=str(config.user_data_dir),
            headless=config.headless,
            user_agent=DEFAULT_USER_AGENT,
            locale=config.locale,
            timezone_id=config.timezone_id,
            viewport={"width": config.viewport_width, "height": config.viewport_height},
            device_scale_factor=1,
        )
        return playwright, context

    browser = await playwright.chromium.launch(headless=config.headless)
    context = await browser.new_context(
        user_agent=DEFAULT_USER_AGENT,
        locale=config.locale,
        timezone_id=config.timezone_id,
        viewport={"width": config.viewport_width, "height": config.viewport_height},
        device_scale_factor=1,
    )
    return playwright, context


async def collect_page_signals(page: Page) -> dict[str, Any]:
    title = ""
    body_text = ""
    try:
        title = await page.title()
    except Exception:
        title = ""
    try:
        body_text = await page.evaluate(
            "() => (document.body && (document.body.innerText || document.body.textContent) || '').slice(0, 3000)"
        )
    except Exception:
        body_text = ""

    combined = f"{page.url}\n{title}\n{body_text}".lower()
    verification_keywords = [
        "登录",
        "验证码",
        "验证",
        "滑块",
        "安全验证",
        "请完成验证",
        "sign in",
        "login",
        "captcha",
        "verify",
        "security check",
    ]
    matched_keywords = [keyword for keyword in verification_keywords if keyword.lower() in combined]
    return {
        "url": page.url,
        "title": title,
        "matched_keywords": matched_keywords,
        "is_login_or_verification_page": bool(matched_keywords),
    }


async def wait_for_manual_login(page: Page, config: CaptureConfig, reason: str) -> dict[str, Any]:
    notes = build_manual_login_notes(config)
    print("")
    print("[manual-login] 已进入人工登录/验证模式")
    print(f"[manual-login] 原因: {reason}")
    print(f"[manual-login] 当前页面: {page.url}")
    if notes["user_data_dir"]:
        print(f"[manual-login] 登录态目录: {notes['user_data_dir']}")
    print("[manual-login] 请在已打开的浏览器里完成登录、滑块或验证，完成后回到终端。")

    if sys.stdin.isatty():
        await asyncio.to_thread(input, "[manual-login] 完成后按 Enter 继续抓取...")
    else:
        wait_sec = max(1, int(config.manual_login_wait_sec))
        print(f"[manual-login] 当前非交互终端，将等待 {wait_sec} 秒后继续。")
        await asyncio.sleep(wait_sec)

    await page.wait_for_timeout(1200)
    return await collect_page_signals(page)


async def capture_product_page(config: CaptureConfig) -> dict[str, Any]:
    config.output_dir.mkdir(parents=True, exist_ok=True)
    screenshots_dir = config.output_dir / "screenshots"
    screenshots_dir.mkdir(parents=True, exist_ok=True)

    if config.manual_login and config.user_data_dir is None:
        config.user_data_dir = default_user_data_dir(config)
    if config.manual_login:
        config.headless = False

    playwright, context = await open_browser(config)
    try:
        existing_pages = context.pages
        page = existing_pages[0] if existing_pages else await context.new_page()
        await page.goto(config.url, wait_until="domcontentloaded", timeout=config.timeout_ms)
        page_signals = await collect_page_signals(page)
        manual_login_used = False
        if config.manual_login or page_signals["is_login_or_verification_page"]:
            reason = "检测到疑似登录/验证页面" if page_signals["is_login_or_verification_page"] else "命令行要求人工登录"
            page_signals = await wait_for_manual_login(page, config, reason)
            manual_login_used = True

        ready_state = await wait_for_page_ready(page, config)
        text_blocks = await extract_text_blocks(page)
        image_candidates = await extract_image_candidates(
            page,
            limit=max(config.max_image_crops, config.detail_image_candidate_limit),
        )
        detail_section_candidates = await capture_detail_section_candidates(page, config.output_dir)
        page_title = await page.title()
        page_url = page.url

        viewport_path = screenshots_dir / "viewport.png"
        full_page_path = screenshots_dir / "full-page.png"
        await page.screenshot(path=str(viewport_path), full_page=False)
        await page.screenshot(path=str(full_page_path), full_page=True)
        text_path = save_text_snapshot(config.output_dir, text_blocks)
        image_crops = crop_image_candidates(
            full_page_image=full_page_path,
            output_dir=config.output_dir,
            candidates=image_candidates,
            enable_ocr=config.enable_ocr,
            ocr_languages=config.ocr_languages,
        )
        fullpage_regions, fullpage_region_detection = detect_detail_regions_from_full_page(
            full_page_image=full_page_path,
            config=config,
        )
        if fullpage_regions:
            detail_images = crop_detail_regions_from_full_page(
                full_page_image=full_page_path,
                output_dir=config.output_dir,
                regions=fullpage_regions,
                enable_ocr=config.enable_ocr,
                ocr_languages=config.ocr_languages,
            )
            detail_image_selection = {
                "strategy": "fullpage_region_llm",
                "used_llm": fullpage_region_detection.get("used_llm", False),
                "llm_available": fullpage_region_detection.get("llm_available", False),
                "candidate_count": len(fullpage_regions),
                "selected_count": len(detail_images),
                "errors": fullpage_region_detection.get("errors", []),
            }
        else:
            detail_images, detail_image_selection = choose_detail_images(
                image_candidates=image_crops,
                detail_section_candidates=detail_section_candidates,
                config=config,
            )
            detail_image_selection["strategy"] = "candidate_images"

        result = {
            "page": {
                "url": page_url,
                "title": page_title,
                "page_name": infer_page_name(page_url),
            },
            "ready_state": ready_state,
            "screenshots": {
                "viewport": str(viewport_path),
                "full_page": str(full_page_path),
                "image_crops": [item["crop_path"] for item in image_crops],
                "detail_image_crops": [item["crop_path"] for item in detail_images if item.get("crop_path")],
            },
            "text": {
                "visible_text_path": str(text_path),
                "blocks": text_blocks,
            },
            "images": detail_images,
            "image_candidates": image_crops,
            "detail_image_selection": detail_image_selection,
            "fullpage_region_detection": fullpage_region_detection,
            "ocr": {
                "enabled": config.enable_ocr,
                "engine": "tesseract" if shutil.which("tesseract") else None,
                "languages": config.ocr_languages,
            },
            "manual_login": {
                **build_manual_login_notes(config),
                "used": manual_login_used,
                "page_signals": page_signals,
            },
        }
        result_path = config.output_dir / "capture-result.json"
        result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        return result
    finally:
        await context.close()
        await playwright.stop()


def parse_args(argv: list[str]) -> CaptureConfig:
    parser = argparse.ArgumentParser(description="Capture product page screenshots and visible text.")
    parser.add_argument("--url", required=True, help="Target product page URL or file:// page")
    parser.add_argument("--output-dir", required=True, help="Directory for screenshots and JSON output")
    parser.add_argument(
        "--wait-selector",
        action="append",
        default=[],
        help="Selector to help determine page readiness. Can be used multiple times.",
    )
    parser.add_argument("--timeout-sec", type=int, default=30, help="Page load timeout in seconds")
    parser.add_argument("--max-image-crops", type=int, default=8, help="Maximum number of image crops")
    parser.add_argument("--ocr", dest="enable_ocr", action="store_true", help="Enable tesseract OCR")
    parser.add_argument("--no-ocr", dest="enable_ocr", action="store_false", help="Disable OCR")
    parser.add_argument("--headful", action="store_true", help="Run browser with UI")
    parser.add_argument(
        "--manual-login",
        action="store_true",
        help="Open a persistent browser profile and wait for you to complete login/verification manually.",
    )
    parser.add_argument(
        "--user-data-dir",
        help="Persistent Chromium profile directory. Reuse the same path to keep login state.",
    )
    parser.add_argument(
        "--manual-login-wait-sec",
        type=int,
        default=300,
        help="Fallback wait time when manual login runs in a non-interactive terminal.",
    )
    parser.add_argument(
        "--no-detail-llm",
        dest="enable_detail_image_llm_filter",
        action="store_false",
        help="Disable multimodal filtering for product detail images and only use heuristics.",
    )
    parser.add_argument(
        "--detail-image-candidate-limit",
        type=int,
        default=12,
        help="Maximum number of candidate image blocks to review for product detail detection.",
    )
    parser.add_argument(
        "--detail-image-max-results",
        type=int,
        default=6,
        help="Maximum number of final product detail images to keep.",
    )
    parser.add_argument(
        "--detail-image-min-confidence",
        type=float,
        default=0.55,
        help="Minimum multimodal confidence required to keep an image as product detail.",
    )
    parser.add_argument(
        "--vision-model",
        default="glm-4.6v",
        help="Vision model alias. Defaults to the locally configured Zhipu-compatible vision model.",
    )
    parser.add_argument(
        "--no-fullpage-region-llm",
        dest="enable_fullpage_region_llm",
        action="store_false",
        help="Disable full-page screenshot region detection and only use candidate image filtering.",
    )
    parser.set_defaults(enable_ocr=True)
    args = parser.parse_args(argv)
    return CaptureConfig(
        url=args.url,
        output_dir=Path(args.output_dir).resolve(),
        timeout_ms=max(1, args.timeout_sec) * 1000,
        wait_selectors=list(DEFAULT_READY_SELECTORS) + list(args.wait_selector),
        headless=not args.headful,
        enable_ocr=bool(args.enable_ocr),
        max_image_crops=max(1, args.max_image_crops),
        manual_login=bool(args.manual_login),
        user_data_dir=Path(args.user_data_dir).resolve() if args.user_data_dir else None,
        manual_login_wait_sec=max(1, int(args.manual_login_wait_sec)),
        enable_detail_image_llm_filter=bool(args.enable_detail_image_llm_filter),
        detail_image_candidate_limit=max(1, int(args.detail_image_candidate_limit)),
        detail_image_max_results=max(1, int(args.detail_image_max_results)),
        detail_image_min_confidence=max(0.0, min(1.0, float(args.detail_image_min_confidence))),
        vision_model=str(args.vision_model or "glm-4.6v").strip() or "glm-4.6v",
        enable_fullpage_region_llm=bool(args.enable_fullpage_region_llm),
    )


def main(argv: list[str] | None = None) -> int:
    config = parse_args(argv or sys.argv[1:])
    result = asyncio.run(capture_product_page(config))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


bootstrap_local_env()


if __name__ == "__main__":
    raise SystemExit(main())
