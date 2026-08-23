"""Capture pre-trim I2 lane composition for each failing-test scenario."""

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from invalidity import analysis  # noqa: E402

spec = importlib.util.spec_from_file_location(
    "test_analysis_mod", ROOT / "tests" / "test_analysis.py"
)
test_mod = importlib.util.module_from_spec(spec)
sys.modules["test_analysis_mod"] = test_mod
spec.loader.exec_module(test_mod)

captured: list[tuple[str, list]] = []
current_label = ["?"]

original_matrix = analysis._ensure_query_matrix


def spy_matrix(**kwargs):
    untrimmed = original_matrix(**{**kwargs, "max_queries": 999})
    captured.append(
        (
            current_label[0],
            [
                (
                    q.provider_kind,
                    q.date_channel.value,
                    q.query_variant.value,
                    [str(f) for f in q.feature_ids],
                    list(q.classification_anchors),
                    q.expression,
                )
                for q in untrimmed
            ],
        )
    )
    return original_matrix(**kwargs)


analysis._ensure_query_matrix = spy_matrix

Fake = test_mod.FakeVisionClient
Engine = analysis.InvalidityAnalysisEngine


def run(label, fn):
    current_label[0] = label
    try:
        fn()
    except Exception as exc:  # noqa: BLE001
        captured.append((label, [("ERROR", str(exc))]))


# scenario 1: 扫地机器人 default
run("s1-robot-default", lambda: Engine(Fake(test_mod._query_plan_response())).plan_queries(
    claim_id="claim-1", expanded_claim_text="一种扫地机器人，包括移动底盘、可升降清洁件和地面传感器。",
    target_images=["t.png"]))

# scenario 6: citation, budget 3
def s6():
    response = test_mod._query_plan_response()
    response["invention_search_profile"]["invention_summary"] = "水枪通过阀控制形成短促水流喷射"
    Engine(Fake(response)).plan_queries(
        claim_id="claim-citation", expanded_claim_text="一种水枪，包括压力罐和阀。",
        target_images=["t.png"],
        patent_context={
            "patent_number": "CN216205649U", "title": "水枪",
            "specification": {
                "技术领域": "本实用新型涉及用于射出短促的水流迸射的水枪。",
                "背景技术": "现有的短促水流玩具水枪已公开于 WO2018/215646A1，其通过阀控制水流。",
            },
        },
        max_queries=3)
run("s6-citation-b3", s6)

# scenario 8: headphone, queries[0] -> npl
def s8():
    response = test_mod._headphone_query_plan_response()
    response["queries"][0]["provider_kind"] = "npl"
    Engine(Fake(response)).plan_queries(
        claim_id="claim-headphone", expanded_claim_text="一种开放式头戴耳机，包括头戴、发音单元及发音模组。",
        target_images=["t.png"])
run("s8-headphone-npl0", s8)

# scenario 10: headphone + model title lane
def s10():
    response = test_mod._headphone_query_plan_response()
    response["queries"].insert(0, {
        "provider_kind": "patent", "purpose": "initial",
        "query_role": "title_abstract_concept", "query_variant": "title_abstract_concept",
        "search_scope": "title_abstract", "language": "zh",
        "expression": "开放式头戴耳机 中空孔 贯通耳侧 外侧",
        "feature_ids": ["f4", "f5"], "feature_terms": ["贯通耳侧", "中空孔"],
        "rationale": "多个词仍全部来自同一项中空孔特征",
    })
    Engine(Fake(response)).plan_queries(
        claim_id="claim-headphone", expanded_claim_text="一种开放式头戴耳机，包括头戴、发音单元及中空孔。",
        target_images=["t.png"])
run("s10-headphone-title", s10)

