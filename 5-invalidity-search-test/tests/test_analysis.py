from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from invalidity.analysis import (
    AnalysisValidationError,
    ExistingCorpusReuseKey,
    I2_PROMPT_VERSION,
    I2_RULE_VERSION,
    InvalidityAnalysisEngine,
    ObviousnessPrecheckAssessment,
    QueryPlan,
    build_distinguishing_features,
    evaluate_existing_corpus_gap_coverage,
    determine_obviousness_search_route,
    _atomic_search_terms,
    _architecture_head_term,
    _boolean_or_members,
    _atomize_executable_expression,
    _atomize_executable_queries,
    _chemical_parent_recall_terms,
    _deterministic_initial_query_candidates,
    _derived_core_meaning_terms,
    _ensure_query_matrix,
    _generic_mechanism_equivalents,
    _generic_subject_equivalent_terms,
    _gap_subject_head_terms,
    _gap_expression_semantic_key,
    _has_bilingual_terms,
    _target_action_recall_groups,
    _target_chemical_structure_context_terms,
    _target_application_action_context_terms,
    _target_application_property_context_terms,
    _target_citation_queries,
    _target_installation_context_terms,
    _target_effect_queries,
    _needs_structural_reconciliation,
    _cluster_consistency_contradictions,
    _consistency_contradictions,
    _merge_structural_review,
    _negation_guard_candidates,
    _negation_guard_triggered,
    _negation_guard_unhandled_candidates,
    _normalise,
    _parse_limitations,
    _reconcile_component_rows_from_supported_composites,
    _reconcile_complete_fluid_valve_mechanism,
    _reconcile_rotational_pair_rows,
    _select_structural_review_limitations,
    _single_reference_evidence_window,
    _same_atomic_search_concept,
    _refine_executable_cn_term,
    _refine_executable_subject,
    _relation_component_terms,
    _search_subject_term,
    _sanitise_subject_synonyms,
    _sanitise_subject_core_terms,
    _structural_review_excerpt,
    _subject_layer_terms,
    _target_patent_effect_terms,
    _target_patent_applicants,
    _target_interface_context_terms,
    _target_title_category_terms,
    _target_title_subject_layer_terms,
    _text_evidence_traceable,
    _traceable_mechanism_expansions,
    select_closest_prior_art,
    select_large_claim_chart_documents,
    single_reference_fully_discloses,
    target_patent_text_for_analysis,
)
from invalidity.contracts import (
    DateChannel,
    DisclosureStatus,
    GapType,
    DocumentComparison,
    FeatureDisclosure,
    I4S_PROMPT_VERSION,
    I4S_RULE_VERSION,
    InventionSearchProfile,
    Limitation,
    MechanismElement,
    MechanismModel,
    ProfileTermGroup,
    QueryRole,
    QueryVariant,
    SearchConcept,
    SearchObjective,
    SearchQuery,
    SearchScope,
)
from invalidity.llm import MultimodalModelError


@pytest.mark.parametrize(
    ("routine_status", "expected"),
    [
        ("supported_by_citable_evidence", "skip_structural_gap_search"),
        ("preliminary_candidate", "search_common_knowledge_evidence"),
    ],
)
def test_obviousness_gate_does_not_reopen_direct_structural_search(
    routine_status: str, expected: str
) -> None:
    assert determine_obviousness_search_route(
        d1_teaching_status="supported",
        routine_means_status=routine_status,
        modification_motivation_status="supported",
        teaching_away_status="absent",
        technical_effect_status="predictable",
    ) == expected


def test_obviousness_gate_keeps_direct_search_when_d1_has_no_teaching() -> None:
    assert determine_obviousness_search_route(
        d1_teaching_status="not_supported",
        routine_means_status="not_supported",
        modification_motivation_status="uncertain",
        teaching_away_status="absent",
        technical_effect_status="predictable",
    ) == "search_direct_feature_evidence"


def test_obviousness_gate_routes_routine_means_to_common_knowledge_evidence() -> None:
    assert determine_obviousness_search_route(
        d1_teaching_status="not_supported",
        routine_means_status="preliminary_candidate",
        modification_motivation_status="supported",
        teaching_away_status="absent",
        technical_effect_status="predictable",
    ) == "search_common_knowledge_evidence"


def test_obviousness_gate_accepts_d1_specific_teaching_without_routine_means() -> None:
    assert determine_obviousness_search_route(
        d1_teaching_status="supported",
        d1_teaching_path_complete=True,
        routine_means_status="not_supported",
        modification_motivation_status="supported",
        teaching_away_status="absent",
        technical_effect_status="predictable",
    ) == "skip_structural_gap_search"


class FakeVisionClient:
    model = "glm-4.6v-test"

    def __init__(
        self,
        *responses: dict[str, Any],
        discovery_response: dict[str, Any] | None = None,
    ) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []
        self._discovery_response = discovery_response

    def _discovery_route(self, kwargs: dict[str, Any]) -> SimpleNamespace | None:
        """候选构件发现调用路由：非发现调用返回 None。

        未配置发现响应时模拟传输失败（不记入 calls，视为未派发），引擎侧
        按失败降级为无清单运行；配置后正常记入 calls 并返回响应。
        """

        if "I4-S 候选构件发现任务" not in str(kwargs.get("prompt") or ""):
            return None
        if self._discovery_response is None:
            raise MultimodalModelError(
                "discovery response not configured",
                reason_code="FAKE_DISCOVERY_UNCONFIGURED",
            )
        self.calls.append(kwargs)
        return SimpleNamespace(data=self._discovery_response, model=self.model)

    def invoke_json(self, **kwargs: Any) -> SimpleNamespace:
        routed = self._discovery_route(kwargs)
        if routed is not None:
            return routed
        self.calls.append(kwargs)
        if not self.responses:
            raise AssertionError("fake response exhausted")
        return SimpleNamespace(data=self.responses.pop(0), model=self.model)


class RejectLargeImageSetOnceClient(FakeVisionClient):
    def invoke_json(self, **kwargs: Any) -> SimpleNamespace:
        routed = self._discovery_route(kwargs)
        if routed is not None:
            return routed
        self.calls.append(kwargs)
        if len(self.calls) == 1:
            raise MultimodalModelError(
                "provider rejected image payload",
                retryable=True,
                http_status=400,
                reason_code="1210",
            )
        if not self.responses:
            raise AssertionError("fake response exhausted")
        return SimpleNamespace(data=self.responses.pop(0), model=self.model)


class FailStructuralReviewClient(FakeVisionClient):
    def invoke_json(self, **kwargs: Any) -> SimpleNamespace:
        routed = self._discovery_route(kwargs)
        if routed is not None:
            return routed
        self.calls.append(kwargs)
        if len(self.calls) in (2, 3):
            raise MultimodalModelError("temporary structural review failure")
        if not self.responses:
            raise AssertionError("fake response exhausted")
        return SimpleNamespace(data=self.responses.pop(0), model=self.model)


class RejectTextOnlyWithoutAllowanceClient(FakeVisionClient):
    """模拟真实客户端的 fail-closed：纯文本调用必须显式 allow_text_only。"""

    def invoke_json(self, **kwargs: Any) -> SimpleNamespace:
        if not kwargs.get("document_images") and not kwargs.get("allow_text_only"):
            raise MultimodalModelError("text-only call requires allow_text_only")
        return super().invoke_json(**kwargs)


class FailGuardReviewClient(FakeVisionClient):
    """首轮之后的守门复核调用一律传输失败。"""

    def invoke_json(self, **kwargs: Any) -> SimpleNamespace:
        if self.calls:
            self.calls.append(kwargs)
            raise MultimodalModelError("temporary guard review failure")
        return super().invoke_json(**kwargs)


def _guard_review_keeps_negative(
    *feature_ids: str,
    reasoning_prefix: str = "",
    confidence: float = 0.7,
) -> dict[str, Any]:
    """守门候选复核响应：模型按角色语义排查候选后维持合规否定结论。"""

    return {
        "disclosures": [
            {
                "feature_id": feature_id,
                "status": "not_disclosed",
                "reasoning": (
                    reasoning_prefix
                    + "已按实质相同标准复核承担该特征角色的候选构件/候选成分，"
                    "结构与位置关系实质不同，均不承担目标角色"
                ),
                "confidence": confidence,
                "mapping_basis": "none",
                "structural_search_summary": (
                    "已逐一排查候选构件/结构区域，连接拓扑不同"
                ),
                "alternative_path_analysis": (
                    "已按角色语义在对比文件全文（含英文段落）搜索承担该角色的"
                    "候选构件并逐条排除"
                ),
                "role_candidates_evaluated": [
                    {
                        "candidate": "给定确定性候选构件",
                        "source": "deterministic",
                        "verdict": "不承担目标角色",
                        "reason": "连接拓扑、介质路径不同",
                    },
                    {
                        "candidate": "全文角色重扫候选构件",
                        "source": "full_text_rescan",
                        "verdict": "不承担目标角色",
                        "reason": "已围绕角色语义搜索全文，无遗漏候选",
                    },
                ],
            }
            for feature_id in feature_ids
        ]
    }


def _feature(
    feature_id: str,
    sequence: int,
    text: str,
    *,
    subject: str = "扫地机器人",
) -> Limitation:
    return Limitation(
        feature_id=feature_id,
        claim_id="claim-1",
        sequence=sequence,
        text=text,
        mandatory=True,
        technical_subject=subject,
    )


def _gap_profile(
    protected_subject: str,
    *,
    subject_core_terms_zh: list[str],
    subject_core_terms_en: list[str],
) -> InventionSearchProfile:
    return InventionSearchProfile(
        protected_subject=protected_subject,
        subject_core_terms_zh=subject_core_terms_zh,
        subject_core_terms_en=subject_core_terms_en,
        invention_summary="测试用冻结画像",
    )


def _target_patent_context() -> dict[str, Any]:
    return {
        "patent_number": "CN-TARGET",
        "title": "一种液体喷射装置",
        "abstract": "该装置利用储液、受压供液和阀件开闭形成喷射流路。",
        "claims": [
            {
                "claim_id": "claim-1",
                "claim_type": "INDEPENDENT",
                "claim_text": "一种液体喷射装置，包括压力罐和控制流路的阀。",
                "expanded_claim_text": "一种液体喷射装置，包括压力罐和控制流路的阀。",
            },
            {
                "claim_id": "claim-2",
                "claim_type": "DEPENDENT",
                "claim_text": "根据权利要求1所述的装置，其中阀杆能够轴向移动。",
                "expanded_claim_text": (
                    "一种液体喷射装置，包括压力罐和控制流路的阀；"
                    "其中阀杆能够轴向移动。"
                ),
            },
        ],
        "specification": {
            "背景技术": "液体喷射装置通常包括储液空间和控制排液的阀件。",
            "发明内容": "本装置通过受压储液路径向喷射口供液。",
            "具体实施方式": (
                "储液空间与喷射流路连通，阀杆在阀体流道内移动以控制通断。"
            ),
        },
    }


def _target_patent_text() -> str:
    value = target_patent_text_for_analysis(_target_patent_context())
    assert "【背景技术】" not in value  # section labels retain their specification prefix
    assert "说明书·背景技术" in value
    return value


def _disclosure(
    feature_id: str,
    *,
    status: DisclosureStatus = DisclosureStatus.EXPLICIT,
    confidence: float = 0.95,
) -> FeatureDisclosure:
    return FeatureDisclosure(
        feature_id=feature_id,
        status=status,
        evidence_quote=f"quote for {feature_id}",
        evidence_location=f"claim 1 / {feature_id}",
        reasoning="direct disclosure",
        confidence=confidence,
    )


def _comparison(
    document_id: str,
    feature_ids: list[str],
    *,
    alignment: float = 0.7,
    evidence_completeness: float = 0.9,
) -> DocumentComparison:
    return DocumentComparison(
        document_id=document_id,
        disclosures=[_disclosure(item) for item in feature_ids],
        field_alignment=alignment,
        purpose_alignment=alignment,
        effect_alignment=alignment,
        evidence_completeness=evidence_completeness,
        model="glm-4.6v-test",
        used_target_images=1,
        used_document_images=1,
    )


def _query_plan_response() -> dict[str, Any]:
    return {
        "technical_subject": "扫地机器人",
        "subject_synonyms_zh": ["扫拖机器人"],
        "subject_synonyms_en": ["robot cleaner"],
        "features": [
            {
                "temp_id": "f1",
                "text": "移动底盘",
                "technical_subject": "扫地机器人",
                "synonyms_zh": ["行走底盘"],
                "synonyms_en": ["mobile chassis"],
            },
            {
                "temp_id": "f2",
                "text": "清洁件相对底盘升降",
                "technical_subject": "扫地机器人",
                "synonyms_zh": ["升降拖布"],
                "synonyms_en": ["liftable cleaning pad"],
            },
            {
                "temp_id": "f3",
                "text": "传感器检测地面材质",
                "technical_subject": "扫地机器人",
                "synonyms_zh": ["地面识别传感器"],
                "synonyms_en": ["floor type sensor"],
            },
        ],
        "invention_search_profile": {
            "protected_subject": "扫地机器人",
            "subject_synonyms_zh": ["扫拖机器人"],
            "subject_synonyms_en": ["robot cleaner"],
            "classification_anchors": [],
            "invention_summary": "通过清洁件升降适应不同地面。",
            "common_context_features": [
                {
                    "concept_id": "context-chassis",
                    "text": "移动底盘",
                    "feature_ids": ["f1"],
                    "synonyms_zh": ["行走底盘"],
                    "synonyms_en": ["mobile chassis"],
                    "source_reference": "权利要求1",
                    "rationale": "扫地机器人类别通常具备移动底盘",
                    "preferred_scope": "claims",
                },
                {
                    "concept_id": "context-sensor",
                    "text": "地面材质传感器",
                    "feature_ids": ["f3"],
                    "synonyms_zh": ["地面识别传感器"],
                    "synonyms_en": ["floor type sensor"],
                    "source_reference": "权利要求1",
                    "rationale": "用于限定同类自动清洁场景",
                    "preferred_scope": "claims",
                },
            ],
            "inventive_point_features": [
                {
                    "concept_id": "inventive-lift",
                    "text": "清洁件相对底盘升降",
                    "feature_ids": ["f2"],
                    "synonyms_zh": ["升降拖布"],
                    "synonyms_en": ["liftable cleaning pad"],
                    "source_reference": "权利要求1及发明内容",
                    "rationale": "清洁件与底盘的可变位置关系说明发明点",
                    "preferred_scope": "full_text",
                },
            ],
        },
        "queries": [
            {
                "provider_kind": "patent",
                "purpose": "initial",
                "query_role": "inventive_point_precision",
                "search_scope": "full_text",
                "scope_reason": "位置关系更可能在说明书全文出现",
                "language": "zh",
                "expression": "升降拖布",
                "feature_ids": ["f2"],
                "feature_terms": ["升降拖布"],
                "rationale": "too broad",
            },
            {
                "provider_kind": "patent",
                "purpose": "initial",
                "query_role": "inventive_point_precision",
                "search_scope": "full_text",
                "scope_reason": "位置关系更可能在说明书全文出现",
                "language": "zh",
                "expression": "扫地机器人 AND 升降拖布",
                "feature_ids": ["f2"],
                "feature_terms": ["升降拖布"],
                "rationale": "subject plus inventive point",
            },
            {
                "provider_kind": "patent",
                "purpose": "initial",
                "query_role": "claim_context_recall",
                "search_scope": "claims",
                "scope_reason": "类别构件通常写入权利要求",
                "language": "zh",
                "expression": "扫地机器人 AND 移动底盘 AND 地面识别传感器",
                "feature_ids": ["f1", "f3"],
                "feature_terms": ["移动底盘", "地面识别传感器"],
                "rationale": "subject plus two category context groups",
            },
        ],
    }


def _headphone_query_plan_response() -> dict[str, Any]:
    return {
        "technical_subject": "开放式头戴耳机",
        "features": [
            {
                "temp_id": "f1",
                "text": "开放式头戴耳机，用于佩戴在用户的头部",
                "technical_subject": "开放式头戴耳机",
            },
            {
                "temp_id": "f2",
                "text": "包括头戴和两个发音单元",
                "technical_subject": "开放式头戴耳机",
                "synonyms_zh": ["头箍与发声单元"],
            },
            {
                "temp_id": "f3",
                "text": "两个发音单元内安装有发音模组，用于产生音频",
                "technical_subject": "开放式头戴耳机",
                "synonyms_zh": ["扬声器模组"],
            },
            {
                "temp_id": "f4",
                "text": "两个发音单元分别连接于头戴的两端",
                "technical_subject": "开放式头戴耳机",
            },
            {
                "temp_id": "f5",
                "text": "发音单元上设置有贯通靠近耳朵一侧与相对的外侧的中空孔",
                "technical_subject": "开放式头戴耳机",
            },
        ],
        "invention_search_profile": {
            "protected_subject": "开放式头戴耳机",
            "invention_summary": "在发音单元上设置贯通耳侧与外侧的中空孔。",
            "classification_anchors": [],
            "common_context_features": [
                {
                    "concept_id": "context-headband",
                    "text": "头戴",
                    "feature_ids": ["f2"],
                    "source_reference": "权利要求1",
                    "rationale": "头戴式耳机类别共有构件",
                    "preferred_scope": "claims",
                },
                {
                    "concept_id": "context-speaker",
                    "text": "发音模组",
                    "feature_ids": ["f3"],
                    "synonyms_zh": ["扬声器模组"],
                    "source_reference": "权利要求1",
                    "rationale": "耳机类别共有发声构件",
                    "preferred_scope": "claims",
                },
            ],
            "inventive_point_features": [
                {
                    "concept_id": "inventive-hole",
                    "text": "贯通耳侧与外侧的中空孔",
                    "feature_ids": ["f5"],
                    "synonyms_zh": ["贯通中空孔"],
                    "source_reference": "权利要求1",
                    "rationale": "具体贯通位置关系体现发明点",
                    "preferred_scope": "full_text",
                },
            ],
        },
        "queries": [
            {
                "provider_kind": "patent",
                "purpose": "initial",
                "query_role": "claim_context_recall",
                "search_scope": "claims",
                "scope_reason": "类别构件通常写入权利要求",
                "language": "zh",
                "expression": "开放式头戴耳机 头戴 发音单元 发音模组",
                "feature_ids": ["f1", "f2", "f3"],
                # This is the malformed positional pairing observed in the live
                # model response: it must not force the compact query to be lost.
                "feature_terms": ["开放式头戴耳机", "发音单元", "发音单元"],
                "rationale": "主题与两项结构特征的紧凑组合",
            },
            {
                "provider_kind": "patent",
                "purpose": "initial",
                "query_role": "inventive_point_precision",
                "search_scope": "full_text",
                "scope_reason": "贯通位置关系更可能在全文出现",
                "language": "zh",
                "expression": "开放式头戴耳机 贯通耳侧与外侧的中空孔",
                "feature_ids": ["f5"],
                "feature_terms": ["贯通耳侧与外侧的中空孔"],
                "rationale": "主题与发明点组合",
            },
        ],
    }


def test_i2_stable_features_and_query_guard_rejects_isolated_term() -> None:
    client = FakeVisionClient(_query_plan_response(), _query_plan_response())
    engine = InvalidityAnalysisEngine(client)

    first = engine.plan_queries(
        claim_id="claim-1",
        expanded_claim_text="一种扫地机器人，包括移动底盘、可升降清洁件和地面传感器。",
        target_images=["target-figure.png"],
    )
    second = engine.plan_queries(
        claim_id="claim-1",
        expanded_claim_text="一种扫地机器人，包括移动底盘、可升降清洁件和地面传感器。",
        target_images=["target-figure.png"],
    )

    assert [item.feature_id for item in first.limitations] == [
        item.feature_id for item in second.limitations
    ]
    # 首轮组合上限为最有希望的 5 条；同一来源画像重跑结果必须逐线稳定。
    assert len(first.queries) <= 5
    assert [item.query_id for item in first.queries] == [
        item.query_id for item in second.queries
    ]
    assert {item.search_objective for item in first.queries} == {
        "full_claim_single_reference"
    }
    # 首轮检索组合固定为五组检索线（宪章 2.15）：不再发射 NPL sibling 线
    # 或任何旧首轮 variant。
    assert not any(item.provider_kind == "npl" for item in first.queries)
    patent_variants = {
        item.query_variant
        for item in first.queries
        if item.provider_kind == "patent"
        and item.date_channel == "ordinary_prior_art"
    }
    assert patent_variants <= {
        "applicant_plus_object",
        "title_object_plus_desc_inventive",
        "classification_plus_desc_inventive",
        "desc_object_plus_desc_inventive_plus_effect",
        "title_keyword_object_plus_desc_function",
    }
    assert patent_variants == {"title_object_plus_desc_inventive"}
    inventive = next(
        item
        for item in first.queries
        if item.provider_kind == "patent"
        and item.date_channel == "ordinary_prior_art"
        and item.query_variant == "title_object_plus_desc_inventive"
    )
    assert (
        inventive.expression
        == "(扫地机器人 OR 扫拖机器人 OR robot cleaner) AND 清洁件 AND 底盘"
        " AND 升降 AND (升降拖布 OR liftable cleaning pad)"
    )
    assert (
        inventive.provider_expression
        == "(TTL:(扫地机器人 OR 扫拖机器人 OR robot cleaner))"
        " AND (DESC:(清洁件)) AND (DESC:(底盘)) AND (DESC:(升降))"
        " AND (DESC:(升降拖布 OR liftable cleaning pad))"
    )
    assert inventive.search_scope == "full_text"
    assert len(inventive.feature_ids) == 1
    # 首轮固定五组只在普通现有技术通道执行，不再把首条线原样复制到抵触
    # 申请通道（复制会产生表达式完全相同的重复线）。
    assert not any(
        item.date_channel == "cn_conflicting_application"
        for item in first.queries
    )
    assert first.rejected_queries[0].expression == "升降拖布"
    assert "四类中至少包含两类" in first.rejected_queries[0].reason
    assert client.calls[0]["target_images"] == ["target-figure.png"]


def test_i2_initial_profile_is_compact_and_scope_defaults_are_non_blocking() -> None:
    response = _query_plan_response()
    for key in ("common_context_features", "inventive_point_features"):
        for concept in response["invention_search_profile"][key]:
            concept.pop("preferred_scope", None)
    client = FakeVisionClient(response)

    plan = InvalidityAnalysisEngine(client).plan_queries(
        claim_id="claim-1",
        expanded_claim_text="一种扫地机器人，包括移动底盘、可升降清洁件和地面传感器。",
        target_images=["target-figure.png"],
    )

    assert client.calls[0]["max_tokens"] == 8192
    assert "features最多10项" in client.calls[0]["prompt"]
    assert "使基础构件是否公开不依赖后续所有关系" in client.calls[0]["prompt"]
    assert "queries必须是空数组" in client.calls[0]["prompt"]
    assert "较宽母核" in client.calls[0]["prompt"]
    profile = plan.invention_search_profile
    assert profile is not None
    assert all(
        item.preferred_scope.value == "claims"
        for item in profile.common_context_features
    )
    assert all(
        item.preferred_scope.value == "full_text"
        for item in profile.inventive_point_features
    )


def test_i2_drops_unbound_auxiliary_common_context_without_stopping_plan() -> None:
    response = _query_plan_response()
    response["invention_search_profile"]["common_context_features"].insert(
        0,
        {
            "concept_id": "model-stale-context",
            "text": "模型自创的辅助语境",
            "feature_ids": ["unknown-feature-id"],
            "source_reference": "无",
            "rationale": "不应阻断 gap/首轮检索",
        },
    )

    plan = InvalidityAnalysisEngine(FakeVisionClient(response)).plan_queries(
        claim_id="claim-1",
        expanded_claim_text="一种扫地机器人，包括移动底盘、可升降清洁件和地面传感器。",
        target_images=["target-figure.png"],
    )

    profile = plan.invention_search_profile
    assert profile is not None
    assert all(
        item.concept_id != "model-stale-context"
        for item in profile.common_context_features
    )
    assert plan.queries


def test_i2_splits_base_component_from_following_inventory_relation() -> None:
    limitations, aliases = _parse_limitations(
        claim_id="claim-1",
        technical_subject="燃气灶调节装置",
        raw_features=[
            {
                "temp_id": "f1",
                "text": (
                    "包括壳体，所述壳体固设有连接部件、传动部件、动力部件、"
                    "电源、控制装置"
                ),
                "technical_subject": "燃气灶调节装置",
                "synonyms_zh": [],
                "synonyms_en": [],
            }
        ],
    )

    assert [item.text for item in limitations] == [
        "包括壳体",
        "所述壳体固设有连接部件、传动部件、动力部件、电源、控制装置",
    ]
    assert aliases["f1"] == limitations[1].feature_id


def test_i2_chemical_parent_recall_is_target_derived_and_bounded() -> None:
    exact = "二丁基芴基衍生物光引发剂"

    assert _chemical_parent_recall_terms(exact) == [
        "芴基衍生物光引发剂",
        "芴类光引发剂",
    ]
    assert _traceable_mechanism_expansions(
        [exact],
        ["芴基衍生物光引发剂", "芴类光引发剂", "蒽类光引发剂"],
    ) == ["芴基衍生物光引发剂", "芴类光引发剂"]
    assert _chemical_parent_recall_terms("普通光引发剂") == []


def test_i2_target_effect_rejects_deictic_non_effect_phrase() -> None:
    assert _target_patent_effect_terms(
        {
            "specification": {"发明内容": "该材料可以是其中一种。"},
            "abstract": "",
        },
        invention_summary="该材料可以是其中一种",
    ) == []


def test_i2_target_action_recall_splits_control_drive_and_adjustment_atoms() -> None:
    result = _target_patent_effect_terms(
        {
            "title": "一种燃气灶调节装置及燃气灶",
            "abstract": (
                "控制装置接收指令并驱动动力部件，用于自动切换火力、"
                "控制火力维持的时间及关闭。"
            ),
        },
        invention_summary="控制装置驱动动力部件调节燃气灶",
        maximum=2,
    )

    action_terms = next(
        terms for terms, _ in result if "调节" in terms and "驱动" in terms
    )
    assert "控制" in action_terms
    assert "定时" in action_terms
    assert "control" in action_terms
    assert "指令控制驱动" not in action_terms
    assert _target_action_recall_groups(action_terms) == [
        ["定时", "timer"],
        ["调节", "adjust"],
        ["控制", "control"],
    ]


def test_i2_target_application_action_recall_uses_target_coating_context() -> None:
    patent_context = {
        "title": "一种锂离子电池隔膜用高固含量水性陶瓷浆料",
        "abstract": "该水性陶瓷浆料具有良好的涂布性能。",
        "specification": {
            "背景技术": (
                "在聚烯烃隔膜表面涂覆陶瓷颗粒形成复合隔膜，"
                "陶瓷浆料可通过涂布工艺附着于隔膜基体。"
            )
        },
    }

    context = _target_application_action_context_terms(patent_context)
    assert context is not None
    terms, excerpt = context
    assert terms == ["涂层", "涂覆", "涂布", "coating"]
    assert "隔膜" in excerpt

    queries = _target_effect_queries(
        patent_context=patent_context,
        technical_subject="锂离子电池隔膜用高固含量水性陶瓷浆料",
        invention_summary="水性陶瓷浆料用于锂电池隔膜并具有良好涂布性能",
        claim_id="claim-coating",
        iteration_number=1,
    )
    coating_lane = next(
        item
        for item in queries
        if item.query_variant == "target_effect_recall"
        and "涂层" in item.target_effect_terms
    )
    assert coating_lane.expression.endswith("AND 涂层")
    assert coating_lane.search_scope == "title_abstract"
    assert coating_lane.classification_anchors == []

    property_context = _target_application_property_context_terms(
        {
            **patent_context,
            "specification": {
                "背景技术": "为改善聚烯烃隔膜的热稳定性，减少高温热收缩。"
            },
        }
    )
    assert property_context is not None
    assert property_context[0][:2] == ["热稳定性", "耐高温"]

    combined_queries = _target_effect_queries(
        patent_context={
            **patent_context,
            "specification": {
                "背景技术": (
                    "在聚烯烃隔膜表面涂覆陶瓷颗粒形成复合隔膜，"
                    "从而改善隔膜的热稳定性并减少高温热收缩。"
                )
            },
        },
        technical_subject="锂离子电池隔膜用高固含量水性陶瓷浆料",
        invention_summary="水性陶瓷浆料用于隔膜涂覆并改善热稳定性",
        claim_id="claim-coating-property",
        iteration_number=1,
    )
    combined_lane = next(
        item
        for item in combined_queries
        if "涂层" in item.target_context_terms
        and "热稳定性" in item.target_effect_terms
    )
    assert combined_lane.expression.endswith("AND 涂层 AND 热稳定性")
    assert "应用动作—性能关系" in combined_lane.rationale


def test_i2_classification_action_recall_omits_subject_name_drift() -> None:
    queries = _target_effect_queries(
        patent_context={
            "title": "一种燃气灶调节装置及燃气灶",
            "abstract": "控制装置接收指令，用于控制火力维持的时间及关闭。",
            "claims": [{"claim_text": "连接部件连接燃气灶开关轴。"}],
            "specification": {
                "发明内容": "该装置可以直接加装到现有燃气灶上。"
            },
        },
        technical_subject="燃气灶调节装置及燃气灶",
        invention_summary="控制装置定时控制燃气灶火力",
        claim_id="claim-timer",
        iteration_number=1,
        classification_anchors=["F24C3/12", "F24C3/00"],
        classification_anchor_sources={"F24C3/12": "model_suggested"},
        classification_anchor_roles={"F24C3/12": "subject"},
    )

    lane = next(
        item
        for item in queries
        if item.query_variant == "classification_action_recall"
        and "定时" in item.target_effect_terms
    )
    assert lane.expression == "F24C3/12 AND 定时"
    assert lane.subject_terms == []
    assert lane.target_context_terms == []
    assert lane.classification_anchors == ["F24C3/12"]
    assert any(
        item.query_variant == "classification_action_recall"
        and item.classification_anchors == ["F24C3/00"]
        and "定时" in item.target_effect_terms
        for item in queries
    )
    broad_action_lane = next(
        item
        for item in queries
        if item.query_variant == "target_effect_recall"
        and "定时" in item.target_effect_terms
        and not item.target_context_terms
    )
    assert broad_action_lane.expression == "燃气灶调节装置及燃气灶 AND 定时"
    assert broad_action_lane.search_scope == "title_abstract"
    assert all(
        item.query_variant == "classification_action_recall"
        for item in queries[:4]
    )
    install_context = _target_installation_context_terms(
        {
            "specification": {
                "发明内容": "该调节装置可以直接加装到现有燃气灶上。"
            }
        }
    )
    assert install_context is not None
    install_lane = next(
        item
        for item in queries
        if item.query_variant == "target_effect_recall"
        and "加装" in item.target_context_terms
        and "定时" in item.target_effect_terms
    )
    assert install_lane.expression.endswith("AND 加装 AND 定时")
    interface_lane = next(
        item
        for item in queries
        if item.query_variant == "target_effect_recall"
        and "加装" in item.target_context_terms
        and "燃气灶开关轴" in item.target_effect_terms
    )
    assert interface_lane.expression.endswith("AND 加装 AND 燃气灶开关轴")
    assert "安装场景—宿主接口关系" in interface_lane.rationale


def test_i2_generic_recall_uses_atomic_action_and_material_families() -> None:
    roles, operations = _generic_mechanism_equivalents("指令控制并驱动动力部件")

    assert "控制机构" in roles
    assert "驱动机构" in roles
    assert "控制" in operations
    assert "驱动" in operations
    assert _architecture_head_term("碱溶性高分子乳液增稠剂") == "增稠剂"
    assert _architecture_head_term("纳米陶瓷粉体") == "陶瓷粉体"
    assert not _same_atomic_search_concept(
        "二丁基芴基衍生物光引发剂", "烯属不饱和化合物"
    )


def test_i2_target_interface_context_reduces_switch_axis_to_search_words() -> None:
    result = _target_interface_context_terms(
        {
            "title": "一种燃气灶调节装置及燃气灶",
            "abstract": "连接部件连接燃气灶开关轴，控制装置驱动动力部件。",
            "claims": [
                {"claim_text": "所述连接部件连接燃气灶开关轴。"},
            ],
        }
    )

    assert result is not None
    terms, excerpt = result
    assert "燃气灶开关轴" in terms
    assert "燃气灶开关" in terms
    assert "开关" in terms
    assert "燃气灶开关轴" in excerpt


def test_i2_recovers_protected_category_from_target_title_only() -> None:
    assert _target_title_category_terms(
        {"title": "取代母核衍生物与其作为光引发剂的应 用"}
    ) == ["光引发剂"]
    assert _target_title_category_terms(
        {"title": "一种燃气灶调节装置及燃气灶"}
    ) == []


def test_i2_formula_claim_uses_target_full_text_structure_fragments() -> None:
    patent_context = {
        "title": "取代母核衍生物与其作为光引发剂的应用",
        "abstract": "提供一种新的光引发剂。",
        "claims": [{"claim_text": "一种组合物，包含具有下式的光引发剂化合物。"}],
        "specification": {
            "发明内容": (
                "2-甲基-1-(2-(9,9-二取代稠环基))-2-(N-吗啉基)-1-丙酮"
                "作为光引发剂。"
            )
        },
    }

    structure_context = _target_chemical_structure_context_terms(patent_context)
    assert structure_context is not None
    terms, excerpt = structure_context
    assert terms == ["N-吗啉基", "二取代稠环基"]
    assert "作为光引发剂" in excerpt

    queries = _target_effect_queries(
        patent_context=patent_context,
        technical_subject="光固化组合物",
        invention_summary="",
        claim_id="claim-formula",
        iteration_number=1,
    )
    structure_queries = [
        item
        for item in queries
        if item.search_scope == "full_text"
        and item.query_role == "inventive_point_precision"
    ]
    assert [item.expression for item in structure_queries] == [
        "光引发剂 AND N-吗啉基",
        "光引发剂 AND 二取代稠环基",
    ]
    assert all(item.feature_ids == [] for item in structure_queries)
    assert all("下式" not in item.expression for item in structure_queries)


def test_i2_nonchemical_target_does_not_invent_structure_fragments() -> None:
    assert _target_chemical_structure_context_terms(
        {
            "title": "一种水枪",
            "abstract": "阀杆经由联接机构控制水流。",
            "specification": {"发明内容": "手柄连接阀杆。"},
        }
    ) is None


def test_i2_atomic_keywords_remove_drafting_wrappers_and_incomplete_fragments() -> None:
    assert _atomic_search_terms("下式的光引发剂化合物") == ["光引发剂化合物"]
    assert _atomic_search_terms("烯属不饱") == []
    assert _relation_component_terms("连接燃气灶开关轴") == ["燃气灶开关轴"]
    assert _relation_component_terms("动力部件电连接") == ["动力部件"]
    assert _relation_component_terms("第一连接件可变换其") == ["连接件"]
    assert _atomic_search_terms("连接燃气灶开关轴") == ["燃气灶开关轴"]
    assert _atomic_search_terms("接收指令并驱动") == ["驱动"]
    assert _atomic_search_terms("切面配合的切面结构") == ["切面结构"]
    assert "化合物" not in _relation_component_terms(
        "可光聚合的碳碳双键式不饱和化合物"
    )
    assert "以使得" not in _atomic_search_terms("连接件相对移动，以使得适配开关轴")
    assert _atomic_search_terms(
        "传动部件连接所述连接部件及所述动力部件"
    ) == ["传动部件", "连接部件", "动力部件"]
    assert _atomic_search_terms("将压力罐与阀导管连接起来的管") == [
        "压力罐",
        "阀导管",
        "管",
    ]
    assert _atomic_search_terms("带有阀导管的阀") == ["阀导管", "阀"]
    assert _atomic_search_terms("有喇叭和电池") == ["喇叭", "电池"]
    assert _atomic_search_terms("locking凸") == []
    assert _atomic_search_terms("中加入配方量的陶瓷颗粒") == ["陶瓷颗粒"]
    assert _atomic_search_terms("圆形凸台与套圈的旋转连接结构") == [
        "圆形凸台",
        "套圈",
        "旋转",
    ]
    assert _atomic_search_terms("油漆（着色/非着色") == ["油漆"]
    assert _atomic_search_terms("第一连接件可变换其在第二连接件的相对位置") == [
        "连接件"
    ]
    assert _atomic_search_terms("陶瓷颗粒的粒径D50") == [
        "陶瓷颗粒",
        "粒径D50",
    ]
    assert _atomic_search_terms("涂布动作") == ["涂布"]
    assert _atomic_search_terms("照射动作") == ["照射"]
    assert _atomic_search_terms("关闭位置和打开位置") == [
        "关闭位置",
        "打开位置",
    ]
    assert _atomic_search_terms("光引发剂化合物占组合物重量的0.5-10%") == [
        "光引发剂化合物",
        "0.5-10%",
    ]
    assert _atomic_search_terms("波长在150-600nm范围的光") == [
        "波长",
        "150-600nm",
    ]
    assert _atomic_search_terms("包括壳体") == ["壳体"]
    assert _atomic_search_terms("陶瓷颗粒 和水") == ["陶瓷颗粒", "水"]
    assert _atomic_search_terms("份和陶瓷颗粒") == ["陶瓷颗粒"]
    assert _atomic_search_terms("不饱和化合物") == ["不饱和化合物"]
    assert _atomic_search_terms("按重量百分比计算的组合物50-65%和水35-50%") == []


def test_i2_executable_keywords_never_exceed_six_chinese_chars() -> None:
    # 任务 12328641 耳机案件：权利要求整句必须提炼为原子技术名词 AND 组合。
    atoms, dropped = _refine_executable_cn_term(
        "所述耳机本体内具有PCBA板，所述PCBA板电性连接有喇叭和电池"
    )
    assert atoms == ["耳机", "PCBA板", "喇叭", "电池"]
    assert dropped == []
    atoms, dropped = _refine_executable_cn_term("所述耳机本体对应所述喇叭设置有出音孔")
    assert atoms == ["耳机", "喇叭", "出音孔"]
    assert dropped == []
    # 长客体提炼为核心类别名词，修饰语成为独立关键词。
    assert _refine_executable_subject("适配于不同耳朵的无线耳机") == ("耳机", ["无线"])
    # 短词与非中文短语不受影响。
    assert _refine_executable_cn_term("圆形凸台") == (["圆形凸台"], [])
    assert _refine_executable_cn_term("pivot shaft") == (["pivot shaft"], [])
    # 完整单一技术术语超过 6 字时按通用后缀规则提炼。
    assert _refine_executable_cn_term("位置选择性联接装置") == (["选择性联接"], [])


def test_i2_atomize_expression_rewrites_long_subject_and_claim_sentences() -> None:
    expression, notes = _atomize_executable_expression(
        "适配于不同耳朵的无线耳机 AND 所述耳机本体内具有PCBA板，所述PCBA板电性连接有喇叭和电池"
        " AND 所述耳机本体对应所述喇叭设置有出音孔",
        technical_subject="适配于不同耳朵的无线耳机",
        subject_terms=["适配于不同耳朵的无线耳机", "耳麦", "无线耳机"],
    )
    assert expression == "(耳机 OR 耳麦) AND 无线 AND PCBA板 AND 喇叭 AND 电池 AND 出音孔"
    assert notes == []
    # 客体同义 OR 组保留，长定语客体不进入任何组。
    expression, _ = _atomize_executable_expression(
        "适配于不同耳朵的无线耳机 AND (圆形凸台 OR 圆形凸起 OR 柱形凸台 OR 转轴 OR 枢轴 OR boss)",
        technical_subject="适配于不同耳朵的无线耳机",
        subject_terms=["适配于不同耳朵的无线耳机", "耳麦"],
    )
    assert expression == (
        "(耳机 OR 耳麦) AND 无线 AND "
        "(圆形凸台 OR boss OR 圆形凸起 OR 柱形凸台 OR 转轴)"
    )
    assert "适配于不同耳朵" not in expression
    # 中英文混合与短词场景保持原样结构。
    expression, _ = _atomize_executable_expression(
        "适配于不同耳朵的无线耳机 AND 套圈 AND pivot shaft",
        technical_subject="适配于不同耳朵的无线耳机",
        subject_terms=["适配于不同耳朵的无线耳机"],
    )
    assert expression == "耳机 AND 无线 AND 套圈 AND pivot shaft"
    # 水枪等短客体场景不回退、不拆词。
    expression, _ = _atomize_executable_expression(
        "水枪 AND (选择性联接 OR 中间位置)",
        technical_subject="水枪",
        subject_terms=["水枪"],
    )
    assert expression == "水枪 AND (选择性联接 OR 中间位置)"
    # 无显式 AND/OR 的 NPL 自然语言整句同样原子化为关键词 AND 组合（豁免已废除）。
    npl_text = "适配于不同耳朵的无线耳机 所述耳机本体内具有PCBA板"
    expression, _ = _atomize_executable_expression(
        npl_text,
        technical_subject="适配于不同耳朵的无线耳机",
        subject_terms=[],
    )
    assert expression == "耳机 AND 无线 AND PCBA板"
    # 单个英文短语或短中文词不被拆散。
    expression, _ = _atomize_executable_expression(
        "pivot shaft",
        technical_subject="适配于不同耳朵的无线耳机",
        subject_terms=[],
    )
    assert expression == "pivot shaft"


def test_i2_subject_layers_do_not_promote_application_as_product_object() -> None:
    assert "应用" not in _subject_layer_terms("取代母核衍生物作为光引发剂的应用")
    assert _search_subject_term("光固化组合物的应用") == "光固化组合物"
    assert _search_subject_term("光固化组合物的固化方法") == "光固化组合物"
    assert _search_subject_term("高固含量水性陶瓷浆料的加工方法") == (
        "高固含量水性陶瓷浆料"
    )
    assert _sanitise_subject_synonyms(
        ["应用", "作为光引发剂的应用", "固化方法", "光固化组合物", "photocuring method"]
    ) == ["光固化组合物"]


def test_i2_derives_target_only_object_layers_from_title_grammar() -> None:
    assert _subject_layer_terms(
        "锂离子电池隔膜的高固含量水性陶瓷浆料"
    ) == [
        "电池隔膜",
        "锂电池隔膜",
        "水性陶瓷浆料",
        "锂离子电池隔膜",
        "高固含量水性陶瓷浆料",
        "锂电池隔膜的高固含量水性陶瓷浆料",
    ]
    assert "燃气灶" in _target_title_subject_layer_terms(
        {"title": "一种燃气灶调节装置及燃气灶"}
    )
    assert _generic_subject_equivalent_terms("燃气灶调节装置") == ["燃气炉"]


def test_i2_material_title_recall_uses_application_layer_and_one_claim_material() -> None:
    response = {
        "technical_subject": "锂离子电池隔膜的高固含量水性陶瓷浆料",
        "subject_synonyms_zh": [],
        "subject_synonyms_en": [
            "high solid content aqueous ceramic slurry for lithium ion battery separator"
        ],
        "features": [
            {
                "temp_id": "latex",
                "text": "水性乳胶",
                "technical_subject": "锂离子电池隔膜的高固含量水性陶瓷浆料",
                "synonyms_zh": ["水性粘结剂"],
            },
            {
                "temp_id": "ceramic",
                "text": "陶瓷颗粒",
                "technical_subject": "锂离子电池隔膜的高固含量水性陶瓷浆料",
                "synonyms_zh": ["无机陶瓷颗粒"],
            },
            {
                "temp_id": "ratio",
                "text": "固含量50-65%",
                "technical_subject": "锂离子电池隔膜的高固含量水性陶瓷浆料",
            },
        ],
        "invention_search_profile": {
            "protected_subject": "锂离子电池隔膜的高固含量水性陶瓷浆料",
            "subject_synonyms_zh": [],
            "subject_synonyms_en": [],
            "classification_anchors": [],
            "invention_summary": "水性乳胶与陶瓷颗粒形成隔膜用水性陶瓷浆料",
            "common_context_features": [],
            "inventive_point_features": [
                {
                    "concept_id": "inv-ceramic",
                    "text": "陶瓷颗粒",
                    "feature_ids": ["ceramic"],
                    "synonyms_zh": [],
                    "synonyms_en": [],
                    "source_reference": "权利要求1",
                    "rationale": "陶瓷颗粒水性分散是发明点",
                    "preferred_scope": "full_text",
                },
            ],
        },
        "queries": [],
    }
    plan = InvalidityAnalysisEngine(FakeVisionClient(response)).plan_queries(
        claim_id="claim-material",
        expanded_claim_text=(
            "一种锂离子电池隔膜的高固含量水性陶瓷浆料，"
            "包括水性乳胶、陶瓷颗粒，固含量为50-65%。"
        ),
        target_images=["target.png"],
        patent_context={
            "title": "锂离子电池隔膜的高固含量水性陶瓷浆料及其加工方法"
        },
    )

    assert "锂离子电池隔膜" in plan.subject_synonyms_zh
    assert "水性陶瓷浆料" in plan.subject_synonyms_zh
    # 首轮固定五组：本案只形成标题客体+说明书核心发明点线（无申请人、
    # 无分类号、无功能效果改写词池），单锚点只取一个可识别材料。
    lanes = [
        item
        for item in plan.queries
        if item.query_variant == "title_object_plus_desc_inventive"
        and item.date_channel == "ordinary_prior_art"
    ]
    assert len(lanes) == 1
    lane = lanes[0]
    assert lane.search_scope == "full_text"
    assert lane.feature_terms == ["陶瓷颗粒"]
    assert "固含量50-65%" not in lane.feature_terms
    # 特征 OR 池补充：权利要求同义词进入可执行 OR 组。
    assert "陶瓷颗粒" in lane.expression
    assert "无机陶瓷颗粒" in lane.expression
    assert lane.provider_expression is not None
    assert lane.provider_expression.startswith("(TTL:(")


def test_i2_timer_angle_anchor_generates_concept_or_groups() -> None:
    response = {
        "technical_subject": "多边形计时器",
        "subject_synonyms_zh": ["多边形计时器", "计时器"],
        "subject_synonyms_en": ["polygonal timer", "timer"],
        "features": [
            {
                "temp_id": "f1",
                "text": "计时器可立面预设有盲人文点字符号",
                "technical_subject": "多边形计时器",
                "synonyms_zh": ["计时器", "可视时间符号"],
                "synonyms_en": ["time symbol"],
            },
            {
                "temp_id": "f2",
                "text": "角度运算单元",
                "technical_subject": "多边形计时器",
                "synonyms_zh": ["姿态识别模块"],
                "synonyms_en": ["attitude calculation unit"],
            },
        ],
        "invention_search_profile": {
            "protected_subject": "多边形计时器",
            "subject_synonyms_zh": ["多边形计时器", "计时器"],
            "subject_synonyms_en": ["polygonal timer", "timer"],
            "classification_anchors": [],
            "invention_summary": (
                "在用户可立面上显示时间符号，并通过角度运算单元感知姿态。"
            ),
            "common_context_features": [
                {
                    "concept_id": "context-display",
                    "text": "计时器外壳与显示符号",
                    "feature_ids": ["f1"],
                    "synonyms_zh": ["显示模块"],
                    "synonyms_en": ["display module"],
                    "source_reference": "说明书",
                    "rationale": "属于显示层语境",
                    "preferred_scope": "full_text",
                },
            ],
            "inventive_point_features": [
                {
                    "concept_id": "inv-time-symbol",
                    "text": "时间符号显示",
                    "feature_ids": ["f1"],
                    "synonyms_zh": ["可视时间符号"],
                    "synonyms_en": ["time symbol display"],
                    "source_reference": "权利要求1",
                    "rationale": "核心发明点之一",
                    "preferred_scope": "full_text",
                },
                {
                    "concept_id": "inv-angle-unit",
                    "text": "角度运算单元",
                    "feature_ids": ["f2"],
                    "synonyms_zh": ["角度运算模组", "角度模块"],
                    "synonyms_en": ["angle calculation unit", "attitude unit"],
                    "source_reference": "权利要求1",
                    "rationale": "核心发明点二",
                    "preferred_scope": "full_text",
                },
            ],
        },
        "queries": [],
    }

    plan = InvalidityAnalysisEngine(FakeVisionClient(response)).plan_queries(
        claim_id="claim-timer-angle",
        expanded_claim_text=(
            "一种多边形计时器，包括可立面预设有盲人文点字符号和可视时间符号；"
            "通过角度运算单元识别姿态并驱动显示。"
        ),
        target_images=["timer-angle.png"],
        patent_context={
            "title": "多边形计时器",
            "abstract": "该计时器可立面显示时间并具备角度运算单元。",
            "claims": [
                {
                    "claim_id": "claim-timer-angle",
                    "claim_text": (
                        "一种多边形计时器，包括可立面预设有盲人文点字符号和可视时间符号。"
                    ),
                }
            ],
        },
    )

    lane = next(
        item
        for item in plan.queries
        if item.query_variant == "title_object_plus_desc_inventive"
        and item.date_channel == "ordinary_prior_art"
    )
    expr = lane.expression.replace(" ", "")
    # 首轮固定五组：核心发明点取画像顺序首个，OR 组由画像冻结的概念词池
    # 组成，不含说明性短语碎片或顿号拼接词。
    assert "OR" in expr
    assert "盲人文点字符号" not in expr
    assert "可立面预设有" not in expr
    assert any(token in expr for token in ["计时器", "秒表", "闹钟"])
    assert "时间符号显示" in expr
    assert "可视时间符号" in expr
    assert "time symbol display" in lane.expression
    assert all(
        "、" not in term and "，" not in term and "," not in term
        for group in lane.feature_term_groups
        for term in group
    )
    # 多个发明点不复制同型线：第二发明点（角度运算单元）不进入首轮任何检索式。
    assert not any(
        "角度运算" in item.expression for item in plan.queries
    )


def test_i2_display_camera_anchor_is_required_for_inventive_point_focus() -> None:
    response = {
        "technical_subject": "智能后视镜",
        "subject_synonyms_zh": ["智能后视镜", "后视镜显示"],
        "subject_synonyms_en": ["smart rear-view mirror", "camera mirror"],
        "features": [
            {
                "temp_id": "f1",
                "text": "一种智能后视镜，包括显示屏和前置摄像头，外壳上设置有控制按键。",
                "technical_subject": "智能后视镜",
                "synonyms_zh": ["显示后视镜", "车载后视镜"],
                "synonyms_en": ["vehicle mirror"],
            },
            {
                "temp_id": "f2",
                "text": "计时器控制单元",
                "technical_subject": "智能后视镜",
                "synonyms_zh": ["定时模块"],
                "synonyms_en": ["timer module"],
            },
        ],
        "invention_search_profile": {
            "protected_subject": "智能后视镜",
            "subject_synonyms_zh": ["智能后视镜", "后视镜显示"],
            "subject_synonyms_en": ["smart rear-view mirror", "vehicle mirror"],
            "classification_anchors": [],
            "invention_summary": "镜子上显示屏与摄像头协同工作。",
            "common_context_features": [
                {
                    "concept_id": "context-housing",
                    "text": "镜体外壳",
                    "feature_ids": ["f1"],
                    "synonyms_zh": ["外壳"],
                    "synonyms_en": ["housing"],
                    "source_reference": "说明书",
                    "rationale": "说明书结构",
                    "preferred_scope": "claims",
                }
            ],
            "inventive_point_features": [
                {
                    "concept_id": "inv-timer",
                    "text": "计时器单元",
                    "feature_ids": ["f2"],
                    "synonyms_zh": ["定时器", "闹钟"],
                    "synonyms_en": ["timer", "clock"],
                    "source_reference": "权利要求1",
                    "rationale": "模型误提供的发明点候选",
                    "preferred_scope": "full_text",
                },
                {
                    "concept_id": "inv-angle",
                    "text": "角度运算单元",
                    "feature_ids": ["f2"],
                    "synonyms_zh": ["姿态传感器"],
                    "synonyms_en": ["attitude calculation unit"],
                    "source_reference": "权利要求1",
                    "rationale": "模型误提供的发明点候选",
                    "preferred_scope": "full_text",
                },
            ],
        },
        "queries": [],
    }

    plan = InvalidityAnalysisEngine(FakeVisionClient(response)).plan_queries(
        claim_id="claim-mirror-display-camera",
        expanded_claim_text=(
            "一种智能后视镜，包括显示屏和前置摄像头，"
            "并通过处理器实现图像显示与目标识别。"
        ),
        target_images=["mirror.png"],
        patent_context={
            "title": "智能后视镜",
            "abstract": "该镜子采用显示屏和前置摄像头实现信息显示与拍摄。",
        },
    )

    lane = next(
        item
        for item in plan.queries
        if item.query_variant == "title_object_plus_desc_inventive"
        and item.date_channel == "ordinary_prior_art"
    )
    display_terms = {"显示屏", "触摸屏", "显示器", "屏幕", "lcd", "oled", "screen", "display"}
    wrong_terms = {"计时器", "定时", "timer", "角度运算单元", "陀螺仪", "角度计"}
    lane_groups = [set(item) for item in lane.feature_term_groups]
    # 模型误标发明点（计时器/角度运算）时，按目标独立权利要求派生的显示
    # 核心构件概念前置为首轮核心发明点；错误候选不得进入首轮检索线。
    assert any(any(term in term_group for term in display_terms) for term_group in lane_groups)
    assert not any(any(term in term_group for term in wrong_terms) for term_group in lane_groups)
    assert "显示屏" in lane.expression
    assert not any(
        wrong in item.expression
        for item in plan.queries
        for wrong in ("计时器", "角度运算")
    )


def test_i2_water_gun_uses_inventive_full_text_and_claim_context_lanes() -> None:
    response = {
        "technical_subject": "水枪",
        "subject_synonyms_zh": ["液体喷射装置"],
        "subject_synonyms_en": ["water gun", "liquid projector"],
        "features": [
            {
                "temp_id": "f1",
                "text": "位置选择性联接装置",
                "technical_subject": "水枪",
                "synonyms_zh": ["可选择位置的联接装置"],
                "synonyms_en": ["position-selective coupling device"],
            },
            {
                "temp_id": "f2",
                "text": "阀",
                "technical_subject": "水枪",
                "synonyms_zh": ["控制阀"],
                "synonyms_en": ["valve"],
            },
            {
                "temp_id": "f3",
                "text": "罐",
                "technical_subject": "水枪",
                "synonyms_zh": ["压力罐"],
                "synonyms_en": ["tank"],
            },
        ],
        "invention_search_profile": {
            "protected_subject": "水枪",
            "subject_synonyms_zh": ["液体喷射装置"],
            "subject_synonyms_en": ["water gun", "liquid projector"],
            "classification_anchors": ["F41B 9/00"],
            "invention_summary": "通过位置选择性联接装置改变水枪组件的联接位置。",
            "common_context_features": [
                {
                    "concept_id": "context-fluid-components",
                    "text": "阀和罐",
                    "feature_ids": ["f2", "f3"],
                    "synonyms_zh": ["控制阀和压力罐"],
                    "synonyms_en": ["valve and tank"],
                    "source_reference": "权利要求1",
                    "rationale": "水枪液路的常见控制和储液构件",
                    "preferred_scope": "claims",
                },
            ],
            "inventive_point_features": [
                {
                    "concept_id": "inventive-coupling",
                    "text": "位置选择性联接装置",
                    "feature_ids": ["f1"],
                    "synonyms_zh": ["可选择位置的联接装置"],
                    "synonyms_en": ["position-selective coupling device"],
                    "source_reference": "权利要求1及具体实施方式",
                    "rationale": "具体联接位置选择关系最能说明发明点",
                    "preferred_scope": "full_text",
                },
            ],
        },
        "queries": [
            {
                "provider_kind": "patent",
                "purpose": "initial",
                "query_role": "inventive_point_precision",
                "search_scope": "full_text",
                "scope_reason": "具体联接关系更可能在说明书全文展开",
                "classification_anchors": ["F41B 9/00"],
                "language": "zh",
                "expression": "位置选择性联接装置",
                "feature_ids": ["f1"],
                "feature_terms": ["位置选择性联接装置"],
                "rationale": "分类号锚定水枪类别，发明点承担精确检索",
            },
            {
                "provider_kind": "patent",
                "purpose": "initial",
                "query_role": "claim_context_recall",
                "search_scope": "claims",
                "scope_reason": "类别结构通常写入权利要求",
                "language": "zh",
                "expression": "(水枪 OR 液体喷射装置) AND 阀 AND 罐",
                "feature_ids": ["f2", "f3"],
                "feature_terms": ["阀", "罐"],
                "rationale": "保护客体同义词与两个类别语境组组合",
            },
        ],
    }

    plan = InvalidityAnalysisEngine(FakeVisionClient(response)).plan_queries(
        claim_id="claim-water-gun",
        expanded_claim_text="一种水枪，包括位置选择性联接装置、阀和罐。",
        target_images=["water-gun.png"],
        patent_context={
            "title": "水枪",
            "abstract": "一种可改变联接位置的水枪。",
            "bibliographic_data": {"classifications": ["F41B 9/00 (2006.01)"]},
            "claims": [{"claim_id": "1", "claim_text": "一种水枪……"}],
            "specification": {"具体实施方式": "位置选择性联接装置……"},
        },
    )

    inventive = next(
        item
        for item in plan.queries
        if item.provider_kind == "patent"
        and item.date_channel == "ordinary_prior_art"
        and item.query_variant == "title_object_plus_desc_inventive"
    )
    classification_lane = next(
        item
        for item in plan.queries
        if item.provider_kind == "patent"
        and item.date_channel == "ordinary_prior_art"
        and item.query_variant == "classification_plus_desc_inventive"
    )
    assert len(inventive.feature_terms) == 1
    assert inventive.search_scope == "full_text"
    assert inventive.provider_expression is not None
    assert inventive.provider_expression.startswith("(TTL:(")
    assert "DESC:" in inventive.provider_expression
    # 客体相关分类号加说明书核心发明点：客体由分类号承载，正文只含发明点组。
    assert classification_lane.classification_anchors == ["F41B9/00"]
    assert classification_lane.provider_expression is not None
    assert "IPC:(F41B9/00)" in classification_lane.provider_expression
    assert "DESC:" in classification_lane.provider_expression
    assert not classification_lane.expression.startswith("(水枪")
    assert [
        item.feature_ids
        for item in plan.invention_search_profile.common_context_features
    ] == [[plan.limitations[1].feature_id], [plan.limitations[2].feature_id]]


def test_i2_first_round_fixed_lanes_replace_architecture_recall() -> None:
    response = _query_plan_response()
    response["technical_subject"] = "液体喷射装置"
    response["subject_synonyms_zh"] = ["喷射器"]
    response["features"] = [
        {
            "temp_id": "tank",
            "text": "压力罐",
            "technical_subject": "液体喷射装置",
            "synonyms_en": ["pressure tank"],
        },
        {
            "temp_id": "valve",
            "text": "带有阀导管的阀",
            "technical_subject": "液体喷射装置",
            "synonyms_en": ["valve with valve conduit"],
        },
        {
            "temp_id": "coupling",
            "text": "位置选择性联接装置",
            "technical_subject": "液体喷射装置",
            "synonyms_zh": ["选择性联接"],
        },
    ]
    response["invention_search_profile"] = {
        "protected_subject": "液体喷射装置",
        "subject_synonyms_zh": ["喷射器"],
        "subject_synonyms_en": ["liquid projector"],
        "classification_anchors": [],
        "invention_summary": "通过选择性联接控制压力液体喷射。",
        "common_context_features": [
            {
                "concept_id": "context-tank",
                "text": "压力罐",
                "feature_ids": ["tank"],
                "source_reference": "权利要求1",
                "rationale": "储液构件",
                "preferred_scope": "claims",
            },
            {
                "concept_id": "context-valve",
                "text": "带有阀导管的阀",
                "feature_ids": ["valve"],
                "source_reference": "权利要求1",
                "rationale": "流路控制构件",
                "preferred_scope": "claims",
            },
        ],
        "inventive_point_features": [
            {
                "concept_id": "inventive-coupling",
                "text": "位置选择性联接装置",
                "feature_ids": ["coupling"],
                "source_reference": "权利要求1",
                "rationale": "联接关系",
                "preferred_scope": "full_text",
            }
        ],
    }
    response["queries"] = []

    plan = InvalidityAnalysisEngine(
        FakeVisionClient(response, response)
    ).plan_queries(
        claim_id="claim-components",
        expanded_claim_text="一种液体喷射装置，包括压力罐、带有阀导管的阀和位置选择性联接装置。",
        target_images=["target.png"],
    )
    # 首轮固定五组（宪章 2.15）：旧 system_architecture_recall 结构召回线
    # 不再首轮发射；标题客体+说明书核心发明点线取而代之。
    assert not any(
        item.query_variant == "system_architecture_recall"
        for item in plan.queries
    )
    lane = next(
        item for item in plan.queries
        if item.query_variant == "title_object_plus_desc_inventive"
        and item.date_channel == "ordinary_prior_art"
    )
    assert lane.feature_terms == ["位置选择性联接装置"]
    assert lane.expression.startswith("(液体喷射装置 OR 喷射器")
    assert "选择性联接" in lane.expression
    assert lane.provider_expression is not None
    assert lane.provider_expression.startswith("(TTL:(")
    assert "DESC:" in lane.provider_expression


def test_i2_first_round_component_relation_concept_enters_fixed_lane() -> None:
    response = {
        "technical_subject": "无线耳机",
        "subject_synonyms_zh": ["耳机"],
        "subject_synonyms_en": ["wireless earphone"],
        "features": [
            {
                "temp_id": "body-hook",
                "text": "耳机本体和可旋转式连接于耳机本体的耳挂",
                "technical_subject": "无线耳机",
                "synonyms_zh": ["耳挂组件"],
            },
            {
                "temp_id": "rotary-joint",
                "text": "圆形凸台与套圈形成可旋转连接结构",
                "technical_subject": "无线耳机",
                "synonyms_zh": ["旋转连接结构"],
            },
            {
                "temp_id": "detent",
                "text": "套圈的定位卡槽与耳机本体的定位卡凸适配",
                "technical_subject": "无线耳机",
                "synonyms_zh": ["定位结构"],
            },
        ],
        "invention_search_profile": {
            "protected_subject": "无线耳机",
            "subject_synonyms_zh": ["耳机"],
            "subject_synonyms_en": ["wireless earphone"],
            "classification_anchors": [],
            "invention_summary": "耳挂通过旋转连接结构相对耳机本体转动并定位。",
            "common_context_features": [],
            "inventive_point_features": [
                {
                    "concept_id": "rotary",
                    "text": "圆形凸台与套圈形成可旋转连接结构",
                    "feature_ids": ["rotary-joint"],
                    "source_reference": "权利要求1",
                    "rationale": "旋转连接关系",
                    "preferred_scope": "full_text",
                },
                {
                    "concept_id": "detent",
                    "text": "定位卡槽与定位卡凸适配",
                    "feature_ids": ["detent"],
                    "source_reference": "权利要求1",
                    "rationale": "角度定位关系",
                    "preferred_scope": "full_text",
                },
            ],
            "mechanism_model": {
                "summary": "耳挂相对耳机本体旋转并定位",
                "elements": [
                    {
                        "element_id": "body",
                        "kind": "component_role",
                        "text": "耳机本体",
                        "feature_ids": ["body-hook"],
                        "source_reference": "权利要求1",
                        "rationale": "主体构件",
                        "original_terms": ["耳机本体"],
                    },
                    {
                        "element_id": "hook",
                        "kind": "component_role",
                        "text": "耳挂",
                        "feature_ids": ["body-hook"],
                        "source_reference": "权利要求1",
                        "rationale": "佩戴和转动构件",
                        "original_terms": ["耳挂"],
                        "role_equivalent_terms": ["ear hook"],
                    },
                    {
                        "element_id": "rotation",
                        "kind": "motion_state_relation",
                        "text": "耳挂相对耳机本体旋转",
                        "feature_ids": ["rotary-joint"],
                        "source_reference": "权利要求1",
                        "rationale": "相对转动关系",
                        "original_terms": ["可旋转连接"],
                        "operation_terms": ["旋转", "转动"],
                    },
                ],
            },
        },
        "queries": [],
    }

    plan = InvalidityAnalysisEngine(FakeVisionClient(response, response)).plan_queries(
        claim_id="claim-wireless-earphone",
        expanded_claim_text=(
            "一种无线耳机，包括耳机本体、耳挂、圆形凸台、套圈以及定位结构。"
        ),
        target_images=["target.png"],
    )

    lane = next(
        item
        for item in plan.queries
        if item.query_variant == "title_object_plus_desc_inventive"
        and item.date_channel == "ordinary_prior_art"
    )
    # 首轮固定五组（宪章 2.15）：旧构件关系召回线不再首轮发射；核心发明点
    # 关系概念（画像首个发明点：圆形凸台与套圈的旋转连接关系）经 ≤6 字原子化
    # 后进入标题客体+说明书核心发明点线。
    assert not any(
        item.query_variant == "system_architecture_recall"
        for item in plan.queries
    )
    assert lane.search_scope == "full_text"
    assert "圆形凸台" in lane.expression
    assert "套圈" in lane.expression
    assert "旋转" in lane.expression
    assert lane.provider_expression is not None
    assert lane.provider_expression.startswith("(TTL:(")
    assert not any(
        term in {"适配", "套设", "匹配", "连接", "设置"}
        or term == "适配 sleeve"
        for query in plan.queries
        for term in query.feature_terms
    )


def test_i2_first_round_drops_target_citation_and_effect_lanes() -> None:
    response = _query_plan_response()
    response["invention_search_profile"]["invention_summary"] = (
        "水枪通过阀控制形成短促水流喷射"
    )
    patent_context = {
        "patent_number": "CN216205649U",
        "title": "水枪",
        "specification": {
            "技术领域": "本实用新型涉及用于射出短促的水流迸射的水枪。",
            "背景技术": (
                "现有的短促水流玩具水枪已公开于 WO2018/215646A1，"
                "其通过阀控制水流。"
            ),
        },
    }
    plan = InvalidityAnalysisEngine(FakeVisionClient(response)).plan_queries(
        claim_id="claim-citation",
        expanded_claim_text="一种水枪，包括压力罐和阀。",
        target_images=["target.png"],
        patent_context=patent_context,
        max_queries=3,
    )
    # 首轮检索组合固定为五组检索线（宪章 2.15）：目标引证回查线与目标
    # 效果召回线不再首轮注入，只在 gap/后续轮次使用。
    assert not any(
        item.query_variant == "target_citation_lookup" for item in plan.queries
    )
    assert not any(
        item.query_variant == "target_effect_recall" for item in plan.queries
    )
    assert len(plan.queries) <= 3
    # 两条确定性线的构造函数保留；直接调用验证其行为不变。
    citations = _target_citation_queries(
        patent_context=patent_context,
        technical_subject="水枪",
        claim_id="claim-citation",
        iteration_number=1,
    )
    assert citations[0].target_citation == "WO2018215646A1"
    assert citations[0].provider_expression == "PN:(WO2018215646A1)"
    effects = _target_effect_queries(
        patent_context=patent_context,
        technical_subject="水枪",
        invention_summary="水枪通过阀控制形成短促水流喷射",
        claim_id="claim-citation",
        iteration_number=1,
        classification_anchors=[],
        classification_anchor_sources={},
        classification_anchor_roles={},
    )
    assert effects
    assert "短促水流迸射" in effects[0].target_effect_terms


def test_i2_derives_compact_subject_and_rejects_unrelated_embodiment_effect() -> None:
    response = _query_plan_response()
    response["technical_subject"] = "适配于不同耳朵的无线耳机"
    response["subject_synonyms_zh"] = []
    response["invention_search_profile"]["protected_subject"] = (
        "适配于不同耳朵的无线耳机"
    )
    response["invention_search_profile"]["subject_synonyms_zh"] = []
    response["invention_search_profile"]["invention_summary"] = (
        "通过耳挂旋转定位适应不同耳朵"
    )
    plan = InvalidityAnalysisEngine(FakeVisionClient(response)).plan_queries(
        claim_id="claim-earphone",
        expanded_claim_text="一种无线耳机，包括可旋转连接的耳挂。",
        target_images=["target.png"],
        patent_context={
            "title": "适配于不同耳朵的无线耳机",
            "specification": {
                "实用新型内容": "设置散热孔用于电池散热；耳挂通过旋转调整佩戴位置。"
            },
        },
    )

    assert "无线耳机" in plan.subject_synonyms_zh
    assert not any(
        "电池散热" in " ".join(item.target_effect_terms)
        for item in plan.queries
        if item.query_variant == "target_effect_recall"
    )


def test_i2_first_round_motion_relation_concept_enters_fixed_lane() -> None:
    response = {
        "technical_subject": "适配于不同耳朵的无线耳机",
        "subject_synonyms_zh": ["无线耳机"],
        "subject_synonyms_en": ["wireless earphone"],
        "features": [
            {
                "temp_id": "body",
                "text": "耳机本体",
                "technical_subject": "适配于不同耳朵的无线耳机",
            },
            {
                "temp_id": "earhook",
                "text": "耳挂",
                "technical_subject": "适配于不同耳朵的无线耳机",
            },
            {
                "temp_id": "rotation",
                "text": "耳挂相对耳机本体旋转连接",
                "technical_subject": "适配于不同耳朵的无线耳机",
                "synonyms_zh": ["旋转", "转动"],
                "synonyms_en": ["rotatable connection"],
            },
        ],
        "invention_search_profile": {
            "protected_subject": "适配于不同耳朵的无线耳机",
            "subject_synonyms_zh": ["无线耳机"],
            "subject_synonyms_en": ["wireless earphone"],
            "classification_anchors": [],
            "invention_summary": "耳挂通过旋转改变相对位置以适配不同耳朵",
            "common_context_features": [
                {
                    "concept_id": "context-body",
                    "text": "耳机本体",
                    "feature_ids": ["body"],
                    "source_reference": "权利要求1",
                    "rationale": "耳机类别构件",
                    "preferred_scope": "claims",
                },
                {
                    "concept_id": "context-earhook",
                    "text": "耳挂",
                    "feature_ids": ["earhook"],
                    "source_reference": "权利要求1",
                    "rationale": "佩戴子组件",
                    "preferred_scope": "claims",
                },
            ],
            "inventive_point_features": [
                {
                    "concept_id": "inventive-rotation",
                    "text": "耳挂相对耳机本体旋转连接",
                    "feature_ids": ["rotation"],
                    "synonyms_zh": ["旋转", "转动"],
                    "synonyms_en": ["rotatable connection"],
                    "source_reference": "权利要求1",
                    "rationale": "核心运动关系",
                    "preferred_scope": "title_abstract",
                }
            ],
        },
        "queries": [],
    }

    plan = InvalidityAnalysisEngine(FakeVisionClient(response, response)).plan_queries(
        claim_id="claim-earphone-title",
        expanded_claim_text=(
            "一种适配于不同耳朵的无线耳机，包括耳机本体、耳挂，"
            "所述耳挂相对耳机本体旋转连接。"
        ),
        target_images=["target.png"],
    )

    lane = next(
        item
        for item in plan.queries
        if item.query_variant == "title_object_plus_desc_inventive"
        and item.date_channel == "ordinary_prior_art"
    )
    # 首轮固定五组（宪章 2.15）：旧标题摘要构件动作线不再首轮发射；核心
    # 运动关系概念原子化后进入标题客体+说明书核心发明点线。
    assert not any(
        item.query_variant == "title_abstract_concept"
        for item in plan.queries
    )
    assert lane.search_scope == "full_text"
    assert "耳挂" in lane.expression
    assert "旋转" in lane.expression
    assert "转动" in lane.expression
    assert lane.provider_expression is not None
    assert lane.provider_expression.startswith("(TTL:(")
    assert all("CN216649931" not in item.expression for item in plan.queries)


def test_i2_accepts_partial_keywords_from_compound_water_gun_concepts() -> None:
    response = {
        "technical_subject": "水枪",
        "subject_synonyms_zh": ["液体喷射装置"],
        "subject_synonyms_en": ["water gun", "liquid projector"],
        "features": [
            {
                "temp_id": "f1",
                "text": "压力罐",
                "technical_subject": "水枪",
                "synonyms_zh": ["加压液体罐"],
                "synonyms_en": ["pressure tank"],
            },
            {
                "temp_id": "f2",
                "text": "带有阀导管的阀",
                "technical_subject": "水枪",
                "synonyms_zh": ["阀导管"],
                "synonyms_en": ["valve conduit"],
            },
            {
                "temp_id": "f3",
                "text": "可移动的阀杆",
                "technical_subject": "水枪",
                "synonyms_zh": ["阀杆"],
                "synonyms_en": ["valve stem"],
            },
            {
                "temp_id": "f4",
                "text": "本体和阀杆经由位置选择性联接装置联接",
                "technical_subject": "水枪",
                "synonyms_zh": ["位置选择性联接装置"],
                "synonyms_en": ["position-selective coupling device"],
            },
            {
                "temp_id": "f5",
                "text": "联接装置在中间位置选择性联接本体和阀杆",
                "technical_subject": "水枪",
                "synonyms_zh": ["中间位置", "中间位置、选择性联接"],
                "synonyms_en": ["intermediate position"],
            },
        ],
        "invention_search_profile": {
            "protected_subject": "水枪",
            "subject_synonyms_zh": ["液体喷射装置"],
            "subject_synonyms_en": ["water gun", "liquid projector"],
            "classification_anchors": [],
            "invention_summary": "通过位置选择性联接关系控制水枪阀杆的不同工作位置。",
            "common_context_features": [
                {
                    "concept_id": "context-fluid-path",
                    "text": "压力罐、带阀导管的阀和可移动阀杆",
                    "feature_ids": ["f1", "f2", "f3"],
                    "synonyms_zh": ["压力罐与阀导管及阀杆"],
                    "synonyms_en": ["pressure tank, valve conduit and valve stem"],
                    "source_reference": "权利要求1",
                    "rationale": "水枪液路的类别技术语境",
                    "preferred_scope": "claims",
                },
            ],
            "inventive_point_features": [
                {
                    "concept_id": "inventive-position-coupling",
                    "text": "位置选择性联接装置，在中间位置选择性联接本体和阀杆",
                    "feature_ids": ["f4", "f5"],
                    "synonyms_zh": ["在中间位置选择性联接本体和阀杆的联接装置"],
                    "synonyms_en": [
                        "position-selective coupling device at an intermediate position"
                    ],
                    "source_reference": "权利要求1及具体实施方式",
                    "rationale": "位置与联接关系共同说明发明点",
                    "preferred_scope": "full_text",
                },
            ],
        },
        "queries": [
            {
                "provider_kind": "patent",
                "purpose": "initial",
                "query_role": "inventive_point_precision",
                "search_scope": "full_text",
                "scope_reason": "联接与位置关系更可能在说明书全文出现",
                "language": "zh",
                "expression": "水枪 AND 位置选择性联接装置 AND 中间位置、选择性联接",
                "feature_ids": ["f4", "f5"],
                "feature_terms": ["位置选择性联接装置", "中间位置、选择性联接"],
                "concept_ids": [],
                "rationale": "用复合发明点中的两个简洁关键词检索",
            },
            {
                "provider_kind": "patent",
                "purpose": "initial",
                "query_role": "claim_context_recall",
                "search_scope": "claims",
                "scope_reason": "液路构件通常写入权利要求",
                "language": "zh",
                "expression": "水枪 AND 压力罐 AND 阀导管 AND 阀杆",
                "feature_ids": ["f1", "f2", "f3"],
                "feature_terms": ["压力罐", "阀导管", "阀杆"],
                "concept_ids": [],
                "rationale": "用三个类别语境的部分构件词检索",
            },
        ],
    }

    plan = InvalidityAnalysisEngine(FakeVisionClient(response)).plan_queries(
        claim_id="claim-water-gun-compound",
        expanded_claim_text=(
            "一种水枪，包括压力罐、带有阀导管的阀、可移动的阀杆，"
            "本体和阀杆经由位置选择性联接装置联接，"
            "所述联接装置在中间位置选择性联接本体和阀杆。"
        ),
        target_images=["water-gun.png"],
        patent_context={
            "title": "水枪",
            "abstract": "一种具有位置选择性联接关系的水枪。",
            "claims": [{"claim_id": "1", "claim_text": "一种水枪……"}],
            "specification": {"具体实施方式": "联接装置在中间位置联接本体和阀杆。"},
        },
    )

    lane = next(
        item
        for item in plan.queries
        if item.query_variant == "title_object_plus_desc_inventive"
        and item.date_channel == "ordinary_prior_art"
    )

    # 首轮固定五组（宪章 2.15）：复合发明点概念进入标题客体+说明书核心发明点
    # 线；长概念词经 ≤6 字原子化拆成 OR 组与独立 AND 原子词，顿号/逗号粘连的
    # 碎片不得进入任何检索组。
    assert lane.expression.startswith(
        "(水枪 OR 液体喷射装置 OR water gun OR liquid projector) AND "
    )
    assert "选择性联接" in lane.expression
    assert lane.provider_expression is not None
    assert lane.provider_expression.startswith("(TTL:(")
    assert lane.feature_terms == ["位置选择性联接装置"]
    assert any(
        "位置选择性联接装置" in group for group in lane.feature_term_groups
    )
    assert "中间位置" not in lane.feature_term_groups[0]
    assert "中间位置、选择性联接" not in lane.feature_term_groups[0]
    assert all(
        "、" not in term and "，" not in term and "," not in term
        for query in plan.queries
        for group in query.feature_term_groups
        for term in group
    )
    assert all(
        "锁定机构" not in group and "解锁机构" not in group
        for query in plan.queries
        for group in query.feature_term_groups
    )
    # 旧首轮多分辨率检索线（客体加双发明点、客体加单发明点、结构召回）
    # 不再首轮发射。
    assert not any(
        item.query_variant
        in {
            "object_plus_two_inventive_points",
            "object_plus_inventive_point",
            "system_architecture_recall",
        }
        for item in plan.queries
    )


def test_i2_first_round_classification_lane_uses_ipc_plus_desc_inventive() -> None:
    response = _query_plan_response()
    response["invention_search_profile"]["classification_anchors"] = ["F16K 1/00"]
    response["queries"] = [
        {
            "provider_kind": "patent",
            "purpose": "initial",
            "query_role": "inventive_point_precision",
            "search_scope": "full_text",
            "scope_reason": "发明点关系更可能在全文出现",
            "classification_anchors": ["F16K 1/00"],
            "language": "zh",
            "expression": "F16K 1/00 AND 升降拖布",
            "feature_ids": ["f2"],
            "feature_terms": ["升降拖布"],
            "rationale": "分类建议加真实发明点关键词",
        },
        {
            "provider_kind": "patent",
            "purpose": "initial",
            "query_role": "claim_context_recall",
            "search_scope": "claims",
            "scope_reason": "类别构件通常写入权利要求",
            "language": "zh",
            "expression": "扫地机器人 AND 移动底盘",
            "feature_ids": ["f1"],
            "feature_terms": ["移动底盘"],
            "rationale": "保护客体加一个真实类别语境词",
        },
    ]
    client = FakeVisionClient(response)
    plan = InvalidityAnalysisEngine(client).plan_queries(
        claim_id="claim-1",
        expanded_claim_text="一种扫地机器人，包括移动底盘和升降拖布。",
        target_images=["target.png"],
        patent_context={"title": "扫地机器人", "bibliographic_data": {}},
    )

    # 首轮固定五组（宪章 2.15）：分类号线是 分类号+说明书核心发明点（③），
    # 分类号走 IPC 前缀、发明点词走 DESC，客体词只留在标题客体线（②）。
    classification_lane = next(
        item
        for item in plan.queries
        if item.query_variant == "classification_plus_desc_inventive"
        and item.date_channel == "ordinary_prior_art"
    )
    assert classification_lane.expression
    assert not classification_lane.subject_terms
    assert "清洁件" in classification_lane.expression
    assert classification_lane.classification_anchors == ["F16K1/00"]
    assert classification_lane.classification_anchor_sources == {
        "F16K1/00": "model_suggested"
    }
    assert classification_lane.provider_expression is not None
    assert classification_lane.provider_expression.startswith("(IPC:(F16K1/00)) AND (DESC:(")
    subject_lane = next(
        item
        for item in plan.queries
        if item.query_variant == "title_object_plus_desc_inventive"
        and item.date_channel == "ordinary_prior_art"
    )
    assert subject_lane.subject_terms == ["扫地机器人"]
    assert subject_lane.provider_expression is not None
    assert subject_lane.provider_expression.startswith("(TTL:(")
    assert not any(
        item.query_variant == "subject_classification_plus_inventive_point"
        for item in plan.queries
    )
    assert plan.invention_search_profile is not None
    assert plan.invention_search_profile.classification_anchor_sources == {
        "F16K1/00": "model_suggested"
    }
    assert len(client.calls) == 1


def test_i2_first_round_core_inventive_anchor_enters_fixed_lane() -> None:
    plan = InvalidityAnalysisEngine(
        FakeVisionClient(_headphone_query_plan_response())
    ).plan_queries(
        claim_id="claim-headphone",
        expanded_claim_text="一种开放式头戴耳机，包括头戴、发音单元及发音模组。",
        target_images=["target-figure.png"],
    )

    # 首轮固定五组（宪章 2.15）：旧结构召回线不再发射；画像核心发明点概念
    # （贯通耳侧与外侧的中空孔）经原子化后进入标题客体+说明书核心发明点线。
    patent_query = next(
        item
        for item in plan.queries
        if item.provider_kind == "patent"
        and item.date_channel == "ordinary_prior_art"
        and item.query_variant == "title_object_plus_desc_inventive"
    )

    assert patent_query.feature_ids == ["claim-headphone-f-3ce8a460b505"]
    assert patent_query.expression.startswith("耳机 AND ")
    assert "贯通中空孔" in patent_query.expression
    assert not any(
        item.query_variant == "system_architecture_recall"
        for item in plan.queries
    )


def test_i2_first_round_ignores_model_returned_npl_query() -> None:
    response = _headphone_query_plan_response()
    response["queries"][0]["provider_kind"] = "npl"

    plan = InvalidityAnalysisEngine(FakeVisionClient(response)).plan_queries(
        claim_id="claim-headphone",
        expanded_claim_text="一种开放式头戴耳机，包括头戴、发音单元及发音模组。",
        target_images=["target-figure.png"],
    )

    # 首轮固定五组（宪章 2.15）：首轮只有专利库五组检索线，不再保留 NPL
    # 首轮线；模型返回的查询（含 npl 线）一律不采用，以确定性编译为准。
    assert not any(item.provider_kind == "npl" for item in plan.queries)
    patent_query = next(
        item
        for item in plan.queries
        if item.provider_kind == "patent"
        and item.date_channel == "ordinary_prior_art"
        and item.query_variant == "title_object_plus_desc_inventive"
    )
    assert patent_query.technical_subject == "开放式头戴耳机"
    assert patent_query.expression
    assert any("未采用" in note for note in plan.portfolio_warnings)


def test_i2_first_round_has_no_npl_lane_and_uses_profile_classification_anchor() -> None:
    response = _query_plan_response()
    response["invention_search_profile"].update(
        {
            "classification_anchors": ["B25J11/00"],
            "classification_anchor_sources": {"B25J11/00": "model_suggested"},
            "classification_anchor_roles": {"B25J11/00": "subject"},
        }
    )
    response["queries"].insert(
        1,
        {
            "provider_kind": "patent",
            "purpose": "initial",
            "query_role": "title_abstract_concept",
            "query_variant": "object_plus_inventive_classification",
            "search_scope": "title_abstract",
            "scope_reason": "客体名称可能出现在标题摘要",
            "language": "zh",
            "expression": "扫地机器人",
            "subject_terms": ["扫地机器人"],
            "classification_anchors": ["B25J11/00"],
            "feature_ids": [],
            "feature_terms": [],
            "rationale": "客体加专利分类号",
        },
    )

    plan = InvalidityAnalysisEngine(FakeVisionClient(response)).plan_queries(
        claim_id="claim-1",
        expanded_claim_text="一种扫地机器人，包括移动底盘、可升降清洁件和地面传感器。",
        target_images=["target-figure.png"],
    )

    # 首轮固定五组（宪章 2.15）：首轮不再有 NPL 检索线；画像冻结的客体相关
    # 分类号进入 分类号+说明书核心发明点 线（③），走 IPC 前缀；模型返回的
    # 裸客体+分类号线不采用。
    assert not any(item.provider_kind == "npl" for item in plan.queries)
    classification_lane = next(
        item
        for item in plan.queries
        if item.query_variant == "classification_plus_desc_inventive"
        and item.date_channel == "ordinary_prior_art"
    )
    assert classification_lane.classification_anchors == ["B25J11/00"]
    assert classification_lane.provider_expression is not None
    assert classification_lane.provider_expression.startswith("(IPC:(B25J11/00))")
    assert not any(item.expression == "扫地机器人" for item in plan.queries)


def test_i2_first_round_ignores_model_compact_expression_query() -> None:
    response = _headphone_query_plan_response()
    response["queries"][0].update(
        {
            "expression": "开放式头戴耳机 头箍 扬声器模组",
            "feature_ids": ["f2", "f3"],
            "feature_terms": [],
        }
    )

    plan = InvalidityAnalysisEngine(FakeVisionClient(response)).plan_queries(
        claim_id="claim-headphone",
        expanded_claim_text="一种开放式头戴耳机，包括头戴、发音单元及发音模组。",
        target_images=["target-figure.png"],
    )

    # 首轮固定五组（宪章 2.15）：模型返回的紧凑检索式不采用，首轮线全部由
    # 画像事实确定性编译；旧结构召回线不再发射。
    patent_query = next(
        item
        for item in plan.queries
        if item.provider_kind == "patent"
        and item.date_channel == "ordinary_prior_art"
        and item.query_variant == "title_object_plus_desc_inventive"
    )
    assert patent_query.expression.startswith("耳机 AND ")
    assert not any(
        item.query_variant == "system_architecture_recall"
        for item in plan.queries
    )
    assert any("未采用" in note for note in plan.portfolio_warnings)


def test_i2_accepts_subject_plus_one_real_feature_under_two_of_three_rule() -> None:
    response = _headphone_query_plan_response()
    response["queries"].insert(
        0,
        {
            "provider_kind": "patent",
            "purpose": "initial",
            "query_role": "title_abstract_concept",
            "query_variant": "title_abstract_concept",
            "search_scope": "title_abstract",
            "language": "zh",
            "expression": "开放式头戴耳机 中空孔 贯通耳侧 外侧",
            # f4 is declared but no f4-specific term is actually in expression.
            "feature_ids": ["f4", "f5"],
            "feature_terms": ["贯通耳侧", "中空孔"],
            "rationale": "多个词仍全部来自同一项中空孔特征",
        },
    )

    plan = InvalidityAnalysisEngine(FakeVisionClient(response)).plan_queries(
        claim_id="claim-headphone",
        expanded_claim_text="一种开放式头戴耳机，包括头戴、发音单元及中空孔。",
        target_images=["target-figure.png"],
    )

    # 首轮固定五组（宪章 2.15）：标题客体+说明书核心发明点线（②）以
    # 客体+发明点两类信息通过守门，绑定中空孔特征。
    accepted = next(
        item
        for item in plan.queries
        if item.query_variant == "title_object_plus_desc_inventive"
        and item.date_channel == "ordinary_prior_art"
    )
    assert accepted.feature_ids == [plan.limitations[4].feature_id]
    assert accepted.subject_terms == ["开放式头戴耳机"]
    assert "中空孔" in accepted.expression or "空孔" in accepted.expression


def test_i2_initial_round_canonicalises_model_generated_purpose_and_objective() -> None:
    response = _query_plan_response()
    for query in response["queries"]:
        query["purpose"] = "novelty_search"
        query["search_objective"] = "compare_all_features"
    client = FakeVisionClient(response)

    plan = InvalidityAnalysisEngine(client).plan_queries(
        claim_id="claim-1",
        expanded_claim_text="一种扫地机器人，包括移动底盘、可升降清洁件和地面传感器。",
        target_images=["target-figure.png"],
    )

    assert plan.queries
    assert {item.purpose for item in plan.queries} == {"initial"}
    assert {item.search_objective for item in plan.queries} == {
        "full_claim_single_reference"
    }
    prompt = str(client.calls[0]["prompt"])
    assert "purpose 必须严格写 initial" in prompt
    assert (
        "target_gap_type 只能写 feature_gap、evidence_gap、date_gap 或 combination_gap"
        in prompt
    )
    assert "忠实名词短语" in prompt
    assert "简洁检索短语" in prompt
    assert "synonyms_en 只能写纯英文术语" in prompt
    assert "through-hole" in prompt


def test_i2_gap_query_must_target_only_remaining_difference_features() -> None:
    initial_client = FakeVisionClient(_query_plan_response())
    initial = InvalidityAnalysisEngine(initial_client).plan_queries(
        claim_id="claim-1",
        expanded_claim_text="展开权利要求文本",
        target_images=["target.png"],
    )
    f1, f2, f3 = [item.feature_id for item in initial.limitations]
    gap_profile = (
        initial.invention_search_profile.model_dump(mode="json")
        if initial.invention_search_profile
        else {}
    )
    gap_profile["subject_core_terms_zh"] = ["机器人"]
    gap_profile["subject_core_terms_en"] = ["robot", "robotic device"]
    gap_response = {
        "technical_subject": "扫地机器人",
        "invention_search_profile": gap_profile,
        "queries": [
            {
                "provider_kind": "patent",
                "purpose": "feature_uncovered",
                "query_role": "gap_followup",
                "query_variant": "gap_followup",
                "search_scope": "full_text",
                "search_objective": "gap_or_combination",
                "date_channel": "ordinary_prior_art",
                "target_gap_type": "feature_gap",
                "language": "zh",
                "expression": "扫地机器人 移动底盘 升降拖布",
                "feature_ids": [f1, f2],
                "feature_terms": ["移动底盘", "升降拖布"],
                "rationale": "does not target the current gap",
            },
            {
                "provider_kind": "patent",
                "purpose": "feature_uncovered",
                "query_role": "gap_followup",
                "query_variant": "gap_followup",
                "search_scope": "full_text",
                "search_objective": "gap_or_combination",
                "date_channel": "ordinary_prior_art",
                "target_gap_type": "feature_gap",
                "language": "bilingual",
                "expression": (
                    "(机器人 OR robot OR robotic device) AND "
                    "(地面识别传感器 OR floor type sensor)"
                ),
                "subject_terms": ["机器人", "robot", "robotic device"],
                "feature_ids": [f3],
                "feature_terms": ["地面识别传感器"],
                "feature_term_groups": [
                    ["地面识别传感器", "floor type sensor"]
                ],
                "rationale": "only the remaining difference feature",
            },
        ],
    }
    gap_client = FakeVisionClient(gap_response)
    reuse_audit = [{"feature_id": f1, "document_id": "D2", "covered": True}]
    plan = InvalidityAnalysisEngine(gap_client).plan_queries(
        claim_id="claim-1",
        expanded_claim_text="展开权利要求文本",
        target_images=["target.png"],
        iteration_number=2,
        round_kind="gap",
        existing_limitations=initial.limitations,
        gap_feature_ids=[f3],
        closest_prior_art_context={"document_id": "D1"},
        covered_difference_feature_ids=[f1, f2],
        existing_corpus_reuse=reuse_audit,
        previous_iteration_failure_reason="上一轮只命中宽泛主题文献",
        previous_query_expressions=["扫地机器人 传感器"],
    )

    assert {item.search_objective for item in plan.queries} == {
        "gap_or_combination"
    }
    gap_queries = [
        item for item in plan.queries if item.search_objective == "gap_or_combination"
    ]
    primary_gap = next(item for item in gap_queries if item.provider_kind == "patent")
    primary_groups = _boolean_or_members(primary_gap.expression)
    assert len(primary_groups) == 2
    assert all(_has_bilingual_terms(group) for group in primary_groups)
    assert "扫地机器人" not in primary_groups[0]
    assert any("传感器" in item for item in primary_groups[1])
    assert "floor type sensor" in primary_groups[1]
    assert all(item.feature_ids == [f3] for item in gap_queries)
    assert all(item.gap_feature_ids == [f3] for item in gap_queries)
    assert all(item.gap_search_iteration == 1 for item in gap_queries)
    assert plan.gap_search_iteration == 1
    assert plan.covered_difference_feature_ids == sorted([f1, f2])
    assert plan.uncovered_difference_feature_ids == [f3]
    assert plan.existing_corpus_reuse == reuse_audit
    assert plan.previous_iteration_failure_reason == "上一轮只命中宽泛主题文献"
    assert all(
        item.previous_iteration_failure_reason == "上一轮只命中宽泛主题文献"
        and item.covered_difference_feature_ids == sorted([f1, f2])
        and item.uncovered_difference_feature_ids == [f3]
        for item in gap_queries
    )
    assert primary_gap.anchor_document_id == "D1"
    assert "目标区别特征" in plan.rejected_queries[0].reason
    # gap 轮只保留区别特征检索；新文献仍在 I4-S 阶段检查完整权利要求。
    expected_scope_by_objective = {
        "gap_or_combination": {
            ("patent", "ordinary_prior_art"),
        },
    }
    for objective, expected_scope in expected_scope_by_objective.items():
        assert {
            (item.provider_kind, item.date_channel)
            for item in plan.queries
            if item.search_objective == objective
        } == expected_scope
    prompt = str(gap_client.calls[0]["prompt"])
    assert "不得再并行生成整项权利要求" in prompt
    assert "previous_query_expressions" in prompt
    assert "上一轮只命中宽泛主题文献" in prompt


def test_i2_gap_deterministic_repair_discards_repeated_initial_portfolio() -> None:
    initial = InvalidityAnalysisEngine(
        FakeVisionClient(_query_plan_response())
    ).plan_queries(
        claim_id="claim-1",
        expanded_claim_text="展开权利要求文本",
        target_images=["target.png"],
    )
    remaining_ids = [item.feature_id for item in initial.limitations[-2:]]
    bad_gap_response = {
        "technical_subject": initial.technical_subject,
        "invention_search_profile": (
            initial.invention_search_profile.model_dump(mode="json")
            if initial.invention_search_profile
            else {}
        ),
        "queries": [
            {
                "provider_kind": "patent",
                "purpose": "initial",
                "query_role": "inventive_point_precision",
                "query_variant": "title_object_plus_desc_inventive",
                "search_scope": "full_text",
                "search_objective": "full_claim_single_reference",
                "target_gap_type": "feature",
                "language": "zh",
                "expression": "扫地机器人 地面识别传感器",
                "feature_ids": remaining_ids,
                "feature_terms": ["地面识别传感器", "清扫组件"],
                "rationale": "错误复用了首轮查询字段",
            }
        ],
    }
    bad_gap_response["invention_search_profile"]["subject_core_terms_zh"] = [
        "机器人"
    ]
    bad_gap_response["invention_search_profile"]["subject_core_terms_en"] = [
        "robot",
        "robotic device",
    ]
    client = FakeVisionClient(bad_gap_response, bad_gap_response)
    engine = InvalidityAnalysisEngine(client)

    plan = engine.plan_queries(
        claim_id="claim-1",
        expanded_claim_text="展开权利要求文本",
        target_images=["target.png"],
        iteration_number=2,
        round_kind="gap",
        existing_limitations=initial.limitations,
        gap_feature_ids=remaining_ids,
        gap_types=["feature_gap"],
        closest_prior_art_context={"document_id": "D1"},
        max_queries=5,
    )

    assert len(client.calls) == 2
    assert engine.last_invocation_audit is not None
    assert engine.last_invocation_audit["successful_attempt"] == 2
    assert plan.queries
    assert all(
        item.search_objective == "gap_or_combination"
        and item.query_role == "gap_followup"
        and item.query_variant == "gap_followup"
        and item.purpose == "feature_uncovered"
        and len(item.feature_ids) == 1
        and item.feature_ids == item.gap_feature_ids
        and item.feature_ids[0] in remaining_ids
        for item in plan.queries
    )
    ordinary_patent_queries = [
        item
        for item in plan.queries
        if item.provider_kind == "patent"
        and item.date_channel == "ordinary_prior_art"
    ]
    assert {item.feature_ids[0] for item in ordinary_patent_queries} == set(
        remaining_ids
    )
    assert all(item.expression.count(" AND ") == 1 for item in plan.queries)
    assert all(
        all(limitation.text not in item.expression for limitation in initial.limitations)
        for item in plan.queries
    )
    assert all(
        item.search_objective != "full_claim_single_reference"
        for item in plan.queries
    )
    assert any(
        "仅使用冻结的未覆盖区别特征" in warning
        for warning in plan.portfolio_warnings
    )


def test_gap_matrix_preserves_one_primary_query_per_feature_beyond_five() -> None:
    limitations = {
        f"F{index}": _feature(
            f"F{index}",
            index,
            f"定位构件{index}",
            subject="玩具喷水车",
        )
        for index in range(1, 7)
    }
    feature_pool = {
        feature_id: {
            "gap_core": [
                limitation.text,
                f"定位件{limitation.sequence}",
                f"positioning member {limitation.sequence}",
            ]
        }
        for feature_id, limitation in limitations.items()
    }

    queries = _ensure_query_matrix(
        claim_id="claim-1",
        iteration_number=2,
        round_kind="gap",
        queries=[],
        limitations=limitations,
        technical_subject="玩具喷水车",
        required_gap_ids=set(limitations),
        default_gap_type=GapType.FEATURE,
        anchor_document_id="D1",
        max_queries=8,
        gap_feature_or_pool=feature_pool,
        gap_subject_or_pool=["车", "vehicle", "toy vehicle"],
        gap_exact_subject_terms=["玩具喷水车", "water-spraying toy vehicle"],
    )

    primary = [
        item
        for item in queries
        if item.provider_kind == "patent"
        and item.date_channel == "ordinary_prior_art"
        and len(item.gap_feature_ids) == 1
    ]
    assert {item.gap_feature_ids[0] for item in primary} == set(limitations)
    assert len(primary) == 6


def test_gap_matrix_changes_each_feature_expression_after_failed_round() -> None:
    limitation = _feature_with_synonyms(
        "F1",
        1,
        "定位卡槽",
        synonyms_zh=["卡槽", "定位槽"],
    )
    common = {
        "claim_id": "claim-1",
        "round_kind": "gap",
        "queries": [],
        "limitations": {"F1": limitation},
        "technical_subject": "耳挂式耳机",
        "required_gap_ids": {"F1"},
        "default_gap_type": GapType.FEATURE,
        "anchor_document_id": "D1",
        "max_queries": 3,
        "gap_feature_or_pool": {
            "F1": {
                "gap_core": ["定位卡槽", "卡槽", "定位槽", "positioning slot"],
                "direct_structure": ["卡槽", "positioning slot"],
                "broader_structure": ["卡合结构", "engagement structure"],
            }
        },
        "gap_subject_or_pool": ["耳机", "headset", "audio device"],
        "gap_exact_subject_terms": ["耳挂式耳机", "ear-hook headset"],
    }
    first = _ensure_query_matrix(iteration_number=2, **common)
    first_expression = next(
        item.expression
        for item in first
        if item.provider_kind == "patent"
        and item.date_channel == "ordinary_prior_art"
        and item.gap_feature_ids == ["F1"]
    )
    repeated_round = dict(common)
    repeated_round["iteration_number"] = 3
    repeated_round["previous_query_expressions"] = [first_expression]
    with pytest.raises(AnalysisValidationError, match="同族"):
        _ensure_query_matrix(**repeated_round)

    broader_round = dict(repeated_round)
    broader_round["gap_subject_or_pool"] = ["声学设备", "acoustic equipment"]
    second = _ensure_query_matrix(**broader_round)
    second_expression = next(
        item.expression
        for item in second
        if item.provider_kind == "patent"
        and item.date_channel == "ordinary_prior_art"
        and item.gap_feature_ids == ["F1"]
    )

    assert _normalise(second_expression) != _normalise(first_expression)
    assert first[0].gap_search_strategy == "adjacent_object_direct_structure"
    assert second[0].gap_search_strategy == "broader_object_structural_family"
    assert "engagement structure" in second_expression


def test_gap_matrix_replaces_model_primary_query_repeated_from_previous_round() -> None:
    limitation = _feature_with_synonyms(
        "F1",
        1,
        "内部输水管道",
        synonyms_zh=["内部输水", "输水", "输水管道"],
    )
    previous_expression = "喷水玩具车 AND 内部输水"
    model_query = SearchQuery(
        query_id="model-q1",
        provider_kind="patent",
        purpose="feature_uncovered",
        technical_subject="喷水玩具车",
        feature_ids=["F1"],
        expression=previous_expression,
        language="zh",
        query_role=QueryRole.GAP_FOLLOWUP,
        query_variant=QueryVariant.GAP_FOLLOWUP,
        search_scope=SearchScope.FULL_TEXT,
        subject_terms=["喷水玩具车"],
        feature_terms=["内部输水"],
        rationale="模型错误复用上一轮检索式",
        search_objective=SearchObjective.GAP_OR_COMBINATION,
        date_channel=DateChannel.ORDINARY_PRIOR_ART,
        target_gap_type=GapType.FEATURE,
        gap_feature_ids=["F1"],
    )
    trimmed: list[SearchQuery] = []

    queries = _ensure_query_matrix(
        claim_id="claim-1",
        iteration_number=3,
        round_kind="gap",
        queries=[model_query],
        limitations={"F1": limitation},
        technical_subject="喷水玩具车",
        required_gap_ids={"F1"},
        default_gap_type=GapType.FEATURE,
        anchor_document_id="D1",
        max_queries=3,
        trimmed_sink=trimmed,
        gap_feature_or_pool={
            "F1": {
                "gap_core": [
                    "内部输水",
                    "输水",
                    "输水管道",
                    "water delivery",
                ],
                "broader_structure": ["输水结构", "water delivery structure"],
            }
        },
        gap_subject_or_pool=["车", "vehicle", "toy vehicle"],
        gap_exact_subject_terms=["喷水玩具车", "water-spraying toy vehicle"],
        previous_query_expressions=[previous_expression],
    )

    primary = next(
        item
        for item in queries
        if item.provider_kind == "patent"
        and item.date_channel == "ordinary_prior_art"
        and item.gap_feature_ids == ["F1"]
    )
    assert _normalise(primary.expression) != _normalise(previous_expression)
    assert primary.gap_search_strategy == "broader_object_structural_family"
    assert any("上一 gap 轮" in item.rationale for item in trimmed)


def test_gap_matrix_water_gun_emits_one_bilingual_group_per_feature_each_round() -> None:
    assert "枪" in _gap_subject_head_terms(["水枪"])
    assert "gun" in _gap_subject_head_terms(["水枪"])
    assert "车" in _gap_subject_head_terms(["水陆喷水车"])
    assert "vehicle" in _gap_subject_head_terms(["水陆喷水车"])
    limitations = {
        "F1": _feature_with_synonyms(
            "F1",
            1,
            "水枪有吸水口",
            synonyms_zh=["吸水", "进水", "入水"],
            synonyms_en=["water intake", "water inlet"],
        ),
        "F2": _feature_with_synonyms(
            "F2",
            2,
            "水枪有加压储水区域",
            synonyms_zh=["加压", "存水", "储水"],
            synonyms_en=["pressurized storage", "water storage"],
        ),
    }
    feature_pool = {
        "F1": {
            "gap_core": ["吸水", "进水", "入水", "water intake", "water inlet"],
            "direct_structure": ["吸水口", "water inlet"],
            "broader_structure": ["进液结构", "liquid intake structure"],
        },
        "F2": {
            "gap_core": [
                "加压",
                "存水",
                "储水",
                "pressurized storage",
                "water storage",
            ],
            "direct_structure": ["加压储水", "pressurized water storage"],
            "broader_structure": ["储液结构", "liquid storage structure"],
        },
    }

    def compile_round(
        iteration_number: int,
        subject_pool: list[str],
        previous: list[str] | None = None,
    ) -> list[SearchQuery]:
        return _ensure_query_matrix(
            claim_id="claim-water-gun",
            iteration_number=iteration_number,
            round_kind="gap",
            queries=[],
            limitations=limitations,
            technical_subject="水枪",
            required_gap_ids={"F1", "F2"},
            default_gap_type=GapType.FEATURE,
            anchor_document_id="D1",
            max_queries=2,
            gap_feature_or_pool=feature_pool,
            gap_subject_or_pool=subject_pool,
            gap_exact_subject_terms=["水枪", "water gun"],
            previous_query_expressions=previous or [],
        )

    first = compile_round(2, ["枪", "gun", "sprayer"])
    first_primary = [
        item
        for item in first
        if item.provider_kind == "patent"
        and item.date_channel is DateChannel.ORDINARY_PRIOR_ART
        and item.search_objective is SearchObjective.GAP_OR_COMBINATION
    ]
    assert len(first_primary) == 2
    assert [item.gap_feature_ids for item in first_primary] == [["F1"], ["F2"]]
    for item in first_primary:
        groups = _boolean_or_members(item.expression)
        assert len(groups) == 2
        assert all(_has_bilingual_terms(group) for group in groups)
        assert "水枪" not in groups[0]
        assert "water gun" not in groups[0]
        assert "枪" in groups[0]
        assert "gun" in groups[0]

    second = compile_round(
        3,
        ["流体喷射玩具", "fluid spraying toy"],
        [item.expression for item in first_primary],
    )
    second_primary = [
        item
        for item in second
        if item.provider_kind == "patent"
        and item.date_channel is DateChannel.ORDINARY_PRIOR_ART
        and item.search_objective is SearchObjective.GAP_OR_COMBINATION
    ]
    assert len(second_primary) == 2
    assert [item.gap_feature_ids for item in second_primary] == [["F1"], ["F2"]]
    assert {
        item.gap_feature_ids[0]: _gap_expression_semantic_key(item.expression)
        for item in second_primary
    } != {
        item.gap_feature_ids[0]: _gap_expression_semantic_key(item.expression)
        for item in first_primary
    }
    assert {
        item.gap_search_strategy for item in second_primary
    } == {"broader_object_structural_family"}


def test_gap_matrix_uses_five_fixed_semantic_strategies_in_order() -> None:
    limitation = _feature_with_synonyms(
        "F1",
        1,
        "水枪有加压储水区域",
        synonyms_zh=["加压储水"],
        synonyms_en=["pressurized water storage"],
    ).model_copy(update={"technical_subject": "水枪"})
    feature_pool = {
        "F1": {
            "gap_core": ["加压储水", "pressurized water storage"],
            "direct_structure": ["储水区", "water reservoir"],
            "broader_structure": ["储液结构", "liquid storage structure"],
            "function_action_role": ["加压供水", "pressurized supply"],
            "relation_or_path": ["流体连通", "fluid communication"],
            "principle_or_effect": ["压力喷射", "pressure spraying"],
            "subsystem_subject": ["储液器", "reservoir"],
        }
    }
    subjects = {
        1: ["枪", "gun"],
        2: ["喷射器", "sprayer"],
        3: ["清洗玩具", "cleaning toy"],
        4: [],
        5: ["灌溉器", "irrigator"],
    }
    expected = [
        "adjacent_object_direct_structure",
        "broader_object_structural_family",
        "same_function_object_action_role",
        "subsystem_component_relation_path",
        "analogous_domain_principle_effect",
    ]
    previous: list[str] = []
    observed: list[str] = []

    for gap_iteration in range(1, 6):
        queries = _ensure_query_matrix(
            claim_id="claim-water-gun",
            iteration_number=gap_iteration + 1,
            round_kind="gap",
            queries=[],
            limitations={"F1": limitation},
            technical_subject="水枪",
            required_gap_ids={"F1"},
            default_gap_type=GapType.FEATURE,
            anchor_document_id="D1",
            max_queries=1,
            gap_feature_or_pool=feature_pool,
            gap_subject_or_pool=subjects[gap_iteration],
            gap_exact_subject_terms=["水枪", "water gun"],
            previous_query_expressions=previous,
        )
        primary = next(
            item
            for item in queries
            if item.provider_kind == "patent"
            and item.date_channel is DateChannel.ORDINARY_PRIOR_ART
        )
        observed.append(str(primary.gap_search_strategy))
        assert primary.gap_search_iteration == gap_iteration
        assert primary.gap_search_strategy_label
        assert _gap_expression_semantic_key(primary.expression) not in {
            _gap_expression_semantic_key(item) for item in previous
        }
        previous.append(primary.expression)

    assert observed == expected


def test_hydrant_gap_vocabulary_uses_five_substantively_distinct_rounds() -> None:
    """真实消火栓特征形态：五轮必须逐轮切换客体与特征语义层。"""

    limitations = [
        _feature_with_synonyms(
            "H1",
            1,
            "安装块埋设在地面内，安装块的顶端开设有活动槽",
            synonyms_zh=["埋设基座", "活动槽"],
            synonyms_en=["buried mounting base", "activity slot"],
        ).model_copy(update={"technical_subject": "室外消火栓"}),
        _feature_with_synonyms(
            "H2",
            2,
            "消火栓本体的后端靠下位置固定连接有限位块",
            synonyms_zh=["限位块", "止动块"],
            synonyms_en=["limit block", "stop block"],
        ).model_copy(update={"technical_subject": "室外消火栓"}),
        _feature_with_synonyms(
            "H3",
            3,
            "收纳井内壁的限位槽与限位块匹配并滑动安装",
            synonyms_zh=["限位槽配合", "块槽滑动"],
            synonyms_en=["sliding stop groove", "block groove engagement"],
        ).model_copy(update={"technical_subject": "室外消火栓"}),
    ]
    profile = _gap_profile(
        "室外消火栓",
        subject_core_terms_zh=["消火栓"],
        subject_core_terms_en=["fire hydrant"],
    )
    vocabularies = [
        {
            "subject_terms_zh": ["消防供水设施"],
            "subject_terms_en": ["fire water supply fixture"],
            "features": [
                {"feature_id": "F1", "terms_zh": ["埋设基座"], "terms_en": ["buried mounting base"]},
                {"feature_id": "F2", "terms_zh": ["限位块"], "terms_en": ["stop block"]},
                {"feature_id": "F3", "terms_zh": ["滑动限位槽"], "terms_en": ["sliding stop groove"]},
            ],
        },
        {
            "subject_terms_zh": ["流体控制设备"],
            "subject_terms_en": ["fluid control equipment"],
            "features": [
                {"feature_id": "F1", "terms_zh": ["地下支承结构"], "terms_en": ["subsurface support assembly"]},
                {"feature_id": "F2", "terms_zh": ["行程约束构件"], "terms_en": ["travel restraint member"]},
                {"feature_id": "F3", "terms_zh": ["导向约束结构"], "terms_en": ["guided restraint assembly"]},
            ],
        },
        {
            "subject_terms_zh": ["高度调节设备"],
            "subject_terms_en": ["height adjustable equipment"],
            "features": [
                {"feature_id": "F1", "terms_zh": ["容纳升降"], "terms_en": ["motion accommodation"]},
                {"feature_id": "F2", "terms_zh": ["行程止挡"], "terms_en": ["travel stopping"]},
                {"feature_id": "F3", "terms_zh": ["升降导向"], "terms_en": ["vertical guiding"]},
            ],
        },
        {
            "subject_terms_zh": ["升降支承组件"],
            "subject_terms_en": ["lifting support module"],
            "features": [
                {"feature_id": "F1", "terms_zh": ["基座容纳路径"], "terms_en": ["base receiving path"]},
                {"feature_id": "F2", "terms_zh": ["本体固定连接"], "terms_en": ["body fixed connection"]},
                {"feature_id": "F3", "terms_zh": ["块槽滑动配合"], "terms_en": ["block groove sliding engagement"]},
            ],
        },
        {
            "subject_terms_zh": ["地下伸缩装置"],
            "subject_terms_en": ["subsurface telescopic apparatus"],
            "features": [
                {"feature_id": "F1", "terms_zh": ["隐蔽收纳"], "terms_en": ["concealed stowage"]},
                {"feature_id": "F2", "terms_zh": ["越程防止"], "terms_en": ["overtravel prevention"]},
                {"feature_id": "F3", "terms_zh": ["受控直线移动"], "terms_en": ["controlled linear movement"]},
            ],
        },
    ]
    client = FakeVisionClient(*vocabularies)
    engine = InvalidityAnalysisEngine(client)
    previous: list[str] = []
    observed: list[str] = []

    for gap_iteration in range(1, 6):
        plan = engine.plan_queries(
            claim_id="claim-1",
            expanded_claim_text="一种具有升降结构的室外消火栓。",
            target_images=["target.png"],
            iteration_number=gap_iteration + 1,
            round_kind="gap",
            existing_limitations=limitations,
            invention_search_profile=profile,
            gap_feature_ids=["H1", "H2", "H3"],
            previous_query_expressions=previous,
            max_queries=3,
        )
        assert len(plan.queries) == 3
        assert [item.gap_feature_ids for item in plan.queries] == [
            ["H1"],
            ["H2"],
            ["H3"],
        ]
        observed.append(str(plan.gap_search_strategy))
        previous = [item.expression for item in plan.queries]

    assert observed == [
        "adjacent_object_direct_structure",
        "broader_object_structural_family",
        "same_function_object_action_role",
        "subsystem_component_relation_path",
        "analogous_domain_principle_effect",
    ]
    assert len(client.calls) == 5
    assert all("I2-G第" in str(call["prompt"]) for call in client.calls)


def test_hydrant_gap_vocabulary_filters_exact_subject_but_keeps_broader_group() -> None:
    """混合客体组只移除精确客体；消防设施等上位类词不得导致整组失败。"""

    limitation = _feature_with_synonyms(
        "H1",
        1,
        "安装块埋设在地面内并设有活动槽",
        synonyms_zh=["埋设基座"],
        synonyms_en=["buried mounting base"],
    ).model_copy(update={"technical_subject": "室外消火栓"})
    profile = _gap_profile(
        "一种具有升降结构的室外消火栓",
        subject_core_terms_zh=["消火栓"],
        subject_core_terms_en=["fire hydrant"],
    ).model_copy(
        update={
            "subject_synonyms_zh": ["室外消火栓", "消防栓", "消防设施"],
            "subject_synonyms_en": [
                "outdoor fire hydrant",
                "fire hydrant",
                "fire-fighting facility",
            ],
        }
    )
    client = FakeVisionClient(
        {
            "subject_terms_zh": ["室外消火栓", "消防设施"],
            "subject_terms_en": ["outdoor fire hydrant", "fire equipment"],
            "features": [
                {
                    "feature_id": "F1",
                    "terms_zh": ["地下支承结构"],
                    "terms_en": ["subsurface support assembly"],
                }
            ],
        }
    )
    engine = InvalidityAnalysisEngine(client)

    plan = engine.plan_queries(
        claim_id="claim-1",
        expanded_claim_text="一种具有升降结构的室外消火栓。",
        target_images=["target.png"],
        iteration_number=3,
        round_kind="gap",
        existing_limitations=[limitation],
        invention_search_profile=profile,
        gap_feature_ids=["H1"],
        max_queries=1,
    )

    assert len(plan.queries) == 1
    assert "室外消火栓" not in plan.queries[0].expression
    assert "outdoor fire hydrant" not in plan.queries[0].expression
    assert "消防设施" in plan.queries[0].expression
    assert "fire equipment" in plan.queries[0].expression
    assert engine.last_invocation_audit["removed_exact_subject_terms"] == [
        "室外消火栓",
        "outdoor fire hydrant",
    ]
    assert any("移除目标专利完全相同类别词" in item for item in plan.portfolio_warnings)


def test_second_gap_round_uses_suitable_lower_object_when_subject_is_already_broad() -> None:
    limitation = _feature_with_synonyms(
        "W1",
        1,
        "通过吸入口将水输送至出水端",
        synonyms_zh=["吸水输送"],
        synonyms_en=["water intake delivery"],
    ).model_copy(update={"technical_subject": "取水设备"})
    client = FakeVisionClient(
        {
            "subject_terms_zh": ["抽水机"],
            "subject_terms_en": ["water pump"],
            "features": [
                {
                    "feature_id": "F1",
                    "terms_zh": ["流体吸入结构"],
                    "terms_en": ["fluid intake assembly"],
                    "semantic_basis": (
                        "取水设备已经是宽泛客体，继续上位只会得到设备泛词；"
                        "抽水机以相同吸水输送角色实现该特征"
                    ),
                }
            ],
        }
    )
    plan = InvalidityAnalysisEngine(client).plan_queries(
        claim_id="claim-1",
        expanded_claim_text="一种取水设备，通过吸入口将水输送至出水端。",
        target_images=["target.png"],
        iteration_number=3,
        round_kind="gap",
        existing_limitations=[limitation],
        invention_search_profile=_gap_profile(
            "取水设备",
            subject_core_terms_zh=["取水设备"],
            subject_core_terms_en=["water extraction equipment"],
        ),
        gap_feature_ids=["W1"],
        max_queries=1,
    )

    assert "抽水机" in plan.queries[0].expression
    assert "water pump" in plan.queries[0].expression
    assert "上位或合适下位产品类别" in plan.gap_search_strategy_label
    assert any("第2轮客体上下位选择依据" in item for item in plan.portfolio_warnings)
    assert "例如取水设备可改为抽水机" in str(client.calls[0]["prompt"])


def test_i2_gap_completes_missing_legacy_feature_english_vocabulary() -> None:
    limitation = _feature_with_synonyms(
        "F1",
        1,
        "吸水口",
        synonyms_zh=["吸水", "进水"],
    )
    response = {
        "technical_subject": "水枪",
        "subject_synonyms_zh": [],
        "subject_synonyms_en": [],
        "features": [],
        "invention_search_profile": {
            "protected_subject": "水枪",
            "subject_core_terms_zh": ["枪"],
            "subject_core_terms_en": ["gun"],
            "invention_summary": "水枪从吸水口取水",
            "common_context_features": [],
            "inventive_point_features": [
                {
                    "concept_id": "inventive-intake",
                    "text": "吸水口",
                    "feature_ids": ["F1"],
                    "synonyms_zh": ["吸水", "进水"],
                    "synonyms_en": [],
                    "source_reference": "权利要求1",
                    "rationale": "区别特征的取水入口",
                    "preferred_scope": "full_text",
                }
            ],
        },
        "queries": [
            {
                "provider_kind": "patent",
                "purpose": "feature_uncovered",
                "query_role": "gap_followup",
                "query_variant": "gap_followup",
                "search_scope": "full_text",
                "search_objective": "gap_or_combination",
                "date_channel": "ordinary_prior_art",
                "target_gap_type": "feature_gap",
                "language": "bilingual",
                "expression": "(枪 OR gun) AND 吸水口",
                "subject_terms": ["枪", "gun"],
                "feature_ids": ["F1"],
                "feature_terms": ["吸水口"],
                "feature_term_groups": [["吸水口", "吸水", "进水"]],
                "rationale": "旧冻结特征尚缺英文检索词",
            }
        ],
    }
    completion = {
        "subject_terms_zh": [],
        "subject_terms_en": [],
        "features": [
            {
                "feature_id": "F1",
                "terms_zh": ["吸水", "进水"],
                "terms_en": ["water intake", "water inlet"],
            }
        ],
    }
    client = FakeVisionClient(response, completion)

    plan = InvalidityAnalysisEngine(client).plan_queries(
        claim_id="claim-1",
        expanded_claim_text="一种水枪，包括吸水口。",
        target_images=["target.png"],
        iteration_number=2,
        round_kind="gap",
        existing_limitations=[limitation],
        gap_feature_ids=["F1"],
        closest_prior_art_context={"document_id": "D1"},
    )

    assert len(client.calls) == 2
    assert "双语检索词补齐任务" in str(client.calls[1]["prompt"])
    assert len(plan.queries) == 1
    groups = _boolean_or_members(plan.queries[0].expression)
    assert len(groups) == 2
    assert all(_has_bilingual_terms(group) for group in groups)
    assert "water intake" in groups[1]


def test_i2_deterministic_gap_fallback_fails_closed_without_current_subject_lane() -> None:
    limitations = [
        _feature_with_synonyms(
            "F1",
            1,
            "底部吸水结构",
            synonyms_zh=["底部吸水", "吸水"],
            synonyms_en=["water intake"],
        ).model_copy(update={"technical_subject": "喷水玩具车"}),
        _feature_with_synonyms(
            "F2",
            2,
            "炮塔壳内喷水管",
            synonyms_zh=["内部喷水", "喷水管"],
            synonyms_en=["internal spray pipe"],
        ).model_copy(update={"technical_subject": "喷水玩具车"}),
        _feature_with_synonyms(
            "F3",
            3,
            "可拆卸水瓶",
            synonyms_zh=["可拆卸", "水瓶连接"],
            synonyms_en=["detachable bottle"],
        ).model_copy(update={"technical_subject": "喷水玩具车"}),
    ]
    previous_expressions = [
        "喷水玩具车 AND 底部吸水",
        "喷水玩具车 AND 内部喷水",
        "喷水玩具车 AND 可拆卸",
    ]

    profile = _gap_profile(
        "喷水玩具车",
        subject_core_terms_zh=["车"],
        subject_core_terms_en=["vehicle", "toy vehicle"],
    ).model_copy(
        update={
            "mechanism_model": MechanismModel(
                summary="通过取水、喷射和拆装完成供水玩耍",
                elements=[
                    MechanismElement(
                        element_id=f"role-{feature_id}",
                        kind="technical_role",
                        text=zh,
                        feature_ids=[feature_id],
                        source_reference="权利要求1",
                        rationale="冻结的功能角色词",
                        role_equivalent_terms=[zh, en],
                        operation_terms=[zh, en],
                    )
                    for feature_id, zh, en in (
                        ("F1", "取水", "water intake"),
                        ("F2", "喷射", "water spraying"),
                        ("F3", "拆装", "detachable coupling"),
                    )
                ],
            )
        }
    )
    with pytest.raises(AnalysisValidationError, match="双语客体组"):
        InvalidityAnalysisEngine(FakeVisionClient()).deterministic_gap_query_plan(
            claim_id="claim-1",
            iteration_number=4,
            existing_limitations=limitations,
            gap_feature_ids=["F1", "F2", "F3"],
            closest_prior_art_context={"document_id": "D1"},
            invention_search_profile=profile,
            previous_query_expressions=previous_expressions,
            max_queries=5,
        )


def test_gap_atomization_keeps_same_text_for_different_feature_lanes() -> None:
    queries = [
        SearchQuery(
            query_id=f"q-{feature_id}",
            provider_kind="patent",
            purpose="feature_uncovered",
            technical_subject="喷水玩具车",
            feature_ids=[feature_id],
            expression="喷水玩具车 AND 供水管连接",
            language="zh",
            query_role=QueryRole.GAP_FOLLOWUP,
            query_variant=QueryVariant.GAP_FOLLOWUP,
            search_scope=SearchScope.FULL_TEXT,
            subject_terms=["喷水玩具车"],
            feature_terms=["供水管连接"],
            rationale="两个不同区别特征可以使用同一核心含义词",
            search_objective=SearchObjective.GAP_OR_COMBINATION,
            date_channel=DateChannel.ORDINARY_PRIOR_ART,
            target_gap_type=GapType.FEATURE,
            gap_feature_ids=[feature_id],
        )
        for feature_id in ("F1", "F3")
    ]

    atomized, _notes = _atomize_executable_queries(
        queries,
        technical_subject="喷水玩具车",
    )

    assert {item.gap_feature_ids[0] for item in atomized} == {"F1", "F3"}


def test_round_five_deterministic_fallback_never_reuses_prior_round_as_analogy() -> None:
    limitations = [
        _feature(
            "F1",
            1,
            "水瓶，所述水瓶可拆卸地连接于炮塔壳，水瓶通过供水管连接水泵",
            subject="水陆喷水车",
        )
    ]
    previous = (
        "(车 OR vehicle OR toy vehicle) AND "
        "(可拆卸 OR detachable OR detachable bottle)"
    )

    with pytest.raises(AnalysisValidationError, match="类比领域客体"):
        InvalidityAnalysisEngine(FakeVisionClient()).deterministic_gap_query_plan(
            claim_id="claim-1",
            iteration_number=6,
            existing_limitations=limitations,
            gap_feature_ids=["F1"],
            invention_search_profile=_gap_profile(
                "水陆喷水车",
                subject_core_terms_zh=["车", "玩具车"],
                subject_core_terms_en=["vehicle", "toy vehicle"],
            ),
            previous_query_expressions=[previous],
            max_queries=3,
        )


def test_i2_regenerates_after_first_query_plan_fails_guard() -> None:
    invalid = _query_plan_response()
    invalid["invention_search_profile"] = None
    valid = _query_plan_response()
    client = FakeVisionClient(invalid, valid)
    engine = InvalidityAnalysisEngine(client)

    plan = engine.plan_queries(
        claim_id="claim-1",
        expanded_claim_text="展开权利要求文本",
        target_images=["target.png"],
    )

    assert plan.queries
    assert len(client.calls) == 2
    assert "模型没有返回发明点检索画像" in client.calls[1]["prompt"]
    assert engine.last_invocation_audit is not None
    assert engine.last_invocation_audit["attempt_count"] == 2
    assert engine.last_invocation_audit["successful_attempt"] == 2
    assert len(engine.last_invocation_audit["attempts"]) == 2


def test_i2_deterministically_compiles_portfolio_without_second_model_call() -> None:
    response = _query_plan_response()
    response["queries"] = [response["queries"][0]]
    response["queries"][0]["provider_kind"] = "npl"
    client = FakeVisionClient(response, response)
    engine = InvalidityAnalysisEngine(client)

    plan = engine.plan_queries(
        claim_id="claim-1",
        expanded_claim_text="展开权利要求文本",
        target_images=["target.png"],
    )

    ordinary_variants = {
        item.query_variant
        for item in plan.queries
        if item.provider_kind == "patent"
        and item.date_channel == "ordinary_prior_art"
    }
    # 首轮固定五组（宪章 2.15）：模型返回的 npl 线不采用，首轮组合由画像
    # 事实确定性编译，不触发第二次模型调用。
    assert ordinary_variants
    assert ordinary_variants <= {
        "applicant_plus_object",
        "title_object_plus_desc_inventive",
        "classification_plus_desc_inventive",
        "desc_object_plus_desc_inventive_plus_effect",
        "title_keyword_object_plus_desc_function",
    }
    assert "title_object_plus_desc_inventive" in ordinary_variants
    assert len(client.calls) == 1


def test_i2_same_source_profile_recompile_uses_no_model_and_keeps_variants() -> None:
    response = _query_plan_response()
    response["invention_search_profile"]["classification_anchors"] = [
        "B60S 1/00",
        "A47L 11/40",
    ]
    response["invention_search_profile"]["classification_anchor_roles"] = {
        "B60S 1/00": "subject",
        "A47L 11/40": "inventive_point",
    }
    client = FakeVisionClient(response)
    engine = InvalidityAnalysisEngine(client)
    first = engine.plan_queries(
        claim_id="claim-1",
        expanded_claim_text="展开权利要求文本",
        target_images=["target.png"],
        patent_context={"source_sha256": "sha-1"},
    )
    assert len(client.calls) == 1

    rebuilt = engine.recompile_initial_query_plan(
        previous_plan=first,
        source_plan_run_id="prior-i2-run",
        source_sha256="sha-1",
        patent_context={
            "source_sha256": "sha-1",
            "bibliographic_data": {
                "classifications": ["B60S 1/00", "A47L 11/40"],
            },
        },
        # 编译器变体覆盖检查需要完整组合预算；首轮执行预算仍为 5 条。
        max_queries=20,
    )

    assert len(client.calls) == 1
    assert rebuilt.generation_source == "same_source_cached_profile"
    assert rebuilt.source_plan_run_id == "prior-i2-run"
    assert rebuilt.source_sha256 == "sha-1"
    ordinary_variants = {
        item.query_variant
        for item in rebuilt.queries
        if item.provider_kind == "patent"
        and item.date_channel == "ordinary_prior_art"
    }
    # 同案重编译同样只走首轮固定五组（宪章 2.15）：该画像无申请人/效果词池
    # 事实，故 ①④⑤ 缺事实留痕跳过，仅编译 ② 与 ③。
    assert ordinary_variants == {
        "title_object_plus_desc_inventive",
        "classification_plus_desc_inventive",
    }
    classification_lane = next(
        item
        for item in rebuilt.queries
        if item.query_variant == "classification_plus_desc_inventive"
    )
    assert classification_lane.classification_anchors == ["B60S1/00"]
    assert classification_lane.provider_expression is not None
    assert classification_lane.provider_expression.startswith("(IPC:(B60S1/00))")


def test_i2_recompile_accepts_profile_only_plan_without_queries() -> None:
    """模块4 live 输入是模块3画像输出：queries 恒空的 QueryPlan 形状 dict。"""

    client = FakeVisionClient(_query_plan_response())
    engine = InvalidityAnalysisEngine(client)
    profile = engine.generate_inventive_profile(
        claim_id="claim-1",
        expanded_claim_text="展开权利要求文本",
        target_images=["target.png"],
        patent_context={"source_sha256": "sha-1"},
    )
    assert profile.queries == []
    assert profile.generation_source == "live_model"
    assert len(client.calls) == 1

    # 模拟律师模块实验室：持久化输出是经 model_dump 的 dict，且带有
    # 运行信封附加字段（extra 字段应被忽略）。
    persisted_output = {
        **profile.model_dump(mode="json"),
        "model_response_audit": None,
        "network_used": True,
        "simulated": False,
    }
    rebuilt = engine.recompile_initial_query_plan(
        previous_plan=persisted_output,
        source_plan_run_id="profile-run-1",
        source_sha256=None,
        patent_context={"source_sha256": "sha-1"},
        max_queries=20,
    )

    assert rebuilt.queries
    assert rebuilt.generation_source == "same_source_cached_profile"
    assert rebuilt.source_plan_run_id == "profile-run-1"
    assert rebuilt.source_sha256 == "sha-1"
    # 确定性重编译不得再次调用模型。
    assert len(client.calls) == 1
    assert engine.last_invocation_audit is None


def _queries_from_profile_response() -> dict[str, Any]:
    """模块4 GLM 提案：在已冻结的模块3画像事实上给出多分辨率检索线。"""

    return {
        "queries": [
            {
                "provider_kind": "patent",
                "purpose": "initial",
                "query_role": "inventive_point_precision",
                "query_variant": "maximal_similarity_precision",
                "search_scope": "full_text",
                "language": "zh",
                "expression": (
                    "扫地机器人 AND 移动底盘 AND 清洁件相对底盘升降 "
                    "AND 地面识别传感器"
                ),
                "subject_terms": ["扫地机器人"],
                "feature_ids": ["f1", "f2", "f3"],
                "feature_terms": ["移动底盘", "清洁件相对底盘升降", "地面识别传感器"],
                "allow_zero_results": True,
                "compact_fallback_allowed": False,
            },
            {
                "provider_kind": "patent",
                "purpose": "initial",
                "query_role": "inventive_point_precision",
                "query_variant": "object_plus_inventive_point",
                "search_scope": "full_text",
                "language": "zh",
                "expression": "扫地机器人 AND 清洁件相对底盘升降",
                "subject_terms": ["扫地机器人"],
                "feature_ids": ["f2"],
                "feature_terms": ["清洁件相对底盘升降"],
            },
            {
                "provider_kind": "patent",
                "purpose": "initial",
                "query_role": "claim_context_recall",
                "query_variant": "system_architecture_recall",
                "search_scope": "claims",
                "language": "zh",
                "expression": "扫地机器人 AND 移动底盘 AND 地面识别传感器",
                "subject_terms": ["扫地机器人"],
                "feature_ids": ["f1", "f3"],
                "feature_terms": ["移动底盘", "地面识别传感器"],
            },
            {
                "provider_kind": "npl",
                "purpose": "initial",
                "query_role": "inventive_point_precision",
                "query_variant": "object_plus_inventive_point",
                "search_scope": "full_text",
                "language": "zh",
                "expression": "扫地机器人 升降拖布",
                "subject_terms": ["扫地机器人"],
                "feature_ids": ["f2"],
                "feature_terms": ["升降拖布"],
                "date_channel": "ordinary_prior_art",
            },
        ]
    }


def _module3_profile_plan(
    engine: InvalidityAnalysisEngine,
    *,
    patent_context: dict[str, Any] | None = None,
) -> QueryPlan:
    """模块3画像输出：profile_only 路径，queries 恒空。"""

    return engine.generate_inventive_profile(
        claim_id="claim-1",
        expanded_claim_text="展开权利要求文本",
        target_images=["target.png"],
        patent_context=patent_context or {"source_sha256": "sha-1"},
    )


def test_module3_profile_uses_full_output_budget() -> None:
    client = FakeVisionClient(_query_plan_response())

    profile = _module3_profile_plan(InvalidityAnalysisEngine(client))

    assert profile.queries == []
    assert client.calls[0]["max_tokens"] == 12288


def test_module3_profile_retries_invalid_json_once_and_keeps_audit() -> None:
    class InvalidJsonOnceClient(FakeVisionClient):
        def invoke_json(self, **kwargs: Any) -> SimpleNamespace:
            self.calls.append(kwargs)
            if len(self.calls) == 1:
                raise MultimodalModelError(
                    "GLM 输出达到长度上限且 JSON 未闭合",
                    reason_code="glm_output_truncated",
                    raw_content='{"features":[',
                    finish_reason="length",
                    model=self.model,
                )
            return SimpleNamespace(
                data=self.responses.pop(0),
                raw_content='{"ok":true}',
                finish_reason="stop",
                model=self.model,
            )

    client = InvalidJsonOnceClient(_query_plan_response())
    engine = InvalidityAnalysisEngine(client)

    profile = _module3_profile_plan(engine)

    assert profile.queries == []
    assert len(client.calls) == 2
    assert all(call["max_tokens"] == 12288 for call in client.calls)
    assert "完整合法 JSON" in client.calls[1]["prompt"]
    assert engine.last_invocation_audit is not None
    assert engine.last_invocation_audit["attempt_count"] == 2
    assert engine.last_invocation_audit["successful_attempt"] == 2
    first_attempt = engine.last_invocation_audit["attempts"][0]
    assert first_attempt["generation_error_code"] == "glm_output_truncated"
    assert first_attempt["finish_reason"] == "length"
    assert first_attempt["raw_content"] == '{"features":['


def _hydrant_profile_response(*, grouped: bool) -> dict[str, Any]:
    features = [
        ("f1", "消火栓本体设置在活动座上"),
        ("f2", "活动座能够相对固定座上下移动"),
        ("f3", "导向件限制活动座的移动方向"),
        ("f4", "限位件限定活动座的移动行程"),
        ("f5", "驱动件带动活动座升降"),
    ]
    inventive = (
        [
            {
                "concept_id": "inventive-mobile-hydrant",
                "text": "室外消火栓可移动",
                "feature_ids": [item[0] for item in features],
                "synonyms_zh": ["消火栓升降移动"],
                "synonyms_en": ["movable fire hydrant"],
                "source_reference": "权利要求1及发明内容",
                "rationale": "支承、导向、限位和驱动细节共同实现消火栓本体移动",
                "preferred_scope": "full_text",
            }
        ]
        if grouped
        else [
            {
                "concept_id": f"inventive-detail-{index}",
                "text": feature_text,
                "feature_ids": [feature_id],
                "source_reference": "权利要求1",
                "rationale": "逐项复述结构细节",
                "preferred_scope": "full_text",
            }
            for index, (feature_id, feature_text) in enumerate(features, start=1)
        ]
    )
    return {
        "technical_subject": "室外消火栓",
        "subject_synonyms_zh": ["消火栓"],
        "subject_synonyms_en": ["fire hydrant"],
        "features": [
            {
                "temp_id": feature_id,
                "text": feature_text,
                "technical_subject": "室外消火栓",
            }
            for feature_id, feature_text in features
        ],
        "invention_search_profile": {
            "protected_subject": "室外消火栓",
            "subject_synonyms_zh": ["消火栓"],
            "subject_synonyms_en": ["fire hydrant"],
            "classification_anchors": [],
            "invention_summary": "通过升降机构使室外消火栓能够移动。",
            "common_context_features": [],
            "inventive_point_features": inventive,
        },
        "queries": [],
    }


def test_module3_retries_feature_by_feature_profile_and_keeps_grouped_bindings() -> None:
    client = FakeVisionClient(
        _hydrant_profile_response(grouped=False),
        _hydrant_profile_response(grouped=True),
    )
    engine = InvalidityAnalysisEngine(client)

    profile = engine.generate_inventive_profile(
        claim_id="claim-1",
        expanded_claim_text="一种具有升降结构的室外消火栓。",
        target_images=["target.png"],
    )

    points = profile.invention_search_profile.inventive_point_features
    assert len(points) == 1
    assert points[0].text == "室外消火栓可移动"
    assert len(points[0].feature_ids) == 5
    assert set(points[0].feature_ids) == {
        item.feature_id for item in profile.limitations
    }
    assert len(client.calls) == 2
    assert "最多允许 3 项" in client.calls[1]["prompt"]
    assert engine.last_invocation_audit["attempt_count"] == 2
    assert engine.last_invocation_audit["successful_attempt"] == 2


def test_module3_keeps_two_independent_search_level_points() -> None:
    response = _hydrant_profile_response(grouped=True)
    response["invention_search_profile"]["inventive_point_features"] = [
        {
            "concept_id": "inventive-mobile",
            "text": "室外消火栓可移动",
            "feature_ids": ["f1", "f2", "f3", "f4"],
            "source_reference": "权利要求1",
            "rationale": "共同形成移动能力",
            "preferred_scope": "full_text",
        },
        {
            "concept_id": "inventive-drive",
            "text": "驱动件提供升降动力",
            "feature_ids": ["f5"],
            "source_reference": "权利要求1",
            "rationale": "独立的驱动控制构思",
            "preferred_scope": "full_text",
        },
    ]
    client = FakeVisionClient(response)

    profile = InvalidityAnalysisEngine(client).generate_inventive_profile(
        claim_id="claim-1",
        expanded_claim_text="一种具有升降结构的室外消火栓。",
        target_images=["target.png"],
    )

    points = profile.invention_search_profile.inventive_point_features
    assert [item.text for item in points] == [
        "室外消火栓可移动",
        "驱动件提供升降动力",
    ]
    assert len(client.calls) == 1


def test_module3_never_silently_truncates_over_detailed_points() -> None:
    client = FakeVisionClient(
        _hydrant_profile_response(grouped=False),
        _hydrant_profile_response(grouped=False),
    )

    with pytest.raises(
        AnalysisValidationError,
        match="核心发明点必须是检索级上位概括",
    ):
        InvalidityAnalysisEngine(client).generate_inventive_profile(
            claim_id="claim-1",
            expanded_claim_text="一种具有升降结构的室外消火栓。",
            target_images=["target.png"],
        )

    assert len(client.calls) == 2


def test_module4_regenerates_queries_from_module3_profile_via_model() -> None:
    """画像-only plan 输入 → GLM 重新生成检索词，守门/原子化/上限/审计生效。"""

    client = FakeVisionClient(
        _query_plan_response(),
        _queries_from_profile_response(),
    )
    engine = InvalidityAnalysisEngine(client)
    profile = _module3_profile_plan(
        engine,
        patent_context={
            **_target_patent_context(),
            "source_sha256": "sha-1",
        },
    )
    assert profile.queries == []

    # 律师模块实验室持久化输出是 model_dump dict，且带运行信封附加字段。
    persisted_output = {
        **profile.model_dump(mode="json"),
        "model_response_audit": None,
        "network_used": True,
        "simulated": False,
    }
    result = engine.generate_queries_from_inventive_profile(
        previous_plan=persisted_output,
        source_plan_run_id="profile-run-1",
        source_sha256="sha-1",
        max_queries=5,
    )

    # 一次模块3画像 + 一次模块4检索词生成；模块4不附目标专利图像。
    assert len(client.calls) == 2
    assert client.calls[1]["target_images"] == []
    assert client.calls[1]["allow_text_only"] is True
    prompt = client.calls[1]["prompt"]
    # prompt 必须包含画像中的发明点事实……
    assert "清洁件相对底盘升降" in prompt
    # ……但不得包含专利说明书/权利要求全文（模块4不重读专利）。
    assert "储液空间与喷射流路连通" not in prompt  # 具体实施方式
    assert "液体喷射装置通常包括储液空间" not in prompt  # 背景技术
    assert "一种液体喷射装置，包括压力罐" not in prompt  # 权利要求原文
    assert "patent_context" not in prompt

    assert result.queries
    assert len(result.queries) <= 5
    assert result.generation_source == "module3_profile_live_model"
    assert result.source_plan_run_id == "profile-run-1"
    assert result.source_sha256 == "sha-1"
    assert result.generation_warning is None
    # 原子化：超过 6 字的中文长词不得原样留在最终可执行检索式中。
    assert all(
        "清洁件相对底盘升降" not in item.expression for item in result.queries
    )
    audit = engine.last_invocation_audit
    assert audit is not None
    assert audit["stage"] == "I2_QUERY_PLAN"
    assert audit["attempt_number"] == 1
    assert audit["attempt_count"] == 1
    assert audit["successful_attempt"] == 1
    assert audit["target_image_count"] == 0
    assert audit["prompt_sha256"]


def test_module4_non_compliant_model_queries_not_adopted() -> None:
    """模型首轮查询不合规也不采用：首轮组合由画像事实确定性编译，一次通过。"""

    non_compliant = {
        "queries": [
            {
                "provider_kind": "patent",
                "purpose": "initial",
                "query_role": "inventive_point_precision",
                "search_scope": "full_text",
                "language": "zh",
                "expression": "智能家居系统",
            }
        ]
    }
    client = FakeVisionClient(
        _query_plan_response(),
        non_compliant,
        _queries_from_profile_response(),
    )
    engine = InvalidityAnalysisEngine(client)
    profile = _module3_profile_plan(engine)

    result = engine.generate_queries_from_inventive_profile(
        previous_plan=profile,
        source_plan_run_id="profile-run-1",
        source_sha256="sha-1",
    )

    # 首轮固定五组（宪章 2.15）：模型查询不参与首轮组合，确定性编译一次
    # 成功，不再消耗反馈重试；不合规的模型表达不进入任何首轮检索式。
    assert len(client.calls) == 2
    assert result.queries
    assert result.generation_source == "module3_profile_live_model"
    assert not any(
        "智能家居系统" in item.expression for item in result.queries
    )
    audit = engine.last_invocation_audit
    assert audit["attempt_count"] == 1
    assert audit["successful_attempt"] == 1
    assert audit["attempts"][0]["validation_error"] is None


def test_module4_deterministic_compile_keeps_audit() -> None:
    """模型连续不合规时首轮组合仍由画像已冻结事实确定性组装，审计完整保留。"""

    non_compliant = {
        "queries": [
            {
                "provider_kind": "patent",
                "purpose": "initial",
                "query_role": "inventive_point_precision",
                "search_scope": "full_text",
                "language": "zh",
                "expression": "智能家居系统",
            }
        ]
    }
    client = FakeVisionClient(
        _query_plan_response(),
        non_compliant,
        dict(non_compliant),
    )
    engine = InvalidityAnalysisEngine(client)
    profile = _module3_profile_plan(engine)

    result = engine.generate_queries_from_inventive_profile(
        previous_plan=profile,
        source_plan_run_id="profile-run-1",
        source_sha256=None,
    )

    # 首轮固定五组（宪章 2.15）：确定性编译是首轮唯一来源，一次调用即成；
    # 模型不合规表达不进入任何首轮检索式，审计完整性不受影响。
    assert len(client.calls) == 2
    assert result.queries
    assert result.generation_source == "module3_profile_live_model"
    assert result.generation_warning is None
    assert not any(
        "智能家居系统" in item.expression for item in result.queries
    )
    audit = engine.last_invocation_audit
    assert audit["attempt_count"] == 1
    assert audit["successful_attempt"] == 1
    assert all(
        attempt["model"] == "glm-4.6v-test" for attempt in audit["attempts"]
    )


def test_module4_query_generation_requires_profile_and_limitations() -> None:
    """输入不足（无画像/无技术特征）明确失败，且不浪费模型调用。"""

    client = FakeVisionClient()
    engine = InvalidityAnalysisEngine(client)
    plan_without_profile = QueryPlan(
        claim_id="claim-1",
        iteration_number=1,
        round_kind="initial",
        technical_subject="扫地机器人",
        limitations=[_feature("f1", 1, "移动底盘")],
        queries=[],
        model="glm-4.6v-test",
        used_target_images=1,
    )
    with pytest.raises(AnalysisValidationError, match="发明检索画像"):
        engine.generate_queries_from_inventive_profile(
            previous_plan=plan_without_profile,
            source_plan_run_id="profile-run-1",
            source_sha256=None,
        )
    assert client.calls == []
    assert engine.last_invocation_audit is None


def test_module4_model_proposal_not_adopted_five_lanes_compiled() -> None:
    """GLM 提案不采用：首轮线由画像冻结事实确定性编译，OR 组全部可回溯。"""

    proposal = {
        "queries": [
            {
                "provider_kind": "patent",
                "purpose": "initial",
                "query_role": "inventive_point_precision",
                "query_variant": "object_plus_inventive_point",
                "search_scope": "full_text",
                "language": "zh",
                "expression": "扫地机器人 AND 移动底盘",
                "subject_terms": ["扫地机器人"],
                "feature_ids": ["f1"],
                "feature_terms": ["移动底盘"],
            },
        ]
    }
    client = FakeVisionClient(_query_plan_response(), proposal)
    engine = InvalidityAnalysisEngine(client)
    profile = _module3_profile_plan(engine)

    result = engine.generate_queries_from_inventive_profile(
        previous_plan=profile,
        source_plan_run_id="profile-run-1",
        source_sha256="sha-1",
    )

    patent_line = next(
        item
        for item in result.queries
        if item.provider_kind == "patent"
        and item.query_variant == "title_object_plus_desc_inventive"
        and item.date_channel == "ordinary_prior_art"
    )
    # 首轮固定五组（宪章 2.15）：客体组与发明点 OR 组的成员全部来自画像
    # 冻结事实，没有画像之外的词。
    assert patent_line.expression.startswith(
        "(扫地机器人 OR 扫拖机器人 OR robot cleaner) AND "
    )
    assert "(升降拖布 OR liftable cleaning pad)" in patent_line.expression
    assert "激光" not in patent_line.expression
    assert "吸尘" not in patent_line.expression
    assert not any(
        item.query_variant == "object_plus_inventive_point"
        for item in result.queries
    )
    assert any("未采用" in note for note in result.portfolio_warnings)


def test_module4_npl_proposal_not_adopted_in_initial_round() -> None:
    """首轮固定五组只有专利库检索线：NPL 提案不采用，整句也不进入任何检索式。"""

    proposal = {
        "queries": [
            {
                "provider_kind": "npl",
                "purpose": "initial",
                "query_role": "claim_context_recall",
                "query_variant": "system_architecture_recall",
                "search_scope": "claims",
                "language": "zh",
                "expression": "扫地机器人 包括移动底盘和设置于底盘上的升降拖布",
                "subject_terms": ["扫地机器人"],
                "feature_ids": ["f1", "f2"],
                "feature_terms": ["移动底盘", "升降拖布"],
                "date_channel": "ordinary_prior_art",
            },
        ]
    }
    client = FakeVisionClient(_query_plan_response(), proposal)
    engine = InvalidityAnalysisEngine(client)
    profile = _module3_profile_plan(engine)

    result = engine.generate_queries_from_inventive_profile(
        previous_plan=profile,
        source_plan_run_id="profile-run-1",
        source_sha256="sha-1",
    )

    # 首轮固定五组（宪章 2.15）：首轮没有 NPL 检索线；模型整句提案不采用，
    # 自然语言句子不会作为可执行检索式放行。
    assert not any(item.provider_kind == "npl" for item in result.queries)
    assert result.queries
    assert all(
        "包括" not in item.expression and "设置于" not in item.expression
        for item in result.queries
    )
    assert any("未采用" in note for note in result.portfolio_warnings)


def test_module4_compiled_portfolio_has_no_duplicate_expressions() -> None:
    """模型重复提案不采用；确定性编译的首轮组合同通道内无规范化重复检索式。"""

    proposal = {
        "queries": [
            {
                "provider_kind": "patent",
                "purpose": "initial",
                "query_role": "inventive_point_precision",
                "query_variant": "object_plus_inventive_point",
                "search_scope": "full_text",
                "language": "zh",
                "expression": "扫地机器人 AND 移动底盘",
                "subject_terms": ["扫地机器人"],
                "feature_ids": ["f1"],
                "feature_terms": ["移动底盘"],
            },
            {
                # 仅空白/大小写/标点差异且变体标签不同：仍是同一条检索。
                "provider_kind": "patent",
                "purpose": "initial",
                "query_role": "inventive_point_precision",
                "query_variant": "maximal_similarity_precision",
                "search_scope": "full_text",
                "language": "zh",
                "expression": "扫地机器人  AND  移动底盘",
                "subject_terms": ["扫地机器人"],
                "feature_ids": ["f1"],
                "feature_terms": ["移动底盘"],
                "allow_zero_results": True,
                "compact_fallback_allowed": False,
            },
        ]
    }
    client = FakeVisionClient(_query_plan_response(), proposal)
    engine = InvalidityAnalysisEngine(client)
    profile = _module3_profile_plan(engine)

    result = engine.generate_queries_from_inventive_profile(
        previous_plan=profile,
        source_plan_run_id="profile-run-1",
        source_sha256="sha-1",
    )

    # 首轮固定五组（宪章 2.15）：模型提案（含重复对）一律不采用；编译产出
    # 的普通现有技术通道内不存在规范化后相同的检索式。
    ordinary_patent = [
        item
        for item in result.queries
        if item.provider_kind == "patent"
        and item.date_channel == "ordinary_prior_art"
    ]
    normalised = [_normalise(item.expression) for item in ordinary_patent]
    assert len(normalised) == len(set(normalised))
    assert any("未采用" in note for note in result.portfolio_warnings)


def test_module4_profile_without_synonyms_keeps_single_term_groups() -> None:
    """画像确实没有同义词时单原词放行，不得硬造画像之外的词。"""

    response = _query_plan_response()
    response["subject_synonyms_zh"] = []
    response["subject_synonyms_en"] = []
    for feature in response["features"]:
        feature.pop("synonyms_zh", None)
        feature.pop("synonyms_en", None)
    profile_payload = response["invention_search_profile"]
    profile_payload["subject_synonyms_zh"] = []
    profile_payload["subject_synonyms_en"] = []
    for concept in [
        *profile_payload["common_context_features"],
        *profile_payload["inventive_point_features"],
    ]:
        concept.pop("synonyms_zh", None)
        concept.pop("synonyms_en", None)
    proposal = {
        "queries": [
            {
                "provider_kind": "patent",
                "purpose": "initial",
                "query_role": "inventive_point_precision",
                "query_variant": "object_plus_inventive_point",
                "search_scope": "full_text",
                "language": "zh",
                "expression": "扫地机器人 AND 移动底盘",
                "subject_terms": ["扫地机器人"],
                "feature_ids": ["f1"],
                "feature_terms": ["移动底盘"],
            },
        ]
    }
    client = FakeVisionClient(response, proposal)
    engine = InvalidityAnalysisEngine(client)
    profile = _module3_profile_plan(engine)

    result = engine.generate_queries_from_inventive_profile(
        previous_plan=profile,
        source_plan_run_id="profile-run-1",
        source_sha256="sha-1",
    )

    patent_line = next(
        item
        for item in result.queries
        if item.provider_kind == "patent"
        and item.query_variant == "title_object_plus_desc_inventive"
        and item.date_channel == "ordinary_prior_art"
    )
    # 首轮固定五组（宪章 2.15）：画像没有同义词时保持单词组，不硬造
    # 画像之外的词。
    assert patent_line.expression == "扫地机器人 AND 清洁件 AND 底盘 AND 升降"
    assert " OR " not in patent_line.expression
    assert "扫拖机器人" not in patent_line.expression
    assert "robot cleaner" not in patent_line.expression


def test_i2_legacy_profile_expands_mechanism_and_bounds_unknown_class_roles() -> None:
    response = {
        "technical_subject": "水枪",
        "subject_synonyms_zh": ["玩具水枪"],
        "subject_synonyms_en": ["water gun"],
        "features": [
            {
                "temp_id": "f1",
                "text": "位置选择性联接装置",
                "technical_subject": "水枪",
                "synonyms_en": ["position selective coupling device"],
            },
            {
                "temp_id": "f2",
                "text": "本体在中间位置时选择性联接阀杆",
                "technical_subject": "水枪",
                "synonyms_en": ["intermediate position selective coupling"],
            },
            {
                "temp_id": "f3",
                "text": "压力罐",
                "technical_subject": "水枪",
                "synonyms_en": ["pressure tank"],
            },
        ],
        "invention_search_profile": {
            "protected_subject": "水枪",
            "subject_synonyms_zh": ["玩具水枪"],
            "subject_synonyms_en": ["water gun"],
            "classification_anchors": [
                "F16K 1/00",
                "F16K 27/02",
                "F16K 31/06",
                "B05B 1/32",
                "F41B 9/00",
            ],
            "classification_anchor_roles": {
                "F16K 1/00": "general",
                "F16K 27/02": "general",
                "F16K 31/06": "general",
                "B05B 1/32": "general",
                "F41B 9/00": "general",
            },
            "invention_summary": "在选定位置联接阀杆。",
            "common_context_features": [
                {
                    "concept_id": "c1",
                    "text": "压力罐",
                    "feature_ids": ["f3"],
                    "source_reference": "权利要求1",
                    "rationale": "类别构件",
                    "preferred_scope": "claims",
                }
            ],
            "inventive_point_features": [
                {
                    "concept_id": "i1",
                    "text": "位置选择性联接装置",
                    "feature_ids": ["f1"],
                    "source_reference": "权利要求1",
                    "rationale": "发明作用",
                    "preferred_scope": "full_text",
                },
                {
                    "concept_id": "i2",
                    "text": "中间位置选择性联接",
                    "feature_ids": ["f2"],
                    "source_reference": "权利要求1",
                    "rationale": "发明动作关系",
                    "preferred_scope": "full_text",
                },
            ],
        },
        "queries": [
            {
                "provider_kind": "patent",
                "purpose": "initial",
                "query_role": "inventive_point_precision",
                "search_scope": "full_text",
                "language": "zh",
                "expression": "水枪 AND 位置选择性联接装置",
                "feature_ids": ["f1"],
                "feature_terms": ["位置选择性联接装置"],
                "rationale": "客体加发明点",
            }
        ],
    }
    engine = InvalidityAnalysisEngine(FakeVisionClient(response))
    first = engine.plan_queries(
        claim_id="claim-1",
        expanded_claim_text="一种水枪，包括压力罐和位置选择性联接装置。",
        target_images=["target.png"],
    )
    assert first.invention_search_profile is not None
    legacy_profile = first.invention_search_profile.model_copy(
        update={"mechanism_model": None}
    )
    legacy = first.model_copy(
        update={"invention_search_profile": legacy_profile}
    )

    rebuilt = engine.recompile_initial_query_plan(
        previous_plan=legacy,
        source_plan_run_id="legacy-i2",
        source_sha256="source-sha",
        # 编译器变体覆盖检查需要完整组合预算；首轮执行预算仍为 5 条。
        max_queries=20,
    )

    assert rebuilt.invention_search_profile is not None
    assert rebuilt.invention_search_profile.mechanism_model is not None
    role_terms = {
        term
        for element in rebuilt.invention_search_profile.mechanism_model.elements
        for term in element.role_equivalent_terms
    }
    assert {
        "选择性联接机构",
        "选择性接合机构",
        "selective coupling mechanism",
    }.issubset(role_terms)
    assert {"锁定机构", "解锁机构", "locking mechanism"}.isdisjoint(role_terms)
    # 首轮固定五组（宪章 2.15）：未知角色的分类号统一按客体相关分类号处理，
    # 进入同一条 分类号+说明书核心发明点 线（③），IPC 组至多两个、不逐条
    # 分线。
    classification_lines = [
        item
        for item in rebuilt.queries
        if item.provider_kind == "patent"
        and item.date_channel == "ordinary_prior_art"
        and item.query_variant == "classification_plus_desc_inventive"
    ]
    assert len(classification_lines) == 1
    classification_lane = classification_lines[0]
    assert classification_lane.classification_anchors == ["F16K1/00", "F16K27/02"]
    assert len(classification_lane.classification_anchors) <= 2
    assert classification_lane.provider_expression is not None
    assert classification_lane.provider_expression.startswith(
        "(IPC:(F16K1/00 OR F16K27/02))"
    )
    water_gun_line = next(
        item
        for item in rebuilt.queries
        if item.query_variant == "title_object_plus_desc_inventive"
        and item.date_channel == "ordinary_prior_art"
    )
    assert water_gun_line.subject_terms == ["水枪"]
    assert "选择性接合" in water_gun_line.expression
    assert "selective coupling mechanism" in water_gun_line.expression
    assert "锁定机构" not in water_gun_line.expression
    assert "解锁机构" not in water_gun_line.expression
    assert "locking mechanism" not in water_gun_line.expression
    assert all("US20200080816" not in item.expression for item in rebuilt.queries)

    contaminated_elements = [
        element.model_copy(
            update={
                "role_equivalent_terms": [
                    *element.role_equivalent_terms,
                    "锁定机构",
                    "解锁机构",
                    "locking mechanism",
                ],
                "operation_terms": [
                    *element.operation_terms,
                    "锁定和解锁",
                    "lock and unlock",
                ],
            }
        )
        for element in rebuilt.invention_search_profile.mechanism_model.elements
    ]
    contaminated_profile = rebuilt.invention_search_profile.model_copy(
        update={
            "mechanism_model": rebuilt.invention_search_profile.mechanism_model.model_copy(
                update={"elements": contaminated_elements}
            )
        }
    )
    contaminated_plan = rebuilt.model_copy(
        update={"invention_search_profile": contaminated_profile}
    )
    cleaned = engine.recompile_initial_query_plan(
        previous_plan=contaminated_plan,
        source_plan_run_id="contaminated-cached-i2",
        source_sha256="source-sha",
        max_queries=3,
    )
    assert all(
        "解锁机构" not in query.expression and "锁定机构" not in query.expression
        for query in cleaned.queries
    )
    assert all(
        "解锁机构" not in group and "锁定机构" not in group
        for query in cleaned.queries
        for group in query.feature_term_groups
    )
    # 首轮固定五组（宪章 2.15）：首轮没有 NPL 检索线；污染词族被剥离后，
    # 标题客体+说明书核心发明点线只保留画像可回溯的联接词族。
    assert not any(item.provider_kind == "npl" for item in cleaned.queries)
    cleaned_lane = next(
        item
        for item in cleaned.queries
        if item.query_variant == "title_object_plus_desc_inventive"
        and item.date_channel == "ordinary_prior_art"
    )
    assert "水枪" in cleaned_lane.expression
    assert (
        "位置选择性联接装置" in cleaned_lane.expression
        or "中间位置" in cleaned_lane.expression
        or "选择性联接" in cleaned_lane.expression
    )


@pytest.mark.parametrize(
    ("wrap", "expected_source"),
    [
        (
            lambda payload: {
                **payload,
                "queries": {"items": payload["queries"]},
            },
            "queries.items.list",
        ),
        (
            lambda payload: {
                "query_plan": {
                    **{key: value for key, value in payload.items() if key != "queries"},
                    "search_queries": payload["queries"],
                }
            },
            "search_queries.list",
        ),
    ],
)
def test_i2_accepts_audited_query_wrapper_shapes(
    wrap: Any, expected_source: str
) -> None:
    response = _query_plan_response()
    engine = InvalidityAnalysisEngine(FakeVisionClient(wrap(response)))

    plan = engine.plan_queries(
        claim_id="claim-1",
        expanded_claim_text="展开权利要求文本",
        target_images=["target.png"],
    )

    assert len(plan.queries) <= 5
    assert engine.last_invocation_audit is not None
    normalization = engine.last_invocation_audit["normalization"]
    assert normalization["schema_normalized"] is True
    assert normalization["query_source"] == expected_source
    assert engine.last_invocation_audit["response_data"] is not None


def test_i2_malformed_query_wrapper_uses_valid_profile_without_second_call() -> None:
    invalid = _query_plan_response()
    invalid["queries"] = {"unexpected": "not a query list"}
    valid = _query_plan_response()
    engine = InvalidityAnalysisEngine(FakeVisionClient(invalid, valid))

    plan = engine.plan_queries(
        claim_id="claim-1",
        expanded_claim_text="展开权利要求文本",
        target_images=["target.png"],
    )

    assert plan.queries
    assert engine.last_invocation_audit is not None
    assert engine.last_invocation_audit["attempt_count"] == 1
    assert engine.last_invocation_audit["attempts"][0]["response_data"]["queries"] == {
        "unexpected": "not a query list"
    }
    assert engine.last_invocation_audit["attempts"][0]["validation_error"] is None


def test_i4s_requires_both_image_groups_and_downgrades_missing_evidence() -> None:
    limitations = [
        _feature("f1", 1, "移动底盘"),
        _feature("f2", 2, "清洁件相对底盘升降"),
    ]
    response = {
        "disclosures": [
            {
                "feature_id": "f1",
                "status": "explicit",
                "evidence_quote": "mobile chassis",
                "evidence_location": "claim 1",
                "reasoning": "direct",
                "confidence": 0.95,
            },
            {
                "feature_id": "f2",
                "status": "explicit",
                "evidence_quote": "liftable cleaning pad",
                "evidence_location": "",
                "reasoning": "location missing",
                "confidence": 0.95,
            },
        ],
        "field_alignment": 0.9,
        "purpose_alignment": 0.8,
        "effect_alignment": 0.7,
        # Must be ignored; aggregation is deterministic.
        "evidence_completeness": 1.0,
    }
    client = FakeVisionClient(response)
    engine = InvalidityAnalysisEngine(client)
    with pytest.raises(AnalysisValidationError, match="对比文件.*缺少图像"):
        engine.compare_single_reference(
            claim_id="claim-1",
            expanded_claim_text="展开权利要求文本",
            target_patent_text=_target_patent_text(),
            limitations=limitations,
            document_id="D1",
            document_text="mobile chassis and liftable cleaning pad",
            target_images=["target.png"],
            document_images=[],
        )

    comparison = engine.compare_single_reference(
        claim_id="claim-1",
        expanded_claim_text="展开权利要求文本",
        target_patent_text=_target_patent_text(),
        limitations=limitations,
        document_id="D1",
        document_text="mobile chassis and liftable cleaning pad",
        target_images=["target.png"],
        document_images=["d1.png"],
    )
    assert comparison.disclosures[1].status is DisclosureStatus.UNCERTAIN
    assert comparison.disclosures[1].feature_text == "清洁件相对底盘升降"
    assert comparison.disclosures[1].confidence == 0.69
    assert comparison.evidence_completeness == 0.5
    assert not single_reference_fully_discloses(
        comparison, limitations, eligible_for_novelty=True
    )
    assert client.calls[0]["require_document_image"] is True


def test_i4s_reconciles_explicit_non_disclosure_reason_and_keeps_feature_text() -> None:
    limitations = [_feature("f-pressure-tank", 1, "压力罐")]
    client = FakeVisionClient(
        {
            "disclosures": [
                {
                    "feature_id": "f-pressure-tank",
                    "status": "uncertain",
                    "evidence_quote": "",
                    "evidence_location": "",
                    "reasoning": (
                        "已检查全部储液、加压和排液结构，均没有承担受压储液并向"
                        "喷射流路供液的构件，因此未披露该结构角色"
                    ),
                    "confidence": 0,
                    "mapping_basis": "none",
                    "structural_search_summary": (
                        "复核储液腔、泵和全部排液流路；储液腔仅靠重力供液，"
                        "不存在承压储液候选结构"
                    ),
                }
            ]
        },
        # 空证据否定触发守门A候选复核：模型按角色语义排查后维持合规否定
        _guard_review_keeps_negative("f-pressure-tank"),
    )
    comparison = InvalidityAnalysisEngine(client).compare_single_reference(
        claim_id="claim-1",
        expanded_claim_text="一种水枪，包括压力罐。",
        target_patent_text=_target_patent_text(),
        limitations=limitations,
        document_id="D1",
        document_text="对比文件全文",
        target_images=["target.png"],
        document_images=["d1.png"],
    )

    disclosure = comparison.disclosures[0]
    assert disclosure.feature_text == "压力罐"
    assert disclosure.status is DisclosureStatus.NOT_DISCLOSED
    assert disclosure.evidence_location == "对比文件全文复核（未检出对应披露）"
    assert disclosure.confidence == 0.7
    assert comparison.evidence_completeness == 1.0
    assert "仅仅没有出现目标术语" in client.calls[0]["prompt"]


def test_i4s_function_similarity_alone_cannot_confirm_structural_disclosure() -> None:
    limitations = [_feature("f-valve", 1, "阀体内设置可移动阀杆")]
    comparison = InvalidityAnalysisEngine(
        FakeVisionClient(
            {
                "target_mechanism_summary": "阀杆在阀体内移动以切换流路",
                "reference_mechanism_summary": "电磁件控制流体",
                "disclosures": [
                    {
                        "feature_id": "f-valve",
                        "status": "direct_and_unambiguous",
                        "evidence_quote": "controls the flow of liquid",
                        "evidence_location": "paragraph 12",
                        "reasoning": "均用于控制流体",
                        "confidence": 0.9,
                        "target_structural_role": "阀体内移动的阀杆",
                        "reference_structure_mapping": "仅说明控制流体功能",
                        "mapping_basis": "function_only",
                        "structural_evidence": [],
                    }
                ],
            }
        )
    ).compare_single_reference(
        claim_id="claim-1",
        expanded_claim_text="一种阀，包括阀体和可移动阀杆。",
        target_patent_text=_target_patent_text(),
        limitations=limitations,
        document_id="D1",
        document_text="paragraph 12 controls the flow of liquid",
        target_images=["target.png"],
        document_images=["d1.png"],
    )
    disclosure = comparison.disclosures[0]
    assert disclosure.status is DisclosureStatus.UNCERTAIN
    assert disclosure.confidence == 0.69
    assert "仅有功能相似" in disclosure.reasoning


def test_i4s_allows_different_terms_when_role_and_relation_are_supported() -> None:
    limitations = [_feature("f-coupling", 1, "位置选择性联接装置")]
    comparison = InvalidityAnalysisEngine(
        FakeVisionClient(
            {
                "target_mechanism_summary": "联接件在多个位置选择性连接",
                "reference_mechanism_summary": "滑动接头在两个位置锁定",
                "disclosures": [
                    {
                        "feature_id": "f-coupling",
                        "status": "direct_and_unambiguous",
                        "evidence_quote": "the sliding joint locks in either position",
                        "evidence_location": "paragraph 24",
                        "reasoning": "名称不同，但均承担选择联接位置的角色并具有同一位置锁定关系",
                        "confidence": 0.86,
                        "target_structural_role": "在多个位置选择联接的构件",
                        "reference_structure_mapping": "滑动接头可在两个位置锁定",
                        "mapping_basis": "role_and_relation",
                        "structural_evidence": [
                            "sliding joint",
                            "locks in either position"
                        ],
                    }
                ],
            }
        )
    ).compare_single_reference(
        claim_id="claim-1",
        expanded_claim_text="一种装置，包括位置选择性联接装置。",
        target_patent_text=_target_patent_text(),
        limitations=limitations,
        document_id="D1",
        document_text="paragraph 24 the sliding joint locks in either position",
        target_images=["target.png"],
        document_images=["d1.png"],
    )
    disclosure = comparison.disclosures[0]
    assert disclosure.status is DisclosureStatus.DIRECT_AND_UNAMBIGUOUS
    assert disclosure.mapping_basis == "role_and_relation"
    assert comparison.analysis_rule_version == I4S_RULE_VERSION


def test_i4s_domain_example_is_injected_only_for_matching_fluid_context() -> None:
    non_fluid_prompt = InvalidityAnalysisEngine._single_reference_prompt(
        claim_id="claim-1",
        expanded_claim_text="一种传动装置，包括旋转轴和齿轮。",
        target_patent_text="目标传动机构全文",
        limitations=[_feature("f-shaft", 1, "旋转轴", subject="传动装置")],
        document_id="D-GEAR",
        document_text="对比文件传动机构全文",
    )
    fluid_prompt = InvalidityAnalysisEngine._single_reference_prompt(
        claim_id="claim-1",
        expanded_claim_text="一种液体喷射装置，包括压力罐和阀。",
        target_patent_text="目标流体机构全文",
        limitations=[_feature("f-tank", 1, "压力罐", subject="液体喷射装置")],
        document_id="D-FLUID",
        document_text="对比文件流体机构全文",
    )

    assert "不得把泵的活塞误说成阀杆" not in non_fluid_prompt
    assert "不得把泵的活塞误说成阀杆" in fluid_prompt


def test_i4s_normalises_structural_mapping_word_used_in_status_field() -> None:
    limitations = [_feature("f-valve-member", 1, "可移动阀杆", subject="流体阀")]
    document_text = "paragraph 12 a valve core moves away from the seat to open flow"
    response = {
        "disclosures": [
                    {
                        "feature_id": "f-valve-member",
                        # Some models put the mapping basis in the status slot.
                        # The remaining evidence gates must still all pass.
                        "status": "structural_equivalent",
                        "evidence_quote": (
                            "a valve core moves away from the seat to open flow"
                        ),
                        "evidence_location": "paragraph 12",
                        "reasoning": "阀芯在流道内移动并相对阀座开闭",
                        "confidence": 0.9,
                        "target_structural_role": "在阀流道内移动控制通断",
                        "reference_structure_mapping": (
                            "valve core 在阀座开闭位置间移动"
                        ),
                        "mapping_basis": "structural_equivalent",
                        "structural_evidence": [
                            "valve core moves away from the seat"
                        ],
                    }
        ]
    }
    comparison = InvalidityAnalysisEngine(
        FakeVisionClient(response)
    ).compare_single_reference(
        claim_id="claim-1",
        expanded_claim_text="一种流体阀，包括可移动阀杆。",
        target_patent_text=_target_patent_text(),
        limitations=limitations,
        document_id="D-STATUS-ALIAS",
        document_text=document_text,
        target_images=["target.png"],
        document_images=["valve.png"],
    )

    assert (
        comparison.disclosures[0].status
        is DisclosureStatus.DIRECT_AND_UNAMBIGUOUS
    )


def test_target_patent_serializer_requires_specification_or_page_texts() -> None:
    incomplete = {
        "title": "一种液体喷射装置",
        "abstract": "涉及液体喷射。",
        "claims": [
            {
                "claim_id": "claim-1",
                "claim_type": "INDEPENDENT",
                "claim_text": "一种液体喷射装置，包括储液空间。",
            }
        ],
    }
    assert target_patent_text_for_analysis(incomplete) == ""

    page_fallback = {
        **incomplete,
        "page_texts": [
            {
                "page_number": 3,
                "text": "具体实施方式：密闭储液空间通过受压流路向喷射口供液。",
            }
        ],
    }
    serialized = target_patent_text_for_analysis(page_fallback)
    assert "目标专利第 3 页" in serialized
    assert "密闭储液空间通过受压流路" in serialized


def test_i4s_accepts_structural_equivalent_reservoir_in_pressurised_discharge_path() -> None:
    limitations = [
        _feature("f-pressure-tank", 1, "用于受压储液并向喷射流路供液的压力罐", subject="液体喷射装置")
    ]
    document_text = (
        "Paragraph 18: a sealed reservoir stores liquid. "
        "A hand pump supplies compressed air to the reservoir. "
        "The resulting pressure drives liquid from the reservoir through the "
        "discharge conduit and nozzle."
    )
    client = FakeVisionClient(
        {
            "target_mechanism_summary": "压力罐储液、承压并向喷射流路供液",
            "reference_mechanism_summary": "密闭 reservoir 经手泵加压后排液",
            "disclosures": [
                {
                    "feature_id": "f-pressure-tank",
                    "status": "direct_and_unambiguous",
                    "evidence_quote": (
                        "A hand pump supplies compressed air to the reservoir."
                    ),
                    "evidence_location": "paragraph 18",
                    "reasoning": (
                        "名称虽为 reservoir，但它位于受压排液链中并承担储液、承压和"
                        "向喷射流路供液的同一结构角色"
                    ),
                    "confidence": 0.91,
                    "target_structural_role": "储液、承压并向喷射流路供液",
                    "reference_structure_mapping": (
                        "密闭 reservoir 接收压缩空气并由内压驱动液体进入排液导管"
                    ),
                    "mapping_basis": "structural_equivalent",
                    "structural_evidence": [
                        "a sealed reservoir stores liquid",
                        "A hand pump supplies compressed air to the reservoir",
                        "pressure drives liquid from the reservoir through the discharge conduit",
                    ],
                }
            ],
            "field_alignment": 0.9,
            "purpose_alignment": 0.9,
            "effect_alignment": 0.9,
        }
    )

    comparison = InvalidityAnalysisEngine(client).compare_single_reference(
        claim_id="claim-1",
        expanded_claim_text="一种液体喷射装置，包括压力罐。",
        target_patent_text=_target_patent_text(),
        limitations=limitations,
        document_id="D-RESERVOIR",
        document_text=document_text,
        target_images=["target.png"],
        document_images=["reservoir.png"],
    )

    disclosure = comparison.disclosures[0]
    assert disclosure.status is DisclosureStatus.DIRECT_AND_UNAMBIGUOUS
    assert disclosure.mapping_basis == "structural_equivalent"
    assert disclosure.reference_structure_mapping.startswith("密闭 reservoir")
    assert single_reference_fully_discloses(
        comparison, limitations, eligible_for_novelty=True
    )
    assert "受压储液路径" in client.calls[0]["prompt"]


def test_i4s_allows_one_integrated_valve_structure_to_map_multiple_features() -> None:
    limitations = [
        _feature("f-valve-guide", 1, "带有阀导管的阀", subject="液体喷射装置"),
        _feature(
            "f-valve-states",
            2,
            "阀杆具有关闭位置和打开位置",
            subject="液体喷射装置",
        ),
    ]
    document_text = (
        "Paragraph 27: within the valve-body flow passage, a piston rod slides "
        "axially between a seated position that blocks flow and a raised position "
        "that permits flow."
    )
    integrated_mapping = (
        "同一阀体流道及其轴向滑动 piston rod 组成集成阀件"
    )
    disclosures = []
    for feature_id, target_role in (
        ("f-valve-guide", "容纳移动阀杆并形成流体通路的阀导向/流道结构"),
        ("f-valve-states", "在关闭位置阻流、在打开位置通流的移动阀杆"),
    ):
        disclosures.append(
            {
                "feature_id": feature_id,
                "status": "direct_and_unambiguous",
                "evidence_quote": (
                    "within the valve-body flow passage, a piston rod slides "
                    "axially between a seated position that blocks flow and a "
                    "raised position that permits flow"
                ),
                "evidence_location": "paragraph 27",
                "reasoning": (
                    "集成阀件虽未按目标术语拆分命名，但流道、移动杆件及开闭状态的"
                    "结构关系直接对应"
                ),
                "confidence": 0.9,
                "target_structural_role": target_role,
                "reference_structure_mapping": integrated_mapping,
                "mapping_basis": "structural_equivalent",
                "structural_evidence": [
                    "valve-body flow passage",
                    "piston rod slides axially",
                    "seated position that blocks flow",
                    "raised position that permits flow",
                ],
                "integrated_structure_mapping": True,
            }
        )

    comparison = InvalidityAnalysisEngine(
        FakeVisionClient(
            {
                "target_mechanism_summary": "阀导管内的阀杆在开闭位置间移动",
                "reference_mechanism_summary": "阀体流道中的活塞杆轴向移动控制通断",
                "disclosures": disclosures,
            }
        )
    ).compare_single_reference(
        claim_id="claim-1",
        expanded_claim_text="一种装置，包括带有阀导管的阀及可在开闭位置间移动的阀杆。",
        target_patent_text=_target_patent_text(),
        limitations=limitations,
        document_id="D-INTEGRATED-VALVE",
        document_text=document_text,
        target_images=["target.png"],
        document_images=["valve.png"],
    )

    assert [item.status for item in comparison.disclosures] == [
        DisclosureStatus.DIRECT_AND_UNAMBIGUOUS,
        DisclosureStatus.DIRECT_AND_UNAMBIGUOUS,
    ]
    assert all(item.integrated_structure_mapping for item in comparison.disclosures)
    assert {
        item.reference_structure_mapping for item in comparison.disclosures
    } == {integrated_mapping}
    assert single_reference_fully_discloses(
        comparison, limitations, eligible_for_novelty=True
    )


def test_i4s_second_pass_repairs_mixed_integrated_mechanism_false_negative() -> None:
    limitations = [
        _feature("f-moving-valve", 1, "可移动阀杆", subject="液体喷射装置"),
        _feature("f-valve-passage", 2, "带有阀导管的阀", subject="液体喷射装置"),
    ]
    moving_quote = "阀芯在弹簧作用下封紧阀座，并可由压柱推动离开阀座"
    passage_quote = "水由内管进入枪体，穿过阀座后流入出水管"
    document_text = f"说明书第12段：{moving_quote}；{passage_quote}。"
    first_pass = {
        "target_mechanism_summary": "移动阀杆在阀导管内开闭流路",
        "reference_mechanism_summary": "枪体内的阀芯相对阀座移动控制水流",
        "disclosures": [
            {
                "feature_id": "f-moving-valve",
                "status": "direct_and_unambiguous",
                "evidence_quote": moving_quote,
                "evidence_location": "说明书第12段",
                "reasoning": "阀芯与压柱共同构成移动阀件并相对阀座开闭",
                "confidence": 0.92,
                "target_structural_role": "在阀流道内移动并控制通断",
                "reference_structure_mapping": "阀芯与压柱共同移动",
                "mapping_basis": "structural_equivalent",
                "structural_evidence": [moving_quote],
                "integrated_structure_mapping": True,
            },
            {
                "feature_id": "f-valve-passage",
                "status": "not_disclosed",
                "evidence_quote": "",
                "evidence_location": "全文复核",
                "reasoning": "没有名为阀导管的独立零件，因此未披露",
                "confidence": 0.9,
                "mapping_basis": "none",
                "structural_search_summary": "检查了枪体、内管、阀座和出水管",
            },
        ],
    }
    second_pass = {
        "target_mechanism_summary": "移动阀杆在阀导管内开闭流路",
        "reference_mechanism_summary": "内管、枪体内部流道、阀座和出水管构成连续阀流路",
        "disclosures": [
            {
                "feature_id": "f-valve-passage",
                "status": "direct_and_unambiguous",
                "evidence_quote": passage_quote,
                "evidence_location": "说明书第12段",
                "reasoning": "枪体内部位于内管、阀座和出水管之间的流道承担阀导管角色",
                "confidence": 0.9,
                "target_structural_role": "容纳移动阀件并连通入口、阀座和出口",
                "reference_structure_mapping": "枪体内部连续流道",
                "mapping_basis": "structural_equivalent",
                "structural_evidence": [passage_quote, moving_quote],
                "integrated_structure_mapping": True,
            }
        ],
    }
    client = FakeVisionClient(first_pass, second_pass)

    comparison = InvalidityAnalysisEngine(client).compare_single_reference(
        claim_id="claim-1",
        expanded_claim_text="一种水枪，包括带有阀导管的阀和可移动阀杆。",
        target_patent_text=_target_patent_text(),
        limitations=limitations,
        document_id="D-MIXED-STRUCTURE",
        document_text=document_text,
        target_images=["target-1.png", "target-2.png"],
        document_images=["reference-1.png", "reference-2.png", "reference-3.png"],
    )

    assert len(client.calls) == 2
    assert client.calls[1]["request_timeout_seconds"] == 60.0
    assert client.calls[1]["direct_attempt_timeout_seconds"] == 40.0
    assert client.calls[1]["max_tokens"] == 4096
    assert client.calls[1]["target_images"] == ["target-1.png"]
    assert client.calls[1]["document_images"] == [
        "reference-1.png",
        "reference-2.png",
    ]
    assert "整体结构复核任务" in client.calls[1]["prompt"]
    assert '"target_context_excerpt"' in client.calls[1]["prompt"]
    assert '"document_evidence_text"' in client.calls[1]["prompt"]
    assert '"immutable_positive_disclosures"' in client.calls[1]["prompt"]
    assert '"target_patent_text"' not in client.calls[1]["prompt"]
    assert [item.status for item in comparison.disclosures] == [
        DisclosureStatus.DIRECT_AND_UNAMBIGUOUS,
        DisclosureStatus.DIRECT_AND_UNAMBIGUOUS,
    ]
    assert comparison.disclosures[1].mapping_basis == "structural_equivalent"
    assert comparison.structural_review_attempted is True
    assert comparison.structural_review_performed is True
    assert comparison.structural_review_status == "completed"
    assert comparison.structural_review_error_code == ""
    assert comparison.structural_review_used_target_images == 1
    assert comparison.structural_review_used_document_images == 2
    assert comparison.analysis_pass_count == 2
    assert comparison.analysis_rule_version == I4S_RULE_VERSION


def test_i4s_complete_fluid_valve_mechanism_overrides_part_name_boundaries() -> None:
    mapped_features = [
        ("f-valve", "带有阀导管的阀"),
        ("f-supply", "将压力罐与阀导管连接的管"),
        ("f-stem", "可移动阀杆"),
        ("f-ports", "阀导管具有入口端口、出口端口和阀座"),
        ("f-inlet", "管连接到阀导管的入口端口"),
        ("f-plug", "阀杆带有阀塞"),
        ("f-states", "阀杆具有关闭位置和打开位置"),
        ("f-closed", "关闭位置中阀塞关闭阀座"),
        ("f-open", "打开位置中阀塞缩回使流体流动穿过阀座"),
        ("f-actuator", "阀驱动器"),
    ]
    untouched_features = [
        ("f-axis", "阀杆限定第一纵向轴线"),
        ("f-coupling", "位置选择性联接装置"),
    ]
    limitations = [
        _feature(feature_id, index, feature_text, subject="水枪")
        for index, (feature_id, feature_text) in enumerate(
            [*mapped_features, *untouched_features],
            1,
        )
    ]
    disclosures = [
        FeatureDisclosure(
            feature_id=feature_id,
            feature_text=feature_text,
            status=DisclosureStatus.NOT_DISCLOSED,
            reasoning="对比文件没有使用目标零件名称，因此未披露",
            confidence=0.9,
            mapping_basis="none",
            structural_search_summary="已检查全文",
        )
        for feature_id, feature_text in [*mapped_features, *untouched_features]
    ]
    document_text = "\n\n".join(
        (
            "进水管与内管相连，内管前端连接枪体。",
            "枪体内设阀座。",
            "枪体出口连接出水管。",
            "阀座中设阀芯，阀芯前端与压柱连接。",
            "弹簧顶住压柱。",
            "使阀芯封紧在阀座上。",
            "按压把手时压柱推动阀芯脱离阀座。",
            "水进入阀座并流入出水管。",
            "松开后弹簧复位。",
        )
    )

    reconciled = _reconcile_complete_fluid_valve_mechanism(
        limitations=limitations,
        disclosures=disclosures,
        document_text=document_text,
    )

    by_id = {item.feature_id: item for item in reconciled}
    assert all(
        by_id[feature_id].status is DisclosureStatus.DIRECT_AND_UNAMBIGUOUS
        for feature_id, _feature_text in mapped_features
    )
    assert all(
        by_id[feature_id].mapping_basis == "structural_equivalent"
        for feature_id, _feature_text in mapped_features
    )
    assert all(
        by_id[feature_id].integrated_structure_mapping
        for feature_id, _feature_text in mapped_features
    )
    assert all(
        by_id[feature_id].status
        not in {
            DisclosureStatus.EXPLICIT,
            DisclosureStatus.DIRECT_AND_UNAMBIGUOUS,
            DisclosureStatus.NECESSARILY_IMPLICIT,
        }
        for feature_id, _feature_text in untouched_features
    )


def test_i4s_admitted_motion_triggers_review_even_when_first_pass_is_all_negative() -> None:
    limitations = [
        _feature("f-moving-valve", 1, "可移动阀杆", subject="液体喷射装置"),
        _feature("f-valve-state", 2, "阀杆具有打开和关闭位置", subject="液体喷射装置"),
    ]
    quote = "阀芯封紧在阀座上，按压压柱时阀芯脱离阀座，水穿过阀座流出"
    document_text = f"说明书第12段：{quote}。"
    first_pass = {
        "disclosures": [
            {
                "feature_id": "f-moving-valve",
                "status": "not_disclosed",
                "reasoning": "阀芯会移动并脱离阀座，但无阀杆结构，无法对应",
                "confidence": 0.95,
                "mapping_basis": "none",
                "structural_search_summary": "检查阀芯、压柱和阀座",
            },
            {
                "feature_id": "f-valve-state",
                "status": "not_disclosed",
                "reasoning": "阀芯有封紧和脱离位置，但不是名为阀杆的部件，故未披露",
                "confidence": 0.95,
                "mapping_basis": "none",
                "structural_search_summary": "检查阀芯相对阀座的动作",
            },
        ]
    }
    second_pass = {
        "disclosures": [
            {
                "feature_id": feature_id,
                "status": "direct_and_unambiguous",
                "evidence_quote": quote,
                "evidence_location": "说明书第12段",
                "reasoning": "阀芯与压柱构成集成移动阀件并在阀座开闭位置间移动",
                "confidence": 0.91,
                "target_structural_role": role,
                "reference_structure_mapping": "阀芯、压柱与阀座的集成开闭结构",
                "mapping_basis": "structural_equivalent",
                "structural_evidence": [quote],
                "integrated_structure_mapping": True,
            }
            for feature_id, role in (
                ("f-moving-valve", "移动并控制阀开闭的阀杆角色"),
                ("f-valve-state", "相对阀座处于打开和关闭位置"),
            )
        ]
    }
    client = FakeVisionClient(first_pass, second_pass)

    comparison = InvalidityAnalysisEngine(client).compare_single_reference(
        claim_id="claim-1",
        expanded_claim_text="一种水枪，包括具有打开和关闭位置的可移动阀杆。",
        target_patent_text=_target_patent_text(),
        limitations=limitations,
        document_id="D-ALL-NEGATIVE-CONTRADICTION",
        document_text=document_text,
        target_images=["target.png"],
        document_images=["reference.png"],
    )

    assert len(client.calls) == 2
    assert all(
        item.status is DisclosureStatus.DIRECT_AND_UNAMBIGUOUS
        for item in comparison.disclosures
    )
    assert all(item.integrated_structure_mapping for item in comparison.disclosures)
    assert comparison.structural_review_performed is True
    assert comparison.analysis_pass_count == 2


def test_i4s_traceable_positive_mapping_gets_evidence_floor_when_confidence_omitted() -> None:
    quote = "驱动马达41通过传动构件42带动旋钮套筒11转动"
    response = {
        "disclosures": [
            {
                "feature_id": "f-drive",
                "status": "direct_and_unambiguous",
                "evidence_quote": quote,
                "evidence_location": "说明书第2页",
                "reasoning": "马达经传动构件输出转矩，承担动力与传动角色",
                "target_structural_role": "提供动力并向连接部件传递转矩",
                "reference_structure_mapping": "驱动马达41与传动构件42",
                "mapping_basis": "role_and_relation",
            }
        ]
    }

    comparison = InvalidityAnalysisEngine(FakeVisionClient(response)).compare_single_reference(
        claim_id="claim-1",
        expanded_claim_text="一种燃气灶调节装置，包括动力部件和传动部件。",
        target_patent_text=_target_patent_text(),
        limitations=[_feature("f-drive", 1, "动力部件和传动部件", subject="燃气灶调节装置")],
        document_id="D-TRACEABLE",
        document_text=f"说明书第2页：{quote}。",
        target_images=["target.png"],
        document_images=["reference.png"],
    )

    disclosure = comparison.disclosures[0]
    assert disclosure.status is DisclosureStatus.DIRECT_AND_UNAMBIGUOUS
    assert disclosure.confidence == 0.7


def test_i4s_traceable_positive_mapping_uses_objective_floor_over_low_model_confidence() -> None:
    quote = "驱动马达41通过传动构件42带动旋钮套筒11转动"
    response = {
        "disclosures": [
            {
                "feature_id": "f-drive",
                "status": "direct_and_unambiguous",
                "evidence_quote": quote,
                "evidence_location": "说明书第2页",
                "reasoning": "马达经传动构件输出转矩，结构角色直接对应",
                "confidence": 0.2,
                "target_structural_role": "提供动力并传递转矩",
                "reference_structure_mapping": "驱动马达41与传动构件42",
                "mapping_basis": "role_and_relation",
                "structural_evidence": [quote],
            }
        ]
    }

    comparison = InvalidityAnalysisEngine(FakeVisionClient(response)).compare_single_reference(
        claim_id="claim-1",
        expanded_claim_text="一种燃气灶调节装置，包括动力部件和传动部件。",
        target_patent_text=_target_patent_text(),
        limitations=[_feature("f-drive", 1, "动力部件和传动部件", subject="燃气灶调节装置")],
        document_id="D-TRACEABLE-LOW-CONFIDENCE",
        document_text=f"说明书第2页：{quote}。",
        target_images=["target.png"],
        document_images=["reference.png"],
    )

    disclosure = comparison.disclosures[0]
    assert disclosure.status is DisclosureStatus.DIRECT_AND_UNAMBIGUOUS
    assert disclosure.confidence == 0.7


def test_i4s_overlapping_ranges_cannot_be_rejected_as_no_overlap() -> None:
    limitation = _feature(
        "f-solids",
        1,
        "按重量百分比计算的组合物50-65%",
        subject="水性陶瓷浆料",
    )
    first_pass = {
        "disclosures": [
            {
                "feature_id": "f-solids",
                "status": "not_disclosed",
                "evidence_quote": "组合物30-60%和水40-70%",
                "evidence_location": "权利要求1",
                "reasoning": "文献范围30-60%未覆盖目标50-65%，所以无重叠",
                "confidence": 0.9,
                "mapping_basis": "none",
                "structural_search_summary": "比较了同一组合物的重量百分比范围",
            }
        ]
    }
    repair = {
        "disclosures": [
            {
                "feature_id": "f-solids",
                "status": "direct_and_unambiguous",
                "evidence_quote": "组合物30-60%和水40-70%",
                "evidence_location": "权利要求1",
                "reasoning": "同一参数在50-60%区间相交",
                "confidence": 0.9,
                "target_structural_role": "限定组合物重量占比",
                "reference_structure_mapping": "组合物重量占比30-60%",
                "mapping_basis": "role_and_relation",
                "structural_evidence": ["组合物30-60%和水40-70%"],
            }
        ]
    }
    client = FakeVisionClient(first_pass, repair)

    comparison = InvalidityAnalysisEngine(client).compare_single_reference(
        claim_id="claim-1",
        expanded_claim_text="一种浆料，包括按重量百分比计算的组合物50-65%。",
        target_patent_text=_target_patent_text(),
        limitations=[limitation],
        document_id="D-OVERLAPPING-RANGE",
        document_text="权利要求1：组合物30-60%和水40-70%。",
        target_images=["target.png"],
        document_images=["reference.png"],
    )

    assert len(client.calls) == 2
    assert "数值范围须先计算交集" in client.calls[1]["prompt"]
    assert comparison.disclosures[0].status is DisclosureStatus.DIRECT_AND_UNAMBIGUOUS


def test_i4s_composition_mass_parts_can_disclose_overlapping_weight_share() -> None:
    quote = "组分(B)光引发剂在感光性树脂组合物中的用量优选为1-5质量份"
    comparison = InvalidityAnalysisEngine(
        FakeVisionClient(
            {
                "disclosures": [
                    {
                        "feature_id": "f-photo",
                        "status": "uncertain",
                        "evidence_quote": quote,
                        "evidence_location": "说明书[0064]",
                        "reasoning": "质量份与百分比的计量形式不同，暂不能确认",
                        "confidence": 0.55,
                        "mapping_basis": "uncertain",
                        "reference_structure_mapping": "光引发剂在组合物中为1-5质量份",
                    }
                ]
            }
        )
    ).compare_single_reference(
        claim_id="claim-1",
        expanded_claim_text="一种组合物，光引发剂化合物占组合物重量的0.5-10%。",
        target_patent_text=_target_patent_text(),
        limitations=[
            _feature(
                "f-photo",
                1,
                "光引发剂化合物占组合物重量的0.5-10%",
                subject="光固化组合物",
            )
        ],
        document_id="D-COMPOSITION-PARTS",
        document_text=f"说明书[0064]：{quote}。",
        target_images=["target.png"],
        document_images=["reference.png"],
    )

    disclosure = comparison.disclosures[0]
    assert disclosure.status is DisclosureStatus.DIRECT_AND_UNAMBIGUOUS
    assert disclosure.mapping_basis == "structural_equivalent"
    assert "其他组分作为计量基准" in disclosure.reasoning


def test_i4s_composition_parts_relative_to_resin_are_not_weight_share() -> None:
    quote = "相对于树脂100质量份，光引发剂的用量为1-5质量份"
    comparison = InvalidityAnalysisEngine(
        FakeVisionClient(
            {
                "disclosures": [
                    {
                        "feature_id": "f-photo",
                        "status": "uncertain",
                        "evidence_quote": quote,
                        "evidence_location": "说明书[0064]",
                        "reasoning": "计量基准是树脂而非整个组合物",
                        "confidence": 0.55,
                        "mapping_basis": "uncertain",
                        "reference_structure_mapping": "相对于树脂的光引发剂用量",
                    }
                ]
            }
        )
    ).compare_single_reference(
        claim_id="claim-1",
        expanded_claim_text="一种组合物，光引发剂化合物占组合物重量的0.5-10%。",
        target_patent_text=_target_patent_text(),
        limitations=[
            _feature(
                "f-photo",
                1,
                "光引发剂化合物占组合物重量的0.5-10%",
                subject="光固化组合物",
            )
        ],
        document_id="D-RESIN-BASIS",
        document_text=f"说明书[0064]：{quote}。",
        target_images=["target.png"],
        document_images=["reference.png"],
    )

    assert comparison.disclosures[0].status is DisclosureStatus.UNCERTAIN


def test_i4s_structural_review_failure_keeps_usable_first_pass() -> None:
    limitations = [
        _feature("f-moving-valve", 1, "可移动阀杆", subject="液体喷射装置"),
        _feature("f-valve-passage", 2, "带有阀导管的阀", subject="液体喷射装置"),
    ]
    moving_quote = "a valve core moves away from the seat to open flow"
    first_pass = {
        "disclosures": [
            {
                "feature_id": "f-moving-valve",
                "status": "direct_and_unambiguous",
                "evidence_quote": moving_quote,
                "evidence_location": "paragraph 12",
                "reasoning": "移动阀芯相对阀座控制开闭",
                "confidence": 0.9,
                "target_structural_role": "移动控制通断",
                "reference_structure_mapping": "移动阀芯",
                "mapping_basis": "structural_equivalent",
            },
            {
                "feature_id": "f-valve-passage",
                "status": "not_disclosed",
                "evidence_quote": "",
                "evidence_location": "全文复核",
                "reasoning": "已核对流路结构但未披露对应阀导管",
                "confidence": 0.8,
                "mapping_basis": "none",
                "structural_search_summary": "检查入口、阀座和出口，未找到连续内部流道",
            },
        ]
    }
    client = FailStructuralReviewClient(
        first_pass,
        # 结构复核失败不影响守门A：空证据否定的候选复核维持合规否定
        _guard_review_keeps_negative("f-valve-passage"),
    )

    comparison = InvalidityAnalysisEngine(client).compare_single_reference(
        claim_id="claim-1",
        expanded_claim_text="一种阀，包括阀导管和可移动阀杆。",
        target_patent_text=_target_patent_text(),
        limitations=limitations,
        document_id="D-REVIEW-TRANSIENT",
        document_text=f"paragraph 12 {moving_quote}",
        target_images=["target.png"],
        document_images=["reference.png"],
    )

    assert len(client.calls) == 4
    assert client.calls[1]["require_document_image"] is True
    assert client.calls[2]["require_document_image"] is False
    assert client.calls[2]["target_images"] == ["target.png"]
    assert client.calls[2]["document_images"] == []
    assert comparison.disclosures[0].status is DisclosureStatus.DIRECT_AND_UNAMBIGUOUS
    assert comparison.disclosures[1].status is DisclosureStatus.NOT_DISCLOSED
    assert comparison.structural_review_attempted is True
    assert comparison.structural_review_performed is False
    assert comparison.structural_review_status == "model_error"
    assert comparison.structural_review_error_code == "MODEL_TRANSPORT_ERROR"
    assert comparison.structural_review_used_target_images == 1
    assert comparison.structural_review_used_document_images == 1
    assert comparison.analysis_pass_count == 1


def test_i4s_partial_structural_review_is_incomplete_and_not_cached_as_complete() -> None:
    quote_1 = "rotor 12 turns output shaft 14"
    quote_2 = "sleeve 16 rotates together with rotor 12"
    first_pass = {
        "disclosures": [
            {
                "feature_id": "f-rotor",
                "status": "direct_and_unambiguous",
                "evidence_quote": quote_1,
                "evidence_location": "paragraph 8",
                "reasoning": "rotor 驱动输出轴",
                "confidence": 0.9,
                "target_structural_role": "旋转并输出扭矩",
                "reference_structure_mapping": "rotor 12 and output shaft 14",
                "mapping_basis": "role_and_relation",
            },
            {
                "feature_id": "f-sleeve",
                "status": "not_disclosed",
                "reasoning": "轴套会旋转并传递扭矩，但不是独立转子构件",
                "confidence": 0.9,
                "mapping_basis": "none",
                "structural_search_summary": "检查轴套和输出轴的共同旋转关系",
            },
            {
                "feature_id": "f-bearing",
                "status": "not_disclosed",
                "reasoning": "轴承与输出轴不相连，不能承担支承输出轴的结构角色",
                "confidence": 0.9,
                "mapping_basis": "none",
                "structural_search_summary": "检查轴承与输出轴的连接拓扑，二者不相连",
            },
        ]
    }
    partial_review = {
        "disclosures": [
            {
                "feature_id": "f-sleeve",
                "status": "direct_and_unambiguous",
                "evidence_quote": quote_2,
                "evidence_location": "paragraph 9",
                "reasoning": "轴套与转子共同旋转并传递扭矩",
                "confidence": 0.9,
                "target_structural_role": "旋转传递扭矩",
                "reference_structure_mapping": "sleeve 16 and rotor 12",
                "mapping_basis": "structural_equivalent",
            }
        ]
    }
    limitations = [
        _feature("f-rotor", 1, "旋转转子", subject="传动装置"),
        _feature("f-sleeve", 2, "传递扭矩的套筒", subject="传动装置"),
        _feature("f-bearing", 3, "支承输出轴的轴承", subject="传动装置"),
    ]
    client = FakeVisionClient(
        first_pass,
        partial_review,
        {"disclosures": []},
        # 空证据否定的守门A候选复核：维持合规否定
        _guard_review_keeps_negative("f-bearing"),
    )
    comparison = InvalidityAnalysisEngine(client).compare_single_reference(
        claim_id="claim-1",
        expanded_claim_text="一种传动装置，包括旋转转子、套筒和轴承。",
        target_patent_text=_target_patent_text(),
        limitations=limitations,
        document_id="D-PARTIAL-REVIEW",
        document_text=f"paragraph 8 {quote_1}. paragraph 9 {quote_2}.",
        target_images=["target.png"],
        document_images=["reference.png"],
    )

    assert comparison.structural_review_attempted is True
    assert comparison.structural_review_status == "model_error"
    assert comparison.structural_review_error_code == (
        "STRUCTURAL_REVIEW_OUTPUT_INCOMPLETE"
    )
    assert comparison.structural_review_performed is False
    assert comparison.analysis_pass_count == 1
    assert comparison.disclosures[1].status is DisclosureStatus.UNCERTAIN
    assert len(client.calls) == 4
    assert '"feature_id": "f-bearing"' in client.calls[2]["prompt"]
    assert '"feature_id": "f-sleeve"' not in client.calls[2]["prompt"]


def test_bounded_structural_review_cannot_create_a_new_conclusive_negative() -> None:
    positive_quote = "source 1 supplies current to conductor 2"
    first_pass = {
        "disclosures": [
            {
                "feature_id": "f-source",
                "status": "direct_and_unambiguous",
                "evidence_quote": positive_quote,
                "evidence_location": "paragraph 3",
                "reasoning": "电源向导线供电",
                "confidence": 0.9,
                "target_structural_role": "提供电能",
                "reference_structure_mapping": "source 1",
                "mapping_basis": "role_and_relation",
            },
            {
                "feature_id": "f-circuit",
                "status": "uncertain",
                "reasoning": "导线连接关系尚不明确",
                "confidence": 0.5,
                "mapping_basis": "uncertain",
                "structural_search_summary": "首轮检查了导线端点",
            },
        ]
    }
    second_pass = {
        "disclosures": [
            {
                "feature_id": "f-circuit",
                "status": "not_disclosed",
                "reasoning": "候选导线保持开路，没有形成闭合回路",
                "confidence": 0.9,
                "mapping_basis": "none",
                "structural_search_summary": "检查完整供电路径，回路保持开路",
            }
        ]
    }
    limitations = [
        _feature("f-source", 1, "电源", subject="供电装置"),
        _feature("f-circuit", 2, "闭合供电回路", subject="供电装置"),
    ]
    comparison = InvalidityAnalysisEngine(
        FakeVisionClient(first_pass, second_pass)
    ).compare_single_reference(
        claim_id="claim-1",
        expanded_claim_text="一种供电装置，包括电源和闭合供电回路。",
        target_patent_text=_target_patent_text(),
        limitations=limitations,
        document_id="D-BOUNDED-NEGATIVE",
        document_text=f"paragraph 3 {positive_quote}. conductor 2 terminates at an open switch.",
        target_images=["target.png"],
        document_images=["reference.png"],
    )

    assert comparison.structural_review_status == "completed"
    assert comparison.disclosures[1].status is DisclosureStatus.UNCERTAIN


def test_i4s_traceable_low_confidence_positive_uses_objective_evidence_floor() -> None:
    quote = "the sliding joint locks in either position"
    comparison = InvalidityAnalysisEngine(
        FakeVisionClient(
            {
                "disclosures": [
                    {
                        "feature_id": "f-coupling",
                        "status": "direct_and_unambiguous",
                        "evidence_quote": quote,
                        "evidence_location": "paragraph 24",
                        "reasoning": "滑动接头承担选择联接位置的角色",
                        "confidence": 0.65,
                        "target_structural_role": "选择联接位置",
                        "reference_structure_mapping": "滑动接头在两个位置锁定",
                        "mapping_basis": "role_and_relation",
                    }
                ]
            }
        )
    ).compare_single_reference(
        claim_id="claim-1",
        expanded_claim_text="一种装置，包括位置选择性联接装置。",
        target_patent_text=_target_patent_text(),
        limitations=[_feature("f-coupling", 1, "位置选择性联接装置")],
        document_id="D-LOW-CONFIDENCE",
        document_text=f"paragraph 24 {quote}",
        target_images=["target.png"],
        document_images=["reference.png"],
    )

    assert (
        comparison.disclosures[0].status
        is DisclosureStatus.DIRECT_AND_UNAMBIGUOUS
    )
    assert comparison.disclosures[0].confidence == 0.7
    assert single_reference_fully_discloses(
        comparison,
        [_feature("f-coupling", 1, "位置选择性联接装置")],
        eligible_for_novelty=True,
    )


def test_i4s_generic_umbrella_quote_cannot_prove_a_compound_literal_limitation() -> None:
    quote = "耳机本体内设置有电子元器件"
    limitation = _feature(
        "f-electronics",
        1,
        "耳机本体内具有PCBA板，PCBA板电性连接有喇叭和电池",
        subject="无线耳机",
    )
    comparison = InvalidityAnalysisEngine(
        FakeVisionClient(
            {
                "disclosures": [
                    {
                        "feature_id": "f-electronics",
                        "status": "explicit",
                        "evidence_quote": quote,
                        "evidence_location": "说明书第0028段",
                        "reasoning": "电子元器件概括了线路板、喇叭和电池",
                        "confidence": 0.92,
                        "target_structural_role": "供电并驱动喇叭",
                        "reference_structure_mapping": "电子元器件",
                        "mapping_basis": "literal",
                    }
                ]
            }
        )
    ).compare_single_reference(
        claim_id="claim-1",
        expanded_claim_text="一种无线耳机，包括耳机本体及内部电子组件。",
        target_patent_text=_target_patent_text(),
        limitations=[limitation],
        document_id="D-GENERIC-UMBRELLA",
        document_text=f"说明书第0028段 {quote}",
        target_images=["target.png"],
        document_images=["reference.png"],
    )

    disclosure = comparison.disclosures[0]
    assert disclosure.status is DisclosureStatus.UNCERTAIN
    assert disclosure.confidence == 0.69
    assert "局部或上位概念" in disclosure.reasoning


def test_i4s_generic_umbrella_evidence_cannot_prove_compound_structural_mapping() -> None:
    quote = "耳机本体内设置有电子元器件和转轴"
    limitation = _feature(
        "f-electronics",
        1,
        "耳机本体内具有PCBA板，PCBA板电性连接有喇叭和电池",
        subject="无线耳机",
    )
    comparison = InvalidityAnalysisEngine(
        FakeVisionClient(
            {
                "disclosures": [
                    {
                        "feature_id": "f-electronics",
                        "status": "direct_and_unambiguous",
                        "evidence_quote": quote,
                        "evidence_location": "权利要求1",
                        "reasoning": "电子元器件承担整体电子组件功能",
                        "confidence": 0.92,
                        "target_structural_role": "PCBA分别电连接喇叭和电池",
                        "reference_structure_mapping": "转轴和电子元器件（含电池）",
                        "mapping_basis": "role_and_relation",
                        "structural_evidence": ["电子元器件设置在转轴内"],
                    }
                ]
            }
        )
    ).compare_single_reference(
        claim_id="claim-1",
        expanded_claim_text="一种无线耳机，包括内部电子组件。",
        target_patent_text=_target_patent_text(),
        limitations=[limitation],
        document_id="D-GENERIC-STRUCTURAL-UMBRELLA",
        document_text=f"权利要求1 {quote}。电子元器件设置在转轴内。",
        target_images=["target.png"],
        document_images=["reference.png"],
    )

    disclosure = comparison.disclosures[0]
    assert disclosure.status is DisclosureStatus.UNCERTAIN
    assert disclosure.confidence == 0.69
    assert "多个核心构件及其关系" in disclosure.reasoning


def test_i4s_figure_location_cannot_validate_a_hallucinated_text_quote() -> None:
    target_only_quote = "所述耳机本体对应所述喇叭设置有出音孔"
    comparison = InvalidityAnalysisEngine(
        FakeVisionClient(
            {
                "disclosures": [
                    {
                        "feature_id": "f-sound-hole",
                        "status": "direct_and_unambiguous",
                        "evidence_quote": target_only_quote,
                        "evidence_location": "权利要求5，图3",
                        "reasoning": "喇叭对应出音孔",
                        "confidence": 0.9,
                        "target_structural_role": "对外声学通道",
                        "reference_structure_mapping": "喇叭和壳体开口",
                        "mapping_basis": "role_and_relation",
                    }
                ]
            }
        )
    ).compare_single_reference(
        claim_id="claim-1",
        expanded_claim_text="耳机本体对应喇叭设置有出音孔。",
        target_patent_text=_target_patent_text(),
        limitations=[
            _feature(
                "f-sound-hole",
                1,
                "耳机本体对应喇叭设置有出音孔",
                subject="无线耳机",
            )
        ],
        document_id="D-HALLUCINATED-FIGURE-QUOTE",
        document_text="权利要求5：喇叭安装在装配腔内。图3示出耳机结构。",
        target_images=["target.png"],
        document_images=["reference.png"],
    )

    disclosure = comparison.disclosures[0]
    assert disclosure.status is DisclosureStatus.UNCERTAIN
    assert "不能逐段回溯" in disclosure.reasoning or "引文无法" in disclosure.reasoning


def test_i4s_source_only_material_audit_corrects_target_contamination() -> None:
    invented_target_quote = "水性润湿剂为目标专利列举的四种聚氧乙烯醚"
    actual_source_quote = "该浆料由水、分散剂、水性乳胶和陶瓷颗粒组成"
    limitation = _feature(
        "f-wetting-agent",
        1,
        "水性润湿剂（0.1-5重量份）",
        subject="锂离子电池隔膜用水性陶瓷浆料",
    )
    client = FakeVisionClient(
        {
            "disclosures": [
                {
                    "feature_id": limitation.feature_id,
                    "status": "explicit",
                    "evidence_quote": invented_target_quote,
                    "evidence_location": "说明书[0010]",
                    "reasoning": "对比文件明确公开目标水性润湿剂",
                    "confidence": 0.95,
                    "mapping_basis": "literal",
                }
            ]
        },
        {
            "disclosures": [
                {
                    "feature_id": limitation.feature_id,
                    "status": "not_disclosed",
                    "evidence_quote": actual_source_quote,
                    "evidence_location": "权利要求1及全部实施例",
                    "reasoning": (
                        "已完整复核权利要求、全部组分清单、配方表和实施例；实际组分仅有"
                        "水、分散剂、水性乳胶和陶瓷颗粒，未公开润湿剂"
                    ),
                    "confidence": 0.94,
                    "target_structural_role": "浆料润湿组分",
                    "reference_structure_mapping": "无对应成分",
                    "mapping_basis": "none",
                    "structural_evidence": [actual_source_quote],
                    "integrated_structure_mapping": False,
                    "structural_search_summary": (
                        "完整组分复核：水、分散剂、水性乳胶和陶瓷颗粒；无润湿剂成分"
                    ),
                }
            ]
        },
    )
    comparison = InvalidityAnalysisEngine(client).compare_single_reference(
        claim_id="claim-1",
        expanded_claim_text="一种浆料，包括水性润湿剂。",
        target_patent_text=(
            _target_patent_text()
            + "。目标专利列举SECRET_TARGET_ONLY作为润湿剂。"
        ),
        limitations=[limitation],
        document_id="D-MATERIAL-SOURCE-AUDIT",
        document_text=(actual_source_quote + "。实施例配方复核。") * 40,
        target_images=["target.png"],
        document_images=["reference.png"],
    )

    disclosure = comparison.disclosures[0]
    assert disclosure.status is DisclosureStatus.NOT_DISCLOSED
    assert disclosure.evidence_quote == actual_source_quote
    assert comparison.analysis_pass_count == 1
    assert len(client.calls) == 2
    assert client.calls[1]["target_images"] == []
    assert client.calls[1]["document_images"] == []
    assert "来源侧配方审计员" in client.calls[1]["prompt"]
    assert "SECRET_TARGET_ONLY" not in client.calls[1]["prompt"]


def test_i4s_repeated_material_contamination_ends_with_clean_negative_reason() -> None:
    target_only_quote = "水性润湿剂为目标专利列举的四种聚氧乙烯醚"
    polluted = {
        "disclosures": [
            {
                "feature_id": "f-wetting-agent",
                "status": "explicit",
                "evidence_quote": target_only_quote,
                "evidence_location": "说明书[0010]",
                "reasoning": "对比文件明确公开目标水性润湿剂及其四种成员",
                "confidence": 0.92,
                "mapping_basis": "literal",
                "reference_structure_mapping": "目标专利四种聚氧乙烯醚",
            }
        ]
    }
    comparison = InvalidityAnalysisEngine(
        FakeVisionClient(
            polluted,
            polluted,
            # 空证据否定的守门A候选复核：维持来源侧配方审计的否定结论
            _guard_review_keeps_negative(
                "f-wetting-agent",
                reasoning_prefix="来源侧配方审计未获得任何可回溯的正向材料证据；",
            ),
        )
    ).compare_single_reference(
        claim_id="claim-1",
        expanded_claim_text="一种浆料，包括水性润湿剂。",
        target_patent_text=_target_patent_text() + target_only_quote,
        limitations=[
            _feature(
                "f-wetting-agent",
                1,
                "水性润湿剂（0.1-5重量份）",
                subject="水性陶瓷浆料",
            )
        ],
        document_id="D-REPEATED-MATERIAL-CONTAMINATION",
        document_text=("对比文件配方仅含水、分散剂、乳胶和陶瓷颗粒。") * 60,
        target_images=["target.png"],
        document_images=["reference.png"],
    )

    disclosure = comparison.disclosures[0]
    assert disclosure.status is DisclosureStatus.NOT_DISCLOSED
    assert disclosure.evidence_quote == ""
    assert disclosure.reference_structure_mapping == ""
    assert "明确公开" not in disclosure.reasoning
    assert "四种聚氧乙烯醚" not in disclosure.reasoning
    assert "来源侧配方审计未获得任何可回溯" in disclosure.reasoning


def test_i4s_material_quote_cannot_fuzzy_match_a_different_ingredient() -> None:
    polluted_quote = "水性润湿剂0.1-2%"
    source_quote = "水性分散剂0.1-2%"
    polluted = {
        "disclosures": [
            {
                "feature_id": "f-wetting-agent",
                "status": "not_disclosed",
                "evidence_quote": polluted_quote,
                "evidence_location": "权利要求1",
                "reasoning": "完整复核配方后未公开水性润湿剂",
                "confidence": 0.9,
                "mapping_basis": "none",
                "structural_search_summary": (
                    "全部组分仅有水性分散剂、水性乳胶和陶瓷颗粒，无润湿剂"
                ),
            }
        ]
    }
    comparison = InvalidityAnalysisEngine(
        FakeVisionClient(
            polluted,
            polluted,
            # 空证据否定的守门A候选复核：维持来源侧配方审计的否定结论
            _guard_review_keeps_negative(
                "f-wetting-agent",
                reasoning_prefix="模型主引文无法回溯到本对比文件，已从证据栏移除；",
            ),
        )
    ).compare_single_reference(
        claim_id="claim-1",
        expanded_claim_text="一种浆料，包括水性润湿剂。",
        target_patent_text=_target_patent_text() + polluted_quote,
        limitations=[
            _feature(
                "f-wetting-agent",
                1,
                "水性润湿剂（0.1-5重量份）",
                subject="水性陶瓷浆料",
            )
        ],
        document_id="D-SIMILAR-NUMERIC-MATERIAL-QUOTE",
        document_text=(source_quote + "；水性乳胶1-5%；陶瓷颗粒80-99%。") * 50,
        target_images=["target.png"],
        document_images=["reference.png"],
    )

    disclosure = comparison.disclosures[0]
    assert disclosure.status is DisclosureStatus.NOT_DISCLOSED
    assert disclosure.evidence_quote == ""
    assert polluted_quote not in disclosure.reasoning
    assert "模型主引文无法回溯到本对比文件" in disclosure.reasoning


def test_material_full_source_negative_survives_cropped_uncertain_review() -> None:
    initial = FeatureDisclosure(
        feature_id="f-wetting-agent",
        feature_text="水性润湿剂（0.1-5重量份）",
        status=DisclosureStatus.NOT_DISCLOSED,
        evidence_quote="配方包括水、分散剂和乳胶",
        evidence_location="权利要求1及实施例",
        reasoning="完整配方复核未公开润湿剂",
        confidence=0.9,
        mapping_basis="none",
        structural_search_summary="全部组分为水、分散剂和乳胶，无润湿剂",
    )
    reviewed = initial.model_copy(
        update={
            "status": DisclosureStatus.UNCERTAIN,
            "reasoning": "裁剪窗口内无法再次确认",
            "confidence": 0.6,
        }
    )

    merged = _merge_structural_review(initial=[initial], reviewed=[reviewed])
    assert merged == [initial]


@pytest.mark.parametrize("mapping_basis", ["structural_equivalent", "literal"])
def test_i4s_distributed_traceable_evidence_supports_a_compound_mapping(
    mapping_basis: str,
) -> None:
    evidence = [
        "电路板5用于与设置在耳机本体内部的电子元器件电连接",
        "电子元器件包括电池4，电池4胶封在转轴3内",
        "电子元器件包括喇叭6，喇叭6安装在装配腔12内",
    ]
    non_verbatim_concatenation = "；".join((evidence[0], evidence[2], evidence[1]))
    limitation = _feature(
        "f-electronics",
        1,
        "耳机本体内具有PCBA板，PCBA板电性连接有喇叭和电池",
        subject="无线耳机",
    )
    comparison = InvalidityAnalysisEngine(
        FakeVisionClient(
            {
                "disclosures": [
                    {
                        "feature_id": "f-electronics",
                        "status": "direct_and_unambiguous",
                        "evidence_quote": non_verbatim_concatenation,
                        "evidence_location": "说明书[0024][0029][0030]",
                        "reasoning": "电路板分别电连接电池和喇叭",
                        "confidence": 0.92,
                        "target_structural_role": "PCBA分别电连接喇叭和电池",
                        "reference_structure_mapping": "电路板5、电池4、喇叭6",
                        "mapping_basis": mapping_basis,
                        "structural_evidence": evidence,
                    }
                ]
            }
        )
    ).compare_single_reference(
        claim_id="claim-1",
        expanded_claim_text="一种无线耳机，包括内部电子组件。",
        target_patent_text=_target_patent_text(),
        limitations=[limitation],
        document_id="D-DISTRIBUTED-COMPOUND",
        document_text=(
            evidence[0]
            + "。"
            + ("其他实施例说明" * 80)
            + "。"
            + evidence[1]
            + "。"
            + ("附图结构说明" * 80)
            + "。"
            + evidence[2]
        ),
        target_images=["target.png"],
        document_images=["reference.png"],
    )

    disclosure = comparison.disclosures[0]
    assert disclosure.status is DisclosureStatus.DIRECT_AND_UNAMBIGUOUS
    assert disclosure.evidence_quote == evidence[0]
    assert disclosure.structural_evidence == evidence
    assert "主引文已改用" in disclosure.reasoning


def test_i4s_repairs_cautious_distributed_electrical_assembly_mapping() -> None:
    evidence = [
        "电路板5用于与设置在耳机本体内部的电子元器件电连接。",
        "电子元器件包括电池4，电池4胶封在转轴3内。",
        "电子元器件包括喇叭6，喇叭6安装在装配腔12内。",
    ]
    limitation = _feature(
        "f-electronics",
        1,
        "耳机本体内具有PCBA板，PCBA板电性连接有喇叭和电池",
        subject="无线耳机",
    )
    comparison = InvalidityAnalysisEngine(
        FakeVisionClient(
            {
                "disclosures": [
                    {
                        "feature_id": "f-electronics",
                        "status": "uncertain",
                        "evidence_quote": "耳机本体内设置有电子元器件和转轴",
                        "evidence_location": "权利要求1",
                        "reasoning": "只在同一句中看到电子元器件，连接链需复核",
                        "confidence": 0.6,
                        "target_structural_role": "PCBA分别电连接喇叭和电池",
                        "reference_structure_mapping": "电路板和电子元器件",
                        "mapping_basis": "role_and_relation",
                    }
                ]
            }
        )
    ).compare_single_reference(
        claim_id="claim-1",
        expanded_claim_text="一种无线耳机，包括内部电子组件。",
        target_patent_text=_target_patent_text(),
        limitations=[limitation],
        document_id="D-DISTRIBUTED-ELECTRICAL-CHAIN",
        document_text="\n".join(evidence),
        target_images=["target.png"],
        document_images=["reference.png"],
    )

    disclosure = comparison.disclosures[0]
    assert disclosure.status is DisclosureStatus.DIRECT_AND_UNAMBIGUOUS
    assert disclosure.mapping_basis == "role_and_relation"
    assert disclosure.integrated_structure_mapping is True
    assert set(disclosure.structural_evidence) == set(evidence)
    assert "完整连接链" in disclosure.reasoning


def test_i4s_distributed_electrical_bridge_rejects_an_umbrella_only_source() -> None:
    limitation = _feature(
        "f-electronics",
        1,
        "耳机本体内具有PCBA板，PCBA板电性连接有喇叭和电池",
        subject="无线耳机",
    )
    comparison = InvalidityAnalysisEngine(
        FakeVisionClient(
            {
                "disclosures": [
                    {
                        "feature_id": "f-electronics",
                        "status": "uncertain",
                        "evidence_quote": "电路板与电子元器件电连接",
                        "evidence_location": "说明书",
                        "reasoning": "未分别找到喇叭和电池",
                        "confidence": 0.6,
                        "target_structural_role": "PCBA分别电连接喇叭和电池",
                        "reference_structure_mapping": "电路板和电子元器件",
                        "mapping_basis": "role_and_relation",
                    }
                ]
            }
        )
    ).compare_single_reference(
        claim_id="claim-1",
        expanded_claim_text="一种无线耳机，包括内部电子组件。",
        target_patent_text=_target_patent_text(),
        limitations=[limitation],
        document_id="D-ELECTRICAL-UMBRELLA-ONLY",
        document_text="电路板与电子元器件电连接。",
        target_images=["target.png"],
        document_images=["reference.png"],
    )

    assert comparison.disclosures[0].status is DisclosureStatus.UNCERTAIN


def test_i4s_removes_citation_wrapper_from_traceable_structural_evidence() -> None:
    source_quote = "所述水性润湿剂为烷基酚聚氧乙烯醚中的一种"
    wrapped_quote = f'CN103633269A说明书[0010]："{source_quote}"'
    comparison = InvalidityAnalysisEngine(
        FakeVisionClient(
            {
                "disclosures": [
                    {
                        "feature_id": "f-wetting-agent",
                        "status": "explicit",
                        "evidence_quote": wrapped_quote,
                        "evidence_location": "说明书[0010]",
                        "reasoning": "直接公开同一水性润湿剂",
                        "confidence": 0.9,
                        "mapping_basis": "literal",
                        "structural_evidence": [wrapped_quote],
                    }
                ]
            }
        )
    ).compare_single_reference(
        claim_id="claim-1",
        expanded_claim_text="一种浆料，包括水性润湿剂。",
        target_patent_text=_target_patent_text(),
        limitations=[_feature("f-wetting-agent", 1, "水性润湿剂")],
        document_id="D-CITATION-WRAPPER",
        document_text=f"说明书[0010]记载：{source_quote}。",
        target_images=["target.png"],
        document_images=["reference.png"],
    )

    disclosure = comparison.disclosures[0]
    assert disclosure.status is DisclosureStatus.EXPLICIT
    assert disclosure.evidence_quote == source_quote
    assert disclosure.structural_evidence == [source_quote]


def test_i4s_discards_one_bad_supplement_without_poisoning_material_mapping() -> None:
    material_quote = (
        "所述水性乳胶为聚甲基丙烯酸甲酯、聚醋酸乙烯酯中的一种或几种组合物"
    )
    invalid_extra = "CN999说明书[0017]：目标专利中的另一段乳胶说明"
    amount_quote = "水性乳胶0.1-5%"
    limitation = _feature(
        "f-latex",
        1,
        "水性乳胶（1-10份重量份额）",
        subject="水性陶瓷浆料",
    )
    comparison = InvalidityAnalysisEngine(
        FakeVisionClient(
            {
                "disclosures": [
                    {
                        "feature_id": limitation.feature_id,
                        "status": "explicit",
                        "evidence_quote": material_quote,
                        "evidence_location": "权利要求3",
                        "reasoning": "乳胶类别相同且用量范围存在交集",
                        "confidence": 0.92,
                        "target_structural_role": "水性乳胶及其用量",
                        "reference_structure_mapping": "聚甲基丙烯酸甲酯乳胶",
                        "mapping_basis": "role_and_relation",
                        "structural_evidence": [material_quote, invalid_extra],
                        "structural_search_summary": "乳胶0.1-5%，与1-10份存在交集",
                    }
                ]
            }
        )
    ).compare_single_reference(
        claim_id="claim-1",
        expanded_claim_text="一种浆料，包括水性乳胶。",
        target_patent_text=_target_patent_text(),
        limitations=[limitation],
        document_id="D-MATERIAL-EXTRA-EVIDENCE",
        document_text=(material_quote + "。" + amount_quote + "。") * 30,
        target_images=["target.png"],
        document_images=["reference.png"],
    )

    disclosure = comparison.disclosures[0]
    assert disclosure.status is DisclosureStatus.EXPLICIT
    assert disclosure.structural_evidence == [material_quote]
    assert "无法回溯的补充引文已剔除" in disclosure.reasoning


def test_i4s_corrects_a_structural_mapping_mislabeled_as_literal() -> None:
    quote = (
        "电子控制组件具有控制电路并由电池提供电能，驱动马达由电子控制组件控制作动"
    )
    limitation = _feature(
        "f-control-chain",
        1,
        "动力部件电连接控制装置，控制装置电连接电源并驱动动力部件",
        subject="燃气灶调节装置",
    )
    comparison = InvalidityAnalysisEngine(
        FakeVisionClient(
            {
                "disclosures": [
                    {
                        "feature_id": limitation.feature_id,
                        "status": "direct_and_unambiguous",
                        "evidence_quote": quote,
                        "evidence_location": "说明书[0020]",
                        "reasoning": "控制电路、电池和驱动马达分别承担对应角色且连接关系一致",
                        "confidence": 0.91,
                        "target_structural_role": "控制装置连接电源并控制动力部件",
                        "reference_structure_mapping": "控制电路连接电池并控制驱动马达",
                        "mapping_basis": "literal",
                        "structural_evidence": [quote],
                    }
                ]
            }
        )
    ).compare_single_reference(
        claim_id="claim-1",
        expanded_claim_text=limitation.text,
        target_patent_text=_target_patent_text(),
        limitations=[limitation],
        document_id="D-CONTROL-CHAIN",
        document_text=f"说明书[0020]：{quote}。",
        target_images=["target.png"],
        document_images=["reference.png"],
    )

    disclosure = comparison.disclosures[0]
    assert disclosure.status is DisclosureStatus.DIRECT_AND_UNAMBIGUOUS
    assert disclosure.mapping_basis == "role_and_relation"
    assert "依据标签已按理由" in disclosure.reasoning


def test_i4s_exact_single_component_literal_does_not_require_model_role_prose() -> None:
    quote = "壳体内设置有电池"
    limitation = _feature("f-battery", 1, "电池", subject="无线耳机")
    comparison = InvalidityAnalysisEngine(
        FakeVisionClient(
            {
                "disclosures": [
                    {
                        "feature_id": "f-battery",
                        "status": "explicit",
                        "evidence_quote": quote,
                        "evidence_location": "权利要求2",
                        "reasoning": "直接公开电池",
                        "confidence": 0.9,
                        "mapping_basis": "literal",
                    }
                ]
            }
        )
    ).compare_single_reference(
        claim_id="claim-1",
        expanded_claim_text="一种无线耳机，包括电池。",
        target_patent_text=_target_patent_text(),
        limitations=[limitation],
        document_id="D-EXACT-BATTERY",
        document_text=f"权利要求2 {quote}",
        target_images=["target.png"],
        document_images=["reference.png"],
    )

    disclosure = comparison.disclosures[0]
    assert disclosure.status is DisclosureStatus.EXPLICIT
    assert disclosure.confidence == 0.9


def test_i4s_prompts_explain_inner_shaft_outer_ring_rotational_equivalence() -> None:
    payload = {
        "expanded_claim_text": "耳挂通过套圈可旋转地套设于圆形凸台外侧",
        "limitations": [
            {
                "feature_id": "f-joint",
                "text": "套圈套设于圆形凸台外侧并可相对旋转",
            }
        ],
        "target_mechanism_summary": "圆形凸台提供轴线，套圈围绕凸台转动",
        "reference_mechanism_summary": "shaft 穿过 ring body 形成旋转连接",
    }

    first_prompt = InvalidityAnalysisEngine._single_reference_prompt(**payload)
    review_prompt = InvalidityAnalysisEngine._structural_reconciliation_prompt(
        **payload
    )

    assert "提供旋转轴线并被环形件包围" in first_prompt
    assert "固定/运动观察基准" in first_prompt
    assert "shaft/pivot/boss" in review_prompt
    assert "ring/sleeve/collar" in review_prompt
    assert "具体承载构件、数量、方向和间距" in review_prompt
    assert "上位类别词、笼统集合词" in first_prompt


def test_i4s_acoustic_prompt_does_not_infer_an_outlet_from_a_speaker_alone() -> None:
    payload = {
        "expanded_claim_text": "耳机本体对应喇叭设置有出音孔",
        "limitations": [
            {
                "feature_id": "f-sound-hole",
                "text": "耳机本体对应喇叭设置有出音孔",
            }
        ],
        "target_mechanism_summary": "喇叭通过出音孔向外输出声音",
        "reference_mechanism_summary": "耳机本体内安装扬声器",
    }

    first_prompt = InvalidityAnalysisEngine._single_reference_prompt(**payload)
    review_prompt = InvalidityAnalysisEngine._structural_reconciliation_prompt(
        **payload
    )

    assert "喇叭本身只证明发声构件" in first_prompt
    assert "喇叭不能替代壳体开口/声学通道" in review_prompt


def test_i4s_untraceable_structural_evidence_cannot_support_equivalence() -> None:
    quote = "member 4 slides along guide 5"
    comparison = InvalidityAnalysisEngine(
        FakeVisionClient(
            {
                "disclosures": [
                    {
                        "feature_id": "f-slider",
                        "status": "direct_and_unambiguous",
                        "evidence_quote": quote,
                        "evidence_location": "paragraph 7",
                        "reasoning": "member 4 在 guide 5 中滑动并承担导向角色",
                        "confidence": 0.9,
                        "target_structural_role": "沿导向件滑动",
                        "reference_structure_mapping": "member 4 and guide 5",
                        "mapping_basis": "structural_equivalent",
                        "structural_evidence": [
                            quote,
                            "invented passage connecting member 4 to actuator 9",
                        ],
                    }
                ]
            }
        )
    ).compare_single_reference(
        claim_id="claim-1",
        expanded_claim_text="一种装置，包括沿导向件滑动的构件。",
        target_patent_text=_target_patent_text(),
        limitations=[_feature("f-slider", 1, "沿导向件滑动的构件")],
        document_id="D-UNTRACEABLE-STRUCTURE",
        document_text=f"paragraph 7 {quote}",
        target_images=["target.png"],
        document_images=["reference.png"],
    )

    assert comparison.disclosures[0].status is DisclosureStatus.UNCERTAIN
    assert "不能逐段回溯" in comparison.disclosures[0].reasoning


def test_structural_review_trigger_is_generic_but_not_a_topology_override() -> None:
    rotation_candidate = FeatureDisclosure(
        feature_id="f-rotor",
        status=DisclosureStatus.NOT_DISCLOSED,
        reasoning="轴套会旋转并传递扭矩，但不是名为转子的独立构件",
        confidence=0.69,
        mapping_basis="none",
        structural_search_summary="检查轴套、输出轴和扭矩传递关系",
    )
    other_open = FeatureDisclosure(
        feature_id="f-bearing",
        status=DisclosureStatus.NOT_DISCLOSED,
        reasoning="未披露轴承位置关系",
        confidence=0.9,
        mapping_basis="none",
        structural_search_summary="检查所有轴承和壳体",
    )
    assert _needs_structural_reconciliation([rotation_candidate, other_open])

    real_topology_mismatch = rotation_candidate.model_copy(
        update={
            "feature_id": "f-closed-circuit",
            "reasoning": "导线彼此连接，但没有形成闭合回路，因此未披露闭合供电路径",
            "structural_search_summary": "检查导线端点和供电路径，回路保持开路",
        }
    )
    assert not _needs_structural_reconciliation(
        [real_topology_mismatch, other_open]
    )


def test_structural_review_scope_is_bounded_to_promising_open_features() -> None:
    limitations = [
        _feature(f"f-{index}", index, f"普通构件 {index}", subject="机械装置")
        for index in range(1, 11)
    ]
    disclosures = [
        FeatureDisclosure(
            feature_id=item.feature_id,
            status=DisclosureStatus.UNCERTAIN,
            reasoning=(
                "候选套筒会旋转并传递扭矩，但不是独立转子构件"
                if item.sequence == 10
                else "候选结构尚待核对"
            ),
            confidence=0.5,
            mapping_basis=(
                "structural_equivalent" if item.sequence == 10 else "uncertain"
            ),
        )
        for item in limitations
    ]

    selected = _select_structural_review_limitations(
        limitations,
        disclosures,
        max_features=8,
    )

    assert len(selected) == 8
    assert limitations[-1] in selected


def test_structural_review_excerpt_keeps_exact_middle_evidence() -> None:
    quote = "the integrated valve member moves away from seat 17 to permit flow"
    document_text = ("H" * 30_000) + quote + ("T" * 30_000)
    disclosure = FeatureDisclosure(
        feature_id="f-valve",
        status=DisclosureStatus.DIRECT_AND_UNAMBIGUOUS,
        evidence_quote=quote,
        evidence_location="paragraph 31",
        reasoning="集成阀件相对阀座移动并开启流路",
        confidence=0.9,
        mapping_basis="structural_equivalent",
        target_structural_role="相对阀座移动并控制通断",
        reference_structure_mapping="integrated valve member and seat 17",
    )

    excerpt = _structural_review_excerpt(
        document_text,
        [disclosure],
        max_chars=16_000,
    )

    assert quote in excerpt
    assert len(excerpt) < len(document_text)


def test_single_reference_evidence_window_keeps_claim_anchor_and_bounds_ocr() -> None:
    middle = "连接套筒能够沿传动轴滑动并传递扭矩"
    document = "首页与权利要求" + ("甲" * 24_000) + middle + ("乙" * 24_000) + "说明书结尾"
    limitation = _feature("f-sleeve", 1, middle, subject="燃气灶调节装置")

    excerpt = _single_reference_evidence_window(
        document,
        [limitation],
        max_chars=36_000,
    )

    assert len(excerpt) <= 36_000
    assert excerpt.startswith("首页与权利要求")
    assert middle in excerpt
    assert excerpt.endswith("说明书结尾")
    assert len(excerpt) < len(document)


def test_evidence_traceability_tolerates_local_ocr_damage_with_numbered_parts() -> None:
    assert _text_evidence_traceable(
        "阀芯（6）封紧在阀座（7）上",
        "弹簧 10 顶住，使 TUS 6 封紧在闪座 7 上。",
    )
    assert _text_evidence_traceable(
        "压柱11推动阀芯6脱离阀座7，水流入出水管8",
        "压柱 11 TAY Hs，使 PRS 6 脱离立论 7，水进入疾座流入出水管 8。",
    )
    assert not _text_evidence_traceable(
        "压柱11推动阀芯6脱离阀座7，水流入出水管8",
        "电机 11 驱动齿轮 6，输出轴 7 带动叶片 8 旋转。",
    )


def test_i4s_cannot_deny_disclosure_by_inventing_independent_part_requirement() -> None:
    limitations = [_feature("f-moving-valve", 1, "可移动阀杆", subject="流体阀")]
    first_pass = {
        "disclosures": [
                    {
                        "feature_id": "f-moving-valve",
                        "status": "not_disclosed",
                        "evidence_quote": "",
                        "evidence_location": "全文及附图复核",
                        "reasoning": (
                            "阀芯通过与其固定连接的压柱轴向移动并相对阀座开闭，"
                            "但阀芯不是独立阀杆，因此未披露可移动阀杆"
                        ),
                        "confidence": 0.92,
                        "mapping_basis": "none",
                        "structural_search_summary": (
                            "检查阀芯、压柱、阀座和内部流道；阀芯与压柱共同运动"
                        ),
                    }
                ]
            }
    quote = "阀芯通过与其固定连接的压柱轴向移动并相对阀座开闭"
    repair = {
        "disclosures": [
            {
                "feature_id": "f-moving-valve",
                "status": "direct_and_unambiguous",
                "evidence_quote": quote,
                "evidence_location": "说明书全文及附图",
                "reasoning": "阀芯与压柱共同构成相对阀座移动的集成阀杆",
                "confidence": 0.9,
                "target_structural_role": "相对阀座移动并控制开闭",
                "reference_structure_mapping": "固定连接并共同运动的阀芯与压柱",
                "mapping_basis": "structural_equivalent",
                "structural_evidence": [quote],
                "integrated_structure_mapping": True,
            }
        ]
    }
    comparison = InvalidityAnalysisEngine(
        FakeVisionClient(first_pass, repair)
    ).compare_single_reference(
        claim_id="claim-1",
        expanded_claim_text="一种流体阀，包括可移动阀杆。",
        target_patent_text=_target_patent_text(),
        limitations=limitations,
        document_id="D-INTEGRATED-MEMBER",
        document_text="阀芯通过与其固定连接的压柱轴向移动并相对阀座开闭。",
        target_images=["target.png"],
        document_images=["valve.png"],
    )

    disclosure = comparison.disclosures[0]
    assert disclosure.status is DisclosureStatus.DIRECT_AND_UNAMBIGUOUS
    assert disclosure.integrated_structure_mapping is True


def test_i4s_necessarily_implicit_without_closed_chain_stays_uncertain() -> None:
    limitations = [
        _feature("f-pressure-role", 1, "受压储液结构", subject="液体喷射装置")
    ]
    document_text = (
        "The chamber supplies liquid to the nozzle after the trigger is pressed."
    )
    comparison = InvalidityAnalysisEngine(
        FakeVisionClient(
            {
                "disclosures": [
                    {
                        "feature_id": "f-pressure-role",
                        "status": "necessarily_implicit",
                        "evidence_quote": document_text,
                        "evidence_location": "paragraph 9",
                        "reasoning": "运行时可能需要压差，但文献没有闭合说明压力来源",
                        "confidence": 0.88,
                        "target_structural_role": "储液并依靠内压向喷嘴供液",
                        "reference_structure_mapping": "向喷嘴供液的 chamber",
                        "mapping_basis": "necessarily_implicit_from_operation",
                        "structural_evidence": [document_text],
                        "necessity_chain": ["chamber 向喷嘴供液"],
                        "reasonable_alternatives_excluded": False,
                        "alternative_path_analysis": (
                            "全文没有排除重力供液或外部泵供液"
                        ),
                    }
                ]
            }
        )
    ).compare_single_reference(
        claim_id="claim-1",
        expanded_claim_text="一种液体喷射装置，包括受压储液结构。",
        target_patent_text=_target_patent_text(),
        limitations=limitations,
        document_id="D-AMBIGUOUS-PRESSURE",
        document_text=document_text,
        target_images=["target.png"],
        document_images=["chamber.png"],
    )

    disclosure = comparison.disclosures[0]
    assert disclosure.status is DisclosureStatus.UNCERTAIN
    assert disclosure.confidence == 0.69
    assert "必然性推导链" in disclosure.reasoning
    assert not single_reference_fully_discloses(
        comparison, limitations, eligible_for_novelty=True
    )


def test_i4s_accepts_necessarily_implicit_only_with_three_step_chain_and_no_alternative() -> None:
    limitations = [
        _feature("f-moving-member", 1, "在流道内移动并控制通断的阀杆", subject="流体阀")
    ]
    document_text = (
        "Paragraph 11: a poppet is guided inside the valve passage and is driven "
        "away from the seat to open the only outlet path; the return spring drives "
        "the poppet against the seat to close that path."
    )
    comparison = InvalidityAnalysisEngine(
        FakeVisionClient(
            {
                "disclosures": [
                    {
                        "feature_id": "f-moving-member",
                        "status": "necessarily_implicit",
                        "evidence_quote": (
                            "a poppet is guided inside the valve passage and is "
                            "driven away from the seat"
                        ),
                        "evidence_location": "paragraph 11",
                        "reasoning": "唯一流路由同一 poppet 相对阀座的往复运动开闭",
                        "confidence": 0.88,
                        "target_structural_role": "在阀流道内移动并相对阀座开闭流路",
                        "reference_structure_mapping": (
                            "阀流道内受导向的 poppet 在阀座开闭位置间往复"
                        ),
                        "mapping_basis": "necessarily_implicit_from_operation",
                        "structural_evidence": [
                            "guided inside the valve passage",
                            "driven away from the seat",
                            "return spring drives the poppet against the seat",
                        ],
                        "necessity_chain": [
                            "全文明确 poppet 位于并受导向于唯一阀流道内",
                            "开启和关闭要求该构件相对阀座发生位置变化",
                            "因此同一构件必然是在流道内移动并控制通断的阀杆角色",
                        ],
                        "reasonable_alternatives_excluded": True,
                        "alternative_path_analysis": (
                            "全文限定唯一出口流路及同一 poppet 的开闭动作，排除了固定件、"
                            "另一流道或仅靠外部泵切换的替代路径"
                        ),
                    }
                ]
            }
        )
    ).compare_single_reference(
        claim_id="claim-1",
        expanded_claim_text="一种流体阀，包括在流道内移动并控制通断的阀杆。",
        target_patent_text=_target_patent_text(),
        limitations=limitations,
        document_id="D-NECESSARY-VALVE",
        document_text=document_text,
        target_images=["target.png"],
        document_images=["valve.png"],
    )

    disclosure = comparison.disclosures[0]
    assert disclosure.status is DisclosureStatus.NECESSARILY_IMPLICIT
    assert len(disclosure.necessity_chain) == 3
    assert disclosure.reasonable_alternatives_excluded is True
    assert single_reference_fully_discloses(
        comparison, limitations, eligible_for_novelty=True
    )


def test_i4s_positive_structural_mapping_is_not_changed_to_not_disclosed_by_absent_term() -> None:
    limitations = [
        _feature("f-pressure-tank", 1, "压力罐", subject="液体喷射装置")
    ]
    comparison = InvalidityAnalysisEngine(
        FakeVisionClient(
            {
                "disclosures": [
                    {
                        "feature_id": "f-pressure-tank",
                        "status": "not_disclosed",
                        "evidence_quote": "the sealed reservoir is pressurized",
                        "evidence_location": "paragraph 18",
                        "reasoning": (
                            "全文没有出现‘压力罐’这一目标名称，但 sealed reservoir "
                            "承担受压储液和排液的相同结构角色"
                        ),
                        "confidence": 0.87,
                        "target_structural_role": "受压储液并向喷射流路供液",
                        "reference_structure_mapping": (
                            "sealed reservoir 被加压并向排液流路供液"
                        ),
                        "mapping_basis": "structural_equivalent",
                        "structural_evidence": [
                            "the sealed reservoir is pressurized"
                        ],
                        "structural_search_summary": (
                            "已定位 sealed reservoir 及其受压排液路径"
                        ),
                    }
                ]
            }
        )
    ).compare_single_reference(
        claim_id="claim-1",
        expanded_claim_text="一种水枪，包括压力罐。",
        target_patent_text=_target_patent_text(),
        limitations=limitations,
        document_id="D-POSITIVE-MAPPING",
        document_text="paragraph 18 the sealed reservoir is pressurized",
        target_images=["target.png"],
        document_images=["reservoir.png"],
    )

    disclosure = comparison.disclosures[0]
    assert disclosure.status is DisclosureStatus.UNCERTAIN
    assert disclosure.status is not DisclosureStatus.NOT_DISCLOSED
    assert disclosure.mapping_basis == "structural_equivalent"
    assert "正向结构映射" in disclosure.reasoning


def test_i4s_batches_long_claim_without_dropping_trailing_features() -> None:
    limitations = [
        _feature(f"f-{index}", index, f"结构特征{index}")
        for index in range(1, 9)
    ]
    document_text = "；".join(f"证据片段{index}" for index in range(1, 9))

    def response(start: int, end: int) -> dict[str, Any]:
        return {
            "disclosures": [
                {
                    "feature_id": f"f-{index}",
                    "status": "explicit",
                    "evidence_quote": f"证据片段{index}",
                    "evidence_location": f"说明书第{index}段",
                    "reasoning": "对比文件直接公开相应结构",
                    "confidence": 0.9,
                    "mapping_basis": "literal",
                }
                for index in range(start, end)
            ],
            "field_alignment": 0.8,
            "purpose_alignment": 0.8,
            "effect_alignment": 0.8,
        }

    # Both responses include the same superset so the assertion is independent
    # of thread scheduling; normalisation keeps only each batch's feature IDs.
    client = FakeVisionClient(response(1, 9), response(1, 9))
    comparison = InvalidityAnalysisEngine(client).compare_single_reference(
        claim_id="claim-1",
        expanded_claim_text="一种装置，包括八项结构特征。",
        target_patent_text=_target_patent_text(),
        limitations=limitations,
        document_id="D-LONG",
        document_text=document_text,
        target_images=["target.png"],
        document_images=["reference-1.png", "reference-2.png"],
    )

    assert len(client.calls) == 2
    assert all(call["max_tokens"] == 4096 for call in client.calls)
    assert [item.feature_id for item in comparison.disclosures] == [
        f"f-{index}" for index in range(1, 9)
    ]
    assert all(
        item.status is not DisclosureStatus.ANALYSIS_FAILED
        for item in comparison.disclosures
    )


def test_i4s_repairs_only_the_incomplete_batch() -> None:
    limitations = [
        _feature(f"f-{index}", index, f"结构特征{index}")
        for index in range(1, 6)
    ]
    document_text = "；".join(f"证据片段{index}" for index in range(1, 6))
    complete = {
        "disclosures": [
            {
                "feature_id": f"f-{index}",
                "status": "explicit",
                "evidence_quote": f"证据片段{index}",
                "evidence_location": f"说明书第{index}段",
                "reasoning": "对比文件直接公开相应结构",
                "confidence": 0.9,
                "mapping_basis": "literal",
            }
            for index in range(1, 6)
        ],
        "field_alignment": 0.8,
        "purpose_alignment": 0.8,
        "effect_alignment": 0.8,
    }
    incomplete = {
        "disclosures": [],
        "field_alignment": 0.0,
        "purpose_alignment": 0.0,
        "effect_alignment": 0.0,
    }
    client = FakeVisionClient(incomplete, complete, complete)

    comparison = InvalidityAnalysisEngine(client).compare_single_reference(
        claim_id="claim-1",
        expanded_claim_text="一种装置，包括五项结构特征。",
        target_patent_text=_target_patent_text(),
        limitations=limitations,
        document_id="D-REPAIR",
        document_text=document_text,
        target_images=["target.png"],
        document_images=["reference-1.png", "reference-2.png"],
    )

    assert len(client.calls) == 3
    assert all(
        item.status is not DisclosureStatus.ANALYSIS_FAILED
        for item in comparison.disclosures
    )


def test_i4s_retries_1210_once_with_two_frozen_document_pages() -> None:
    limitations = [_feature("f-pressure-tank", 1, "压力罐")]
    client = RejectLargeImageSetOnceClient(
        {
            "disclosures": [
                {
                    "feature_id": "f-pressure-tank",
                    "status": "not_disclosed",
                    "evidence_quote": "",
                    "evidence_location": "全文及附图复核",
                    "reasoning": "已核对储液和排液机构，未披露受压储液结构",
                    "confidence": 0.8,
                    "mapping_basis": "none",
                    "structural_search_summary": (
                        "检查全部储液空间及其供液路径，没有结构承担受压储液角色"
                    ),
                }
            ],
            "field_alignment": 0.5,
            "purpose_alignment": 0.5,
            "effect_alignment": 0.5,
        },
        # 空证据否定的守门A候选复核：维持合规否定
        _guard_review_keeps_negative("f-pressure-tank"),
    )
    comparison = InvalidityAnalysisEngine(client).compare_single_reference(
        claim_id="claim-1",
        expanded_claim_text="一种水枪，包括压力罐。",
        target_patent_text=_target_patent_text(),
        limitations=limitations,
        document_id="D1",
        document_text="对比文件全文",
        target_images=["target-1.png", "target-2.png"],
        document_images=["page-1.png", "page-2.png", "page-3.png"],
    )

    assert len(client.calls) == 3
    assert client.calls[0]["document_images"] == [
        "page-1.png",
        "page-2.png",
        "page-3.png",
    ]
    assert client.calls[1]["document_images"] == [
        "page-1.png",
        "page-2.png",
    ]
    assert comparison.used_document_images == 2


def test_i4s_keeps_genuinely_incomplete_non_disclosure_review_uncertain() -> None:
    limitations = [_feature("f-pressure-tank", 1, "压力罐")]
    comparison = InvalidityAnalysisEngine(
        FakeVisionClient(
            {
                "disclosures": [
                    {
                        "feature_id": "f-pressure-tank",
                        "status": "uncertain",
                        "evidence_quote": "",
                        "evidence_location": "",
                        "reasoning": "由于全文不完整，无法确认是否未披露压力罐",
                        "confidence": 0.2,
                    }
                ]
            }
        )
    ).compare_single_reference(
        claim_id="claim-1",
        expanded_claim_text="一种水枪，包括压力罐。",
        target_patent_text=_target_patent_text(),
        limitations=limitations,
        document_id="D1",
        document_text="不完整文本",
        target_images=["target.png"],
        document_images=["d1.png"],
    )

    assert comparison.disclosures[0].status is DisclosureStatus.UNCERTAIN
    assert comparison.disclosures[0].confidence == 0.2
    assert comparison.evidence_completeness == 0.0


def test_i4s_material_composition_difference_can_confirm_non_disclosure() -> None:
    comparison = InvalidityAnalysisEngine(
        FakeVisionClient(
            {
                "disclosures": [
                    {
                        "feature_id": "f-thickener",
                        "status": "not_disclosed",
                        "evidence_quote": "水溶性纤维素类增稠剂",
                        "evidence_location": "权利要求1",
                        "reasoning": (
                            "文献未公开碱溶性高分子乳液；候选物是水溶性纤维素，"
                            "化学形态和组成不同，不能承担该材料限定"
                        ),
                        "confidence": 0.91,
                        "mapping_basis": "none",
                        "structural_search_summary": (
                            "已复核全部增稠剂成分、溶解性和乳液相态；仅找到水溶性"
                            "纤维素，未包含碱溶性高分子乳液"
                        ),
                    }
                ]
            }
        )
    ).compare_single_reference(
        claim_id="claim-1",
        expanded_claim_text="一种浆料，包括碱溶性高分子乳液增稠剂。",
        target_patent_text=_target_patent_text(),
        limitations=[_feature("f-thickener", 1, "碱溶性高分子乳液增稠剂")],
        document_id="D-MATERIAL",
        document_text="权利要求1记载水溶性纤维素类增稠剂。",
        target_images=["target.png"],
        document_images=["reference.png"],
    )

    assert comparison.disclosures[0].status is DisclosureStatus.NOT_DISCLOSED
    assert comparison.disclosures[0].confidence == 0.91


def test_i4s_candidate_with_substantive_topology_difference_is_not_uncertain() -> None:
    comparison = InvalidityAnalysisEngine(
        FakeVisionClient(
            {
                "disclosures": [
                    {
                        "feature_id": "f-slot",
                        "status": "not_disclosed",
                        "reasoning": (
                            "文献虽有卡槽，但卡槽位于固定壳体并仅供轴向装配，"
                            "目标卡槽位于旋转套圈且沿周向间距设置；承载构件、方向和运动关系不同"
                        ),
                        "confidence": 0.9,
                        "mapping_basis": "none",
                        "structural_search_summary": (
                            "已检查环形件、轴形件及全部卡槽；候选槽的连接拓扑和运动方向不同"
                        ),
                    }
                ]
            },
            # 空证据否定的守门A候选复核：维持合规否定
            _guard_review_keeps_negative("f-slot"),
        )
    ).compare_single_reference(
        claim_id="claim-1",
        expanded_claim_text="套圈上沿周向间距设置定位卡槽。",
        target_patent_text=_target_patent_text(),
        limitations=[_feature("f-slot", 1, "套圈上沿周向间距设置定位卡槽")],
        document_id="D-SLOT",
        document_text="文献公开固定壳体上的轴向装配卡槽。",
        target_images=["target.png"],
        document_images=["reference.png"],
    )

    assert comparison.disclosures[0].status is DisclosureStatus.NOT_DISCLOSED


def test_i4s_does_not_reject_simple_component_because_it_is_not_independent() -> None:
    comparison = InvalidityAnalysisEngine(
        FakeVisionClient(
            {
                "disclosures": [
                    {
                        "feature_id": "f-housing",
                        "status": "not_disclosed",
                        "reasoning": (
                            "文献未公开独立壳体结构，其调节器直接安装在开关旋杆上，"
                            "无对应容纳连接、传动、动力、电源和控制装置的壳体构件"
                        ),
                        "confidence": 0.9,
                        "mapping_basis": "none",
                        "structural_search_summary": (
                            "检查壳体、动力组件和控制系统，确认壳体容纳机械调节盘"
                        ),
                    }
                ]
            }
        )
    ).compare_single_reference(
        claim_id="claim-1",
        expanded_claim_text="一种装置，包括壳体。",
        target_patent_text=_target_patent_text(),
        limitations=[_feature("f-housing", 1, "包括壳体")],
        document_id="D-HOUSING",
        document_text="对比文件公开调节器的壳体内装有调节盘。",
        target_images=["target.png"],
        document_images=["reference.png"],
    )

    disclosure = comparison.disclosures[0]
    assert disclosure.status is DisclosureStatus.UNCERTAIN
    assert "目标未限定的独立零件要求" in disclosure.reasoning


@pytest.mark.parametrize(
    ("feature_id", "feature_text", "reasoning", "search_summary"),
    [
        (
            "f-sound-hole",
            "耳机本体对应喇叭设置有出音孔",
            "全文及附图只有喇叭，未公开对外声学通道，声学输出结构存在差异",
            "检查壳体、装配腔和喇叭，未找到出音孔、开口或声学通道",
        ),
        (
            "f-detent",
            "圆形凸台一侧设置有和定位卡槽适配的定位卡凸",
            "文献定位由另一卡紧组件承担，未在目标安装部位设置卡凸，定位结构不同",
            "检查轴形件、环形件及全部定位构件，未找到圆形凸台一侧的适配卡凸",
        ),
    ],
)
def test_i4s_completed_acoustic_or_positioning_difference_is_not_disclosed(
    feature_id: str,
    feature_text: str,
    reasoning: str,
    search_summary: str,
) -> None:
    comparison = InvalidityAnalysisEngine(
        FakeVisionClient(
            {
                "disclosures": [
                    {
                        "feature_id": feature_id,
                        "status": "not_disclosed",
                        "reasoning": reasoning,
                        "confidence": 0.9,
                        "mapping_basis": "none",
                        "structural_search_summary": search_summary,
                    }
                ]
            },
            # 否定断言守门：候选「耳机本体」在全文出现但未逐一处理，触发一次
            # 有界候选复核；复核逐一处理候选后维持合规否定结论
            {
                "disclosures": [
                    {
                        "feature_id": feature_id,
                        "status": "not_disclosed",
                        "reasoning": (
                            "候选耳机本体仅为装配载体，未设对外声学通道，"
                            "声学输出拓扑不同"
                        ),
                        "confidence": 0.9,
                        "mapping_basis": "none",
                        "structural_search_summary": (
                            "已逐一排查候选耳机本体、壳体、装配腔和喇叭："
                            "耳机本体无对外开口，连接拓扑不同，"
                            "未找到出音孔、开口或声学通道"
                        ),
                        "alternative_path_analysis": (
                            "已按角色语义在对比文件全文搜索承担出音角色的"
                            "候选构件并逐条排除"
                        ),
                        "role_candidates_evaluated": [
                            {
                                "candidate": "耳机本体",
                                "source": "deterministic",
                                "verdict": "不承担出音角色",
                                "reason": "仅为装配载体，声学输出拓扑不同",
                            },
                            {
                                "candidate": "全文角色重扫",
                                "source": "full_text_rescan",
                                "verdict": "无其他承担出音角色的候选",
                                "reason": "已按角色语义搜索全文，无遗漏候选",
                            },
                        ],
                    }
                ]
            },
        )
    ).compare_single_reference(
        claim_id="claim-1",
        expanded_claim_text=feature_text,
        target_patent_text=_target_patent_text(),
        limitations=[_feature(feature_id, 1, feature_text, subject="无线耳机")],
        document_id=f"D-{feature_id}",
        document_text="对比文件公开耳机本体、喇叭、转轴、环形转体和卡紧组件。",
        target_images=["target.png"],
        document_images=["reference.png"],
    )

    assert comparison.disclosures[0].status is DisclosureStatus.NOT_DISCLOSED


def test_i4s_complete_full_text_and_figure_acoustic_review_resolves_uncertainty() -> None:
    comparison = InvalidityAnalysisEngine(
        FakeVisionClient(
            {
                "disclosures": [
                    {
                        "feature_id": "f-sound-hole",
                        "status": "uncertain",
                        "reasoning": (
                            "对比文件未公开耳机本体对应喇叭设置的出音孔，仅描述喇叭"
                            "安装在装配腔内，未提及声学输出通道。"
                        ),
                        "confidence": 0.62,
                        "mapping_basis": "none",
                        "reference_structure_mapping": "无",
                        "structural_search_summary": (
                            "对比文件全文及附图未提及出音孔、开口或声学通道，仅公开"
                            "喇叭安装在装配腔内。"
                        ),
                    }
                ]
            },
            # 空证据否定的守门A候选复核：维持合规否定
            _guard_review_keeps_negative("f-sound-hole"),
        )
    ).compare_single_reference(
        claim_id="claim-1",
        expanded_claim_text="耳机本体对应喇叭设置有出音孔。",
        target_patent_text=_target_patent_text(),
        limitations=[
            _feature(
                "f-sound-hole",
                1,
                "耳机本体对应喇叭设置有出音孔",
                subject="无线耳机",
            )
        ],
        document_id="D-COMPLETE-ACOUSTIC-NEGATIVE",
        document_text="对比文件公开耳机本体、装配腔和喇叭。" * 40,
        target_images=["target.png"],
        document_images=["reference.png"],
    )

    assert comparison.disclosures[0].status is DisclosureStatus.NOT_DISCLOSED


def test_i4s_supported_composite_reconciles_its_simple_component_presence() -> None:
    limitations = [
        _feature("f-boss", 1, "耳机本体内具有圆形凸台"),
        _feature(
            "f-joint",
            2,
            "耳挂具有和圆形凸台适配的套圈，套圈套设于圆形凸台外侧",
        ),
    ]
    disclosures = [
        FeatureDisclosure(
            feature_id="f-boss",
            feature_text=limitations[0].text,
            status=DisclosureStatus.UNCERTAIN,
            reasoning="文献称为转轴而非圆形凸台",
            confidence=0.4,
        ),
        FeatureDisclosure(
            feature_id="f-joint",
            feature_text=limitations[1].text,
            status=DisclosureStatus.DIRECT_AND_UNAMBIGUOUS,
            evidence_quote="转轴可转动地穿设于环形转体内",
            evidence_location="权利要求1",
            reasoning="转轴与环形转体形成同一旋转副",
            confidence=0.9,
            target_structural_role="耳机本体上的圆形凸台与耳挂套圈形成旋转副",
            reference_structure_mapping="转轴与环形转体",
            mapping_basis="structural_equivalent",
        ),
    ]

    reconciled = _reconcile_component_rows_from_supported_composites(
        limitations,
        disclosures,
    )

    assert reconciled[0].status is DisclosureStatus.DIRECT_AND_UNAMBIGUOUS
    assert reconciled[0].reference_structure_mapping == "转轴与环形转体"
    assert reconciled[0].integrated_structure_mapping is True


def test_i4s_supported_rotational_pair_reconciles_names_but_not_positioning() -> None:
    limitations = [
        _feature("f-rotate", 1, "可旋转式连接于耳机本体的耳挂"),
        _feature("f-boss", 2, "耳机本体内具有圆形凸台"),
        _feature("f-loop", 3, "套圈套设于圆形凸台外侧"),
        _feature("f-slot", 4, "套圈上间距设置定位卡槽"),
    ]
    donor = FeatureDisclosure(
        feature_id="f-rotate",
        feature_text=limitations[0].text,
        status=DisclosureStatus.DIRECT_AND_UNAMBIGUOUS,
        evidence_quote="转轴可转动地穿设于环形转体内",
        evidence_location="权利要求1",
        reasoning="转轴与环形转体形成旋转副",
        confidence=0.9,
        target_structural_role="可旋转连接",
        reference_structure_mapping="转轴与环形转体",
        mapping_basis="structural_equivalent",
    )
    open_rows = [
        FeatureDisclosure(
            feature_id=item.feature_id,
            feature_text=item.text,
            status=DisclosureStatus.UNCERTAIN,
            reasoning="名称不同",
            confidence=0.4,
        )
        for item in limitations[1:]
    ]

    reconciled = _reconcile_rotational_pair_rows(
        limitations,
        [donor, *open_rows],
    )

    assert reconciled[1].status is DisclosureStatus.DIRECT_AND_UNAMBIGUOUS
    assert reconciled[2].status is DisclosureStatus.DIRECT_AND_UNAMBIGUOUS
    assert reconciled[3].status is DisclosureStatus.UNCERTAIN


def test_i4s_rotational_pair_replaces_a_positive_but_wrong_inner_component_mapping() -> None:
    limitations = [
        _feature("f-rotate", 1, "可旋转式连接于耳机本体的耳挂"),
        _feature("f-boss", 2, "耳机本体内具有圆形凸台"),
    ]
    donor = FeatureDisclosure(
        feature_id="f-rotate",
        feature_text=limitations[0].text,
        status=DisclosureStatus.DIRECT_AND_UNAMBIGUOUS,
        evidence_quote="转轴可转动地穿设于环形转体内",
        evidence_location="权利要求1",
        reasoning="转轴与环形转体形成旋转副",
        confidence=0.9,
        target_structural_role="可旋转连接",
        reference_structure_mapping="转轴与环形转体",
        mapping_basis="structural_equivalent",
    )
    wrong_positive = FeatureDisclosure(
        feature_id="f-boss",
        feature_text=limitations[1].text,
        status=DisclosureStatus.DIRECT_AND_UNAMBIGUOUS,
        evidence_quote="耳机本体内形成有容纳腔和装配腔",
        evidence_location="说明书第20段",
        reasoning="容纳腔承担旋转支点功能",
        confidence=0.9,
        target_structural_role="旋转支点构件",
        reference_structure_mapping="容纳腔与装配腔",
        mapping_basis="role_and_relation",
    )

    reconciled = _reconcile_rotational_pair_rows(
        limitations,
        [donor, wrong_positive],
    )

    assert reconciled[1].status is DisclosureStatus.DIRECT_AND_UNAMBIGUOUS
    assert reconciled[1].evidence_quote == donor.evidence_quote
    assert reconciled[1].reference_structure_mapping == "转轴与环形转体"


def test_i4s_explicit_absence_of_required_positioning_arrangement_is_not_disclosed() -> None:
    feature_text = "套圈上沿套圈延伸方向间距设置有若干个定位卡槽"
    comparison = InvalidityAnalysisEngine(
        FakeVisionClient(
            {
                "disclosures": [
                    {
                        "feature_id": "f-slot",
                        "status": "uncertain",
                        "reasoning": (
                            "环形件上未设置沿延伸方向间距排列的定位卡槽；"
                            "虽存在一个装配槽，但其位置和排列关系不匹配"
                        ),
                        "confidence": 0.69,
                        "mapping_basis": "none",
                        "structural_search_summary": (
                            "检查全部环形件和卡槽，未发现沿延伸方向间距设置的槽列"
                        ),
                    }
                ]
            },
            # 空证据否定的守门A候选复核：维持合规否定
            _guard_review_keeps_negative(
                "f-slot",
                reasoning_prefix="状态已按明确的未披露理由校正为未披露；",
            ),
        )
    ).compare_single_reference(
        claim_id="claim-1",
        expanded_claim_text=feature_text,
        target_patent_text=_target_patent_text(),
        limitations=[_feature("f-slot", 1, feature_text, subject="无线耳机")],
        document_id="D-POSITIONING-ABSENT",
        document_text="对比文件仅公开环形件上的一个轴向装配槽。",
        target_images=["target.png"],
        document_images=["reference.png"],
    )

    assert comparison.disclosures[0].status is DisclosureStatus.NOT_DISCLOSED
    assert "状态已按明确的未披露理由校正" in comparison.disclosures[0].reasoning


def test_i4s_numeric_overlap_does_not_override_material_identity_difference() -> None:
    comparison = InvalidityAnalysisEngine(
        FakeVisionClient(
            {
                "disclosures": [
                    {
                        "feature_id": "f-thickener",
                        "status": "not_disclosed",
                        "evidence_quote": "水溶性纤维素增稠剂0.1-2份",
                        "evidence_location": "权利要求1",
                        "reasoning": (
                            "目标为碱溶性高分子乳液增稠剂0.1-5份，文献为水溶性"
                            "纤维素增稠剂0.1-2份；虽然用量区间重叠，但成分不同，"
                            "不存在目标乳液材料"
                        ),
                        "confidence": 0.92,
                        "mapping_basis": "none",
                        "structural_search_summary": (
                            "全文仅公开水溶性纤维素；其材料类别和乳液相态均不同"
                        ),
                    }
                ]
            }
        )
    ).compare_single_reference(
        claim_id="claim-1",
        expanded_claim_text="一种浆料，包括0.1-5份碱溶性高分子乳液增稠剂。",
        target_patent_text=_target_patent_text(),
        limitations=[
            _feature("f-thickener", 1, "碱溶性高分子乳液增稠剂（0.1-5份）")
        ],
        document_id="D-MATERIAL-RANGE",
        document_text="权利要求1记载水溶性纤维素增稠剂0.1-2份。",
        target_images=["target.png"],
        document_images=["reference.png"],
    )

    assert comparison.disclosures[0].status is DisclosureStatus.NOT_DISCLOSED
    assert "原无重叠判断错误" not in comparison.disclosures[0].reasoning


def test_i4s_numeric_overlap_does_not_override_generic_material_type_difference() -> None:
    comparison = InvalidityAnalysisEngine(
        FakeVisionClient(
            {
                "disclosures": [
                    {
                        "feature_id": "f-thickener",
                        "status": "not_disclosed",
                        "evidence_quote": "水溶性纤维素增稠剂0.1-2份",
                        "evidence_location": "权利要求1",
                        "reasoning": (
                            "目标为碱溶性高分子乳液增稠剂0.1-5份，文献为水溶性"
                            "纤维素增稠剂0.1-2份；用量范围未完全覆盖且增稠剂类型不同"
                        ),
                        "confidence": 0.92,
                        "mapping_basis": "none",
                        "structural_search_summary": (
                            "已复核全文全部增稠剂及其形态；仅有水溶性纤维素，"
                            "未包含碱溶性高分子乳液"
                        ),
                    }
                ]
            }
        )
    ).compare_single_reference(
        claim_id="claim-1",
        expanded_claim_text="一种浆料，包括0.1-5份碱溶性高分子乳液增稠剂。",
        target_patent_text=_target_patent_text(),
        limitations=[
            _feature("f-thickener", 1, "碱溶性高分子乳液增稠剂（0.1-5份）")
        ],
        document_id="D-MATERIAL-TYPE-RANGE",
        document_text="权利要求1记载水溶性纤维素增稠剂0.1-2份。",
        target_images=["target.png"],
        document_images=["reference.png"],
    )

    disclosure = comparison.disclosures[0]
    assert disclosure.status is DisclosureStatus.NOT_DISCLOSED
    assert "原无重叠判断错误" not in disclosure.reasoning


def test_i4s_non_overlapping_rotational_speed_is_a_substantive_difference() -> None:
    comparison = InvalidityAnalysisEngine(
        FakeVisionClient(
            {
                "disclosures": [
                    {
                        "feature_id": "f-speed",
                        "status": "not_disclosed",
                        "evidence_quote": "高速分散机的转速为1500转/分钟",
                        "evidence_location": "实施例1",
                        "reasoning": (
                            "文献转速为1500r/min，目标为30-60r/min，"
                            "同一转速参数的范围无重叠"
                        ),
                        "confidence": 0.92,
                        "mapping_basis": "none",
                        "structural_search_summary": (
                            "已复核全部分散步骤和转速参数，仅找到1000或1500r/min，"
                            "与30-60r/min范围无重叠"
                        ),
                    }
                ]
            }
        )
    ).compare_single_reference(
        claim_id="claim-1",
        expanded_claim_text="以30-60r/min的转速进行搅拌。",
        target_patent_text=_target_patent_text(),
        limitations=[_feature("f-speed", 1, "转速30-60r/min")],
        document_id="D-SPEED",
        document_text="实施例1记载高速分散机的转速为1500转/分钟。",
        target_images=["target.png"],
        document_images=["reference.png"],
    )

    assert comparison.disclosures[0].status is DisclosureStatus.NOT_DISCLOSED


@pytest.mark.parametrize(
    ("feature_text", "reasoning", "summary"),
    [
        (
            "第一连接件固定套设在第二连接件内",
            "候选只有两个并列轴件，无套设关系，连接拓扑不同",
            "已检查全部连接件及装配关系，未找到一个连接件嵌套在另一个连接件内",
        ),
        (
            "第一连接件具有与开关轴配合的切面结构",
            "候选轴为完整圆轴，无切面配合结构，安装界面不同",
            "已检查全部轴端和配合面，未设置与开关轴切面配合的切面",
        ),
        (
            "第一连接件可变换其与第二连接件的相对位置，以适用各种开关轴安装",
            "对比文件调节盘固定套接旋杆，无相对位置变换设计，无法适应不同开关轴安装需求",
            "对比文件调节盘固定安装，无位置变换机制，无法实现目标可调适应性",
        ),
        (
            "动力部件电连接控制装置，控制装置电连接电源并接收指令驱动动力部件",
            "对比文件无动力部件、控制装置或电源，仅依赖机械结构实现调节，无电控驱动系统",
            "对比文件无电控组件，无法实现目标电控驱动功能",
        ),
    ],
)
def test_i4s_completed_nested_or_mating_surface_difference_is_not_disclosed(
    feature_text: str,
    reasoning: str,
    summary: str,
) -> None:
    comparison = InvalidityAnalysisEngine(
        FakeVisionClient(
            {
                "disclosures": [
                    {
                        "feature_id": "f-interface",
                        "status": "not_disclosed",
                        "reasoning": reasoning,
                        "confidence": 0.9,
                        "mapping_basis": "none",
                        "structural_search_summary": summary,
                    }
                ]
            },
            # 否定断言守门：候选「连接件」在全文出现但未逐一处理，触发一次
            # 有界候选复核；复核逐一处理候选后维持合规否定结论
            {
                "disclosures": [
                    {
                        "feature_id": "f-interface",
                        "status": "not_disclosed",
                        "reasoning": (
                            "候选连接件为两个并列圆轴、固定安装，无位置变换"
                            "机制，运动状态关系不同"
                        ),
                        "confidence": 0.9,
                        "mapping_basis": "none",
                        "structural_search_summary": (
                            "已逐一排查候选连接件与调节盘：连接件固定套接"
                            "旋杆，不承担相对位置变换角色，连接拓扑不同"
                        ),
                        "alternative_path_analysis": (
                            "已按角色语义在对比文件全文搜索承担位置变换角色的"
                            "候选构件并逐条排除"
                        ),
                        "role_candidates_evaluated": [
                            {
                                "candidate": "连接件",
                                "source": "deterministic",
                                "verdict": "不承担位置变换角色",
                                "reason": "固定套接，连接拓扑不同",
                            },
                            {
                                "candidate": "全文角色重扫",
                                "source": "full_text_rescan",
                                "verdict": "无其他承担位置变换角色的候选",
                                "reason": "已按角色语义搜索全文，无遗漏候选",
                            },
                        ],
                    }
                ]
            },
        )
    ).compare_single_reference(
        claim_id="claim-1",
        expanded_claim_text=feature_text,
        target_patent_text=_target_patent_text(),
        limitations=[_feature("f-interface", 1, feature_text)],
        document_id="D-INTERFACE",
        document_text="对比文件公开两个并列圆轴连接件。",
        target_images=["target.png"],
        document_images=["reference.png"],
    )

    assert comparison.disclosures[0].status is DisclosureStatus.NOT_DISCLOSED


def test_i4s_prompts_require_actual_connection_interface_geometry_review() -> None:
    payload = {
        "claim_id": "claim-1",
        "expanded_claim_text": "连接件具有与开关轴配合的切面结构",
        "limitations": [
            _feature("f-interface", 1, "与开关轴配合的切面结构")
        ],
        "target_patent_text": _target_patent_text(),
        "document_id": "D-INTERFACE-PROMPT",
        "document_text": "文献公开齿轮与圆轴。",
    }
    first_pass = InvalidityAnalysisEngine._single_reference_prompt(**payload)
    review = InvalidityAnalysisEngine._structural_reconciliation_prompt(
        **payload,
        target_mechanism_summary="切面连接",
        reference_mechanism_summary="齿轮与圆轴",
        first_pass_disclosures=[],
        immutable_positive_disclosures=[],
    )

    assert "候选连接件与被连接轴之间的实际接合界面" in first_pass
    assert "任意齿轮、套筒或传动件" in review
    assert "不能只写‘未提及切面’" in first_pass


def test_i4s_prompts_require_complete_material_negative_review_without_target_contamination() -> None:
    payload = {
        "claim_id": "claim-1",
        "expanded_claim_text": "浆料包括碱溶性高分子乳液增稠剂",
        "limitations": [
            _feature("f-material", 1, "碱溶性高分子乳液增稠剂（0.1-5份）")
        ],
        "target_patent_text": _target_patent_text(),
        "document_id": "D-MATERIAL-PROMPT",
        "document_text": "文献公开纤维素增稠剂。",
    }
    first_pass = InvalidityAnalysisEngine._single_reference_prompt(**payload)
    review = InvalidityAnalysisEngine._structural_reconciliation_prompt(
        **payload,
        target_mechanism_summary="乳液增稠剂",
        reference_mechanism_summary="纤维素增稠剂",
        first_pass_disclosures=[],
        immutable_positive_disclosures=[],
    )

    assert "全部组分清单" in first_pass
    assert "不得把目标专利列举的材料成员复制" in first_pass
    assert "完整组分复核确无对应成分" in review


def test_i4s_untraceable_negative_quote_is_removed_without_losing_complete_review() -> None:
    target_only_quote = "碱溶性高分子乳液增稠剂0.1-5份"
    comparison = InvalidityAnalysisEngine(
        FakeVisionClient(
            {
                "disclosures": [
                    {
                        "feature_id": "f-thickener",
                        "status": "not_disclosed",
                        "evidence_quote": target_only_quote,
                        "evidence_location": "目标专利权利要求1",
                        "reasoning": (
                            "文献仅公开水溶性纤维素增稠剂，材料类型和乳液相态不同，"
                            "未公开目标材料"
                        ),
                        "confidence": 0.9,
                        "mapping_basis": "none",
                        "structural_search_summary": (
                            "已复核全文的增稠剂、材料类型及相态，仅找到水溶性纤维素，"
                            "未包含碱溶性高分子乳液"
                        ),
                    }
                ]
            },
            # 空证据否定的守门A候选复核：维持合规否定
            _guard_review_keeps_negative(
                "f-thickener",
                reasoning_prefix="模型主引文无法回溯到本对比文件，已从证据栏移除；",
            ),
        )
    ).compare_single_reference(
        claim_id="claim-1",
        expanded_claim_text=f"一种浆料，包括{target_only_quote}。",
        target_patent_text=f"目标专利记载{target_only_quote}。",
        limitations=[_feature("f-thickener", 1, target_only_quote)],
        document_id="D-NEGATIVE-PROVENANCE",
        document_text="对比文件仅公开水溶性纤维素增稠剂0.1-2份。",
        target_images=["target.png"],
        document_images=["reference.png"],
    )

    disclosure = comparison.disclosures[0]
    assert disclosure.status is DisclosureStatus.NOT_DISCLOSED
    assert disclosure.evidence_quote == ""
    assert disclosure.evidence_location == "对比文件全文复核（未检出对应披露）"
    assert "已从证据栏移除" in disclosure.reasoning


def test_i4s_named_material_and_overlapping_amount_may_use_nearby_claim_rows() -> None:
    comparison = InvalidityAnalysisEngine(
        FakeVisionClient(
            {
                "disclosures": [
                    {
                        "feature_id": "f-dispersant",
                        "status": "explicit",
                        "evidence_quote": "水性分散剂为聚乙二醇或聚丙烯酸钠",
                        "evidence_location": "权利要求4",
                        "reasoning": "材料类别和具体组分直接对应",
                        "confidence": 0.9,
                        "mapping_basis": "literal",
                        "structural_search_summary": "已核对权利要求1和4",
                    }
                ]
            }
        )
    ).compare_single_reference(
        claim_id="claim-1",
        expanded_claim_text="一种浆料，包括水性分散剂0.1-5份。",
        target_patent_text=_target_patent_text(),
        limitations=[_feature("f-dispersant", 1, "水性分散剂（0.1-5份）")],
        document_id="D-DISTRIBUTED-AMOUNT",
        document_text=(
            "权利要求1：组合物包括水性分散剂0.1-2份。"
            "权利要求4：水性分散剂为聚乙二醇或聚丙烯酸钠。"
        ),
        target_images=["target.png"],
        document_images=["reference.png"],
    )

    assert comparison.disclosures[0].status is DisclosureStatus.EXPLICIT


def test_i4s_target_only_material_quote_cannot_use_target_amount_as_source() -> None:
    comparison = InvalidityAnalysisEngine(
        FakeVisionClient(
            {
                "disclosures": [
                    {
                        "feature_id": "f-wetting",
                        "status": "explicit",
                        "evidence_quote": "水性润湿剂为烷基酚聚氧乙烯醚",
                        "evidence_location": "目标权利要求1",
                        "reasoning": "名称相同",
                        "confidence": 0.9,
                        "mapping_basis": "literal",
                    }
                ]
            }
        )
    ).compare_single_reference(
        claim_id="claim-1",
        expanded_claim_text="一种浆料，包括水性润湿剂0.1-5份。",
        target_patent_text="目标专利：水性润湿剂为烷基酚聚氧乙烯醚。",
        limitations=[_feature("f-wetting", 1, "水性润湿剂（0.1-5份）")],
        document_id="D-NO-WETTING-AGENT",
        document_text="对比文件仅公开水性分散剂0.1-2份和水性乳胶0.1-5份。",
        target_images=["target.png"],
        document_images=["reference.png"],
    )

    disclosure = comparison.disclosures[0]
    assert disclosure.status is DisclosureStatus.UNCERTAIN
    assert disclosure.evidence_quote == ""


def test_i4s_strips_location_suffix_from_traceable_structural_evidence() -> None:
    comparison = InvalidityAnalysisEngine(
        FakeVisionClient(
            {
                "disclosures": [
                    {
                        "feature_id": "f-latex",
                        "status": "explicit",
                        "evidence_quote": "水性乳胶为聚丙烯酸乙酯",
                        "evidence_location": "说明书第3页",
                        "reasoning": "材料及用量范围均直接对应",
                        "confidence": 0.9,
                        "mapping_basis": "role_and_relation",
                        "target_structural_role": "水性乳胶粘结剂",
                        "reference_structure_mapping": "水性乳胶0.1-5份",
                        "structural_evidence": [
                            "水性乳胶为聚丙烯酸乙酯（CN100000001A说明书第3页）",
                            "水性乳胶0.1-5份（CN100000001A权利要求1）",
                        ],
                    }
                ]
            }
        )
    ).compare_single_reference(
        claim_id="claim-1",
        expanded_claim_text="一种浆料，包括水性乳胶1-10份。",
        target_patent_text=_target_patent_text(),
        limitations=[_feature("f-latex", 1, "水性乳胶（1-10份）")],
        document_id="D-LOCATION-SUFFIX",
        document_text="水性乳胶为聚丙烯酸乙酯。水性乳胶0.1-5份。",
        target_images=["target.png"],
        document_images=["reference.png"],
    )

    disclosure = comparison.disclosures[0]
    assert disclosure.status is DisclosureStatus.EXPLICIT
    assert disclosure.structural_evidence == [
        "水性乳胶为聚丙烯酸乙酯",
        "水性乳胶0.1-5份",
    ]


def test_i4s_complete_material_full_text_review_can_confirm_absence() -> None:
    comparison = InvalidityAnalysisEngine(
        FakeVisionClient(
            {
                "disclosures": [
                    {
                        "feature_id": "f-wetting",
                        "status": "uncertain",
                        "reasoning": "对比文件全文未提及水性润湿剂，未公开该组分",
                        "confidence": 0.65,
                        "mapping_basis": "none",
                        "reference_structure_mapping": "无对应组分",
                        "structural_search_summary": (
                            "已检查对比文件全文全部成分和表面活性剂，"
                            "未发现水性润湿剂候选组分"
                        ),
                    }
                ]
            },
            # 空证据否定的守门A候选复核：维持合规否定
            _guard_review_keeps_negative("f-wetting"),
        )
    ).compare_single_reference(
        claim_id="claim-1",
        expanded_claim_text="一种浆料，包括水性润湿剂。",
        target_patent_text=_target_patent_text(),
        limitations=[_feature("f-wetting", 1, "水性润湿剂")],
        document_id="D-COMPLETE-MATERIAL-REVIEW",
        document_text="对比文件包括水性分散剂和水性乳胶。",
        target_images=["target.png"],
        document_images=["reference.png"],
    )

    assert comparison.disclosures[0].status is DisclosureStatus.NOT_DISCLOSED


def test_i4s_coordinated_material_identity_difference_is_substantive() -> None:
    comparison = InvalidityAnalysisEngine(
        FakeVisionClient(
            {
                "disclosures": [
                    {
                        "feature_id": "f-thickener",
                        "status": "not_disclosed",
                        "reasoning": "候选与目标的材料类型和化学身份不同",
                        "confidence": 0.9,
                        "mapping_basis": "none",
                        "reference_structure_mapping": "水溶性高分子增稠剂",
                        "structural_search_summary": (
                            "已检查全文全部增稠剂成分，候选材料类型和化学身份不同"
                        ),
                    }
                ]
            },
            # 空证据否定的守门A候选复核：维持合规否定
            _guard_review_keeps_negative("f-thickener"),
        )
    ).compare_single_reference(
        claim_id="claim-1",
        expanded_claim_text="一种浆料，包括碱溶性高分子乳液增稠剂。",
        target_patent_text=_target_patent_text(),
        limitations=[_feature("f-thickener", 1, "碱溶性高分子乳液增稠剂")],
        document_id="D-COORDINATED-MATERIAL-DIFFERENCE",
        document_text="候选增稠剂为纤维素或聚丙烯酰胺。",
        target_images=["target.png"],
        document_images=["reference.png"],
    )

    assert comparison.disclosures[0].status is DisclosureStatus.NOT_DISCLOSED


def test_i4s_detailed_material_form_mismatch_does_not_require_duplicate_summary() -> None:
    comparison = InvalidityAnalysisEngine(
        FakeVisionClient(
            {
                "disclosures": [
                    {
                        "feature_id": "f-thickener",
                        "status": "not_disclosed",
                        "reasoning": (
                            "对比文件公开纤维素类或聚丙烯酸类高分子聚合物，"
                            "而目标限定碱溶性高分子乳液增稠剂；对比文件未公开"
                            "该具体材料类型，且未限定为乳液形式，故未披露。"
                        ),
                        "confidence": 0.9,
                        "mapping_basis": "none",
                        "reference_structure_mapping": "纤维素类高分子增稠剂",
                    }
                ]
            },
            # 空证据否定的守门A候选复核：维持合规否定
            _guard_review_keeps_negative("f-thickener"),
        )
    ).compare_single_reference(
        claim_id="claim-1",
        expanded_claim_text="一种浆料，包括碱溶性高分子乳液增稠剂。",
        target_patent_text=_target_patent_text(),
        limitations=[_feature("f-thickener", 1, "碱溶性高分子乳液增稠剂")],
        document_id="D-MATERIAL-FORM-NO-DUPLICATE-SUMMARY",
        document_text="候选增稠剂为纤维素类或聚丙烯酸类高分子聚合物。",
        target_images=["target.png"],
        document_images=["reference.png"],
    )

    assert comparison.disclosures[0].status is DisclosureStatus.NOT_DISCLOSED


def test_i4s_full_source_material_member_absence_is_not_mechanical_uncertainty() -> None:
    comparison = InvalidityAnalysisEngine(
        FakeVisionClient(
            {
                "disclosures": [
                    {
                        "feature_id": "f-wetting-agent",
                        "status": "not_disclosed",
                        "reasoning": (
                            "对比文件未提及目标所列水性润湿剂类别或0.1-5份"
                            "重量份范围，故未披露。"
                        ),
                        "confidence": 0.9,
                        "mapping_basis": "none",
                        "structural_search_summary": (
                            "复核全部配方成分和重量份范围，未发现对应润湿剂。"
                        ),
                    }
                ]
            },
            # 空证据否定的守门A候选复核：维持合规否定
            _guard_review_keeps_negative("f-wetting-agent"),
        )
    ).compare_single_reference(
        claim_id="claim-1",
        expanded_claim_text="一种浆料，包括0.1-5份水性润湿剂。",
        target_patent_text=_target_patent_text(),
        limitations=[_feature("f-wetting-agent", 1, "水性润湿剂（0.1-5重量份）")],
        document_id="D-FULL-MATERIAL-NEGATIVE",
        document_text="候选文献的完整配方段落。" * 100,
        target_images=["target.png"],
        document_images=["reference.png"],
    )

    assert comparison.disclosures[0].status is DisclosureStatus.NOT_DISCLOSED


def test_i4s_untraceable_positive_material_quote_becomes_full_source_negative() -> None:
    target_only_quote = "水性润湿剂为烷基酚聚氧乙烯醚，含量为0.1-5份"
    comparison = InvalidityAnalysisEngine(
        FakeVisionClient(
            {
                "disclosures": [
                    {
                        "feature_id": "f-wetting-agent",
                        "status": "explicit",
                        "evidence_quote": target_only_quote,
                        "evidence_location": "权利要求4",
                        "reasoning": "认为对比文件直接公开相同水性润湿剂",
                        "confidence": 0.9,
                        "mapping_basis": "literal",
                        "reference_structure_mapping": "烷基酚聚氧乙烯醚",
                    }
                ]
            },
            {
                "disclosures": [
                    {
                        "feature_id": "f-wetting-agent",
                        "status": "not_disclosed",
                        "evidence_quote": "候选文献仅公开陶瓷颗粒、水、乳胶和分散剂",
                        "evidence_location": "全部配方及实施例",
                        "reasoning": (
                            "完整复核权利要求、全部组分清单、配方表和实施例，实际组分仅为"
                            "陶瓷颗粒、水、乳胶和分散剂，未公开润湿剂"
                        ),
                        "confidence": 0.9,
                        "mapping_basis": "none",
                        "reference_structure_mapping": "无对应成分",
                        "structural_evidence": [
                            "候选文献仅公开陶瓷颗粒、水、乳胶和分散剂"
                        ],
                        "structural_search_summary": (
                            "完整组分复核：陶瓷颗粒、水、乳胶和分散剂；无润湿剂"
                        ),
                    }
                ]
            },
        )
    ).compare_single_reference(
        claim_id="claim-1",
        expanded_claim_text="一种浆料，包括0.1-5份水性润湿剂。",
        target_patent_text=_target_patent_text() + target_only_quote,
        limitations=[_feature("f-wetting-agent", 1, "水性润湿剂（0.1-5重量份）")],
        document_id="D-HALLUCINATED-MATERIAL-QUOTE",
        document_text="候选文献仅公开陶瓷颗粒、水、乳胶和分散剂。" * 100,
        target_images=["target.png"],
        document_images=["reference.png"],
    )

    assert comparison.disclosures[0].status is DisclosureStatus.NOT_DISCLOSED
    assert len(comparison.disclosures[0].evidence_quote) > 0


@pytest.mark.parametrize(
    ("feature_text", "mapping", "source_text", "expected_quote"),
    [
        (
            "光固化组合物",
            "感光性树脂组合物",
            "本发明公开一种感 光 性 树 脂 组 合 物。",
            "感光性树脂组合物",
        ),
        (
            "膜材料（液体/干膜）",
            "液体膜和干膜",
            "该组合物可在湿 膜、干 膜方面得到应用。",
            "干膜",
        ),
    ],
)
def test_i4s_uses_traceable_minimal_reference_mapping_after_bad_main_quote(
    feature_text: str,
    mapping: str,
    source_text: str,
    expected_quote: str,
) -> None:
    comparison = InvalidityAnalysisEngine(
        FakeVisionClient(
            {
                "disclosures": [
                    {
                        "feature_id": "f-use",
                        "status": "explicit",
                        "evidence_quote": "目标专利中的一段不可回溯长引文",
                        "evidence_location": "权利要求8",
                        "reasoning": "对比文件的结构或用途与目标限定直接对应",
                        "confidence": 0.9,
                        "target_structural_role": feature_text,
                        "reference_structure_mapping": mapping,
                        "mapping_basis": "literal",
                    }
                ]
            }
        )
    ).compare_single_reference(
        claim_id="claim-8",
        expanded_claim_text=f"一种{feature_text}的应用。",
        target_patent_text=_target_patent_text(),
        limitations=[
            _feature("f-use", 1, feature_text).model_copy(
                update={"claim_id": "claim-8"}
            )
        ],
        document_id="D-TRACEABLE-MINIMAL-MAPPING",
        document_text=(source_text + "相关实施例。") * 50,
        target_images=["target.png"],
        document_images=["reference.png"],
    )

    assert comparison.disclosures[0].status in {
        DisclosureStatus.EXPLICIT,
        DisclosureStatus.DIRECT_AND_UNAMBIGUOUS,
    }
    assert comparison.disclosures[0].evidence_quote == expected_quote


def test_i4s_negative_material_reason_corrects_mistyped_positive_basis() -> None:
    comparison = InvalidityAnalysisEngine(
        FakeVisionClient(
            {
                "disclosures": [
                    {
                        "feature_id": "f-thickener",
                        "status": "not_disclosed",
                        "reasoning": (
                            "对比文件为聚丙烯酸类高分子聚合物，而目标为碱溶性"
                            "高分子乳液增稠剂，两者材料类型和具体组成存在差异，"
                            "且对比文件未限定乳液形式，故未披露。"
                        ),
                        "confidence": 0.9,
                        "mapping_basis": "structural_equivalent",
                        "reference_structure_mapping": "聚丙烯酸类高分子聚合物",
                        "structural_search_summary": "复核全部增稠剂，材料类型和乳液形态不同。",
                    }
                ]
            },
            # 空证据否定的守门A候选复核：维持合规否定
            _guard_review_keeps_negative("f-thickener"),
        )
    ).compare_single_reference(
        claim_id="claim-1",
        expanded_claim_text="一种浆料，包括碱溶性高分子乳液增稠剂。",
        target_patent_text=_target_patent_text(),
        limitations=[_feature("f-thickener", 1, "碱溶性高分子乳液增稠剂")],
        document_id="D-MISTYPED-POSITIVE-BASIS",
        document_text="候选增稠剂为聚丙烯酸类高分子聚合物。" * 50,
        target_images=["target.png"],
        document_images=["reference.png"],
    )

    assert comparison.disclosures[0].status is DisclosureStatus.NOT_DISCLOSED
    assert comparison.disclosures[0].mapping_basis == "none"


def test_i4s_clear_scaffold_difference_is_not_downgraded_by_word_order() -> None:
    comparison = InvalidityAnalysisEngine(
        FakeVisionClient(
            {
                "disclosures": [
                    {
                        "feature_id": "f-scaffold",
                        "status": "not_disclosed",
                        "evidence_quote": "式(I)所示的苯并噻吩化合物",
                        "evidence_location": "权利要求1",
                        "reasoning": "候选化合物与目标化合物的母核骨架完全不同，无结构等同",
                        "confidence": 0.9,
                        "mapping_basis": "none",
                        "structural_search_summary": (
                            "已核对全部结构式；仅有苯并噻吩母核，无法对应目标芴母核"
                        ),
                    }
                ]
            }
        )
    ).compare_single_reference(
        claim_id="claim-1",
        expanded_claim_text="一种含芴母核的光引发剂。",
        target_patent_text=_target_patent_text(),
        limitations=[_feature("f-scaffold", 1, "含芴母核的光引发剂")],
        document_id="D-SCAFFOLD",
        document_text="权利要求1公开式(I)所示的苯并噻吩化合物。",
        target_images=["target.png"],
        document_images=["reference.png"],
    )

    assert comparison.disclosures[0].status is DisclosureStatus.NOT_DISCLOSED


@pytest.mark.parametrize(
    ("feature_text", "reasoning"),
    [
        ("压力罐", "全文未提及压力罐"),
        ("带有阀导管的阀", "全文未记载阀导管"),
    ],
)
def test_i4s_target_name_absence_alone_never_proves_non_disclosure(
    feature_text: str,
    reasoning: str,
) -> None:
    comparison = InvalidityAnalysisEngine(
        FakeVisionClient(
            {
                "disclosures": [
                    {
                        "feature_id": "f1",
                        "status": "not_disclosed",
                        "reasoning": reasoning,
                        "confidence": 0.9,
                        "mapping_basis": "none",
                        "structural_search_summary": "已搜索目标名称及其常见同义词",
                    }
                ]
            }
        )
    ).compare_single_reference(
        claim_id="claim-1",
        expanded_claim_text=f"一种装置，包括{feature_text}。",
        target_patent_text=_target_patent_text(),
        limitations=[_feature("f1", 1, feature_text)],
        document_id="D-LITERAL-ABSENCE",
        document_text="对比文件描述了完整装置及其工作过程。",
        target_images=["target.png"],
        document_images=["reference.png"],
    )

    assert comparison.disclosures[0].status is DisclosureStatus.UNCERTAIN
    assert "目标术语缺失" in comparison.disclosures[0].reasoning


def test_i4s_explicit_absence_of_reviewed_relative_position_structure_is_negative() -> None:
    comparison = InvalidityAnalysisEngine(
        FakeVisionClient(
            {
                "disclosures": [
                    {
                        "feature_id": "f-relative-position",
                        "status": "not_disclosed",
                        "reasoning": (
                            "对比文件的多段调节器通过调节盘与弹性组件配合分级调节，"
                            "未公开第一连接件与第二连接件可变换相对位置的结构，也未公开"
                            "用于适配不同开关轴的切面配合结构。"
                        ),
                        "confidence": 0.9,
                        "mapping_basis": "none",
                        "reference_structure_mapping": "调节盘与弹性组件",
                        "structural_search_summary": (
                            "已检查调节盘、弹性组件和开关旋杆之间的完整连接及运动关系。"
                        ),
                    }
                ]
            },
            # 空证据否定的守门A候选复核：维持合规否定
            _guard_review_keeps_negative("f-relative-position"),
        )
    ).compare_single_reference(
        claim_id="claim-1",
        expanded_claim_text="第一连接件可变换其与第二连接件的相对位置。",
        target_patent_text=_target_patent_text(),
        limitations=[
            _feature(
                "f-relative-position",
                1,
                "第一连接件可变换其与第二连接件的相对位置以适配不同开关轴",
            )
        ],
        document_id="D-REVIEWED-RELATIVE-POSITION-ABSENCE",
        document_text="多段调节器由调节盘、弹性组件和开关旋杆组成并进行分级调节。" * 40,
        target_images=["target.png"],
        document_images=["reference.png"],
    )

    assert comparison.disclosures[0].status is DisclosureStatus.NOT_DISCLOSED


def test_i4s_no_matching_position_relation_is_not_left_uncertain() -> None:
    comparison = InvalidityAnalysisEngine(
        FakeVisionClient(
            {
                "disclosures": [
                    {
                        "feature_id": "f-positioning-projection",
                        "status": "uncertain",
                        "reasoning": (
                            "候选凸起未明确位于支撑轴一侧，且未与环形件上的定位槽形成"
                            "适配关系；另一卡紧组件位于两个壳体接触表面，位置与作用不同。"
                        ),
                        "confidence": 0.62,
                        "mapping_basis": "none",
                        "structural_search_summary": (
                            "已检查全文中的支撑轴、环形件、卡槽和弹性卡接部，未明确设置"
                            "与环形件定位槽适配的轴侧定位凸起。"
                        ),
                    }
                ]
            },
            # 空证据否定的守门A候选复核：维持合规否定
            _guard_review_keeps_negative("f-positioning-projection"),
        )
    ).compare_single_reference(
        claim_id="claim-1",
        expanded_claim_text="支撑轴一侧设置有和定位槽适配的定位凸起。",
        target_patent_text=_target_patent_text(),
        limitations=[
            _feature(
                "f-positioning-projection",
                1,
                "支撑轴一侧设置有和定位槽适配的定位凸起",
            )
        ],
        document_id="D-REVIEWED-POSITIONING-RELATION-ABSENCE",
        document_text=(
            "环形件设置卡槽，弹性卡接部位于两个壳体接触表面并卡入卡槽。"
        )
        * 40,
        target_images=["target.png"],
        document_images=["reference.png"],
    )

    disclosure = comparison.disclosures[0]
    assert disclosure.status is DisclosureStatus.NOT_DISCLOSED
    assert disclosure.mapping_basis == "none"


def test_i4s_missing_or_duplicate_feature_cannot_be_aggregated_as_complete() -> None:
    limitations = [
        _feature("f1", 1, "移动底盘"),
        _feature("f2", 2, "清洁件相对底盘升降"),
    ]
    duplicate = {
        "disclosures": [
            {
                "feature_id": "f1",
                "status": "explicit",
                "evidence_quote": "chassis",
                "evidence_location": "claim 1",
                "reasoning": "one",
                "confidence": 0.9,
            },
            {
                "feature_id": "f1",
                "status": "explicit",
                "evidence_quote": "frame",
                "evidence_location": "paragraph 2",
                "reasoning": "duplicate",
                "confidence": 0.9,
            },
        ]
    }
    comparison = InvalidityAnalysisEngine(
        FakeVisionClient(duplicate, duplicate)
    ).compare_single_reference(
        claim_id="claim-1",
        expanded_claim_text="展开权利要求文本",
        target_patent_text=_target_patent_text(),
        limitations=limitations,
        document_id="D1",
        document_text="document text",
        target_images=["target.png"],
        document_images=["d1.png"],
    )
    assert [item.status for item in comparison.disclosures] == [
        DisclosureStatus.ANALYSIS_FAILED,
        DisclosureStatus.ANALYSIS_FAILED,
    ]


def test_i4c_uses_coverage_alignment_evidence_and_date_and_generates_gap() -> None:
    limitations = [
        _feature("f1", 1, "移动底盘"),
        _feature("f2", 2, "升降清洁件"),
        _feature("f3", 3, "地面材质传感器"),
    ]
    d1 = _comparison("D1", ["f1", "f2"], alignment=0.7)
    high_alignment_but_low_coverage = _comparison(
        "D2", ["f1"], alignment=1.0, evidence_completeness=1.0
    )
    complete_but_late = _comparison("D3", ["f1", "f2", "f3"], alignment=1.0)

    selection = select_closest_prior_art(
        limitations=limitations,
        comparisons=[high_alignment_but_low_coverage, complete_but_late, d1],
        date_qualifications={
            "D1": {"inventive_step_eligible": True},
            "D2": {"inventive_step_eligible": True},
            "D3": {"inventive_step_eligible": False},
        },
    )

    assert selection.document_id == "D1"
    assert [item.document_id for item in selection.ranked_candidates] == ["D1", "D2"]
    assert selection.uncovered_feature_ids == ["f3"]
    assert selection.gaps[0].kind == "feature_gap"
    assert selection.gaps[0].subtype == "feature_uncovered"
    assert "文本相似度" in selection.selection_reason


def test_i4c_prioritises_confirmed_feature_count_before_alignment() -> None:
    limitations = [
        _feature("f1", 1, "构件一"),
        _feature("f2", 2, "构件二"),
        _feature("f3", 3, "构件三"),
    ]
    two_features = _comparison("D-more", ["f1", "f2"], alignment=0.1)
    one_feature = _comparison(
        "D-aligned", ["f1"], alignment=1.0, evidence_completeness=1.0
    )
    selection = select_closest_prior_art(
        limitations=limitations,
        comparisons=[one_feature, two_features],
        date_qualifications={
            "D-more": {"inventive_step_eligible": True},
            "D-aligned": {"inventive_step_eligible": True},
        },
    )
    assert selection.document_id == "D-more"
    assert selection.ranked_candidates[0].confirmed_feature_count == 2


def test_existing_corpus_reuse_closes_only_same_role_effect_with_current_versions() -> None:
    limitations = [_feature("f1", 1, "传感器位于密闭壳体内")]
    profile = InventionSearchProfile(
        protected_subject="检测装置",
        invention_summary="密闭壳体内布置传感器以隔绝杂散光",
        mechanism_model=MechanismModel(
            summary="密闭光学检测机构",
            elements=[
                MechanismElement(
                    element_id="m1",
                    kind="component_role",
                    text="传感器位于密闭壳体内承担检测角色",
                    feature_ids=["f1"],
                    source_reference="权利要求1",
                    rationale="目标结构角色",
                ),
                MechanismElement(
                    element_id="m2",
                    kind="technical_role",
                    text="隔绝杂散光",
                    feature_ids=["f1"],
                    source_reference="说明书[0010]",
                    rationale="目标技术效果",
                ),
            ],
        ),
    )
    d1 = _comparison("D1", [], alignment=0.8).model_copy(
        update={
            "disclosures": [
                _disclosure(
                    "f1", status=DisclosureStatus.NOT_DISCLOSED, confidence=0.9
                ).model_copy(
                    update={
                        "feature_text": "传感器位于密闭壳体内",
                        "reasoning": "D1 的传感器位于开放支架",
                        "target_structural_role": "传感器位于密闭壳体内承担检测角色",
                        "mapping_basis": "none",
                    }
                )
            ],
            "analysis_rule_version": I4S_RULE_VERSION,
        }
    )
    candidate = _comparison("D2", ["f1"], alignment=0.9).model_copy(
        update={
            "disclosures": [
                _disclosure("f1").model_copy(
                    update={
                        "feature_text": "传感器位于密闭壳体内",
                        "target_structural_role": "传感器位于密闭壳体内承担检测角色",
                        "reference_structure_mapping": "探测器安装在封闭腔体中",
                        "mapping_basis": "role_and_relation",
                        "structural_evidence": ["探测器安装在封闭腔体中"],
                        "reasoning": "封闭腔体隔绝外部杂散光",
                    }
                )
            ],
            "analysis_rule_version": I4S_RULE_VERSION,
        }
    )
    differences = build_distinguishing_features(
        limitations=limitations,
        d1_comparison=d1,
        invention_search_profile=profile,
    )
    decision = evaluate_existing_corpus_gap_coverage(
        differences=differences,
        comparisons=[d1, candidate],
        date_qualifications={
            "D1": {"inventive_step_eligible": True},
            "D2": {"inventive_step_eligible": True},
        },
        reuse_keys={
            "D2": ExistingCorpusReuseKey(
                document_version_id="dv2",
                content_sha256="a" * 64,
                limitation_version_id="lv1",
                date_qualification_revision="dq2",
                i4s_run_id="i4s2",
                i4s_rule_version=I4S_RULE_VERSION,
            )
        },
        gap_search_iteration=1,
    )
    assert decision.search_required is False
    assert decision.stop_reason == "all_differences_covered"
    assert decision.covered_difference_feature_ids == ["f1"]

    stale = evaluate_existing_corpus_gap_coverage(
        differences=differences,
        comparisons=[candidate],
        date_qualifications={"D2": {"inventive_step_eligible": True}},
        reuse_keys={
            "D2": {
                "document_version_id": "dv2",
                "content_sha256": "a" * 64,
                "limitation_version_id": "lv1",
                "date_qualification_revision": "dq2",
                "i4s_run_id": "i4s2",
                "i4s_rule_version": "old-rule",
            }
        },
        gap_search_iteration=5,
        iteration_completed=True,
    )
    assert stale.gap_search_exhausted is True
    assert stale.stop_reason == "gap_search_exhausted"
    assert stale.uncovered_difference_feature_ids == ["f1"]

    serialized_null = evaluate_existing_corpus_gap_coverage(
        differences=differences,
        comparisons=[d1, candidate],
        date_qualifications={"D2": {"inventive_step_eligible": True}},
        reuse_keys={
            "D2": {
                "document_version_id": "None",
                "content_sha256": "a" * 64,
                "limitation_version_id": "lv1",
                "date_qualification_revision": "None",
                "i4s_run_id": "i4s2",
                "i4s_rule_version": I4S_RULE_VERSION,
            }
        },
        gap_search_iteration=1,
    )
    assert serialized_null.search_required is True
    assert serialized_null.covered_difference_feature_ids == []
    assert serialized_null.uncovered_difference_feature_ids == ["f1"]
    assert any(
        "缺少可复用版本键" in item.rationale
        for item in serialized_null.coverage_evidence
    )


def test_large_claim_chart_selects_d1_plus_top_five_by_gap_increment() -> None:
    limitations = [_feature(f"f{i}", i, f"特征{i}") for i in range(1, 8)]
    comparisons = [
        _comparison("D1", ["f1"]),
        _comparison("D2", ["f2", "f3"]),
        _comparison("D3", ["f3", "f4"]),
        _comparison("D4", ["f5"]),
        _comparison("D5", ["f6"]),
        _comparison("D6", ["f7"]),
        _comparison("D7", ["f2"]),
    ]
    qualifications = {
        item.document_id: {"inventive_step_eligible": True}
        for item in comparisons
    }
    selected = select_large_claim_chart_documents(
        limitations=limitations,
        closest_document_id="D1",
        comparisons=comparisons,
        date_qualifications=qualifications,
        difference_feature_ids=[f"f{i}" for i in range(2, 8)],
    )
    assert selected[0].document_id == "D1"
    assert len(selected) == 6
    assert selected[1].document_id == "D2"
    assert "D7" not in {item.document_id for item in selected}


def _combination_response(*, motivation_status: str) -> dict[str, Any]:
    motivation_supported = motivation_status == "supported"
    return {
        "distinguishing_feature_analysis": [
            {
                "feature_id": feature_id,
                "objective_technical_problem": "adapt the cleaner to different floor surfaces",
                "supporting_document_ids": ["D2" if feature_id == "f2" else "D3"],
                "same_role_and_effect": {
                    "status": "supported",
                    "evidence_quote": "the pad is raised when carpet is detected",
                    "evidence_location": "paragraph 12",
                    "reasoning": "the disclosed control performs the same floor-adaptation role",
                    "confidence": 0.9,
                },
                "technical_teaching": {
                    "status": "supported",
                    "evidence_quote": "D2 teaches applying its sensor to an autonomous cleaner",
                    "evidence_location": "paragraph 8",
                    "reasoning": "the source expressly teaches application to the D1 device",
                    "confidence": 0.9,
                },
                "modification_motivation": {
                    "status": motivation_status,
                    "evidence_quote": (
                        "D2 teaches applying its sensor to an autonomous cleaner"
                        if motivation_supported
                        else ""
                    ),
                    "evidence_location": "paragraph 8" if motivation_supported else "",
                    "reasoning": (
                        "explicit cross-application teaching"
                        if motivation_supported
                        else "no source teaches the modification"
                    ),
                    "confidence": 0.9,
                },
                "modification_path": "apply the disclosed sensor and lifting control to D1",
                "teaching_away": {
                    "status": "absent",
                    "reasoning": "the operating conditions do not conflict",
                    "confidence": 0.85,
                },
                "technical_effect": {
                    "status": "predictable",
                    "evidence_quote": "the pad is raised when carpet is detected",
                    "evidence_location": "paragraph 12",
                    "reasoning": "the stated effect follows from the disclosed control",
                    "confidence": 0.9,
                },
            }
            for feature_id in ("f2", "f3", "f4")
        ],
        "related_technical_problem": {
            "status": "same_or_related",
            "evidence_quote": "both address cleaning on different floor surfaces",
            "evidence_location": "D1 paragraph 10; D2 paragraph 4",
            "reasoning": "related floor-cleaning problem",
            "confidence": 0.9,
        },
        "combination_motivation": {
            "status": motivation_status,
            "evidence_quote": "D2 teaches applying its sensor to an autonomous cleaner"
            if motivation_supported
            else "",
            "evidence_location": "D2 paragraph 8" if motivation_supported else "",
            "reasoning": "explicit cross-application teaching"
            if motivation_supported
            else "no source teaches the combination",
            "confidence": 0.9,
        },
        "teaching_away": {
            "status": "absent",
            "evidence_quote": "",
            "evidence_location": "",
            "reasoning": "the disclosed operating conditions do not conflict",
            "confidence": 0.85,
        },
        "technical_effect": {
            "status": "predictable",
            "evidence_quote": "the pad is raised when carpet is detected",
            "evidence_location": "D2 paragraph 12",
            "reasoning": "the stated effect follows from the disclosed control",
            "confidence": 0.9,
        },
    }


def _combination_document_texts() -> dict[str, str]:
    return {
        "D1": "Both address cleaning on different floor surfaces.",
        "D2": (
            "D2 teaches applying its sensor to an autonomous cleaner. "
            "The pad is raised when carpet is detected."
        ),
    }


def test_i4i_combined_coverage_alone_is_not_complete_and_creates_reason_gap() -> None:
    limitations = [
        _feature("f1", 1, "移动底盘"),
        _feature("f2", 2, "清洁件相对底盘升降"),
    ]
    client = FakeVisionClient(_combination_response(motivation_status="not_supported"))
    result = InvalidityAnalysisEngine(client).analyze_inventive_step(
        limitations=limitations,
        closest_document_id="D1",
        comparisons=[_comparison("D1", ["f1"]), _comparison("D2", ["f2"])],
        date_qualifications={
            "D1": {"inventive_step_eligible": True},
            "D2": {"inventive_step_eligible": True},
        },
        expanded_claim_text="展开权利要求文本",
        target_images=["target.png"],
        document_texts=_combination_document_texts(),
        document_images={"D1": ["d1.png"], "D2": ["d2.png"]},
    )

    assert result.combined_feature_coverage_complete is True
    assert result.evidence_complete is False
    assert "combination_gap" in {item.kind for item in result.gaps}
    assert "combination_motivation" in {item.subtype for item in result.gaps}
    assert client.calls[0]["require_document_image"] is True
    assert len(client.calls[0]["document_images"]) == 2


def test_i4i_complete_requires_motivation_teaching_away_and_effect_checks() -> None:
    limitations = [
        _feature("f1", 1, "移动底盘"),
        _feature("f2", 2, "清洁件相对底盘升降"),
    ]
    result = InvalidityAnalysisEngine(
        FakeVisionClient(_combination_response(motivation_status="supported"))
    ).analyze_inventive_step(
        limitations=limitations,
        closest_document_id="D1",
        comparisons=[_comparison("D1", ["f1"]), _comparison("D2", ["f2"])],
        date_qualifications={
            "D1": {"inventive_step_eligible": True},
            "D2": {"inventive_step_eligible": True},
        },
        expanded_claim_text="展开权利要求文本",
        target_images=["target.png"],
        document_texts=_combination_document_texts(),
        document_images={"D1": ["d1.png"], "D2": ["d2.png"]},
    )

    assert result.evidence_complete is True
    assert result.gaps == []
    assert result.conclusion == "lack_of_inventive_step_evidence_complete"
    assert result.distinguishing_feature_analysis[0].evidence_chain_complete is True


def test_i4i_one_difference_with_teaching_away_cannot_be_closed_by_global_summary() -> None:
    limitations = [
        _feature("f1", 1, "移动底盘"),
        _feature("f2", 2, "清洁件相对底盘升降"),
    ]
    response = _combination_response(motivation_status="supported")
    response["distinguishing_feature_analysis"][0]["teaching_away"] = {
        "status": "present",
        "evidence_quote": "",
        "evidence_location": "",
        "reasoning": "D2 requires keeping the pad fixed during carpet travel",
        "confidence": 0.9,
    }
    result = InvalidityAnalysisEngine(FakeVisionClient(response)).analyze_inventive_step(
        limitations=limitations,
        closest_document_id="D1",
        comparisons=[_comparison("D1", ["f1"]), _comparison("D2", ["f2"])],
        date_qualifications={
            "D1": {"inventive_step_eligible": True},
            "D2": {"inventive_step_eligible": True},
        },
        expanded_claim_text="展开权利要求文本",
        target_images=["target.png"],
        document_texts=_combination_document_texts(),
        document_images={"D1": ["d1.png"], "D2": ["d2.png"]},
    )

    assert result.combined_feature_coverage_complete is True
    assert result.evidence_complete is False
    assert result.distinguishing_feature_analysis[0].evidence_chain_complete is False
    assert "反向教导尚未排除或已有相反教导" in (
        result.distinguishing_feature_analysis[0].unresolved_reasons
    )
    assert result.conclusion_text == "现有证据尚不足以证明不具备创造性"


def test_i4i_accepts_d1_plus_common_knowledge_without_fake_d2() -> None:
    limitations = [_feature("f1", 1, "耳机本体"), _feature("f2", 2, "定位卡槽与卡凸")]
    precheck = ObviousnessPrecheckAssessment.model_validate(
        {
            "closest_document_id": "D1",
            "feature_groups": [{
                "feature_group_id": "G01",
                "feature_ids": ["f2"],
                "feature_texts": ["定位卡槽与卡凸"],
                "objective_technical_problem": "转动后的多档定位",
                "d1_teaching": {"status": "supported", "reasoning": "D1给出转动定位", "confidence": 0.9},
                "routine_means": {"status": "preliminary_candidate", "reasoning": "卡槽卡凸为惯用手段候选", "confidence": 0.9},
                "modification_motivation": {"status": "supported", "reasoning": "实现多档定位", "confidence": 0.9},
                "teaching_away": {"status": "absent", "reasoning": "未见排斥", "confidence": 0.9},
                "technical_effect": {"status": "predictable", "reasoning": "效果可预期", "confidence": 0.9},
                "search_route": "search_common_knowledge_evidence",
                "ordinary_structural_search_required": False,
                "common_knowledge_confirmation_required": True,
                "reasoning": "只补公知常识证据",
            }],
            "resolved_feature_ids": [],
            "unresolved_feature_ids": ["f2"],
            "search_targets": [{
                "feature_group_id": "G01",
                "feature_ids": ["f2"],
                "route": "search_common_knowledge_evidence",
                "target_gap_type": "evidence_gap",
                "search_anchor": "卡槽 卡凸 多档定位",
                "rationale": "补公知常识证据",
            }],
            "ordinary_structural_search_required": False,
            "evidence_complete": False,
            "model": "fixture",
            "used_target_images": 1,
            "used_document_images": 1,
        }
    )
    d1_text = "Rotating positioning addresses the related problem. The locking component keeps the position stable."
    response = {
        "related_technical_problem": {
            "status": "same_or_related", "evidence_quote": "Rotating positioning",
            "evidence_location": "paragraph 1", "reasoning": "same positioning problem", "confidence": 0.9,
        },
        "combination_motivation": {
            "status": "supported", "evidence_quote": "locking component",
            "evidence_location": "paragraph 1", "reasoning": "D1 asks for positioning", "confidence": 0.9,
        },
        "teaching_away": {"status": "absent", "reasoning": "no contrary teaching", "confidence": 0.9},
        "technical_effect": {
            "status": "predictable", "evidence_quote": "keeps the position stable",
            "evidence_location": "paragraph 1", "reasoning": "predictable positioning", "confidence": 0.9,
        },
    }
    result = InvalidityAnalysisEngine(FakeVisionClient(response)).analyze_inventive_step(
        limitations=limitations,
        closest_document_id="D1",
        comparisons=[_comparison("D1", ["f1"])],
        date_qualifications={"D1": {"inventive_step_eligible": True}},
        expanded_claim_text="一种可转动定位耳机",
        target_images=["target.png"],
        document_texts={"D1": d1_text},
        document_images={"D1": ["d1.png"]},
        obviousness_precheck=precheck,
    )

    assert result.combination_document_ids == ["D1"]
    assert result.combination_basis == "d1_plus_common_knowledge"
    assert result.used_document_images == 1
    assert result.evidence_complete is False
    assert "common_knowledge_evidence" in {item.subtype for item in result.gaps}


def test_i4i_runs_with_only_persisted_d1_and_reports_gap_round_errors() -> None:
    limitations = [
        _feature("f1", 1, "移动底盘"),
        _feature("f2", 2, "清洁件相对底盘升降"),
    ]
    ledger = [
        {
            "gap_search_iteration": 2,
            "stage": "I2_GAP_QUERY_PLAN",
            "error_code": "AnalysisValidationError",
            "safe_summary": "本轮检索策略未通过守门",
            "affected_feature_ids": ["f2"],
        }
    ]
    result = InvalidityAnalysisEngine(
        FakeVisionClient(_combination_response(motivation_status="not_supported"))
    ).analyze_inventive_step(
        limitations=limitations,
        closest_document_id="D1",
        comparisons=[_comparison("D1", ["f1"])],
        date_qualifications={"D1": {"inventive_step_eligible": True}},
        expanded_claim_text="展开权利要求文本",
        target_images=["target.png"],
        document_texts={"D1": "Both address cleaning on different floor surfaces."},
        document_images={"D1": ["d1.png"]},
        upstream_gap_search_errors=ledger,
    )

    assert result.combination_document_ids == ["D1"]
    assert result.combination_basis == "unresolved"
    assert result.evidence_complete is False
    assert result.conclusion == "insufficient_evidence_to_establish_lack_of_inventive_step"
    assert result.upstream_gap_search_errors == ledger


def test_i4i_nineteen_document_batch_uses_bounded_greedy_combination() -> None:
    limitations = [
        _feature("f1", 1, "移动底盘"),
        _feature("f2", 2, "清洁件相对底盘升降"),
        _feature("f3", 3, "传感器检测地面材质"),
        _feature("f4", 4, "检测后控制清洁件"),
    ]
    comparisons = [
        _comparison("D1", ["f1"]),
        _comparison("D2", ["f2"]),
        _comparison("D3", ["f3", "f4"]),
        *[_comparison(f"D{index}", []) for index in range(4, 20)],
    ]
    document_texts = {
        item.document_id: (
            "Both address cleaning on different floor surfaces. "
            "D2 teaches applying its sensor to an autonomous cleaner. "
            "The pad is raised when carpet is detected."
        )
        for item in comparisons
    }
    document_images = {
        item.document_id: [
            f"{item.document_id}-page-1.png",
            f"{item.document_id}-page-2.png",
            f"{item.document_id}-page-3.png",
        ]
        for item in comparisons
    }
    date_qualifications = {
        item.document_id: {"inventive_step_eligible": True}
        for item in comparisons
    }
    client = FakeVisionClient(
        _combination_response(motivation_status="supported")
    )

    result = InvalidityAnalysisEngine(client).analyze_inventive_step(
        limitations=limitations,
        closest_document_id="D1",
        comparisons=comparisons,
        date_qualifications=date_qualifications,
        expanded_claim_text="展开权利要求文本",
        target_images=["target.png"],
        document_texts=document_texts,
        document_images=document_images,
    )

    assert result.combination_document_ids == ["D1", "D3", "D2"]
    assert result.combined_feature_coverage_complete is True
    assert result.used_document_images == 6
    assert client.calls[0]["document_images"] == [
        "D1-page-1.png",
        "D1-page-2.png",
        "D3-page-1.png",
        "D3-page-2.png",
        "D2-page-1.png",
        "D2-page-2.png",
    ]
    assert '"D19"' in client.calls[0]["prompt"]
    assert len(result.considered_document_ids) == 19


# ---------------------------------------------------------------------------
# I4-S v46/v23：实质相同标准 prompt 强化 + 两道确定性守门
# ---------------------------------------------------------------------------


def _guard_disclosure(
    feature_id: str,
    status: DisclosureStatus,
    *,
    feature_text: str = "",
    reasoning: str = "",
    structural_search_summary: str = "",
    reference_structure_mapping: str = "",
    target_structural_role: str = "",
    structural_evidence: list[str] | None = None,
    alternative_path_analysis: str = "",
) -> FeatureDisclosure:
    return FeatureDisclosure(
        feature_id=feature_id,
        feature_text=feature_text,
        status=status,
        reasoning=reasoning,
        confidence=0.9,
        target_structural_role=target_structural_role,
        reference_structure_mapping=reference_structure_mapping,
        structural_evidence=structural_evidence or [],
        structural_search_summary=structural_search_summary,
        alternative_path_analysis=alternative_path_analysis,
    )


def _feature_with_synonyms(
    feature_id: str,
    sequence: int,
    text: str,
    *,
    synonyms_en: list[str] | None = None,
    synonyms_zh: list[str] | None = None,
) -> Limitation:
    return Limitation(
        feature_id=feature_id,
        claim_id="claim-1",
        sequence=sequence,
        text=text,
        mandatory=True,
        technical_subject="水枪",
        synonyms_en=synonyms_en or [],
        synonyms_zh=synonyms_zh or [],
    )


def test_i4s_rule_and_prompt_versions_v46_v23() -> None:
    assert I4S_RULE_VERSION == "structural-disclosure-v50"
    assert I4S_PROMPT_VERSION == "i4-s-structural-v27"


def test_i4s_prompts_include_substantial_identity_standard() -> None:
    payload = {
        "claim_id": "claim-1",
        "expanded_claim_text": "一种水枪，包括压力罐。",
        "limitations": [_feature("f-tank", 1, "压力罐")],
        "target_patent_text": _target_patent_text(),
        "document_id": "D-PROMPT",
        "document_text": "对比文件全文",
    }
    first_pass = InvalidityAnalysisEngine._single_reference_prompt(**payload)
    review = InvalidityAnalysisEngine._structural_reconciliation_prompt(
        **payload,
        target_mechanism_summary="",
        reference_mechanism_summary="",
        first_pass_disclosures=[],
        immutable_positive_disclosures=[],
    )
    for prompt in (first_pass, review):
        assert "实质相同" in prompt
        assert "结构和位置关系与目标专利没有不同" in prompt
        assert "钉子承担" in prompt
        assert "不放宽必然隐含披露" in prompt
        assert "候选构件" in prompt
        assert "逐一" in prompt


def test_negation_guard_trigger_conditions() -> None:
    assert _negation_guard_triggered(
        _guard_disclosure("f1", DisclosureStatus.NOT_DISCLOSED)
    )
    assert _negation_guard_triggered(
        _guard_disclosure(
            "f1",
            DisclosureStatus.UNCERTAIN,
            reasoning="对比文件未提及该结构",
        )
    )
    assert not _negation_guard_triggered(
        _guard_disclosure(
            "f1",
            DisclosureStatus.UNCERTAIN,
            reasoning="附图不完整，无法确认",
        )
    )
    assert not _negation_guard_triggered(
        _guard_disclosure(
            "f1",
            DisclosureStatus.EXPLICIT,
            reasoning="未提及也不影响正向映射",
        )
    )


def test_negation_guard_candidates_sources_and_cap() -> None:
    limitation = _feature_with_synonyms(
        "f-tank",
        1,
        "压力罐",
        synonyms_en=["reservoir"],
        synonyms_zh=["储液罐"],
    )
    disclosure = _guard_disclosure(
        "f-tank",
        DisclosureStatus.NOT_DISCLOSED,
        feature_text="压力罐",
        target_structural_role="承压囊",
    )
    other = _guard_disclosure(
        "f-valve",
        DisclosureStatus.EXPLICIT,
        feature_text="阀杆",
        reference_structure_mapping="弹性膜片",
    )
    candidates = _negation_guard_candidates(
        limitation=limitation,
        disclosure=disclosure,
        disclosures=[disclosure, other],
        reference_mechanism_summary="柔性囊袋",
    )
    normalised = [_normalise(item) for item in candidates]
    # ① 特征核心名词 ② 绑定同义词 ③ 角色词 ④ 其他特征映射 ⑤ 机构总结
    assert "压力罐" in normalised
    assert "reservoir" in normalised
    assert "储液罐" in normalised
    assert "承压囊" in normalised
    assert "弹性膜片" in normalised
    assert "柔性囊袋" in normalised
    assert len(candidates) <= 8
    # 有序并集：特征核心名词先于同义词
    assert normalised.index("压力罐") < normalised.index("reservoir")


def test_negation_guard_unhandled_candidates() -> None:
    disclosure = _guard_disclosure(
        "f-tank",
        DisclosureStatus.NOT_DISCLOSED,
        feature_text="压力罐",
        reasoning="候选 reservoir 仅靠重力供液，不承担承压角色",
        structural_search_summary="已排查 reservoir",
    )
    unhandled = _negation_guard_unhandled_candidates(
        disclosure,
        ["压力罐", "reservoir", "spring"],
        "The reservoir stores liquid and a spring drives the valve.",
    )
    # 压力罐不在全文；reservoir 已被处理；spring 在全文且未处理
    assert unhandled == ["spring"]


def test_i4s_negation_guard_reviews_candidates_and_keeps_supported_negative() -> None:
    limitations = [
        _feature_with_synonyms("f-tank", 1, "压力罐", synonyms_en=["reservoir"])
    ]
    client = FakeVisionClient(
        {
            "disclosures": [
                {
                    "feature_id": "f-tank",
                    "status": "not_disclosed",
                    "reasoning": (
                        "已检查全部储液、加压和排液结构，均没有承担受压储液并"
                        "向喷射流路供液的构件，因此未披露该结构角色"
                    ),
                    "confidence": 1.0,
                    "mapping_basis": "none",
                    "structural_search_summary": (
                        "复核储液腔、泵和全部排液流路；储液腔仅靠重力供液，"
                        "不存在承压储液候选结构"
                    ),
                }
            ]
        },
        # 守门A候选复核：模型逐一处理 reservoir 后维持合规否定
        {
            "disclosures": [
                {
                    "feature_id": "f-tank",
                    "status": "not_disclosed",
                    "reasoning": (
                        "候选 reservoir 仅靠重力供液，不承担受压储液并排液"
                        "角色，连接拓扑不同"
                    ),
                    "confidence": 0.9,
                    "mapping_basis": "none",
                    "structural_search_summary": (
                        "已逐一排查候选 reservoir：其接入重力供液路径，未接入"
                        "受压排液路径，路径与拓扑不同"
                    ),
                    "role_candidates_evaluated": [
                        {
                            "candidate": "reservoir",
                            "source": "deterministic",
                            "verdict": "不承担受压储液角色",
                            "reason": "仅靠重力供液，路径与拓扑不同",
                        },
                        {
                            "candidate": "全文角色重扫",
                            "source": "full_text_rescan",
                            "verdict": "未发现候选",
                            "reason": "已围绕受压储液角色搜索全文，无遗漏候选",
                        },
                    ],
                }
            ]
        },
    )
    comparison = InvalidityAnalysisEngine(client).compare_single_reference(
        claim_id="claim-1",
        expanded_claim_text="一种水枪，包括压力罐。",
        target_patent_text=_target_patent_text(),
        limitations=limitations,
        document_id="D-TANK",
        document_text=(
            "The reservoir stores liquid under gravity and a flexible bladder "
            "feeds the outlet passage."
        ),
        target_images=["target.png"],
        document_images=["d1.png"],
    )

    disclosure = comparison.disclosures[0]
    assert disclosure.status is DisclosureStatus.NOT_DISCLOSED
    assert disclosure.guard_original_status == ""
    assert comparison.negation_guard_attempted is True
    assert comparison.negation_guard_performed is True
    assert comparison.negation_guard_downgraded == []
    assert comparison.guard_warnings == []
    assert len(client.calls) == 2
    assert "reservoir" in client.calls[1]["prompt"]
    assert "候选" in client.calls[1]["prompt"]
    assert client.calls[1]["require_document_image"] is False
    assert client.calls[1]["allow_text_only"] is True


def test_i4s_negation_guard_downgrades_still_unhandled_candidates() -> None:
    limitations = [
        _feature_with_synonyms("f-tank", 1, "压力罐", synonyms_en=["reservoir"])
    ]
    client = FakeVisionClient(
        {
            "disclosures": [
                {
                    "feature_id": "f-tank",
                    "status": "not_disclosed",
                    "reasoning": (
                        "已检查全部储液、加压和排液结构，均没有承担受压储液并"
                        "向喷射流路供液的构件，因此未披露该结构角色"
                    ),
                    "confidence": 1.0,
                    "mapping_basis": "none",
                    "structural_search_summary": (
                        "复核储液腔、泵和全部排液流路；储液腔仅靠重力供液，"
                        "不存在承压储液候选结构"
                    ),
                }
            ]
        },
        # 守门A候选复核：模型维持否定但仍未处理 reservoir
        {
            "disclosures": [
                {
                    "feature_id": "f-tank",
                    "status": "not_disclosed",
                    "reasoning": (
                        "对比文件储液结构仅靠重力供液，与受压排液路径的"
                        "拓扑不同"
                    ),
                    "confidence": 0.9,
                    "mapping_basis": "none",
                    "structural_search_summary": (
                        "已检查储液腔与排液路径，未找到承压结构"
                    ),
                }
            ]
        },
    )
    comparison = InvalidityAnalysisEngine(client).compare_single_reference(
        claim_id="claim-1",
        expanded_claim_text="一种水枪，包括压力罐。",
        target_patent_text=_target_patent_text(),
        limitations=limitations,
        document_id="D-TANK",
        document_text=(
            "The reservoir stores liquid under gravity and a flexible bladder "
            "feeds the outlet passage."
        ),
        target_images=["target.png"],
        document_images=["d1.png"],
    )

    disclosure = comparison.disclosures[0]
    assert disclosure.status is DisclosureStatus.UNCERTAIN
    assert disclosure.confidence <= 0.69
    # 降级留痕：原模型结论不被静默覆盖
    assert disclosure.guard_original_status == "not_disclosed"
    assert "reservoir" in disclosure.guard_note
    assert "候选构件" in disclosure.reasoning
    assert comparison.negation_guard_attempted is True
    assert comparison.negation_guard_performed is True
    assert comparison.negation_guard_downgraded == ["f-tank"]
    assert any(
        "f-tank" in warning and "reservoir" in warning
        for warning in comparison.guard_warnings
    )
    assert len(client.calls) == 2


def test_i4s_negation_guard_not_triggered_when_candidates_handled() -> None:
    limitations = [
        _feature_with_synonyms("f-tank", 1, "压力罐", synonyms_en=["reservoir"])
    ]
    client = FakeVisionClient(
        {
            "disclosures": [
                {
                    "feature_id": "f-tank",
                    "status": "not_disclosed",
                    # 有可回溯负向引文且候选已逐一处理：两道触发都不命中
                    "evidence_quote": "The reservoir stores liquid under gravity",
                    "evidence_location": "paragraph 3",
                    "reasoning": (
                        "已检查全部储液、加压和排液结构，均没有承担受压储液并"
                        "向喷射流路供液的构件，因此未披露该结构角色"
                    ),
                    "confidence": 1.0,
                    "mapping_basis": "none",
                    "structural_search_summary": (
                        "复核储液腔、泵、reservoir 和全部排液流路；reservoir "
                        "仅靠重力供液，不存在承压储液候选结构"
                    ),
                }
            ]
        }
    )
    comparison = InvalidityAnalysisEngine(client).compare_single_reference(
        claim_id="claim-1",
        expanded_claim_text="一种水枪，包括压力罐。",
        target_patent_text=_target_patent_text(),
        limitations=limitations,
        document_id="D-TANK",
        document_text=(
            "The reservoir stores liquid under gravity and a flexible bladder "
            "feeds the outlet passage."
        ),
        target_images=["target.png"],
        document_images=["d1.png"],
    )

    assert comparison.disclosures[0].status is DisclosureStatus.NOT_DISCLOSED
    assert comparison.negation_guard_attempted is False
    assert comparison.negation_guard_performed is False
    assert comparison.negation_guard_downgraded == []
    assert comparison.guard_warnings == []
    assert len(client.calls) == 1


def test_consistency_contradictions_detects_both_patterns() -> None:
    parent = _guard_disclosure(
        "f-parent",
        DisclosureStatus.EXPLICIT,
        feature_text="锁定件",
        reasoning="锁定件40与卡槽配合承担锁定角色",
        reference_structure_mapping="锁定件40与卡槽配合实现锁定",
    )
    child = _guard_disclosure(
        "f-child",
        DisclosureStatus.NOT_DISCLOSED,
        feature_text="锁定件具有释放斜面",
        reasoning="对比文件无锁定件结构，未提及锁定件",
        structural_search_summary="未找到锁定件",
    )
    spring = _guard_disclosure(
        "f-spring",
        DisclosureStatus.NOT_DISCLOSED,
        feature_text="复位弹簧",
        reasoning="全文未提及复位弹簧",
        structural_search_summary="未找到复位弹簧",
    )
    contradictions = _consistency_contradictions(
        [parent, child, spring],
        "执行器通过复位弹簧驱动阀体",
    )
    kinds = {(item["kind"], item["negative_id"]) for item in contradictions}
    # 矛盾模式一（父子）：父披露共享构件、子以「无该构件」否定
    assert ("parent_child", "f-child") in kinds
    # 矛盾模式二（机构总结）：机构总结出现的构件被判「全文未提及」
    assert ("mechanism_summary", "f-spring") in kinds


def test_cluster_consistency_contradictions_merges_related_pairs() -> None:
    clusters = _cluster_consistency_contradictions(
        [
            {
                "kind": "parent_child",
                "positive_id": "f1",
                "negative_id": "f2",
                "note": "",
            },
            {
                "kind": "cross_feature",
                "positive_id": "f2",
                "negative_id": "f3",
                "note": "",
            },
            {
                "kind": "mechanism_summary",
                "positive_id": "",
                "negative_id": "f4",
                "note": "",
            },
        ]
    )
    normalised = sorted(sorted(cluster) for cluster in clusters)
    assert normalised == [["f1", "f2", "f3"], ["f4"]]


def test_i4s_consistency_guard_merged_rejudgment_resolves_contradiction() -> None:
    limitations = [
        _feature("f-lock", 1, "锁定件"),
        _feature("f-lock-detail", 2, "锁定件具有释放斜面"),
        _feature("f-roller", 3, "包括滚轮"),
    ]
    client = FakeVisionClient(
        {
            "disclosures": [
                {
                    "feature_id": "f-lock",
                    "status": "explicit",
                    "evidence_quote": "the locking member 40 engages the slot",
                    "evidence_location": "paragraph 9",
                    "reasoning": "锁定件40与卡槽配合，承担锁定件角色",
                    "confidence": 0.9,
                    "target_structural_role": "锁定件",
                    "reference_structure_mapping": "锁定件40与卡槽配合实现锁定",
                    "mapping_basis": "role_and_relation",
                    "structural_evidence": [],
                },
                {
                    "feature_id": "f-lock-detail",
                    "status": "not_disclosed",
                    "reasoning": (
                        "候选调节盘固定安装，与锁定件的卡接拓扑不同；"
                        "无锁定件结构，未提及锁定件"
                    ),
                    "confidence": 0.9,
                    "mapping_basis": "none",
                    "structural_search_summary": (
                        "已检查调节盘与壳体，连接拓扑不同，未找到锁定件"
                    ),
                },
                {
                    "feature_id": "f-roller",
                    "status": "uncertain",
                    "reasoning": "附图不完整，无法确认滚轮",
                    "confidence": 0.5,
                    "mapping_basis": "uncertain",
                },
            ]
        },
        # 守门A空证据复核：模型记录排查过程并维持否定（矛盾仍在）
        {
            "disclosures": [
                {
                    "feature_id": "f-lock-detail",
                    "status": "not_disclosed",
                    "reasoning": (
                        "候选调节盘固定安装，与锁定件的卡接拓扑不同；"
                        "无锁定件结构，未提及锁定件"
                    ),
                    "confidence": 0.9,
                    "mapping_basis": "none",
                    "structural_search_summary": (
                        "已检查调节盘与壳体，连接拓扑不同，未找到锁定件"
                    ),
                    "alternative_path_analysis": (
                        "已按角色语义在对比文件全文搜索锁定件候选并逐条排除"
                    ),
                    "role_candidates_evaluated": [
                        {
                            "candidate": "调节盘",
                            "source": "deterministic",
                            "verdict": "不承担锁定件角色",
                            "reason": "固定安装，卡接拓扑不同",
                        },
                        {
                            "candidate": "全文角色重扫",
                            "source": "full_text_rescan",
                            "verdict": "无其他承担锁定件角色的候选",
                            "reason": "已按角色语义搜索全文，无遗漏候选",
                        },
                    ],
                }
            ]
        },
        # 守门B合并重判：矛盾双方放同一上下文，子特征改判不确定
        {
            "disclosures": [
                {
                    "feature_id": "f-lock-detail",
                    "status": "uncertain",
                    "reasoning": "锁定件已映射，但释放斜面在附图中无法核验",
                    "confidence": 0.6,
                    "mapping_basis": "uncertain",
                    "structural_search_summary": "释放斜面无法核验",
                }
            ]
        },
    )
    comparison = InvalidityAnalysisEngine(client).compare_single_reference(
        claim_id="claim-1",
        expanded_claim_text="一种装置，包括锁定件，锁定件具有释放斜面和滚轮。",
        target_patent_text=_target_patent_text(),
        limitations=limitations,
        document_id="D-LOCK",
        document_text="paragraph 9 the locking member 40 engages the slot",
        target_images=["target.png"],
        document_images=["d1.png"],
    )

    by_id = {item.feature_id: item for item in comparison.disclosures}
    assert by_id["f-lock"].status is DisclosureStatus.EXPLICIT
    assert by_id["f-lock-detail"].status is DisclosureStatus.UNCERTAIN
    assert by_id["f-lock-detail"].guard_original_status == ""
    assert comparison.negation_guard_attempted is True
    assert comparison.negation_guard_performed is True
    assert comparison.negation_guard_downgraded == []
    assert comparison.consistency_guard_attempted is True
    assert comparison.consistency_guard_performed is True
    assert comparison.consistency_guard_clusters == 1
    assert comparison.consistency_guard_downgraded == []
    assert comparison.guard_warnings == []
    assert len(client.calls) == 3
    assert "合并重判" in client.calls[2]["prompt"]
    assert client.calls[1]["allow_text_only"] is True
    assert client.calls[2]["allow_text_only"] is True
    assert client.calls[1]["require_document_image"] is False


def test_i4s_consistency_guard_downgrades_persistent_contradiction() -> None:
    limitations = [_feature("f-spring", 1, "复位弹簧")]
    client = FakeVisionClient(
        {
            "reference_mechanism_summary": "执行器通过复位弹簧驱动阀体",
            "disclosures": [
                {
                    "feature_id": "f-spring",
                    "status": "not_disclosed",
                    "reasoning": (
                        "对比文件执行器为凸轮直驱，与弹簧复位路径的连接拓扑"
                        "不同；全文未提及复位弹簧"
                    ),
                    "confidence": 0.9,
                    "mapping_basis": "none",
                    "structural_search_summary": (
                        "已检查执行器与全部驱动链，候选凸轮连接拓扑不同，"
                        "未找到复位弹簧"
                    ),
                }
            ]
        },
        # 守门A空证据复核：模型记录排查过程并维持否定（矛盾仍在）
        {
            "disclosures": [
                {
                    "feature_id": "f-spring",
                    "status": "not_disclosed",
                    "reasoning": (
                        "对比文件执行器为凸轮直驱，与弹簧复位路径的连接拓扑"
                        "不同；全文未提及复位弹簧"
                    ),
                    "confidence": 0.9,
                    "mapping_basis": "none",
                    "structural_search_summary": (
                        "已检查执行器与全部驱动链，候选凸轮连接拓扑不同，"
                        "未找到复位弹簧"
                    ),
                    "alternative_path_analysis": (
                        "已按角色语义在对比文件全文搜索弹性复位候选并逐条排除"
                    ),
                    "role_candidates_evaluated": [
                        {
                            "candidate": "凸轮",
                            "source": "deterministic",
                            "verdict": "不承担弹性复位角色",
                            "reason": "凸轮直驱，复位路径拓扑不同",
                        },
                        {
                            "candidate": "全文角色重扫",
                            "source": "full_text_rescan",
                            "verdict": "无其他承担弹性复位角色的候选",
                            "reason": "已按角色语义搜索全文，无遗漏候选",
                        },
                    ],
                }
            ]
        },
        # 守门B合并重判仍给出同样的矛盾否定
        {
            "disclosures": [
                {
                    "feature_id": "f-spring",
                    "status": "not_disclosed",
                    "reasoning": (
                        "候选凸轮直驱与弹性复位路径拓扑不同，仍未提及复位弹簧"
                    ),
                    "confidence": 0.9,
                    "mapping_basis": "none",
                    "structural_search_summary": (
                        "已复核执行器驱动链，连接拓扑不同，未找到复位弹簧"
                    ),
                }
            ]
        },
    )
    comparison = InvalidityAnalysisEngine(client).compare_single_reference(
        claim_id="claim-1",
        expanded_claim_text="一种装置，包括复位弹簧。",
        target_patent_text=_target_patent_text(),
        limitations=limitations,
        document_id="D-SPRING",
        document_text="the cam actuator drives the valve body directly",
        target_images=["target.png"],
        document_images=["d1.png"],
    )

    disclosure = comparison.disclosures[0]
    assert disclosure.status is DisclosureStatus.UNCERTAIN
    assert disclosure.guard_original_status == "not_disclosed"
    assert "合并重判后仍矛盾" in disclosure.guard_note
    assert comparison.consistency_guard_attempted is True
    assert comparison.consistency_guard_performed is True
    assert comparison.consistency_guard_clusters == 1
    assert comparison.consistency_guard_downgraded == ["f-spring"]
    assert any("f-spring" in warning for warning in comparison.guard_warnings)
    assert len(client.calls) == 3


def test_i4s_consistency_guard_cluster_cap() -> None:
    limitations = [
        _feature("f-bracket", 1, "支架"),
        _feature("f-rail", 2, "导轨"),
        _feature("f-baffle", 3, "挡板"),
        _feature("f-rod", 4, "连杆"),
    ]

    def negative(feature_id: str, noun: str) -> dict[str, Any]:
        return {
            "feature_id": feature_id,
            "status": "not_disclosed",
            "reasoning": (
                f"对比文件为整体铸件，与分体{noun}的连接拓扑不同；"
                f"全文未提及{noun}"
            ),
            "confidence": 0.9,
            "mapping_basis": "none",
            "structural_search_summary": (
                f"已检查全部承载结构，候选整体铸件拓扑不同，未找到{noun}"
            ),
        }

    def reviewed_negative(feature_id: str, noun: str) -> dict[str, Any]:
        # 守门A空证据复核：模型记录排查过程并维持否定（矛盾仍在）
        return {
            **negative(feature_id, noun),
            "alternative_path_analysis": (
                f"已按角色语义在对比文件全文搜索承担{noun}角色的候选并逐条排除"
            ),
            "role_candidates_evaluated": [
                {
                    "candidate": "整体铸件",
                    "source": "deterministic",
                    "verdict": f"不承担{noun}角色",
                    "reason": "整体铸造，分体连接拓扑不同",
                },
                {
                    "candidate": "全文角色重扫",
                    "source": "full_text_rescan",
                    "verdict": f"无其他承担{noun}角色的候选",
                    "reason": "已按角色语义搜索全文，无遗漏候选",
                },
            ],
        }

    client = FakeVisionClient(
        {
            "reference_mechanism_summary": "机构通过支架、导轨、挡板和连杆协同工作",
            "disclosures": [
                negative("f-bracket", "支架"),
                negative("f-rail", "导轨"),
                negative("f-baffle", "挡板"),
                negative("f-rod", "连杆"),
            ],
        },
        # 守门A空证据复核：四个空证据否定一次复核，均记录排查过程并维持
        {
            "disclosures": [
                reviewed_negative("f-bracket", "支架"),
                reviewed_negative("f-rail", "导轨"),
                reviewed_negative("f-baffle", "挡板"),
                reviewed_negative("f-rod", "连杆"),
            ]
        },
        # 簇上限 3：只有前三个簇各获得一次合并重判，且重判仍矛盾
        {"disclosures": [negative("f-bracket", "支架")]},
        {"disclosures": [negative("f-rail", "导轨")]},
        {"disclosures": [negative("f-baffle", "挡板")]},
    )
    comparison = InvalidityAnalysisEngine(client).compare_single_reference(
        claim_id="claim-1",
        expanded_claim_text="一种装置，包括支架、导轨、挡板和连杆。",
        target_patent_text=_target_patent_text(),
        limitations=limitations,
        document_id="D-FRAME",
        document_text="the housing is a single cast body",
        target_images=["target.png"],
        document_images=["d1.png"],
    )

    assert comparison.consistency_guard_attempted is True
    assert comparison.consistency_guard_performed is True
    assert comparison.consistency_guard_clusters == 4
    # 首轮 1 次 + 守门A空证据复核 1 次 + 前三个簇各 1 次合并重判；第四簇不获模型调用
    assert len(client.calls) == 5
    # 重判后仍矛盾（含超出上限未获重判的簇）的否定侧全部降级
    assert comparison.consistency_guard_downgraded == [
        "f-bracket",
        "f-rail",
        "f-baffle",
        "f-rod",
    ]
    assert all(
        item.status is DisclosureStatus.UNCERTAIN
        for item in comparison.disclosures
    )
    assert all(
        item.guard_original_status == "not_disclosed"
        for item in comparison.disclosures
    )
    assert len(comparison.guard_warnings) == 4


# ---------------------------------------------------------------------------
# I4-S 守门复核回归：allow_text_only 放行、告警文案、空证据盲区
# ---------------------------------------------------------------------------


def test_i4s_guard_reviews_pass_allow_text_only_through_fail_closed_client() -> None:
    """问题1回归：守门复核是纯文本调用，必须显式 allow_text_only 放行。"""

    limitations = [
        _feature_with_synonyms("f-tank", 1, "压力罐", synonyms_en=["reservoir"])
    ]
    client = RejectTextOnlyWithoutAllowanceClient(
        {
            "disclosures": [
                {
                    "feature_id": "f-tank",
                    "status": "not_disclosed",
                    "reasoning": (
                        "已检查全部储液、加压和排液结构，均没有承担受压储液并"
                        "向喷射流路供液的构件，因此未披露该结构角色"
                    ),
                    "confidence": 1.0,
                    "mapping_basis": "none",
                    "structural_search_summary": (
                        "复核储液腔、泵和全部排液流路；储液腔仅靠重力供液，"
                        "不存在承压储液候选结构"
                    ),
                }
            ]
        },
        {
            "disclosures": [
                {
                    "feature_id": "f-tank",
                    "status": "not_disclosed",
                    "reasoning": (
                        "已按实质相同标准复核候选构件 reservoir：其仅靠重力供液，"
                        "结构与位置关系实质不同，不承担受压储液的目标角色"
                    ),
                    "confidence": 0.7,
                    "mapping_basis": "none",
                    "structural_search_summary": (
                        "已逐一排查候选构件 reservoir 及全部储液结构，连接拓扑不同"
                    ),
                    "alternative_path_analysis": (
                        "已按角色语义在对比文件全文（含英文段落）搜索承担该角色的"
                        "候选构件并逐条排除"
                    ),
                    "role_candidates_evaluated": [
                        {
                            "candidate": "reservoir",
                            "source": "deterministic",
                            "verdict": "不承担受压储液角色",
                            "reason": "仅靠重力供液，连接拓扑不同",
                        },
                        {
                            "candidate": "全文角色重扫",
                            "source": "full_text_rescan",
                            "verdict": "未发现候选",
                            "reason": "已围绕受压储液角色搜索全文，无遗漏候选",
                        },
                    ],
                }
            ]
        },
    )
    comparison = InvalidityAnalysisEngine(client).compare_single_reference(
        claim_id="claim-1",
        expanded_claim_text="一种水枪，包括压力罐。",
        target_patent_text=_target_patent_text(),
        limitations=limitations,
        document_id="D-TANK",
        document_text=(
            "The reservoir stores liquid under gravity and a flexible bladder "
            "feeds the outlet passage."
        ),
        target_images=["target.png"],
        document_images=["d1.png"],
    )

    # 若守门复核漏传 allow_text_only，此处 performed 会是 False 并被降级
    assert comparison.negation_guard_attempted is True
    assert comparison.negation_guard_performed is True
    assert comparison.negation_guard_downgraded == []
    assert comparison.disclosures[0].status is DisclosureStatus.NOT_DISCLOSED
    assert client.calls[1]["allow_text_only"] is True
    assert client.calls[1]["document_images"] == []


def test_i4s_negation_guard_warning_distinguishes_unexecuted_review() -> None:
    """问题2回归：候选复核未执行/失败时，告警不得写成「复核后仍遗漏」。"""

    limitations = [
        _feature_with_synonyms("f-tank", 1, "压力罐", synonyms_en=["reservoir"])
    ]
    client = FailGuardReviewClient(
        {
            "disclosures": [
                {
                    "feature_id": "f-tank",
                    "status": "not_disclosed",
                    "reasoning": (
                        "已检查全部储液、加压和排液结构，均没有承担受压储液并"
                        "向喷射流路供液的构件，因此未披露该结构角色"
                    ),
                    "confidence": 1.0,
                    "mapping_basis": "none",
                    "structural_search_summary": (
                        "复核储液腔、泵和全部排液流路；储液腔仅靠重力供液，"
                        "不存在承压储液候选结构"
                    ),
                }
            ]
        }
    )
    comparison = InvalidityAnalysisEngine(client).compare_single_reference(
        claim_id="claim-1",
        expanded_claim_text="一种水枪，包括压力罐。",
        target_patent_text=_target_patent_text(),
        limitations=limitations,
        document_id="D-TANK",
        document_text=(
            "The reservoir stores liquid under gravity and a flexible bladder "
            "feeds the outlet passage."
        ),
        target_images=["target.png"],
        document_images=["d1.png"],
    )

    disclosure = comparison.disclosures[0]
    assert disclosure.status is DisclosureStatus.UNCERTAIN
    assert comparison.negation_guard_attempted is True
    assert comparison.negation_guard_performed is False
    assert comparison.negation_guard_downgraded == ["f-tank"]
    assert "候选复核未执行/失败" in disclosure.guard_note
    assert "fail-safe" in disclosure.guard_note
    assert "经候选复核后仍未逐一处理" not in disclosure.guard_note
    assert any(
        "候选复核未执行/失败" in warning
        for warning in comparison.guard_warnings
    )


def test_i4s_consistency_guard_warning_distinguishes_unexecuted_rejudgment() -> None:
    """问题2回归：合并重判未执行/失败时，告警不得写成「重判后仍矛盾」。"""

    limitations = [_feature("f-spring", 1, "复位弹簧")]
    client = FailGuardReviewClient(
        {
            "reference_mechanism_summary": "执行器通过复位弹簧驱动阀体",
            "disclosures": [
                {
                    "feature_id": "f-spring",
                    "status": "not_disclosed",
                    # 有可回溯负向引文：不触发守门A空证据复核，只触发守门B
                    "evidence_quote": "the cam actuator drives the valve body",
                    "evidence_location": "paragraph 5",
                    "reasoning": (
                        "对比文件执行器为凸轮直驱，与弹簧复位路径的连接拓扑"
                        "不同；全文未提及复位弹簧"
                    ),
                    "confidence": 0.9,
                    "mapping_basis": "none",
                    "structural_search_summary": (
                        "已检查执行器与全部驱动链，候选凸轮连接拓扑不同，"
                        "未找到复位弹簧"
                    ),
                }
            ]
        }
    )
    comparison = InvalidityAnalysisEngine(client).compare_single_reference(
        claim_id="claim-1",
        expanded_claim_text="一种装置，包括复位弹簧。",
        target_patent_text=_target_patent_text(),
        limitations=limitations,
        document_id="D-SPRING",
        document_text="the cam actuator drives the valve body directly",
        target_images=["target.png"],
        document_images=["d1.png"],
    )

    disclosure = comparison.disclosures[0]
    assert disclosure.status is DisclosureStatus.UNCERTAIN
    assert comparison.negation_guard_attempted is False
    assert comparison.consistency_guard_attempted is True
    assert comparison.consistency_guard_performed is False
    assert comparison.consistency_guard_downgraded == ["f-spring"]
    assert "合并重判未执行/失败" in disclosure.guard_note
    assert "fail-safe" in disclosure.guard_note
    assert "合并重判后仍矛盾" not in disclosure.guard_note
    assert any(
        "合并重判未执行/失败" in warning
        for warning in comparison.guard_warnings
    )


def test_i4s_negation_guard_blind_empty_evidence_triggers_role_search() -> None:
    """问题3回归：空证据否定即使词面零命中也触发按角色语义的候选复核。"""

    limitations = [_feature("f-tank", 1, "压力罐")]
    client = FakeVisionClient(
        {
            "disclosures": [
                {
                    "feature_id": "f-tank",
                    "status": "not_disclosed",
                    "evidence_quote": "",
                    "reasoning": (
                        "已检查全部储液、加压和排液结构，均没有承担受压储液并"
                        "向喷射流路供液的构件，因此未披露该结构角色"
                    ),
                    "confidence": 1.0,
                    "mapping_basis": "none",
                    "structural_search_summary": (
                        "复核储液腔、泵和全部排液流路；储液腔仅靠重力供液，"
                        "不存在承压储液候选结构"
                    ),
                }
            ]
        },
        # 模型按角色语义（英文 reservoir/bladder）复核后记录排查过程并维持否定
        {
            "disclosures": [
                {
                    "feature_id": "f-tank",
                    "status": "not_disclosed",
                    "reasoning": (
                        "候选 reservoir 与 flexible bladder 仅靠重力供液，"
                        "不承担受压储液并排液角色，连接拓扑不同"
                    ),
                    "confidence": 0.9,
                    "mapping_basis": "none",
                    "structural_search_summary": (
                        "已逐一排查 reservoir 与 flexible bladder，路径与"
                        "拓扑不同"
                    ),
                    "alternative_path_analysis": (
                        "已按角色语义在英文全文搜索 reservoir/storage/bladder "
                        "候选：均接入重力供液路径而非受压排液路径"
                    ),
                    "role_candidates_evaluated": [
                        {
                            "candidate": "reservoir",
                            "source": "full_text_rescan",
                            "verdict": "不承担受压储液角色",
                            "reason": "仅靠重力供液，供液路径不同",
                        },
                        {
                            "candidate": "flexible bladder",
                            "source": "full_text_rescan",
                            "verdict": "不承担受压储液角色",
                            "reason": "柔性排液，非承压储存",
                        },
                    ],
                }
            ]
        },
    )
    comparison = InvalidityAnalysisEngine(client).compare_single_reference(
        claim_id="claim-1",
        expanded_claim_text="一种水枪，包括压力罐。",
        target_patent_text=_target_patent_text(),
        limitations=limitations,
        document_id="D-TANK",
        document_text=(
            "The reservoir stores liquid under gravity and a flexible bladder "
            "feeds the outlet passage."
        ),
        target_images=["target.png"],
        document_images=["d1.png"],
    )

    assert comparison.negation_guard_attempted is True
    assert comparison.negation_guard_performed is True
    assert comparison.negation_guard_downgraded == []
    assert comparison.disclosures[0].status is DisclosureStatus.NOT_DISCLOSED
    # role_candidates_evaluated 逐条合并进 alternative_path_analysis 留痕
    assert (
        "角色候选复核[full_text_rescan] reservoir"
        in comparison.disclosures[0].alternative_path_analysis
    )
    # 复核 prompt 必须给出空证据特征的按角色语义搜索指令
    guard_prompt = client.calls[1]["prompt"]
    assert "empty_evidence_negative" in guard_prompt
    assert "按角色语义而非中文词面搜索" in guard_prompt
    assert len(client.calls) == 2


def test_i4s_negation_guard_blind_still_empty_evidence_is_downgraded() -> None:
    """问题3回归：空证据复核后仍无正向痕迹且未记录排查过程的确定性降级。"""

    limitations = [_feature("f-tank", 1, "压力罐")]
    client = FakeVisionClient(
        {
            "disclosures": [
                {
                    "feature_id": "f-tank",
                    "status": "not_disclosed",
                    "evidence_quote": "",
                    "reasoning": (
                        "已检查全部储液、加压和排液结构，均没有承担受压储液并"
                        "向喷射流路供液的构件，因此未披露该结构角色"
                    ),
                    "confidence": 1.0,
                    "mapping_basis": "none",
                    "structural_search_summary": (
                        "复核储液腔、泵和全部排液流路；储液腔仅靠重力供液，"
                        "不存在承压储液候选结构"
                    ),
                }
            ]
        },
        # 复核仍不给引文、也不在 alternative_path_analysis 记录排查过程
        {
            "disclosures": [
                {
                    "feature_id": "f-tank",
                    "status": "not_disclosed",
                    "reasoning": (
                        "对比文件储液结构仅靠重力供液，与受压排液路径的"
                        "拓扑不同"
                    ),
                    "confidence": 0.9,
                    "mapping_basis": "none",
                    "structural_search_summary": (
                        "已检查储液腔与排液路径，未找到承压结构"
                    ),
                }
            ]
        },
    )
    comparison = InvalidityAnalysisEngine(client).compare_single_reference(
        claim_id="claim-1",
        expanded_claim_text="一种水枪，包括压力罐。",
        target_patent_text=_target_patent_text(),
        limitations=limitations,
        document_id="D-TANK",
        document_text=(
            "The reservoir stores liquid under gravity and a flexible bladder "
            "feeds the outlet passage."
        ),
        target_images=["target.png"],
        document_images=["d1.png"],
    )

    disclosure = comparison.disclosures[0]
    assert disclosure.status is DisclosureStatus.UNCERTAIN
    assert disclosure.guard_original_status == "not_disclosed"
    assert "空证据" in disclosure.guard_note
    assert comparison.negation_guard_performed is True
    assert comparison.negation_guard_downgraded == ["f-tank"]
    assert any("空证据" in warning for warning in comparison.guard_warnings)


def test_i4s_negation_guard_blind_review_may_upgrade_only_via_model() -> None:
    """问题3回归：空证据复核中模型可按实质相同标准改判（升级只能来自模型）。"""

    limitations = [_feature("f-tank", 1, "压力罐")]
    client = FakeVisionClient(
        {
            "disclosures": [
                {
                    "feature_id": "f-tank",
                    "status": "not_disclosed",
                    "evidence_quote": "",
                    "reasoning": (
                        "已检查全部储液、加压和排液结构，均没有承担受压储液并"
                        "向喷射流路供液的构件，因此未披露该结构角色"
                    ),
                    "confidence": 1.0,
                    "mapping_basis": "none",
                    "structural_search_summary": (
                        "复核储液腔、泵和全部排液流路；储液腔仅靠重力供液，"
                        "不存在承压储液候选结构"
                    ),
                }
            ]
        },
        # 模型在英文全文中找到承担受压储液角色的 reservoir，改判正向
        {
            "disclosures": [
                {
                    "feature_id": "f-tank",
                    "status": "direct_and_unambiguous",
                    "evidence_quote": "The reservoir stores liquid under gravity",
                    "evidence_location": "paragraph 3",
                    "reasoning": (
                        "reservoir 在受压排液路径中承担储液承压角色，与压力罐"
                        "实质相同"
                    ),
                    "confidence": 0.9,
                    "target_structural_role": "受压储液并向喷射流路供液",
                    "reference_structure_mapping": (
                        "reservoir 承担受压储液供液角色"
                    ),
                    "mapping_basis": "role_and_relation",
                    "structural_evidence": [
                        "The reservoir stores liquid under gravity"
                    ],
                }
            ]
        },
    )
    comparison = InvalidityAnalysisEngine(client).compare_single_reference(
        claim_id="claim-1",
        expanded_claim_text="一种水枪，包括压力罐。",
        target_patent_text=_target_patent_text(),
        limitations=limitations,
        document_id="D-TANK",
        document_text=(
            "The reservoir stores liquid under gravity and a flexible bladder "
            "feeds the outlet passage."
        ),
        target_images=["target.png"],
        document_images=["d1.png"],
    )

    disclosure = comparison.disclosures[0]
    assert disclosure.status is DisclosureStatus.DIRECT_AND_UNAMBIGUOUS
    assert comparison.negation_guard_attempted is True
    assert comparison.negation_guard_performed is True
    assert comparison.negation_guard_downgraded == []
    assert comparison.guard_warnings == []


# ---------------------------------------------------------------------------
# I4-S 守门复核回归（v47/v24）：全特征角色重扫指令、full_text_rescan 门槛、
# 守门错误码记录、截断/部分无效响应按 feature_id 部分合并
# ---------------------------------------------------------------------------


class FailGuardReviewWithCodeClient(FakeVisionClient):
    """第 fail_from 次调用起抛带安全错误码的传输失败。"""

    def __init__(
        self,
        *responses: dict[str, Any],
        fail_from: int = 2,
        reason_code: str = "GLM_TIMEOUT",
    ) -> None:
        super().__init__(*responses)
        self._fail_from = fail_from
        self._reason_code = reason_code

    def invoke_json(self, **kwargs: Any) -> SimpleNamespace:
        routed = self._discovery_route(kwargs)
        if routed is not None:
            return routed
        self.calls.append(kwargs)
        if len(self.calls) >= self._fail_from:
            raise MultimodalModelError(
                "temporary guard review failure",
                reason_code=self._reason_code,
            )
        if not self.responses:
            raise AssertionError("fake response exhausted")
        return SimpleNamespace(data=self.responses.pop(0), model=self.model)


def _blind_negative_first_pass(feature_id: str, noun: str) -> dict[str, Any]:
    """空证据否定的首轮响应（触发守门A盲区复核）。"""

    return {
        "disclosures": [
            {
                "feature_id": feature_id,
                "status": "not_disclosed",
                "evidence_quote": "",
                "reasoning": (
                    f"已检查全部相关结构，均没有承担{noun}角色的构件，"
                    "因此未披露该结构角色"
                ),
                "confidence": 1.0,
                "mapping_basis": "none",
                "structural_search_summary": (
                    f"复核全部候选结构区域，连接拓扑不同，"
                    f"不存在承担{noun}角色的候选结构"
                ),
            }
        ]
    }


def test_i4s_negation_guard_review_prompt_requires_role_rescan_for_all_features() -> (
    None
):
    """问题1回归：复核 prompt 对所有被复核特征要求角色语义全文重扫。"""

    limitations = [
        _feature_with_synonyms("f-tank", 1, "压力罐", synonyms_en=["reservoir"])
    ]
    client = FakeVisionClient(
        {
            "disclosures": [
                {
                    "feature_id": "f-tank",
                    "status": "not_disclosed",
                    "evidence_quote": "The reservoir stores liquid under gravity",
                    "evidence_location": "paragraph 3",
                    "reasoning": (
                        "已检查全部储液、加压和排液结构，均没有承担受压储液并"
                        "向喷射流路供液的构件，因此未披露该结构角色"
                    ),
                    "confidence": 1.0,
                    "mapping_basis": "none",
                    "structural_search_summary": (
                        "复核全部储液与排液流路，不存在承压储液候选结构"
                    ),
                }
            ]
        },
        # 有词面候选（reservoir 命中全文）的非盲特征：驳回候选并做全文重扫
        {
            "disclosures": [
                {
                    "feature_id": "f-tank",
                    "status": "not_disclosed",
                    "reasoning": (
                        "候选 reservoir 仅靠重力供液，不承担受压储液并排液的"
                        "目标角色，连接拓扑不同"
                    ),
                    "confidence": 0.9,
                    "mapping_basis": "none",
                    "structural_search_summary": (
                        "已逐一排查候选 reservoir 及全部储液结构，路径不同"
                    ),
                    "alternative_path_analysis": (
                        "已按角色语义在英文全文重扫受压储液候选并逐条排除"
                    ),
                    "role_candidates_evaluated": [
                        {
                            "candidate": "reservoir",
                            "source": "deterministic",
                            "verdict": "不承担受压储液角色",
                            "reason": "仅靠重力供液，路径不同",
                        },
                        {
                            "candidate": "flexible bladder",
                            "source": "full_text_rescan",
                            "verdict": "不承担受压储液角色",
                            "reason": "柔性排液，非承压储存",
                        },
                    ],
                }
            ]
        },
    )
    comparison = InvalidityAnalysisEngine(client).compare_single_reference(
        claim_id="claim-1",
        expanded_claim_text="一种水枪，包括压力罐。",
        target_patent_text=_target_patent_text(),
        limitations=limitations,
        document_id="D-TANK-RESCAN",
        document_text=(
            "The reservoir stores liquid under gravity and a flexible bladder "
            "feeds the outlet passage."
        ),
        target_images=["target.png"],
        document_images=["d1.png"],
    )

    assert comparison.negation_guard_attempted is True
    assert comparison.negation_guard_performed is True
    assert comparison.negation_guard_downgraded == []
    assert comparison.disclosures[0].status is DisclosureStatus.NOT_DISCLOSED
    guard_prompt = client.calls[1]["prompt"]
    # 有词面候选的非盲特征也收到角色语义全文重扫指令
    assert '"empty_evidence_negative": false' in guard_prompt
    assert "对每个被复核特征（不只标记 empty_evidence_negative 的）" in guard_prompt
    assert "按角色语义而非中文词面搜索" in guard_prompt
    assert "role_candidates_evaluated" in guard_prompt
    assert "full_text_rescan" in guard_prompt
    # 全文重扫需要可读的对比文件全文（有界）
    assert "The reservoir stores liquid under gravity" in guard_prompt
    # 评估记录逐条并入 apa 留痕
    apa = comparison.disclosures[0].alternative_path_analysis
    assert "角色候选复核[deterministic] reservoir" in apa
    assert "角色候选复核[full_text_rescan] flexible bladder" in apa


def test_i4s_negation_guard_blind_without_full_text_rescan_is_downgraded() -> None:
    """问题1回归：空证据否定复核缺少 full_text_rescan 记录时确定性降级。"""

    limitations = [_feature("f-tank", 1, "压力罐")]
    client = FakeVisionClient(
        _blind_negative_first_pass("f-tank", "受压储液"),
        # 复核只逐条驳回给定确定性候选，未做全文角色重扫
        {
            "disclosures": [
                {
                    "feature_id": "f-tank",
                    "status": "not_disclosed",
                    "reasoning": (
                        "给定候选均不承担受压储液角色，连接拓扑不同"
                    ),
                    "confidence": 0.9,
                    "mapping_basis": "none",
                    "structural_search_summary": (
                        "已逐一排查给定候选构件，路径不同"
                    ),
                    "alternative_path_analysis": (
                        "已逐条评估给定确定性候选并排除"
                    ),
                    "role_candidates_evaluated": [
                        {
                            "candidate": "给定确定性候选构件",
                            "source": "deterministic",
                            "verdict": "不承担目标角色",
                            "reason": "连接拓扑不同",
                        }
                    ],
                }
            ]
        },
    )
    comparison = InvalidityAnalysisEngine(client).compare_single_reference(
        claim_id="claim-1",
        expanded_claim_text="一种水枪，包括压力罐。",
        target_patent_text=_target_patent_text(),
        limitations=limitations,
        document_id="D-TANK-NO-RESCAN",
        document_text=(
            "The reservoir stores liquid under gravity and a flexible bladder "
            "feeds the outlet passage."
        ),
        target_images=["target.png"],
        document_images=["d1.png"],
    )

    disclosure = comparison.disclosures[0]
    assert disclosure.status is DisclosureStatus.UNCERTAIN
    assert disclosure.guard_original_status == "not_disclosed"
    assert "缺少全文角色重扫痕迹" in disclosure.guard_note
    assert comparison.negation_guard_performed is True
    assert comparison.negation_guard_downgraded == ["f-tank"]
    assert comparison.negation_guard_error == ""
    assert any("全文角色重扫" in warning for warning in comparison.guard_warnings)


def test_i4s_negation_guard_error_code_is_recorded_on_failure() -> None:
    """问题2回归：守门A复核调用失败时记录安全错误码并写进告警。"""

    limitations = [_feature("f-tank", 1, "压力罐")]
    client = FailGuardReviewWithCodeClient(
        _blind_negative_first_pass("f-tank", "受压储液"),
        fail_from=2,
        reason_code="GLM_TIMEOUT",
    )
    comparison = InvalidityAnalysisEngine(client).compare_single_reference(
        claim_id="claim-1",
        expanded_claim_text="一种水枪，包括压力罐。",
        target_patent_text=_target_patent_text(),
        limitations=limitations,
        document_id="D-TANK-TIMEOUT",
        document_text="The reservoir stores liquid under gravity.",
        target_images=["target.png"],
        document_images=["d1.png"],
    )

    disclosure = comparison.disclosures[0]
    assert comparison.negation_guard_attempted is True
    assert comparison.negation_guard_performed is False
    assert comparison.negation_guard_error == "GLM_TIMEOUT"
    assert disclosure.status is DisclosureStatus.UNCERTAIN
    assert comparison.negation_guard_downgraded == ["f-tank"]
    assert any(
        "fail-safe" in warning and "GLM_TIMEOUT" in warning
        for warning in comparison.guard_warnings
    )


def test_i4s_consistency_guard_error_code_is_recorded_on_failure() -> None:
    """问题2回归：守门B合并重判失败时记录安全错误码并写进告警。"""

    limitations = [_feature("f-spring", 1, "复位弹簧")]
    client = FailGuardReviewWithCodeClient(
        {
            "reference_mechanism_summary": "执行器通过复位弹簧驱动阀体",
            "disclosures": [
                {
                    "feature_id": "f-spring",
                    "status": "not_disclosed",
                    "reasoning": (
                        "对比文件执行器为凸轮直驱，与弹簧复位路径的连接拓扑"
                        "不同；全文未提及复位弹簧"
                    ),
                    "confidence": 0.9,
                    "mapping_basis": "none",
                    "structural_search_summary": (
                        "已检查执行器与全部驱动链，候选凸轮连接拓扑不同，"
                        "未找到复位弹簧"
                    ),
                }
            ],
        },
        # 守门A空证据复核（第 2 次调用）成功：记录重扫并维持否定
        {
            "disclosures": [
                {
                    "feature_id": "f-spring",
                    "status": "not_disclosed",
                    "reasoning": (
                        "对比文件执行器为凸轮直驱，与弹簧复位路径的连接拓扑"
                        "不同；全文未提及复位弹簧"
                    ),
                    "confidence": 0.9,
                    "mapping_basis": "none",
                    "structural_search_summary": (
                        "已检查执行器与全部驱动链，候选凸轮连接拓扑不同，"
                        "未找到复位弹簧"
                    ),
                    "alternative_path_analysis": (
                        "已按角色语义在对比文件全文搜索弹性复位候选并逐条排除"
                    ),
                    "role_candidates_evaluated": [
                        {
                            "candidate": "凸轮",
                            "source": "deterministic",
                            "verdict": "不承担弹性复位角色",
                            "reason": "凸轮直驱，复位路径拓扑不同",
                        },
                        {
                            "candidate": "全文角色重扫",
                            "source": "full_text_rescan",
                            "verdict": "无其他承担弹性复位角色的候选",
                            "reason": "已按角色语义搜索全文，无遗漏候选",
                        },
                    ],
                }
            ]
        },
        fail_from=3,
        reason_code="GLM_TIMEOUT",
    )
    comparison = InvalidityAnalysisEngine(client).compare_single_reference(
        claim_id="claim-1",
        expanded_claim_text="一种装置，包括复位弹簧。",
        target_patent_text=_target_patent_text(),
        limitations=limitations,
        document_id="D-SPRING-TIMEOUT",
        document_text="the cam actuator drives the valve body directly",
        target_images=["target.png"],
        document_images=["d1.png"],
    )

    disclosure = comparison.disclosures[0]
    assert comparison.negation_guard_performed is True
    assert comparison.consistency_guard_attempted is True
    assert comparison.consistency_guard_performed is False
    assert comparison.consistency_guard_error == "GLM_TIMEOUT"
    assert disclosure.status is DisclosureStatus.UNCERTAIN
    assert "合并重判未执行/失败（GLM_TIMEOUT）" in disclosure.guard_note
    assert comparison.consistency_guard_downgraded == ["f-spring"]
    assert len(client.calls) == 3


def test_i4s_consistency_guard_partial_response_is_merged_per_feature() -> None:
    """问题2回归：合并重判响应截断/部分无效时按 feature_id 部分合并。"""

    limitations = [
        _feature("f-lock", 1, "锁定件"),
        _feature("f-lock-detail", 2, "锁定件具有释放斜面"),
        _feature("f-roller", 3, "包括滚轮"),
    ]
    client = FakeVisionClient(
        {
            "disclosures": [
                {
                    "feature_id": "f-lock",
                    "status": "explicit",
                    "evidence_quote": "the locking member 40 engages the slot",
                    "evidence_location": "paragraph 9",
                    "reasoning": "锁定件40与卡槽配合，承担锁定件角色",
                    "confidence": 0.9,
                    "target_structural_role": "锁定件",
                    "reference_structure_mapping": "锁定件40与卡槽配合实现锁定",
                    "mapping_basis": "role_and_relation",
                    "structural_evidence": [],
                },
                {
                    "feature_id": "f-lock-detail",
                    "status": "not_disclosed",
                    "reasoning": (
                        "候选调节盘固定安装，与锁定件的卡接拓扑不同；"
                        "无锁定件结构，未提及锁定件"
                    ),
                    "confidence": 0.9,
                    "mapping_basis": "none",
                    "structural_search_summary": (
                        "已检查调节盘与壳体，连接拓扑不同，未找到锁定件"
                    ),
                },
                {
                    "feature_id": "f-roller",
                    "status": "uncertain",
                    "reasoning": "附图不完整，无法确认滚轮",
                    "confidence": 0.5,
                    "mapping_basis": "uncertain",
                },
            ]
        },
        # 守门A空证据复核：记录全文重扫并维持否定（矛盾仍在）
        {
            "disclosures": [
                {
                    "feature_id": "f-lock-detail",
                    "status": "not_disclosed",
                    "reasoning": (
                        "候选调节盘固定安装，与锁定件的卡接拓扑不同；"
                        "无锁定件结构，未提及锁定件"
                    ),
                    "confidence": 0.9,
                    "mapping_basis": "none",
                    "structural_search_summary": (
                        "已检查调节盘与壳体，连接拓扑不同，未找到锁定件"
                    ),
                    "alternative_path_analysis": (
                        "已按角色语义在对比文件全文搜索锁定件候选并逐条排除"
                    ),
                    "role_candidates_evaluated": [
                        {
                            "candidate": "调节盘",
                            "source": "deterministic",
                            "verdict": "不承担锁定件角色",
                            "reason": "固定安装，卡接拓扑不同",
                        },
                        {
                            "candidate": "全文角色重扫",
                            "source": "full_text_rescan",
                            "verdict": "无其他承担锁定件角色的候选",
                            "reason": "已按角色语义搜索全文，无遗漏候选",
                        },
                    ],
                }
            ]
        },
        # 守门B合并重判响应被截断：只返回 f-lock，缺 f-lock-detail
        {
            "disclosures": [
                {
                    "feature_id": "f-lock",
                    "status": "explicit",
                    "evidence_quote": "the locking member 40 engages the slot",
                    "evidence_location": "paragraph 9",
                    "reasoning": "合并重判维持：锁定件40承担锁定件角色",
                    "confidence": 0.95,
                    "target_structural_role": "锁定件",
                    "reference_structure_mapping": "锁定件40与卡槽配合实现锁定",
                    "mapping_basis": "role_and_relation",
                    "structural_evidence": [],
                }
            ]
        },
    )
    comparison = InvalidityAnalysisEngine(client).compare_single_reference(
        claim_id="claim-1",
        expanded_claim_text="一种装置，包括锁定件，锁定件具有释放斜面。",
        target_patent_text=_target_patent_text(),
        limitations=limitations,
        document_id="D-LOCK-PARTIAL",
        document_text="paragraph 9 the locking member 40 engages the slot",
        target_images=["target.png"],
        document_images=["d1.png"],
    )

    by_id = {item.feature_id: item for item in comparison.disclosures}
    # 有效条目按 feature_id 正常合并，不整体丢弃
    assert comparison.consistency_guard_attempted is True
    assert comparison.consistency_guard_performed is True
    assert comparison.consistency_guard_error == "GUARD_REVIEW_OUTPUT_INCOMPLETE"
    assert by_id["f-lock"].status is DisclosureStatus.EXPLICIT
    assert by_id["f-lock"].confidence == 0.95
    # 未返回的否定条目按未重判处理：fail-safe 降级并写明真实原因
    assert by_id["f-lock-detail"].status is DisclosureStatus.UNCERTAIN
    assert (
        "合并重判未执行/失败（GUARD_REVIEW_OUTPUT_INCOMPLETE）"
        in by_id["f-lock-detail"].guard_note
    )
    assert comparison.consistency_guard_downgraded == ["f-lock-detail"]
    assert len(client.calls) == 3


# ---------------------------------------------------------------------------
# I4-S 守门回归（v48/v25）：守门B反向对账（子披露/父否定）、
# 全部复核否定特征的 full_text_rescan 留痕门槛
# ---------------------------------------------------------------------------


def test_i4s_consistency_guard_reverse_parent_child_contradiction() -> None:
    """缺陷1回归：子特征正向披露、父特征「无该结构」否定检出为反向矛盾。"""

    limitations = [
        _feature("f-rod", 1, "可移动阀杆"),
        _feature("f-rod-plug", 2, "阀杆带有阀塞"),
        _feature("f-roller", 3, "包括滚轮"),
    ]
    client = FakeVisionClient(
        {
            "disclosures": [
                {
                    "feature_id": "f-rod",
                    "status": "not_disclosed",
                    "reasoning": (
                        "候选凸轮固定安装，连接拓扑不同；无阀杆结构，"
                        "未提及阀杆"
                    ),
                    "confidence": 0.9,
                    "mapping_basis": "none",
                    "structural_search_summary": (
                        "已检查全部杆状构件，连接拓扑不同，未找到阀杆"
                    ),
                },
                {
                    "feature_id": "f-rod-plug",
                    "status": "explicit",
                    "evidence_quote": "the valve stem 32 carries the plug 33",
                    "evidence_location": "paragraph 5",
                    "reasoning": "阀杆32携带阀塞33，承担带阀塞阀杆角色",
                    "confidence": 0.9,
                    "target_structural_role": "阀杆带有阀塞",
                    "reference_structure_mapping": "阀杆32与阀塞33联动",
                    "mapping_basis": "role_and_relation",
                    "structural_evidence": [
                        "the valve stem 32 carries the plug 33"
                    ],
                },
                {
                    "feature_id": "f-roller",
                    "status": "uncertain",
                    "reasoning": "附图不完整，无法确认滚轮",
                    "confidence": 0.5,
                    "mapping_basis": "uncertain",
                },
            ]
        },
        # 守门A空证据复核（父特征）：记录全文重扫并维持否定（反向矛盾仍在）
        {
            "disclosures": [
                {
                    "feature_id": "f-rod",
                    "status": "not_disclosed",
                    "reasoning": (
                        "候选凸轮固定安装，连接拓扑不同；无阀杆结构，"
                        "未提及阀杆"
                    ),
                    "confidence": 0.9,
                    "mapping_basis": "none",
                    "structural_search_summary": (
                        "已检查全部杆状构件，连接拓扑不同，未找到阀杆"
                    ),
                    "alternative_path_analysis": (
                        "已按角色语义在对比文件全文搜索阀杆候选并逐条排除"
                    ),
                    "role_candidates_evaluated": [
                        {
                            "candidate": (
                                "the valve stem 32 carries the plug 33"
                            ),
                            "source": "deterministic",
                            "verdict": "不承担可移动阀杆角色",
                            "reason": "该句仅公开阀杆携带阀塞，未公开可移动限定",
                        },
                        {
                            "candidate": "全文角色重扫",
                            "source": "full_text_rescan",
                            "verdict": "未发现候选",
                            "reason": "已围绕阀杆角色搜索全文，无遗漏候选",
                        },
                    ],
                }
            ]
        },
        # 守门B合并重判：子特征已映射阀杆32，父特征改判不确定，矛盾消解
        {
            "disclosures": [
                {
                    "feature_id": "f-rod",
                    "status": "uncertain",
                    "reasoning": (
                        "阀杆32已由子特征映射，但「可移动」附加限定在附图中"
                        "无法核验"
                    ),
                    "confidence": 0.6,
                    "mapping_basis": "uncertain",
                    "structural_search_summary": "可移动性无法核验",
                },
                {
                    "feature_id": "f-rod-plug",
                    "status": "explicit",
                    "evidence_quote": "the valve stem 32 carries the plug 33",
                    "evidence_location": "paragraph 5",
                    "reasoning": "阀杆32携带阀塞33，承担带阀塞阀杆角色",
                    "confidence": 0.9,
                    "target_structural_role": "阀杆带有阀塞",
                    "reference_structure_mapping": "阀杆32与阀塞33联动",
                    "mapping_basis": "role_and_relation",
                    "structural_evidence": [
                        "the valve stem 32 carries the plug 33"
                    ],
                },
            ]
        },
    )
    comparison = InvalidityAnalysisEngine(client).compare_single_reference(
        claim_id="claim-1",
        expanded_claim_text="一种阀，包括可移动阀杆，阀杆带有阀塞和滚轮。",
        target_patent_text=_target_patent_text(),
        limitations=limitations,
        document_id="D-VALVE-REVERSE",
        document_text="paragraph 5 the valve stem 32 carries the plug 33",
        target_images=["target.png"],
        document_images=["d1.png"],
    )

    by_id = {item.feature_id: item for item in comparison.disclosures}
    assert comparison.negation_guard_performed is True
    assert comparison.negation_guard_downgraded == []
    assert comparison.consistency_guard_attempted is True
    assert comparison.consistency_guard_performed is True
    assert comparison.consistency_guard_clusters == 1
    # 反向矛盾对进入合并重判 prompt
    assert "reverse_parent_child" in client.calls[2]["prompt"]
    # 模型合并重判消解矛盾后不做确定性降级
    assert comparison.consistency_guard_downgraded == []
    assert by_id["f-rod"].status is DisclosureStatus.UNCERTAIN
    assert by_id["f-rod"].guard_original_status == ""
    assert by_id["f-rod-plug"].status is DisclosureStatus.EXPLICIT
    assert len(client.calls) == 3


def test_i4s_negation_guard_quoted_negative_without_rescan_is_downgraded() -> None:
    """缺陷2回归：evidence_quote 非空的复核否定缺 full_text_rescan 也降级。"""

    limitations = [
        _feature_with_synonyms("f-tank", 1, "压力罐", synonyms_en=["reservoir"])
    ]
    client = FakeVisionClient(
        {
            "disclosures": [
                {
                    "feature_id": "f-tank",
                    "status": "not_disclosed",
                    "evidence_quote": "The reservoir stores liquid under gravity",
                    "evidence_location": "paragraph 3",
                    "reasoning": (
                        "已检查全部储液、加压和排液结构，均没有承担受压储液并"
                        "向喷射流路供液的构件，因此未披露该结构角色"
                    ),
                    "confidence": 1.0,
                    "mapping_basis": "none",
                    "structural_search_summary": (
                        "复核全部储液与排液流路，连接拓扑不同，"
                        "不存在承压储液候选结构"
                    ),
                }
            ]
        },
        # 复核只逐条驳回给定确定性候选：reservoir 处理了，但无全文重扫记录
        {
            "disclosures": [
                {
                    "feature_id": "f-tank",
                    "status": "not_disclosed",
                    "reasoning": (
                        "候选 reservoir 仅靠重力供液，不承担受压储液并排液"
                        "角色，连接拓扑不同"
                    ),
                    "confidence": 0.9,
                    "mapping_basis": "none",
                    "structural_search_summary": (
                        "已逐一排查候选 reservoir，路径与拓扑不同"
                    ),
                    "role_candidates_evaluated": [
                        {
                            "candidate": "reservoir",
                            "source": "deterministic",
                            "verdict": "不承担受压储液角色",
                            "reason": "仅靠重力供液，路径不同",
                        }
                    ],
                }
            ]
        },
    )
    comparison = InvalidityAnalysisEngine(client).compare_single_reference(
        claim_id="claim-1",
        expanded_claim_text="一种水枪，包括压力罐。",
        target_patent_text=_target_patent_text(),
        limitations=limitations,
        document_id="D-TANK-QUOTED-NO-RESCAN",
        document_text=(
            "The reservoir stores liquid under gravity and a flexible bladder "
            "feeds the outlet passage."
        ),
        target_images=["target.png"],
        document_images=["d1.png"],
    )

    disclosure = comparison.disclosures[0]
    assert disclosure.status is DisclosureStatus.UNCERTAIN
    assert disclosure.guard_original_status == "not_disclosed"
    assert "候选复核缺少全文角色重扫痕迹" in disclosure.guard_note
    assert comparison.negation_guard_performed is True
    assert comparison.negation_guard_downgraded == ["f-tank"]
    assert any(
        "缺少全文角色重扫痕迹" in warning
        for warning in comparison.guard_warnings
    )


def test_i4s_negation_guard_explicit_no_candidate_rescan_record_passes() -> None:
    """缺陷2回归：「重扫未发现候选」的显式 full_text_rescan 记录可通过复检。"""

    limitations = [
        _feature_with_synonyms("f-tank", 1, "压力罐", synonyms_en=["reservoir"])
    ]
    client = FakeVisionClient(
        {
            "disclosures": [
                {
                    "feature_id": "f-tank",
                    "status": "not_disclosed",
                    "evidence_quote": "The reservoir stores liquid under gravity",
                    "evidence_location": "paragraph 3",
                    "reasoning": (
                        "已检查全部储液、加压和排液结构，均没有承担受压储液并"
                        "向喷射流路供液的构件，因此未披露该结构角色"
                    ),
                    "confidence": 1.0,
                    "mapping_basis": "none",
                    "structural_search_summary": (
                        "复核全部储液与排液流路，连接拓扑不同，"
                        "不存在承压储液候选结构"
                    ),
                }
            ]
        },
        {
            "disclosures": [
                {
                    "feature_id": "f-tank",
                    "status": "not_disclosed",
                    "reasoning": (
                        "候选 reservoir 仅靠重力供液，不承担受压储液并排液"
                        "角色，连接拓扑不同"
                    ),
                    "confidence": 0.9,
                    "mapping_basis": "none",
                    "structural_search_summary": (
                        "已逐一排查候选 reservoir，路径与拓扑不同"
                    ),
                    "role_candidates_evaluated": [
                        {
                            "candidate": "reservoir",
                            "source": "deterministic",
                            "verdict": "不承担受压储液角色",
                            "reason": "仅靠重力供液，路径不同",
                        },
                        {
                            "candidate": "全文角色重扫",
                            "source": "full_text_rescan",
                            "verdict": "未发现候选",
                            "reason": (
                                "已围绕受压储液角色按角色语义搜索全文"
                                "（含英文段落），无遗漏候选"
                            ),
                        },
                    ],
                }
            ]
        },
    )
    comparison = InvalidityAnalysisEngine(client).compare_single_reference(
        claim_id="claim-1",
        expanded_claim_text="一种水枪，包括压力罐。",
        target_patent_text=_target_patent_text(),
        limitations=limitations,
        document_id="D-TANK-EXPLICIT-RESCAN",
        document_text=(
            "The reservoir stores liquid under gravity and a flexible bladder "
            "feeds the outlet passage."
        ),
        target_images=["target.png"],
        document_images=["d1.png"],
    )

    assert comparison.negation_guard_performed is True
    assert comparison.negation_guard_downgraded == []
    assert comparison.disclosures[0].status is DisclosureStatus.NOT_DISCLOSED
    assert comparison.guard_warnings == []
    assert (
        "角色候选复核[full_text_rescan] 全文角色重扫：未发现候选"
        in comparison.disclosures[0].alternative_path_analysis
    )


# ---------------------------------------------------------------------------
# I4-S 候选构件发现预检（v49/v26，宪章 2.12 / WORKFLOW_SPEC 3.17 §6）
# ---------------------------------------------------------------------------


def _discovery_response(*entries: dict[str, Any]) -> dict[str, Any]:
    return {"features": list(entries)}


def test_i4s_candidate_discovery_schema_and_quote_verification() -> None:
    """发现步骤：schema 校验 + 引文确定性回验（伪造引文丢弃留痕）。"""

    limitations = [_feature("f-tank", 1, "压力罐")]
    document_text = (
        "The reservoir stores liquid under gravity and a flexible bladder "
        "feeds the outlet passage."
    )
    client = FakeVisionClient(
        {
            "disclosures": [
                {
                    "feature_id": "f-tank",
                    "status": "explicit",
                    "evidence_quote": "The reservoir stores liquid under gravity",
                    "evidence_location": "paragraph 3",
                    "reasoning": "reservoir 承担储液角色，直接对应",
                    "confidence": 0.9,
                    "target_structural_role": "储液",
                    "reference_structure_mapping": "reservoir 承担储液角色",
                    "mapping_basis": "role_and_relation",
                    "structural_evidence": [],
                }
            ]
        },
        discovery_response=_discovery_response(
            {
                "feature_id": "f-tank",
                "role_understanding": "储液并向喷射流路供液的受压容器",
                "candidates": [
                    {
                        "candidate": "reservoir",
                        "quote": "The reservoir stores liquid under gravity",
                        "location": "paragraph 3",
                        "role_rationale": "承担储液并供液角色",
                    },
                    {
                        "candidate": "phantom pressure tank",
                        "quote": "a pressurized tank feeds the valve",
                        "location": "paragraph 9",
                        "role_rationale": "编造的承压罐",
                    },
                ],
                "no_candidate": False,
            },
            # 集合外 feature_id：整条丢弃
            {
                "feature_id": "f-ghost",
                "role_understanding": "不在输入特征集合内",
                "candidates": [],
                "no_candidate": True,
            },
        ),
    )
    comparison = InvalidityAnalysisEngine(client).compare_single_reference(
        claim_id="claim-1",
        expanded_claim_text="一种水枪，包括压力罐。",
        target_patent_text=_target_patent_text(),
        limitations=limitations,
        document_id="D-TANK-DISCOVERY",
        document_text=document_text,
        target_images=["target.png"],
        document_images=["d1.png"],
    )

    assert comparison.candidate_discovery_attempted is True
    assert comparison.candidate_discovery_performed is True
    assert comparison.candidate_discovery_error == ""
    # 真引文候选保留并持久化；伪造引文整条丢弃留痕；集合外条目不进清单
    assert len(comparison.candidate_inventory) == 1
    entry = comparison.candidate_inventory[0]
    assert entry["feature_id"] == "f-tank"
    assert [item["candidate"] for item in entry["candidates"]] == ["reservoir"]
    assert entry["candidates"][0]["quote"] == (
        "The reservoir stores liquid under gravity"
    )
    assert comparison.candidate_discovery_dropped == [
        {
            "feature_id": "f-tank",
            "candidate": "phantom pressure tank",
            "reason": "引文无法回验到对比文件原文",
        }
    ]
    # 发现调用是纯文本子任务且显式 allow_text_only
    assert client.calls[0]["allow_text_only"] is True
    assert client.calls[0]["document_images"] == []
    assert "I4-S 候选构件发现任务" in client.calls[0]["prompt"]
    # 清单注入首轮 prompt（含指令段与候选内容）
    first_pass_prompt = client.calls[1]["prompt"]
    assert "candidate_inventory 是逐特征比对前的候选构件发现预检" in first_pass_prompt
    assert "reservoir" in first_pass_prompt
    assert len(client.calls) == 2


def test_i4s_candidate_discovery_failure_degrades_to_inventory_free_run() -> None:
    """发现调用失败：降级为无清单运行，记录安全错误码，不阻断比对。"""

    limitations = [_feature("f-tank", 1, "压力罐")]
    client = FakeVisionClient(
        {
            "disclosures": [
                {
                    "feature_id": "f-tank",
                    "status": "explicit",
                    "evidence_quote": "The reservoir stores liquid under gravity",
                    "evidence_location": "paragraph 3",
                    "reasoning": "reservoir 承担储液角色，直接对应",
                    "confidence": 0.9,
                    "target_structural_role": "储液",
                    "reference_structure_mapping": "reservoir 承担储液角色",
                    "mapping_basis": "role_and_relation",
                    "structural_evidence": [],
                }
            ]
        }
    )
    comparison = InvalidityAnalysisEngine(client).compare_single_reference(
        claim_id="claim-1",
        expanded_claim_text="一种水枪，包括压力罐。",
        target_patent_text=_target_patent_text(),
        limitations=limitations,
        document_id="D-TANK-NO-DISCOVERY",
        document_text="The reservoir stores liquid under gravity.",
        target_images=["target.png"],
        document_images=["d1.png"],
    )

    assert comparison.candidate_discovery_attempted is True
    assert comparison.candidate_discovery_performed is False
    assert comparison.candidate_discovery_error == "FAKE_DISCOVERY_UNCONFIGURED"
    assert comparison.candidate_inventory == []
    assert comparison.candidate_discovery_dropped == []
    assert comparison.disclosures[0].status is DisclosureStatus.EXPLICIT
    # 无清单运行时首轮 prompt 省略清单段
    assert "candidate_inventory" not in client.calls[0]["prompt"]


def test_i4s_candidate_inventory_injects_into_review_prompts() -> None:
    """清单注入首轮/结构复核/守门A/守门B 四类 prompt 的指令段断言。"""

    inventory = [
        {
            "feature_id": "f-tank",
            "role_understanding": "受压储液容器",
            "candidates": [
                {
                    "candidate": "reservoir",
                    "quote": "The reservoir stores liquid under gravity",
                    "location": "paragraph 3",
                    "role_rationale": "承担储液角色",
                }
            ],
            "no_candidate": False,
        }
    ]
    base_payload = {
        "claim_id": "claim-1",
        "expanded_claim_text": "一种水枪，包括压力罐。",
        "limitations": [_feature("f-tank", 1, "压力罐")],
        "target_patent_text": _target_patent_text(),
        "document_id": "D-TANK",
        "document_text": "The reservoir stores liquid under gravity.",
    }

    first_pass = InvalidityAnalysisEngine._single_reference_prompt(
        **base_payload,
        candidate_inventory=inventory,
    )
    assert "候选构件发现预检" in first_pass
    assert "reservoir" in first_pass
    first_pass_without = InvalidityAnalysisEngine._single_reference_prompt(
        **base_payload
    )
    assert "candidate_inventory" not in first_pass_without

    reconciliation = InvalidityAnalysisEngine._structural_reconciliation_prompt(
        **base_payload,
        target_mechanism_summary="储液喷射",
        reference_mechanism_summary="reservoir 储液",
        first_pass_disclosures=[],
        immutable_positive_disclosures=[],
        candidate_inventory=inventory,
    )
    assert "候选构件发现预检" in reconciliation
    assert "reservoir" in reconciliation

    negation_review = InvalidityAnalysisEngine._negation_guard_review_prompt(
        document_id="D-TANK",
        document_evidence_text="The reservoir stores liquid under gravity.",
        features=[
            {
                "feature_id": "f-tank",
                "feature_text": "压力罐",
                "status": "not_disclosed",
                "reasoning": "未见承压储液",
                "structural_search_summary": "已排查",
                "alternative_path_analysis": "",
                "target_structural_role": "受压储液",
                "empty_evidence_negative": True,
                "discovered_candidates": inventory,
                "unhandled_candidates": [],
            }
        ],
    )
    assert "discovered_candidates 来自候选构件发现预检清单" in negation_review
    assert "reservoir" in negation_review

    consistency_review = InvalidityAnalysisEngine._consistency_guard_review_prompt(
        reference_mechanism_summary="reservoir 储液",
        contradictions=[],
        features=[],
        candidate_inventory=inventory,
    )
    assert "候选构件发现预检清单" in consistency_review
    assert "reservoir" in consistency_review


def test_i4s_negation_guard_candidates_include_verified_inventory() -> None:
    """守门A候选词集包含已回验清单候选（跨语言最可靠候选来源）。"""

    limitations = [_feature("f-tank", 1, "压力罐")]
    client = FakeVisionClient(
        {
            "disclosures": [
                {
                    "feature_id": "f-tank",
                    "status": "not_disclosed",
                    "reasoning": (
                        "已检查全部储液、加压和排液结构，均没有承担受压储液并"
                        "向喷射流路供液的构件，因此未披露该结构角色"
                    ),
                    "confidence": 1.0,
                    "mapping_basis": "none",
                    "structural_search_summary": (
                        "复核全部储液与排液流路，连接拓扑不同，"
                        "不存在承压储液候选结构"
                    ),
                }
            ]
        },
        # 守门A复核：驳回 reservoir 并完成全文重扫，维持合规否定
        {
            "disclosures": [
                {
                    "feature_id": "f-tank",
                    "status": "not_disclosed",
                    "reasoning": (
                        "候选 reservoir 仅靠重力供液，不承担受压储液并排液"
                        "角色，连接拓扑不同"
                    ),
                    "confidence": 0.9,
                    "mapping_basis": "none",
                    "structural_search_summary": (
                        "已逐一排查候选 reservoir，路径与拓扑不同"
                    ),
                    "alternative_path_analysis": (
                        "已按角色语义在英文全文重扫受压储液候选并逐条排除"
                    ),
                    "role_candidates_evaluated": [
                        {
                            "candidate": "reservoir",
                            "source": "deterministic",
                            "verdict": "不承担受压储液角色",
                            "reason": "仅靠重力供液，路径不同",
                        },
                        {
                            "candidate": "全文角色重扫",
                            "source": "full_text_rescan",
                            "verdict": "未发现候选",
                            "reason": "已围绕受压储液角色搜索全文，无遗漏候选",
                        },
                    ],
                }
            ]
        },
        discovery_response=_discovery_response(
            {
                "feature_id": "f-tank",
                "role_understanding": "受压储液并向喷射流路供液",
                "candidates": [
                    {
                        "candidate": "reservoir",
                        "quote": "The reservoir stores liquid under gravity",
                        "location": "paragraph 3",
                        "role_rationale": "承担储液供液角色",
                    }
                ],
                "no_candidate": False,
            }
        ),
    )
    comparison = InvalidityAnalysisEngine(client).compare_single_reference(
        claim_id="claim-1",
        expanded_claim_text="一种水枪，包括压力罐。",
        target_patent_text=_target_patent_text(),
        limitations=limitations,
        document_id="D-TANK-VERIFIED",
        document_text=(
            "The reservoir stores liquid under gravity and a flexible bladder "
            "feeds the outlet passage."
        ),
        target_images=["target.png"],
        document_images=["d1.png"],
    )

    # 特征未绑定英文同义词：词面来源找不到 reservoir，只有清单候选使其
    # 进入守门A候选词集并命中全文，触发候选复核
    assert comparison.candidate_discovery_performed is True
    assert comparison.negation_guard_attempted is True
    assert comparison.negation_guard_performed is True
    assert comparison.negation_guard_downgraded == []
    assert comparison.disclosures[0].status is DisclosureStatus.NOT_DISCLOSED
    guard_prompt = client.calls[2]["prompt"]
    assert '"term": "reservoir"' in guard_prompt
    assert "discovered_candidates" in guard_prompt
    assert len(client.calls) == 3


# ---------------------------------------------------------------------------
# I4-S 候选构件发现传输重试（v50/v27）：有界一次重试 + attempts 审计
# ---------------------------------------------------------------------------


class FlakyDiscoveryClient(FakeVisionClient):
    """发现调用前 fail_count 次传输失败，之后按配置的 discovery_response 响应。"""

    def __init__(
        self,
        *responses: dict[str, Any],
        fail_count: int = 1,
        discovery_response: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(*responses, discovery_response=discovery_response)
        self._discovery_fail_count = fail_count
        self._discovery_failures = 0

    def _discovery_route(self, kwargs: dict[str, Any]) -> SimpleNamespace | None:
        if "I4-S 候选构件发现任务" not in str(kwargs.get("prompt") or ""):
            return None
        if self._discovery_failures < self._discovery_fail_count:
            self._discovery_failures += 1
            self.calls.append(kwargs)
            raise MultimodalModelError(
                "temporary discovery transport failure",
                reason_code="MODEL_TRANSPORT_ERROR",
            )
        return super()._discovery_route(kwargs)


def _positive_first_pass(feature_id: str = "f-tank") -> dict[str, Any]:
    return {
        "disclosures": [
            {
                "feature_id": feature_id,
                "status": "explicit",
                "evidence_quote": "The reservoir stores liquid under gravity",
                "evidence_location": "paragraph 3",
                "reasoning": "reservoir 承担储液角色，直接对应",
                "confidence": 0.9,
                "target_structural_role": "储液",
                "reference_structure_mapping": "reservoir 承担储液角色",
                "mapping_basis": "role_and_relation",
                "structural_evidence": [],
            }
        ]
    }


def _discovery_entry(feature_id: str = "f-tank") -> dict[str, Any]:
    return {
        "feature_id": feature_id,
        "role_understanding": "受压储液并向喷射流路供液",
        "candidates": [
            {
                "candidate": "reservoir",
                "quote": "The reservoir stores liquid under gravity",
                "location": "paragraph 3",
                "role_rationale": "承担储液供液角色",
            }
        ],
        "no_candidate": False,
    }


def _run_discovery_comparison(
    client: FakeVisionClient,
    limitations: list[Limitation],
) -> Any:
    return InvalidityAnalysisEngine(client).compare_single_reference(
        claim_id="claim-1",
        expanded_claim_text="一种水枪，包括压力罐。",
        target_patent_text=_target_patent_text(),
        limitations=limitations,
        document_id="D-TANK-RETRY",
        document_text="The reservoir stores liquid under gravity.",
        target_images=["target.png"],
        document_images=["d1.png"],
    )


def test_i4s_candidate_discovery_retries_transport_failure_once() -> None:
    """首次传输失败自动重试一次：重试后成功，attempts=2。"""

    limitations = [_feature("f-tank", 1, "压力罐")]
    client = FlakyDiscoveryClient(
        _positive_first_pass(),
        fail_count=1,
        discovery_response=_discovery_response(_discovery_entry()),
    )
    comparison = _run_discovery_comparison(client, limitations)

    assert comparison.candidate_discovery_attempted is True
    assert comparison.candidate_discovery_performed is True
    assert comparison.candidate_discovery_error == ""
    assert comparison.candidate_discovery_attempts == 2
    assert len(comparison.candidate_inventory) == 1
    # 发现 2 次 + 首轮 1 次，无额外调用
    assert len(client.calls) == 3
    assert client.calls[0]["request_timeout_seconds"] == 240.0
    assert client.calls[1]["request_timeout_seconds"] == 240.0


def test_i4s_candidate_discovery_two_transport_failures_degrade() -> None:
    """两次传输均失败：记错误码降级为无清单运行，attempts=2，不阻断比对。"""

    limitations = [_feature("f-tank", 1, "压力罐")]
    client = FlakyDiscoveryClient(
        _positive_first_pass(),
        fail_count=2,
        discovery_response=_discovery_response(_discovery_entry()),
    )
    comparison = _run_discovery_comparison(client, limitations)

    assert comparison.candidate_discovery_attempted is True
    assert comparison.candidate_discovery_performed is False
    assert comparison.candidate_discovery_error == "MODEL_TRANSPORT_ERROR"
    assert comparison.candidate_discovery_attempts == 2
    assert comparison.candidate_inventory == []
    assert comparison.disclosures[0].status is DisclosureStatus.EXPLICIT
    # 无清单运行：首轮 prompt 省略清单段
    assert "candidate_inventory" not in client.calls[2]["prompt"]
    assert len(client.calls) == 3


def test_i4s_candidate_discovery_invalid_schema_is_not_retried() -> None:
    """响应 schema 无效不是传输问题：不重试，attempts=1。"""

    limitations = [_feature("f-tank", 1, "压力罐")]
    client = FlakyDiscoveryClient(
        _positive_first_pass(),
        fail_count=0,
        discovery_response={"unexpected_wrapper": True},
    )
    comparison = _run_discovery_comparison(client, limitations)

    assert comparison.candidate_discovery_attempted is True
    assert comparison.candidate_discovery_performed is False
    assert (
        comparison.candidate_discovery_error
        == "CANDIDATE_DISCOVERY_OUTPUT_INVALID"
    )
    assert comparison.candidate_discovery_attempts == 1
    assert comparison.candidate_inventory == []
    assert comparison.disclosures[0].status is DisclosureStatus.EXPLICIT
    # 发现 1 次 + 首轮 1 次
    assert len(client.calls) == 2


def _water_gun_terminology_response() -> dict[str, Any]:
    """水枪案：发明点为自创术语「泄压迸射阀组」，画像冻结两组改写词。"""

    return {
        "technical_subject": "水枪",
        "subject_synonyms_zh": [],
        "subject_synonyms_en": ["water gun"],
        "features": [
            {
                "temp_id": "f1",
                "text": "储水罐",
                "technical_subject": "水枪",
                "synonyms_zh": ["水箱"],
                "synonyms_en": ["water tank"],
            },
            {
                "temp_id": "f2",
                "text": "泄压迸射阀组，用于受压后瞬间释放水流",
                "technical_subject": "水枪",
            },
        ],
        "invention_search_profile": {
            "protected_subject": "水枪",
            "subject_synonyms_zh": [],
            "subject_synonyms_en": ["water gun"],
            "classification_anchors": [],
            "invention_summary": "通过泄压迸射阀组实现短促水流迸射。",
            "common_context_features": [
                {
                    "concept_id": "context-tank",
                    "text": "储水罐",
                    "feature_ids": ["f1"],
                    "synonyms_zh": ["水箱"],
                    "synonyms_en": ["water tank"],
                    "source_reference": "权利要求1",
                    "rationale": "水枪类别常见储水构件",
                    "preferred_scope": "claims",
                },
            ],
            "inventive_point_features": [
                {
                    "concept_id": "inventive-burst-valve",
                    "text": "泄压迸射阀组",
                    "feature_ids": ["f2"],
                    "synonyms_zh": ["迸射阀"],
                    "synonyms_en": ["burst valve"],
                    "source_reference": "权利要求1及发明内容",
                    "rationale": "自创术语泄压迸射阀组是发明点",
                    "preferred_scope": "full_text",
                    "generic_component": {
                        "text": "阀",
                        "synonyms_zh": [],
                        "synonyms_en": ["valve"],
                        "source_reference": "说明书发明内容",
                        "rationale": "说明书明确该自创构件是一种阀",
                    },
                    "function_effect": {
                        "text": "迸射",
                        "synonyms_zh": ["水弹"],
                        "synonyms_en": ["Shot", "water bullet"],
                        "source_reference": "说明书实施方式",
                        "rationale": "该阀实现短促水流迸射成水弹的效果",
                    },
                },
            ],
        },
        "queries": [],
    }


def _water_gun_plan(
    response: dict[str, Any] | None = None,
    **kwargs: Any,
) -> QueryPlan:
    engine = InvalidityAnalysisEngine(
        FakeVisionClient(response or _water_gun_terminology_response())
    )
    return engine.plan_queries(
        claim_id="claim-1",
        expanded_claim_text=(
            "一种水枪，包括储水罐和泄压迸射阀组，"
            "泄压迸射阀组用于受压后瞬间释放水流。"
        ),
        target_images=["target-figure.png"],
        **kwargs,
    )


def test_i2_terminology_rewrite_fields_parsed_and_defaulted() -> None:
    """自创术语改写两字段解析；旧画像缺省或字段非法时容忍为 None。"""

    plan = _water_gun_plan()
    concept = plan.invention_search_profile.inventive_point_features[0]
    assert concept.generic_component is not None
    assert concept.generic_component.text == "阀"
    assert concept.generic_component.synonyms_zh == []
    assert concept.generic_component.synonyms_en == ["valve"]
    assert concept.generic_component.source_reference == "说明书发明内容"
    assert concept.function_effect is not None
    assert concept.function_effect.text == "迸射"
    assert concept.function_effect.synonyms_zh == ["水弹"]
    assert concept.function_effect.synonyms_en == ["Shot", "water bullet"]

    legacy_plan = InvalidityAnalysisEngine(
        FakeVisionClient(_query_plan_response())
    ).plan_queries(
        claim_id="claim-1",
        expanded_claim_text="一种扫地机器人，包括移动底盘、可升降清洁件和地面传感器。",
        target_images=["target-figure.png"],
    )
    for item in legacy_plan.invention_search_profile.inventive_point_features:
        assert item.generic_component is None
        assert item.function_effect is None

    malformed = _water_gun_terminology_response()
    inventive = malformed["invention_search_profile"]["inventive_point_features"][0]
    inventive["generic_component"] = "阀"
    inventive["function_effect"] = {"synonyms_zh": ["水弹"]}
    malformed_plan = _water_gun_plan(malformed)
    malformed_concept = (
        malformed_plan.invention_search_profile.inventive_point_features[0]
    )
    assert malformed_concept.generic_component is None
    assert malformed_concept.function_effect is None
    assert not any(
        query.query_variant is QueryVariant.OBJECT_PLUS_COMPONENT_PLUS_EFFECT
        for query in malformed_plan.queries
    )


def test_i2_fixed_first_round_lanes_expression_and_provider_scope() -> None:
    """首轮固定五组（宪章 2.15）：②④⑤ 的检索式形态与提供者字段映射。"""

    plan = _water_gun_plan()
    ordinary = {
        query.query_variant: query
        for query in plan.queries
        if query.date_channel == "ordinary_prior_art"
    }
    title_lane = ordinary[QueryVariant.TITLE_OBJECT_PLUS_DESC_INVENTIVE]
    assert title_lane.expression == (
        "(水枪 OR water gun) AND (泄压迸射阀组 OR 迸射阀 OR burst valve)"
    )
    assert title_lane.provider_expression == (
        "(TTL:(水枪 OR water gun)) AND "
        "(DESC:(泄压迸射阀组 OR 迸射阀 OR burst valve))"
    )
    effect_lane = ordinary[QueryVariant.DESC_OBJECT_PLUS_DESC_INVENTIVE_PLUS_EFFECT]
    # 「迸射」与发明点术语「泄压迸射阀组/迸射阀」同一原子词族，确定性去污后
    # 效果组只剩真效果词。
    assert effect_lane.expression == (
        "(水枪 OR water gun) AND (泄压迸射阀组 OR 迸射阀 OR burst valve) AND "
        "(水弹 OR Shot OR water bullet)"
    )
    assert effect_lane.provider_expression == (
        "(DESC:(水枪 OR water gun)) AND "
        "(DESC:(泄压迸射阀组 OR 迸射阀 OR burst valve)) AND "
        "(DESC:(水弹 OR Shot OR water bullet))"
    )
    function_lane = ordinary[QueryVariant.TITLE_KEYWORD_OBJECT_PLUS_DESC_FUNCTION]
    assert function_lane.expression == (
        "(水枪 OR water gun) AND (水弹 OR Shot OR water bullet)"
    )
    # 提供者语法无独立关键词字段：标题关键词客体以 TTL+ABST 组合承载。
    assert function_lane.provider_expression == (
        "(TTL:(水枪 OR water gun) OR ABST:(水枪 OR water gun)) AND "
        "(DESC:(水弹 OR Shot OR water bullet))"
    )
    assert any(
        "title_keyword_object_plus_desc_function 功能效果词组成员与核心发明点"
        "术语组" in note and "迸射" in note
        for note in plan.portfolio_warnings
    )
    for lane in (title_lane, effect_lane, function_lane):
        assert lane.query_role is QueryRole.INVENTIVE_POINT_PRECISION
        assert lane.search_scope is SearchScope.FULL_TEXT
        assert lane.search_objective is SearchObjective.FULL_CLAIM_SINGLE_REFERENCE
        assert lane.compact_fallback_allowed is False
        # 每个 OR 成员都是 ≤6 字原子词（英文短语按整体透传），每组至多 5 个。
        for group in lane.expression.split(" AND "):
            inner = group.strip()
            assert inner.startswith("(") and inner.endswith(")")
            members = inner[1:-1].split(" OR ")
            assert 1 <= len(members) <= 5
            for member in members:
                atoms, dropped = _refine_executable_cn_term(member.strip())
                assert atoms == [member.strip()]
                assert dropped == []


def test_i2_v4_core_meaning_terms_broaden_same_or_group() -> None:
    """具体词保留，核心含义/合理上位词只扩同组 OR，不制造额外 AND。"""

    assert "喷水车" in _derived_core_meaning_terms("水陆喷水车", subject=True)
    assert _derived_core_meaning_terms("第一供水管")[:2] == ["供水管", "供水"]
    assert "吸水" in _derived_core_meaning_terms("水中吸水结构")
    assert _sanitise_subject_core_terms(
        ["水陆", "喷水", "玩具车", "喷水车", "amphibious", "toy vehicle"],
        protected_subject="水陆喷水车",
        subject_synonyms=["amphibious water spray vehicle"],
    ) == ["玩具车", "喷水车", "toy vehicle"]

    subject_expression, _ = _atomize_executable_expression(
        "(水陆喷水车 OR 喷水车 OR 玩具车 OR 玩具水车)",
        technical_subject="水陆喷水车",
        subject_terms=["水陆喷水车", "喷水车", "玩具车", "玩具水车"],
    )
    assert subject_expression == "(水陆喷水车 OR 喷水车 OR 玩具车 OR 玩具水车)"

    feature_expression, notes = _atomize_executable_expression(
        "(车主体底部设置吸水口 OR 吸水)",
        technical_subject="水陆喷水车",
        subject_terms=[],
    )
    assert "吸水" in feature_expression
    assert " AND " not in feature_expression
    assert any("同组核心 OR 原子词" in note for note in notes)

    water_path_expression, _ = _atomize_executable_expression(
        "(车主体底部吸水口通过第一供水管连接水泵进水口 OR "
        "供水管 OR 供水 OR 吸水 OR 直接供水)",
        technical_subject="水陆喷水车",
        subject_terms=[],
    )
    assert water_path_expression == (
        "(体底部吸水口 OR 第一供水管 OR 水泵进水口 OR 供水 OR 吸水)"
    )

    feature = Limitation(
        feature_id="f-water",
        claim_id="1",
        sequence=1,
        text="车主体底部设置吸水口，通过第一供水管连接水泵进水口",
        technical_subject="水陆喷水车",
    )
    concept = SearchConcept(
        concept_id="water-intake",
        text="车主体底部吸水口通过第一供水管连接水泵进水口",
        feature_ids=[feature.feature_id],
        core_meaning_terms_zh=["吸水", "供水"],
        source_reference="权利要求1",
        rationale="水中直接取水",
        preferred_scope=SearchScope.FULL_TEXT,
    )
    profile = InventionSearchProfile(
        protected_subject="水陆喷水车",
        subject_core_terms_zh=["喷水车", "玩具车"],
        classification_anchors=["A63H17/00"],
        classification_anchor_roles={"A63H17/00": "subject"},
        inventive_point_features=[concept],
        mechanism_model=MechanismModel(
            summary="从水中吸水并喷水",
            elements=[
                MechanismElement(
                    element_id="water-flow",
                    kind="technical_role",
                    text="从吸水口吸水并由喷水口喷水",
                    feature_ids=[feature.feature_id],
                    source_reference="说明书",
                    rationale="供水与喷水作用",
                    original_terms=["吸水", "喷水"],
                    operation_terms=["吸水", "喷水"],
                )
            ],
        ),
        invention_summary="从水中吸水并持续喷水",
    )
    candidates, warnings = _deterministic_initial_query_candidates(
        technical_subject="水陆喷水车",
        search_profile=profile,
        limitations={feature.feature_id: feature},
        applicants=["示例玩具公司"],
        target_publication="CN221412222U",
    )
    assert {item["query_variant"] for item in candidates} == {
        "applicant_plus_object",
        "title_object_plus_desc_inventive",
        "classification_plus_desc_inventive",
        "desc_object_plus_desc_inventive_plus_effect",
        "title_keyword_object_plus_desc_function",
    }
    assert any("恢复功能词组" in warning for warning in warnings)
    assert all(
        "喷水车" in item["expression"] and "玩具车" in item["expression"]
        for item in candidates
        if item["query_variant"] != "classification_plus_desc_inventive"
    )


def test_i2_v6_strips_patent_drafting_shell_from_executable_terms() -> None:
    """保留可追溯原文，但可执行词只使用产品客体和特征核心含义。"""

    assert _refine_executable_cn_term("具有升降结构") == (["升降"], [])
    assert _refine_executable_cn_term("带有旋转机构") == (["旋转"], [])
    assert _refine_executable_cn_term("设有吸水装置") == (["吸水"], [])
    assert _derived_core_meaning_terms("具有升降结构") == ["升降"]
    assert _search_subject_term("具有升降结构的室外消火栓") == "室外消火栓"
    assert _refine_executable_subject("具有升降结构的室外消火栓") == (
        "室外消火栓",
        [],
    )
    assert _sanitise_subject_synonyms(
        ["具有升降结构", "具有升降结构的室外消火栓", "消防栓"]
    ) == ["室外消火栓", "消防栓"]

    expression, _ = _atomize_executable_expression(
        "(具有升降结构的室外消火栓 OR 室外消火栓 OR 具有升降结构) AND 具有升降结构",
        technical_subject="具有升降结构的室外消火栓",
        subject_terms=["具有升降结构", "室外消火栓", "消防栓"],
    )
    assert expression == "(室外消火栓 OR 消防栓) AND 升降"

    feature = Limitation(
        feature_id="f-lift",
        claim_id="claim-1",
        sequence=1,
        text="活动块与固定槽配合实现升降导向",
        technical_subject="具有升降结构的室外消火栓",
    )
    concept = SearchConcept(
        concept_id="lift-guide",
        text="活动块与固定槽配合实现升降导向",
        feature_ids=[feature.feature_id],
        core_meaning_terms_zh=["升降"],
        source_reference="权利要求1",
        rationale="升降导向",
        preferred_scope=SearchScope.FULL_TEXT,
    )
    profile = InventionSearchProfile(
        protected_subject="具有升降结构的室外消火栓",
        subject_synonyms_zh=["室外消火栓", "消防栓", "具有升降结构"],
        subject_core_terms_zh=["消火栓", "消防设施"],
        classification_anchors=["A62C35/20"],
        classification_anchor_roles={"A62C35/20": "subject"},
        inventive_point_features=[concept],
        mechanism_model=MechanismModel(
            summary="消火栓本体沿导向结构升降",
            elements=[
                MechanismElement(
                    element_id="lift-motion",
                    kind="motion_state_relation",
                    text="活动块沿固定槽升降",
                    feature_ids=[feature.feature_id],
                    source_reference="权利要求1",
                    rationale="升降导向关系",
                    original_terms=["具有升降结构"],
                    operation_terms=["升降"],
                )
            ],
        ),
        invention_summary="通过活动块与固定槽配合实现升降导向",
    )
    candidates, _warnings = _deterministic_initial_query_candidates(
        technical_subject="具有升降结构的室外消火栓",
        search_profile=profile,
        limitations={feature.feature_id: feature},
        applicants=["示例消防公司"],
        target_publication="CN000000000U",
    )
    assert candidates
    assert all("具有升降结构" not in item["expression"] for item in candidates)
    assert all(
        "室外消火栓" in item["expression"]
        for item in candidates
        if item["query_variant"] != "classification_plus_desc_inventive"
    )
    assert any("升降" in item["expression"] for item in candidates)


def test_i2_first_round_five_lane_mix_and_budget() -> None:
    """首轮组合只有固定五组（每组至多 1 条）；紧预算按最有希望优先级裁减。"""

    plan = _water_gun_plan()
    assert len(plan.queries) <= 5
    fixed_variants = {
        QueryVariant.APPLICANT_PLUS_OBJECT,
        QueryVariant.TITLE_OBJECT_PLUS_DESC_INVENTIVE,
        QueryVariant.CLASSIFICATION_PLUS_DESC_INVENTIVE,
        QueryVariant.DESC_OBJECT_PLUS_DESC_INVENTIVE_PLUS_EFFECT,
        QueryVariant.TITLE_KEYWORD_OBJECT_PLUS_DESC_FUNCTION,
    }
    ordinary_variants = {
        query.query_variant
        for query in plan.queries
        if query.date_channel == "ordinary_prior_art"
    }
    assert ordinary_variants
    assert ordinary_variants <= fixed_variants
    assert all(
        query.compact_fallback_allowed is False
        for query in plan.queries
        if query.date_channel == "ordinary_prior_art"
    )
    # 同型线不复制：每个变体至多一条。
    assert len(ordinary_variants) == len(
        [
            query
            for query in plan.queries
            if query.date_channel == "ordinary_prior_art"
        ]
    )
    # 旧功能表达线（2.14）已被五组取代，不再首轮发射。
    assert not any(
        query.query_variant is QueryVariant.OBJECT_PLUS_COMPONENT_PLUS_EFFECT
        for query in plan.queries
    )

    tight_plan = _water_gun_plan(max_queries=2)
    assert len(tight_plan.queries) <= 2
    # 排名最前的标题客体+说明书核心发明点线在紧预算下也不被裁掉。
    assert any(
        query.query_variant is QueryVariant.TITLE_OBJECT_PLUS_DESC_INVENTIVE
        for query in tight_plan.queries
    )


def test_i2_first_round_lanes_bind_core_point_only_and_or_cap() -> None:
    """多发明点不复制分线：五组只绑定画像首个核心发明点；OR 组最多 5 个成员。"""

    limitations = {
        feature_id: Limitation(
            feature_id=feature_id,
            claim_id="claim-1",
            sequence=sequence,
            text=text,
            technical_subject="水枪",
        )
        for sequence, (feature_id, text) in enumerate(
            [
                ("f2", "旋喷雾化头，用于旋转雾化水流"),
                ("f3", "泄压迸射阀组，用于受压后瞬间释放水流"),
                ("f4", "脉冲推进囊，用于脉冲推进水流"),
            ],
            start=1,
        )
    }

    def concept(
        concept_id: str,
        feature_id: str,
        text: str,
        component: ProfileTermGroup,
        effect: ProfileTermGroup,
    ) -> SearchConcept:
        return SearchConcept(
            concept_id=concept_id,
            text=text,
            feature_ids=[feature_id],
            synonyms_zh=[],
            synonyms_en=[],
            source_reference="权利要求1",
            rationale="自创术语发明点",
            preferred_scope=SearchScope.FULL_TEXT,
            generic_component=component,
            function_effect=effect,
        )

    profile = InventionSearchProfile(
        protected_subject="水枪",
        subject_synonyms_zh=[],
        subject_synonyms_en=["water gun"],
        classification_anchors=[],
        common_context_features=[],
        inventive_point_features=[
            concept(
                "inv-mist",
                "f2",
                "旋喷雾化头",
                ProfileTermGroup(text="喷头", synonyms_en=["nozzle"]),
                ProfileTermGroup(
                    text="雾化",
                    synonyms_zh=["喷雾"],
                    synonyms_en=["atomize", "mist", "spray", "spraying"],
                ),
            ),
            concept(
                "inv-burst",
                "f3",
                "泄压迸射阀组",
                ProfileTermGroup(text="阀", synonyms_en=["valve"]),
                ProfileTermGroup(
                    text="迸射",
                    synonyms_zh=["水弹"],
                    synonyms_en=["Shot", "water bullet"],
                ),
            ),
            concept(
                "inv-pulse",
                "f4",
                "脉冲推进囊",
                ProfileTermGroup(text="囊", synonyms_en=["bladder"]),
                ProfileTermGroup(text="脉冲", synonyms_en=["pulse"]),
            ),
        ],
        invention_summary="多种自创术语构件分别实现各自功能效果。",
    )
    candidates, _warnings = _deterministic_initial_query_candidates(
        technical_subject="水枪",
        search_profile=profile,
        limitations=limitations,
    )
    lanes = [
        candidate
        for candidate in candidates
        if candidate["query_variant"]
        in {
            QueryVariant.DESC_OBJECT_PLUS_DESC_INVENTIVE_PLUS_EFFECT.value,
            QueryVariant.TITLE_KEYWORD_OBJECT_PLUS_DESC_FUNCTION.value,
        }
    ]
    # 三个发明点都有改写词组，但五组只绑定画像首个核心发明点，不复制分线。
    assert len(lanes) == 2
    assert all(lane["concept_ids"] == ["inv-mist"] for lane in lanes)
    for lane in lanes:
        for group in lane["expression"].split(" AND "):
            inner = group.strip()
            if inner.startswith("(") and inner.endswith(")"):
                assert len(inner[1:-1].split(" OR ")) <= 5
        for term_group in lane["feature_term_groups"]:
            assert 1 <= len(term_group) <= 5
        assert "DESC:(" in lane["provider_expression"]
    # 超过 5 个候选成员先确定性截断到 5 个；与核心发明点术语同一原子词族的
    # 成员（雾化、喷雾 均为 旋喷雾化头 的子串）再被去污剔除，效果组只剩
    # 真效果词。
    function_lane = next(
        lane
        for lane in lanes
        if lane["query_variant"]
        == QueryVariant.TITLE_KEYWORD_OBJECT_PLUS_DESC_FUNCTION.value
    )
    effect_group = function_lane["feature_term_groups"][-1]
    assert effect_group == ["atomize", "mist", "spray"]
    assert any(
        "功能效果词组成员与核心发明点术语组" in note
        for note in _warnings
    )


def _five_lane_water_gun_plan() -> QueryPlan:
    """带齐五组事实（申请人/公开号/客体分类号/功能效果词组）的水枪案首轮 plan。"""

    response = _water_gun_terminology_response()
    response["invention_search_profile"]["classification_anchors"] = ["F41B 9/00"]
    return _water_gun_plan(
        response,
        patent_context={
            "patent_number": "CN112345678A",
            "title": "水枪",
            "bibliographic_data": {
                "applicants": ["示例玩具公司"],
                "publication_number": "CN112345678A",
                "classifications": ["F41B 9/00"],
            },
        },
    )


def test_i2_first_round_five_lanes_full_shape() -> None:
    """五组事实齐备时首轮即五条固定线，provider 字段映射逐组精确。"""

    plan = _five_lane_water_gun_plan()
    ordinary = {
        query.query_variant: query
        for query in plan.queries
        if query.date_channel == "ordinary_prior_art"
    }
    assert set(ordinary) == {
        QueryVariant.APPLICANT_PLUS_OBJECT,
        QueryVariant.TITLE_OBJECT_PLUS_DESC_INVENTIVE,
        QueryVariant.CLASSIFICATION_PLUS_DESC_INVENTIVE,
        QueryVariant.DESC_OBJECT_PLUS_DESC_INVENTIVE_PLUS_EFFECT,
        QueryVariant.TITLE_KEYWORD_OBJECT_PLUS_DESC_FUNCTION,
    }
    # 首轮执行预算至多 5 条（抵触申请通道另有同型复制）。
    assert len(ordinary) <= 5
    applicant_lane = ordinary[QueryVariant.APPLICANT_PLUS_OBJECT]
    assert applicant_lane.applicant_terms == ["示例玩具公司"]
    assert applicant_lane.excluded_publication == "CN112345678A"
    assert applicant_lane.provider_expression == (
        "(AN:(示例玩具公司)) AND "
        "(TTL:(水枪 OR water gun) OR ABST:(水枪 OR water gun))"
    )
    classification_lane = ordinary[QueryVariant.CLASSIFICATION_PLUS_DESC_INVENTIVE]
    assert classification_lane.classification_anchors == ["F41B9/00"]
    assert classification_lane.provider_expression == (
        "(IPC:(F41B9/00)) AND "
        "(DESC:(泄压迸射阀组 OR 迸射阀 OR burst valve))"
    )
    # 申请人/公开号事实在首轮 plan 生成时冻结进 plan，供模块4重编译复用。
    assert plan.target_applicants == ["示例玩具公司"]
    assert plan.target_publication_number == "CN112345678A"


def test_i2_first_round_applicant_lane_skipped_with_trace() -> None:
    """申请人缺失或存在多个有歧义申请人时整组跳过，并在组合留痕中说明。"""

    missing = _water_gun_plan()
    assert not any(
        query.query_variant is QueryVariant.APPLICANT_PLUS_OBJECT
        for query in missing.queries
    )
    assert any(
        "applicant_plus_object 申请人缺失或存在多个有歧义申请人" in note
        for note in missing.portfolio_warnings
    )

    ambiguous = _water_gun_plan(
        patent_context={
            "patent_number": "CN112345678A",
            "title": "水枪",
            "bibliographic_data": {
                "applicants": ["甲公司", "乙公司"],
                "publication_number": "CN112345678A",
            },
        },
    )
    assert not any(
        query.query_variant is QueryVariant.APPLICANT_PLUS_OBJECT
        for query in ambiguous.queries
    )
    assert any(
        "applicant_plus_object 申请人缺失或存在多个有歧义申请人" in note
        for note in ambiguous.portfolio_warnings
    )


def test_i2_target_applicant_collapses_ocr_spaces_between_chinese_characters() -> None:
    assert _target_patent_applicants({
        "bibliographic_data": {
            "applicants": ["深圳 市 星 源 材质 科技 股份 有 限 公 司"],
        },
    }) == ["深圳市星源材质科技股份有限公司"]
    assert _target_patent_applicants({
        "bibliographic_data": {"applicant": "Example Materials Co Ltd"},
    }) == ["Example Materials Co Ltd"]


def test_i2_first_round_applicant_lane_without_publication_kept_with_trace() -> None:
    """有唯一申请人但无公开号时仍发射申请人组并留痕。"""

    plan = _water_gun_plan(
        patent_context={
            "title": "水枪",
            "bibliographic_data": {"applicants": ["示例玩具公司"]},
        },
    )
    lane = next(
        query
        for query in plan.queries
        if query.query_variant is QueryVariant.APPLICANT_PLUS_OBJECT
        and query.date_channel == "ordinary_prior_art"
    )
    assert lane.provider_expression == (
        "(AN:(示例玩具公司)) AND "
        "(TTL:(水枪 OR water gun) OR ABST:(水枪 OR water gun))"
    )
    assert lane.excluded_publication is None
    assert any(
        "applicant_plus_object 缺少目标公开号" in note
        for note in plan.portfolio_warnings
    )


def test_i2_first_round_portfolio_contains_only_fixed_lanes() -> None:
    """首轮组合不含任何 2.15 之前的旧首轮变体；守门接受申请人+客体两类线。"""

    plan = _five_lane_water_gun_plan()
    retired_variants = {
        "maximal_similarity_precision",
        "object_plus_inventive_point",
        "subject_classification_plus_inventive_point",
        "object_plus_inventive_classification",
        "object_plus_two_inventive_points",
        "object_plus_component_plus_effect",
        "system_architecture_recall",
        "title_abstract_concept",
        "target_citation_lookup",
        "target_effect_recall",
    }
    assert not any(
        query.query_variant.value in retired_variants for query in plan.queries
    )
    ordinary_patent = [
        query
        for query in plan.queries
        if query.provider_kind == "patent"
        and query.date_channel == "ordinary_prior_art"
    ]
    assert len(ordinary_patent) <= 5
    # 四类任二守门：申请人组只有 申请人+客体 两类信息（无发明点锚点、无分类
    # 号），仍通过守门进入首轮组合。
    applicant_lane = next(
        query
        for query in ordinary_patent
        if query.query_variant is QueryVariant.APPLICANT_PLUS_OBJECT
    )
    assert applicant_lane.feature_terms == []
    assert applicant_lane.classification_anchors == []
    assert applicant_lane.applicant_terms == ["示例玩具公司"]
    assert applicant_lane.subject_terms


def test_i2_rule_and_prompt_version_stamped() -> None:
    """I2 规则/prompt 版本升版并写入调用审计。"""

    assert I2_RULE_VERSION == "i2-query-plan-v11"
    assert I2_PROMPT_VERSION == "i2-inventive-profile-v10"
    engine = InvalidityAnalysisEngine(
        FakeVisionClient(_water_gun_terminology_response())
    )
    engine.plan_queries(
        claim_id="claim-1",
        expanded_claim_text=(
            "一种水枪，包括储水罐和泄压迸射阀组，"
            "泄压迸射阀组用于受压后瞬间释放水流。"
        ),
        target_images=["target-figure.png"],
    )
    assert engine.last_invocation_audit["rule_version"] == I2_RULE_VERSION
    assert engine.last_invocation_audit["prompt_version"] == I2_PROMPT_VERSION
