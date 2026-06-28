import sys
import builtins
import asyncio
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from src.graphs.nodes.get_keywords_node import get_keywords_node
from src.graphs.nodes.secondary_enrichment_node import (
    _classify_supplement_evidence,
    _extract_accepted_text,
    _extract_search_items,
    _generic_search_supplement,
    _image_vision_supplement,
    _merge_product_with_enrichment,
    _normalize_picture_urls,
    _same_product,
)
from src.graphs.state import GetKeywordsInput


def test_get_keywords_empty_list_does_not_fall_back_to_database(monkeypatch) -> None:
    real_import = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        if name.startswith("storage.database"):
            raise AssertionError("empty input_keywords must not read database keywords")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)

    output = get_keywords_node(
        GetKeywordsInput(patent_record_id=91, analysis_session_id="analysis_test", input_keywords=[]),
        config=None,
        runtime=None,
    )

    assert output.keywords == []
    assert output.error_message == ""


def test_get_keywords_explicit_list_is_trimmed_and_deduplicated() -> None:
    output = get_keywords_node(
        GetKeywordsInput(
            patent_record_id=91,
            analysis_session_id="analysis_test",
            input_keywords=[" 扫地机器人 ", "", "拖地", "扫地机器人"],
        ),
        config=None,
        runtime=None,
    )

    assert output.keywords == ["扫地机器人", "拖地"]
    assert output.error_message == ""


def test_same_product_accepts_exact_url() -> None:
    original = {
        "product_name": "普森斯中扫升降扫地机器人",
        "product_url": "https://example.com/item/123?spm=abc",
    }
    candidate = {
        "title": "其他页面标题",
        "product_url": "https://example.com/item/123",
    }

    accepted, reason, score = _same_product(original, candidate)

    assert accepted is True
    assert reason == "url_exact"
    assert score == 1.0


def test_same_product_rejects_unrelated_product() -> None:
    original = {
        "product_name": "普森斯中扫升降扫地机器人",
        "product_url": "https://example.com/item/123",
    }
    candidate = {
        "title": "空气炸锅家用大容量",
        "product_url": "https://another.example.com/item/999",
    }

    accepted, reason, score = _same_product(original, candidate)

    assert accepted is False
    assert reason == "identity_mismatch"
    assert score < 0.72


def test_same_product_accepts_product_id_inside_article_url() -> None:
    original = {
        "product_name": "某品牌 P10 Pro 扫地机器人",
        "product_id": "offer-123456789",
        "product_url": "https://detail.1688.com/offer/123456789.html",
    }
    candidate = {
        "title": "P10 Pro 扫地机器人拆机图文",
        "url": "https://post.example.com/teardown/offer-123456789",
    }

    accepted, reason, score = _same_product(original, candidate)

    assert accepted is True
    assert reason == "product_id_in_candidate"
    assert score == 0.98


def test_same_product_accepts_brand_model_video_title() -> None:
    original = {
        "product_name": "普森斯 P10 Pro 中扫升降扫地机器人",
        "brand": "普森斯",
        "product_url": "https://detail.example.com/item/1",
    }
    candidate = {
        "title": "普森斯 P10 Pro 拆机视频：底部结构与拖布支架",
        "url": "https://www.bilibili.com/video/BV123",
    }

    accepted, reason, score = _same_product(original, candidate)

    assert accepted is True
    assert reason == "brand_model_match"
    assert score == 0.92


def test_same_product_rejects_same_brand_different_model() -> None:
    original = {
        "product_name": "普森斯 P10 Pro 中扫升降扫地机器人",
        "brand": "普森斯",
    }
    candidate = {
        "title": "普森斯 X20 扫地机器人评测",
        "url": "https://post.example.com/x20-review",
    }

    accepted, reason, score = _same_product(original, candidate)

    assert accepted is False
    assert reason == "identity_mismatch"
    assert score < 0.72


def test_merge_product_with_enrichment_keeps_same_product_supplement() -> None:
    product = {
        "product_name": "普森斯中扫升降扫地机器人",
        "product_url": "https://example.com/item/123",
        "description": "首次检索描述：支持扫地。",
        "raw_payload": {"source": "first_search"},
    }
    enrichment = {
        "version": 1,
        "accepted_sources_count": 1,
        "supplement_text": "详情页补充：拖布组件可升降，支持拖地模式。",
        "sources": [
            {
                "source_type": "product_page_http",
                "url": "https://example.com/item/123",
                "identity_check": {
                    "accepted": True,
                    "reason": "url_exact",
                    "score": 1.0,
                },
            }
        ],
    }

    merged = _merge_product_with_enrichment(product, enrichment)

    assert "首次检索描述" in merged["description"]
    assert "拖布组件可升降" in merged["description"]
    assert merged["raw_payload"]["source"] == "first_search"
    assert merged["raw_payload"]["secondary_enrichment"]["accepted_sources_count"] == 1


def test_merge_product_with_enrichment_preserves_existing_success_when_new_run_empty() -> None:
    product = {
        "product_name": "普森斯中扫升降扫地机器人",
        "description": "首次检索描述。",
        "raw_payload": {
            "secondary_enrichment": {
                "accepted_sources_count": 1,
                "supplement_text": "已有 OCR 补充资料。",
                "sources": [{"source_type": "first_search_image_ocr", "accepted": True}],
            }
        },
    }
    empty_enrichment = {
        "accepted_sources_count": 0,
        "supplement_text": "",
        "sources": [{"source_type": "direct_web_search", "accepted": []}],
    }

    merged = _merge_product_with_enrichment(product, empty_enrichment)

    assert merged["raw_payload"]["secondary_enrichment"]["accepted_sources_count"] == 1
    assert "已有 OCR 补充资料" in merged["description"]