# scenario 9: robot + B25J + model inv-class, budget 3
def s9():
    response = test_mod._query_plan_response()
    response["invention_search_profile"].update({
        "classification_anchors": ["B25J11/00"],
        "classification_anchor_sources": {"B25J11/00": "model_suggested"},
        "classification_anchor_roles": {"B25J11/00": "inventive_point"},
    })
    response["queries"].insert(1, {
        "provider_kind": "patent", "purpose": "initial",
        "query_role": "title_abstract_concept", "query_variant": "object_plus_inventive_classification",
        "search_scope": "title_abstract", "scope_reason": "客体名称可能出现在标题摘要",
        "language": "zh", "expression": "扫地机器人",
        "subject_terms": ["扫地机器人"], "classification_anchors": ["B25J11/00"],
        "feature_ids": [], "feature_terms": [], "rationale": "客体加专利分类号",
    })
    Engine(Fake(response)).plan_queries(
        claim_id="claim-1", expanded_claim_text="一种扫地机器人，包括移动底盘、可升降清洁件和地面传感器。",
        target_images=["t.png"], max_queries=3)
run("s9-robot-invclass-b3", s9)

# scenario 12: water gun legacy recompile, budget 3
def s12():
    response = {
        "technical_subject": "水枪",
        "subject_synonyms_zh": ["玩具水枪"],
        "subject_synonyms_en": ["water gun"],
        "features": [
            {"temp_id": "f1", "text": "位置选择性联接装置", "technical_subject": "水枪",
             "synonyms_en": ["position selective coupling device"]},
            {"temp_id": "f2", "text": "本体在中间位置时选择性联接阀杆", "technical_subject": "水枪",
             "synonyms_en": ["intermediate position selective coupling"]},
            {"temp_id": "f3", "text": "压力罐", "technical_subject": "水枪",
             "synonyms_en": ["pressure tank"]},
        ],
        "invention_search_profile": {
            "protected_subject": "水枪",
            "subject_synonyms_zh": ["玩具水枪"],
            "subject_synonyms_en": ["water gun"],
            "classification_anchors": ["F16K 1/00", "F16K 27/02", "F16K 31/06", "B05B 1/32", "F41B 9/00"],
            "classification_anchor_roles": {
                "F16K 1/00": "general", "F16K 27/02": "general", "F16K 31/06": "general",
                "B05B 1/32": "general", "F41B 9/00": "general",
            },
            "invention_summary": "在选定位置联接阀杆。",
            "common_context_features": [
                {"concept_id": "c1", "text": "压力罐", "feature_ids": ["f3"],
                 "source_reference": "权利要求1", "rationale": "类别构件", "preferred_scope": "claims"}
            ],
            "inventive_point_features": [
                {"concept_id": "i1", "text": "位置选择性联接装置", "feature_ids": ["f1"],
                 "source_reference": "权利要求1", "rationale": "发明作用", "preferred_scope": "full_text"},
                {"concept_id": "i2", "text": "中间位置选择性联接", "feature_ids": ["f2"],
                 "source_reference": "权利要求1", "rationale": "发明动作关系", "preferred_scope": "full_text"},
            ],
        },
        "queries": [
            {"provider_kind": "patent", "purpose": "initial", "query_role": "inventive_point_precision",
             "search_scope": "full_text", "language": "zh",
             "expression": "水枪 AND 位置选择性联接装置",
             "feature_ids": ["f1"], "feature_terms": ["位置选择性联接装置"], "rationale": "客体加发明点"}
        ],
    }
    engine = Engine(Fake(response))
    first = engine.plan_queries(
        claim_id="claim-1", expanded_claim_text="一种水枪，包括压力罐和位置选择性联接装置。",
        target_images=["t.png"])
    legacy = first.model_copy(update={
        "invention_search_profile": first.invention_search_profile.model_copy(
            update={"mechanism_model": None})
    })
    engine.recompile_initial_query_plan(
        previous_plan=legacy, source_plan_run_id="legacy-i2",
        source_sha256="source-sha", max_queries=3)
run("s12-watergun-legacy-b3", s12)

for label, lanes in captured:
    print(f"\n=== {label} ({len(lanes)} pre-trim lanes) ===")
    for lane in lanes:
        print("   ", lane)
