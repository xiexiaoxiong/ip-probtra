from __future__ import annotations

import pytest

from invalidity.llm import MultimodalModelError, _extract_json


def test_extract_json_repairs_missing_commas_without_changing_values() -> None:
    content = """```json
    {
      "summary": "阀杆沿轴线移动"
      "disclosures": [
        {"feature_id": "f-1", "status": "direct_and_unambiguous"}
        {"feature_id": "f-2", "status": "not_disclosed"}
      ]
    }
    ```"""

    assert _extract_json(content) == {
        "summary": "阀杆沿轴线移动",
        "disclosures": [
            {"feature_id": "f-1", "status": "direct_and_unambiguous"},
            {"feature_id": "f-2", "status": "not_disclosed"},
        ],
    }


def test_extract_json_repairs_trailing_commas_only_at_closing_delimiters() -> None:
    assert _extract_json('{"items": [{"value": 1,},],}') == {
        "items": [{"value": 1}]
    }


def test_extract_json_does_not_guess_missing_values_or_nesting() -> None:
    with pytest.raises(MultimodalModelError, match="模型 JSON 无法解析"):
        _extract_json('{"feature_id":, "status": "not_disclosed"}')
