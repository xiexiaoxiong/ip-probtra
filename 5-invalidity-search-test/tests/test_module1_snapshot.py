import pytest

from invalidity.module1 import (
    Module1Adapter,
    PatentSnapshotError,
    extract_parent_claim_ids,
    normalize_claims,
)


def test_dependency_is_only_taken_from_claim_prefix() -> None:
    parents, uncertain = extract_parent_claim_ids(
        "23. 一种球类运动器材定位系统，包括处理器，所述处理器引用权利要求1中的算法。"
    )
    assert parents == []
    assert not uncertain


def test_multiple_dependency_is_preserved_and_flagged() -> None:
    parents, uncertain = extract_parent_claim_ids("8. 根据权利要求1至3中任一项所述的装置，其特征在于……")
    assert parents == ["1", "2", "3"]
    assert uncertain


def test_direct_claim_reference_prefix_is_dependent() -> None:
    parents, uncertain = extract_parent_claim_ids(
        "2. 权利要求1的二丁基芴基衍生物，其特征在于取代基为烷基。"
    )
    assert parents == ["1"]
    assert not uncertain


def test_direct_claim_reference_does_not_capture_later_reference() -> None:
    parents, uncertain = extract_parent_claim_ids(
        "2. 一种系统，包括处理器，所述处理器使用权利要求1的数据结构。"
    )
    assert parents == []
    assert not uncertain


def test_missing_claim_one_fails_snapshot_validation() -> None:
    with pytest.raises(PatentSnapshotError, match="不连续"):
        normalize_claims(
            [
                {
                    "claim_id": "2",
                    "claim_text": "2. 根据权利要求1所述的一种扫地机器人，其具有足够长的附加技术结构。",
                }
            ]
        )


def test_independent_claim_with_later_reference_stays_independent() -> None:
    claims = normalize_claims(
        [
            {
                "claim_id": "1",
                "claim_text": "1. 一种装置，其包括控制器、传感器以及用于显示状态的显示器。",
            },
            {
                "claim_id": "2",
                "claim_text": "2. 一种系统，包括处理器，所述处理器使用权利要求1中记载的数据结构。",
            },
        ]
    )
    assert claims[1].claim_type == "INDEPENDENT"


def test_module1_snapshot_keeps_target_publication_date(tmp_path, monkeypatch) -> None:
    source = tmp_path / "target.txt"
    source.write_text("目标专利原始内容", encoding="utf-8")
    adapter = Module1Adapter(
        module1_api_url="http://127.0.0.1:5201/run",
        database_url="postgresql://unused",
    )
    monkeypatch.setattr(
        adapter,
        "_call_module1",
        lambda _source_uri, _task_id: {
            "metadata": {
                "patent_number": "CN123456789A",
                "application_date": "2019-01-02",
                "publication_date": "2020-03-04",
            },
            "claims": [
                {
                    "claim_id": "1",
                    "claim_text": "1. 一种测试装置，包括控制器、传感器以及用于输出状态的显示器。",
                }
            ],
            "specification": {},
            "figures": [],
            "errors": [],
        },
    )

    snapshot = adapter.snapshot(
        source_path=str(source),
        artifact_dir=tmp_path / "artifacts",
    )

    assert snapshot.publication_date == "2020-03-04"
