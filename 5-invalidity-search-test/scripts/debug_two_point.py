"""Debug two-inventive-point lanes for timer and display/camera scenarios."""

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

Fake = test_mod.FakeVisionClient
Engine = analysis.InvalidityAnalysisEngine

# --- timer scenario (test 2) ---
timer_response = {
    "technical_subject": "多边形计时器",
    "subject_synonyms_zh": ["多边形计时器", "计时器"],
    "subject_synonyms_en": ["polygonal timer", "timer"],
    "features": [
        {"temp_id": "f1", "text": "计时器可立面预设有盲人文点字符号",
         "technical_subject": "多边形计时器",
         "synonyms_zh": ["计时器", "可视时间符号"], "synonyms_en": ["time symbol"]},
        {"temp_id": "f2", "text": "角度运算单元",
         "technical_subject": "多边形计时器",
         "synonyms_zh": ["姿态识别模块"], "synonyms_en": ["attitude calculation unit"]},
    ],
    "invention_search_profile": {
        "protected_subject": "多边形计时器",
        "subject_synonyms_zh": ["多边形计时器", "计时器"],
        "subject_synonyms_en": ["polygonal timer", "timer"],
        "classification_anchors": [],
        "invention_summary": "在用户可立面上显示时间符号，并通过角度运算单元感知姿态。",
        "common_context_features": [
            {"concept_id": "context-display", "text": "计时器外壳与显示符号",
             "feature_ids": ["f1"], "synonyms_zh": ["显示模块"], "synonyms_en": ["display module"],
             "source_reference": "说明书", "rationale": "属于显示层语境", "preferred_scope": "full_text"},
        ],
        "inventive_point_features": [
            {"concept_id": "inv-time-symbol", "text": "计时器并可立面预设有盲人文点字符号",
             "feature_ids": ["f1"], "synonyms_zh": ["计时器", "可视时间符号", "时间符号显示"],
             "synonyms_en": ["time symbol display"], "source_reference": "权利要求1",
             "rationale": "核心发明点之一", "preferred_scope": "full_text"},
            {"concept_id": "inv-angle-unit", "text": "角度运算单元",
             "feature_ids": ["f2"], "synonyms_zh": ["角度运算模组", "角度模块"],
             "synonyms_en": ["angle calculation unit", "attitude unit"], "source_reference": "权利要求1",
             "rationale": "核心发明点二", "preferred_scope": "full_text"},
        ],
    },
    "queries": [],
}
plan = Engine(Fake(timer_response)).plan_queries(
    claim_id="claim-timer-angle",
    expanded_claim_text="一种多边形计时器，包括可立面预设有盲人文点字符号和可视时间符号；通过角度运算单元识别姿态并驱动显示。",
    target_images=["t.png"],
    patent_context={
        "title": "多边形计时器",
        "abstract": "该计时器可立面显示时间并具备角度运算单元。",
        "claims": [{"claim_id": "claim-timer-angle",
                    "claim_text": "一种多边形计时器，包括可立面预设有盲人文点字符号和可视时间符号。"}],
    },
)
print("=== TIMER plan ===")
for q in plan.queries:
    print(" ", q.provider_kind, q.query_variant.value, "|", q.expression)
    if q.query_variant.value == "object_plus_two_inventive_points":
        print("    feature_ids:", q.feature_ids)
        print("    feature_terms:", q.feature_terms)
        print("    groups:", q.feature_term_groups)

cands, warns = analysis._deterministic_initial_query_candidates(
    technical_subject=plan.technical_subject,
    search_profile=plan.invention_search_profile,
    limitations={item.feature_id: item for item in plan.limitations},
)
print("\n=== TIMER raw candidates ===")
for c in cands:
    if c["query_variant"] == "object_plus_two_inventive_points":
        print("  feature_ids:", c["feature_ids"])
        print("  feature_terms:", c["feature_terms"])
        print("  groups:", c["feature_term_groups"])
        print("  expression:", c["expression"])
print("limitations:", [(l.feature_id, l.text) for l in plan.limitations])

# --- mirror scenario (test 3) ---
mirror_response = {
    "technical_subject": "智能后视镜",
    "subject_synonyms_zh": ["智能后视镜", "后视镜显示"],
    "subject_synonyms_en": ["smart rear-view mirror", "camera mirror"],
    "features": [
        {"temp_id": "f1", "text": "一种智能后视镜，包括显示屏和前置摄像头，外壳上设置有控制按键。",
         "technical_subject": "智能后视镜",
         "synonyms_zh": ["显示后视镜", "车载后视镜"], "synonyms_en": ["vehicle mirror"]},
        {"temp_id": "f2", "text": "计时器控制单元",
         "technical_subject": "智能后视镜",
         "synonyms_zh": ["定时模块"], "synonyms_en": ["timer module"]},
    ],
    "invention_search_profile": {
        "protected_subject": "智能后视镜",
        "subject_synonyms_zh": ["智能后视镜", "后视镜显示"],
        "subject_synonyms_en": ["smart rear-view mirror", "vehicle mirror"],
        "classification_anchors": [],
        "invention_summary": "镜子上显示屏与摄像头协同工作。",
        "common_context_features": [
            {"concept_id": "context-housing", "text": "镜体外壳", "feature_ids": ["f1"],
             "synonyms_zh": ["外壳"], "synonyms_en": ["housing"], "source_reference": "说明书",
             "rationale": "说明书结构", "preferred_scope": "claims"},
        ],
        "inventive_point_features": [
            {"concept_id": "inv-timer", "text": "计时器单元", "feature_ids": ["f2"],
             "synonyms_zh": ["定时器", "闹钟"], "synonyms_en": ["timer", "clock"],
             "source_reference": "权利要求1", "rationale": "模型误提供的发明点候选", "preferred_scope": "full_text"},
            {"concept_id": "inv-angle", "text": "角度运算单元", "feature_ids": ["f2"],
             "synonyms_zh": ["姿态传感器"], "synonyms_en": ["attitude calculation unit"],
             "source_reference": "权利要求1", "rationale": "模型误提供的发明点候选", "preferred_scope": "full_text"},
        ],
    },
    "queries": [],
}
plan2 = Engine(Fake(mirror_response)).plan_queries(
    claim_id="claim-mirror",
    expanded_claim_text="一种智能后视镜，包括显示屏和前置摄像头，并通过处理器实现图像显示与目标识别。",
    target_images=["m.png"],
    patent_context={"title": "智能后视镜", "abstract": "该镜子采用显示屏和前置摄像头实现信息显示与拍摄。"},
)
print("\n=== MIRROR plan ===")
for q in plan2.queries:
    print(" ", q.provider_kind, q.query_variant.value, "|", q.expression)
    if q.query_variant.value == "object_plus_two_inventive_points":
        print("    feature_ids:", q.feature_ids)
        print("    feature_terms:", q.feature_terms)
        print("    groups:", q.feature_term_groups)

cands2, _ = analysis._deterministic_initial_query_candidates(
    technical_subject=plan2.technical_subject,
    search_profile=plan2.invention_search_profile,
    limitations={item.feature_id: item for item in plan2.limitations},
)
print("\n=== MIRROR raw candidates ===")
for c in cands2:
    if c["query_variant"] in {
        "object_plus_two_inventive_points",
        "object_plus_inventive_point",
        "maximal_similarity_precision",
    }:
        print(" ", c["query_variant"], c["feature_terms"], c["expression"])
print("limitations:", [(l.feature_id, l.text) for l in plan2.limitations])
