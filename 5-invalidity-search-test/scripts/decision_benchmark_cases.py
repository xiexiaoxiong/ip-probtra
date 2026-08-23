"""Source identities for decision-derived blind benchmark fixtures.

This module deliberately lives under ``scripts`` rather than runtime code.
It contains only document identities and source roles taken from the evidence
lists.  Human feature mappings and legal conclusions live in separate oracle
files and are never imported by query generation or I4-S.
"""

from __future__ import annotations


CASES = (
    {
        "case_id": "wireless-earphone-decision-2022215166391",
        "decision_file": "20260429无效宣告请求审查决定书 (集优品) .pdf",
        "target": {
            "application_number": "202221516639.1",
            "publication_number": "CN217770321U",
            "title": "一种适配于不同耳朵的无线耳机",
        },
        "documents": (
            ("evidence-01", "CN216649931U", True),
            ("evidence-02", "CN215818549U", False),
            ("evidence-03", "CN210781272U", True),
            ("evidence-04", "CN210274475U", False),
            ("evidence-05", "CN210431794U", True),
            ("evidence-06", "CN210042148U", True),
        ),
    },
    {
        "case_id": "photoinitiator-decision-2017111751351",
        "decision_file": "无效决定（华鸿）.pdf",
        "target": {
            "application_number": "201711175135.1",
            "publication_number": "CN108117616B",
            "title": "二丁基芴基衍生物与其作为光引发剂的应用",
        },
        "documents": (
            ("evidence-01", "CN107272336A", True),
            ("evidence-02", "CN106883114A", True),
            ("evidence-03", "CN104004395A", True),
            ("evidence-04", "CN106010144A", True),
            ("evidence-06", "CN102766045A", True),
        ),
        "non_patent_documents": (
            {
                "acquisition_status": "missing_external_source",
                "available_for_module5": False,
                "evidence_label": "evidence-05",
                "title": "胺类光引发剂的研究进展",
                "authors": "许红光等",
                "publication": "信息记录材料",
                "year": 2007,
                "volume_issue": "第8卷第6期",
                "pages": "50-57",
                "decision_date_qualified": True,
            },
        ),
    },
    {
        "case_id": "gas-stove-decision-2016110150593",
        "decision_file": "金斗亿201611015059.3无效宣告审查决定书（4W120559）.pdf",
        "target": {
            "application_number": "201611015059.3",
            "publication_number": "CN106338092B",
            "title": "一种燃气灶调节装置及燃气灶",
        },
        "documents": (
            ("evidence-01", "CN2519162Y", True),
            ("evidence-02", "CN106091032A", True),
            ("evidence-03", "CN104508377A", True),
        ),
    },
    {
        "case_id": "battery-slurry-decision-2014106703299",
        "decision_file": "无效决定（收到日：20210127）-锂离子电池隔膜的高固含量水性陶瓷浆料及其加工方法-ZL201410670329.9-专利权人：深圳星源.pdf",
        "target": {
            "application_number": "201410670329.9",
            "publication_number": "CN104446515B",
            "title": "锂离子电池隔膜的高固含量水性陶瓷浆料及其加工方法",
        },
        "documents": (
            ("evidence-01", "CN103633269A", True),
            ("evidence-05", "CN103915594A", True),
            ("evidence-06", "CN103647034A", True),
            ("evidence-07", "CN103915595A", True),
            ("evidence-08", "CN103396710A", True),
            ("evidence-09", "CN103618059A", True),
        ),
        "non_patent_documents": (
            {
                "acquisition_status": "missing_external_source",
                "available_for_module5": False,
                "evidence_label": "evidence-02",
                "title": "涂料助剂大全",
                "authors": "焦可伯编著，朱传渠译",
                "publication": "上海科学技术文献出版社",
                "year": 2000,
                "pages": "32,46-54,432-434,448（证据10另补目录页）",
                "decision_date_qualified": True,
            },
            {
                "acquisition_status": "missing_external_source",
                "available_for_module5": False,
                "evidence_label": "evidence-03",
                "title": "水性涂料助剂",
                "authors": "朱万章等",
                "publication": "化学工业出版社",
                "year": 2011,
                "pages": "77-107",
                "decision_date_qualified": True,
            },
            {
                "acquisition_status": "missing_external_source",
                "available_for_module5": False,
                "evidence_label": "evidence-04",
                "title": "水性涂料用增稠剂的选择及研究进展",
                "authors": "谢筱薇等",
                "publication": "宁波首届涂料用助剂论坛及应用技术交流会论文集",
                "year": 2005,
                "pages": "39-42",
                "decision_date_qualified": True,
            },
        ),
    },
)
