from pathlib import Path
from fastapi.testclient import TestClient

from invalidity.config import ParserSettings
from invalidity.parser_service import _metadata, create_parser_app, parse_source
from test_api_worker import PARSER_TOKEN


PARSER_AUTH_HEADERS = {"Authorization": f"Bearer {PARSER_TOKEN}"}


def parser_settings(tmp_path: Path) -> ParserSettings:
    return ParserSettings(
        environment="test",
        parser_port=5201,
        parser_api_token=PARSER_TOKEN,
        artifact_root=tmp_path / "invalidity" / "test",
        allowed_source_roots=(tmp_path.resolve(),),
    )


def test_isolated_parser_extracts_claims_and_never_writes_shared_record(tmp_path: Path) -> None:
    source = tmp_path / "CN123456789U.txt"
    source.write_text(
        "\n".join([
            "(54)实用新型名称 测试装置",
            "(22)申请日 2020.01.02",
            "(45)授权公告日 2021.03.04",
            "权利要求书",
            "1. 一种测试装置，包括主体、第一部件和第二部件。",
            "2. 根据权利要求1所述的测试装置，其特征在于，还包括第三部件。",
            "技术领域",
            "本实用新型涉及测试设备领域。",
            "背景技术",
            "现有测试设备包括主体和部件。",
            "实用新型内容",
            "本实用新型提供一种测试装置。",
            "附图说明",
            "图1为测试装置结构图。",
            "具体实施方式",
            "以下结合附图说明具体结构。",
        ]),
        encoding="utf-8",
    )
    application = create_parser_app(parser_settings(tmp_path))
    with TestClient(application, headers=PARSER_AUTH_HEADERS) as client:
        response = client.post(
            "/run",
            json={"patent_file": {"url": str(source), "file_type": "image"}, "task_id": "parser-test"},
        )
    assert response.status_code == 200
    data = response.json()
    assert [item["claim_id"] for item in data["claims"]] == ["1", "2"]
    assert data["db_record_id"] is None
    assert data["metadata"]["patent_number"] == "CN123456789U"
    assert data["metadata"]["application_date"] == "2020-01-02"
    assert data["metadata"]["grant_date"] == "2021-03-04"
    assert data["source_format"] == "txt"
    assert data["source_byte_size"] == source.stat().st_size
    assert data["page_count"] == 1
    assert data["parser_version"] == "invalidity-isolated-neutral-parser-v2"
    assert data["claims"][1]["parent_claim_ids"] == ["1"]
    assert list(data["specification"]) == [
        "技术领域",
        "背景技术",
        "实用新型内容",
        "附图说明",
        "具体实施方式",
        "全文",
    ]
    assert data["page_texts"][0]["page_role"] == "cover_and_abstract"
    assert "技术领域" not in data["claims"][1]["claim_text"]


def test_parser_health_is_public_but_parse_routes_require_bearer(tmp_path: Path) -> None:
    application = create_parser_app(parser_settings(tmp_path))
    with TestClient(application) as client:
        assert client.get("/health").status_code == 200
        response = client.post(
            "/run",
            json={"patent_file": {"url": str(tmp_path / "missing.pdf")}, "task_id": "auth"},
        )
        assert response.status_code == 401
        assert response.json()["code"] == "AUTHENTICATION_REQUIRED"


def test_parse_source_rejects_missing_claims(tmp_path: Path) -> None:
    source = tmp_path / "empty.txt"
    source.write_text("仅有说明书，没有权利要求书页面。", encoding="utf-8")
    try:
        parse_source(str(source))
    except ValueError as exc:
        assert "权利要求" in str(exc)
    else:
        raise AssertionError("缺少权利要求时不应伪造成功")


def test_reference_parse_keeps_readable_text_when_claims_cannot_be_split(
    tmp_path: Path,
) -> None:
    source = tmp_path / "reference.txt"
    source.write_text("仅有可读说明书正文，没有可拆分权利要求。", encoding="utf-8")

    parsed = parse_source(str(source), require_claims=False)

    assert parsed["raw_text"] == "仅有可读说明书正文，没有可拆分权利要求。"
    assert parsed["claims"] == []
    assert any(
        str(item).startswith("reference_claims_not_parsed:")
        for item in parsed["errors"]
    )


def test_sparse_cover_ocr_can_supply_publication_date_without_guessing() -> None:
    claims = [{"claim_text": "1. 一种扫地机器人，包括本体。"}]
    metadata = _metadata(
        [
            "(10) 授权公告号 CN 204260680 U\n(22) 申请日 2014. 11.25",
            "1. 一种扫地机器人，包括本体。",
        ],
        "CN204260680U.pdf",
        claims,
        used_ocr=True,
        supplemental_metadata_text="(45) 授权 公告 日 2015. 04. 15",
    )

    assert metadata["application_date"] == "2014-11-25"
    assert metadata["publication_date"] == "2015-04-15"
    assert metadata["grant_date"] == "2015-04-15"
