from __future__ import annotations

from pathlib import Path

import pytest

from src.tools.product_page_capture import (
    CaptureConfig,
    capture_product_page,
    choose_detail_images,
    collect_page_signals,
    default_user_data_dir,
    extract_json_from_text,
    normalize_fullpage_regions,
    normalize_image_candidates,
    normalize_text_blocks,
)


def test_normalize_text_blocks_prefers_prominent_content() -> None:
    blocks = normalize_text_blocks(
        [
            {
                "text": "智能阻力训练器 Pro Max",
                "tag_name": "h1",
                "class_name": "product-title",
                "top": 120,
                "left": 50,
                "width": 500,
                "height": 80,
                "text_length": 16,
            },
            {
                "text": "活动价: ¥ 1,699",
                "tag_name": "p",
                "class_name": "price-box",
                "top": 260,
                "left": 50,
                "width": 220,
                "height": 40,
                "text_length": 13,
            },
            {
                "text": "活动价: ¥ 1,699",
                "tag_name": "span",
                "class_name": "",
                "top": 261,
                "left": 50,
                "width": 220,
                "height": 40,
                "text_length": 13,
            },
        ]
    )
    assert blocks[0]["text"] == "智能阻力训练器 Pro Max"
    assert len(blocks) == 2


def test_normalize_image_candidates_filters_small_and_duplicate_images() -> None:
    candidates = normalize_image_candidates(
        [
            {"src": "https://example.com/a.png", "width": 500, "height": 500, "top": 120, "left": 30},
            {"src": "https://example.com/a.png", "width": 510, "height": 500, "top": 130, "left": 30},
            {"src": "https://example.com/b.png", "width": 40, "height": 40, "top": 200, "left": 30},
            {"src": "https://example.com/c.png", "width": 260, "height": 180, "top": 900, "left": 30},
        ],
        limit=5,
    )
    assert [item["src"] for item in candidates] == [
        "https://example.com/a.png",
        "https://example.com/c.png",
    ]


def test_default_user_data_dir_uses_host_name(tmp_path: Path) -> None:
    config = CaptureConfig(
        url="https://detail.1688.com/offer/930711203351.html",
        output_dir=tmp_path / "capture-output",
        enable_ocr=False,
    )
    user_data_dir = default_user_data_dir(config)
    assert user_data_dir.name == "detail.1688.com"


def test_extract_json_from_text_handles_code_fence() -> None:
    text = '```json\n{"is_product_detail": true, "confidence": 0.9}\n```'
    parsed = extract_json_from_text(text)
    assert parsed["is_product_detail"] is True
    assert parsed["confidence"] == 0.9


def test_normalize_fullpage_regions_scales_review_coordinates() -> None:
    regions = normalize_fullpage_regions(
        raw_regions={
            "regions": [
                {
                    "x": 100,
                    "y": 200,
                    "width": 300,
                    "height": 400,
                    "confidence": 0.88,
                    "reason": "参数说明图",
                }
            ]
        },
        review_size=(1000, 2000),
        original_size=(2000, 4000),
        min_confidence=0.5,
    )
    assert regions == [
        {
            "x": 200,
            "y": 400,
            "width": 600,
            "height": 800,
            "confidence": 0.88,
            "reason": "参数说明图",
            "category": "detail",
            "source": "fullpage_llm",
        }
    ]


@pytest.mark.asyncio
async def test_choose_detail_images_prefers_llm_marked_detail(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    crop_main = tmp_path / "main.png"
    crop_detail = tmp_path / "detail.png"
    crop_main.write_bytes(b"main")
    crop_detail.write_bytes(b"detail")
    config = CaptureConfig(
        url="https://detail.1688.com/offer/930711203351.html",
        output_dir=tmp_path / "capture-output",
        enable_ocr=False,
    )
    image_candidates = [
        {
            "src": "main",
            "crop_path": str(crop_main),
            "width": 600,
            "height": 600,
            "top": 120,
            "left": 0,
            "class_name": "gallery-main",
            "id": "",
        },
        {
            "src": "detail",
            "crop_path": str(crop_detail),
            "width": 790,
            "height": 1200,
            "top": 1680,
            "left": 0,
            "class_name": "detail-image",
            "id": "",
        },
    ]

    monkeypatch.setattr(
        "src.tools.product_page_capture.can_use_multimodal_detail_filter",
        lambda: True,
    )

    def fake_classify(candidate: dict, _: CaptureConfig) -> dict:
        return {
            "is_product_detail": candidate["src"] == "detail",
            "confidence": 0.92 if candidate["src"] == "detail" else 0.12,
            "category": "detail" if candidate["src"] == "detail" else "main",
            "reason": "mock",
            "source": "llm",
        }

    monkeypatch.setattr("src.tools.product_page_capture.classify_detail_image_with_llm", fake_classify)
    selected, meta = choose_detail_images(image_candidates=image_candidates, detail_section_candidates=[], config=config)
    assert meta["used_llm"] is True
    assert [item["src"] for item in selected] == ["detail"]


@pytest.mark.asyncio
async def test_collect_page_signals_detects_login_keywords(tmp_path: Path) -> None:
    fixture = tmp_path / "login.html"
    fixture.write_text(
        "<html><head><title>安全验证</title></head><body>请先完成验证码验证</body></html>",
        encoding="utf-8",
    )
    config = CaptureConfig(
        url=fixture.as_uri(),
        output_dir=tmp_path / "capture-output",
        enable_ocr=False,
    )
    try:
        from playwright.async_api import async_playwright

        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            context = await browser.new_context()
            page = await context.new_page()
            await page.goto(config.url)
            signals = await collect_page_signals(page)
            await context.close()
            await browser.close()
    except Exception as exc:  # pragma: no cover
        message = str(exc).lower()
        if "executable doesn't exist" in message or "please run the following command" in message:
            pytest.skip("Playwright Chromium browser is not installed in this environment.")
        raise

    assert signals["is_login_or_verification_page"] is True
    assert "验证" in signals["matched_keywords"]


@pytest.mark.asyncio
async def test_capture_product_page_fixture_smoke(tmp_path: Path) -> None:
    fixture = (
        Path(__file__).resolve().parents[1] / "src" / "static" / "product_capture_fixture.html"
    ).resolve()
    config = CaptureConfig(
        url=fixture.as_uri(),
        output_dir=tmp_path / "capture-output",
        enable_ocr=False,
        max_image_crops=4,
    )
    try:
        result = await capture_product_page(config)
    except Exception as exc:  # pragma: no cover
        message = str(exc).lower()
        if "executable doesn't exist" in message or "please run the following command" in message:
            pytest.skip("Playwright Chromium browser is not installed in this environment.")
        raise

    assert result["page"]["title"] == "商品页抓取原型测试页"
    assert result["ready_state"]["matched_selector"] is not None
    assert result["screenshots"]["full_page"].endswith("full-page.png")
    assert len(result["text"]["blocks"]) >= 4
    assert any("活动价" in block["text"] for block in result["text"]["blocks"])
    assert "detail_image_selection" in result
    assert "fullpage_region_detection" in result