def test_extract_accepted_text_includes_generic_search_only_when_accepted() -> None:
    enrichment = {
        "sources": [
            {
                "source_type": "generic_search_supplement",
                "accepted": [
                    {
                        "title": "拆机评测",
                        "description": "拆机文章显示该扫地机器人包含可升降拖布组件。",
                    }
                ],
                "rejected": [
                    {
                        "title": "其他商品",
                        "description": "空气炸锅拆机文章。",
                    }
                ],
            }
        ]
    }

    text = _extract_accepted_text(enrichment)

    assert "可升降拖布组件" in text
    assert "空气炸锅" not in text


def test_extract_accepted_text_includes_direct_web_search() -> None:
    enrichment = {
        "sources": [
            {
                "source_type": "direct_web_search",
                "accepted": [
                    {
                        "title": "商品拆机视频",
                        "description": "视频摘要显示该型号底部有拖布支架和吸尘口。",
                        "evidence_type": "video",
                    }
                ],
            }
        ]
    }

    text = _extract_accepted_text(enrichment)

    assert "网页搜索/video" in text
    assert "拖布支架" in text


def test_classify_supplement_evidence_video_and_article() -> None:
    assert _classify_supplement_evidence("扫地机器人拆机视频", "https://www.bilibili.com/video/abc") == "video"
    assert _classify_supplement_evidence("扫地机器人深度评测文章", "https://post.example.com/1") == "article"
    assert _classify_supplement_evidence("1688商品详情", "https://detail.1688.com/offer/1.html") == "product_page"


def test_extract_accepted_text_includes_first_search_image_vision() -> None:
    enrichment = {
        "sources": [
            {
                "source_type": "first_search_image_vision",
                "accepted": True,
                "text": "图片显示商品包含圆形机身、底部拖布组件和升降结构示意。",
            }
        ]
    }

    text = _extract_accepted_text(enrichment)

    assert "商品图片视觉读取" in text
    assert "拖布组件" in text


def test_extract_accepted_text_includes_first_search_image_ocr() -> None:
    enrichment = {
        "sources": [
            {
                "source_type": "first_search_image_ocr",
                "accepted": True,
                "text": "图中文字：扫拖吸三合一，可升降拖布。",
            }
        ]
    }

    text = _extract_accepted_text(enrichment)

    assert "商品图片OCR" in text
    assert "可升降拖布" in text


def test_normalize_picture_urls_accepts_strings_and_dicts() -> None:
    product = {
        "picture": [
            "https://example.com/a.jpg",
            {"url": "https://example.com/b.jpg"},
            {"src": "https://example.com/c.jpg"},
            "not-a-url",
            "https://example.com/a.jpg",
        ]
    }

    assert _normalize_picture_urls(product)[:3] == [
        "https://example.com/a.jpg",
        "https://example.com/b.jpg",
        "https://example.com/c.jpg",
    ]


def test_extract_search_items_combines_nested_video_article_and_organic_results() -> None:
    payload = {
        "data": {
            "organic": [
                {"title": "商品详情", "url": "https://example.com/detail"},
            ],
            "videos": [
                {"title": "拆机视频", "url": "https://video.example.com/1"},
            ],
            "articles": [
                {"title": "拆机文章", "url": "https://post.example.com/1"},
            ],
        }
    }

    items = _extract_search_items(payload)

    assert [item["title"] for item in items] == ["商品详情", "拆机视频", "拆机文章"]


def test_generic_search_accepts_same_model_video_and_rejects_other_model(monkeypatch) -> None:
    class FakeResponse:
        def raise_for_status(self) -> None:
            return

        def json(self) -> dict:
            return {
                "data": {
                    "videos": [
                        {
                            "title": "普森斯 P10 Pro 拆机视频：底部拖布支架结构",
                            "url": "https://www.bilibili.com/video/BV123",
                            "description": "视频展示该型号底部有拖布组件和升降机构。",
                        }
                    ],
                    "articles": [
                        {
                            "title": "普森斯 X20 扫地机器人评测",
                            "url": "https://post.example.com/x20-review",
                            "description": "另一型号的拆机文章。",
                        }
                    ],
                }
            }

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs) -> None:
            return

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args) -> None:
            return

        async def get(self, *args, **kwargs):
            return FakeResponse()

    monkeypatch.setattr(
        "src.graphs.nodes.secondary_enrichment_node.SECONDARY_SEARCH_API_URL",
        "http://secondary-search.test/search",
    )
    monkeypatch.setattr("src.graphs.nodes.secondary_enrichment_node.httpx.AsyncClient", FakeAsyncClient)

    product = {
        "product_name": "普森斯 P10 Pro 中扫升降扫地机器人",
        "brand": "普森斯",
    }
    result = asyncio.run(_generic_search_supplement(product))

    assert result["enabled"] is True
    assert len(result["accepted"]) == 1
    assert result["accepted"][0]["evidence_type"] == "video"
    assert "拖布组件" in result["accepted"][0]["description"]
    assert any(item["title"] == "普森斯 X20 扫地机器人评测" for item in result["rejected"])


def test_image_vision_supplement_uses_first_search_images(monkeypatch) -> None:
    monkeypatch.setattr("tools.product_page_capture.invoke_local_llm", lambda **_: "图片显示扫拖吸三合一。")
    product = {
        "product_name": "智能扫地机器人",
        "picture": ["https://example.com/a.jpg"],
    }

    result = _image_vision_supplement(product)

    assert result["source_type"] == "first_search_image_vision"
    assert result["accepted"] is True
    assert "扫拖吸三合一" in result["text"]
