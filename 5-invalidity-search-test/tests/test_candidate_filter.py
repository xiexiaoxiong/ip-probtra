from __future__ import annotations

from invalidity.candidate_filter import (
    apply_filter_decisions,
    build_raw_candidates,
    group_candidates,
    matched_technical_terms,
    publication_identity,
    target_filter_context,
)
from invalidity.lab import _fixture_query_plan


def _patent(
    publication_number: str,
    *,
    title: str,
    application_number: str | None = None,
) -> dict[str, object]:
    return {
        "provider": "patsnap",
        "source_type": "patent",
        "external_id": publication_number,
        "publication_number": publication_number,
        "title": title,
        "source_url": f"https://example.test/{publication_number}",
        "raw_metadata": {
            "application_number": application_number,
        },
    }


def _search(documents: list[dict[str, object]], *, run_id: str = "run-1"):
    return {
        "search_run_id": run_id,
        "query_id": f"{run_id}-query",
        "query_text": "optical sensor filter",
        "provider_kind": "patent",
        "documents": documents,
    }


def _target_context() -> dict[str, object]:
    return target_filter_context(
        _fixture_query_plan().model_dump(mode="json")
    )


def test_publication_identity_removes_only_kind_code() -> None:
    assert publication_identity("WO 2019/112939 A4") == (
        "WO2019112939",
        "A4",
    )
    assert publication_identity("arxiv:2401.12345") == (
        "ARXIV240112345",
        None,
    )


def test_same_application_and_kind_versions_are_one_fetch_candidate() -> None:
    raw = build_raw_candidates(
        [
            _search(
                [
                    _patent(
                        "WO2019112939A3",
                        title="Refrigerator assembly",
                        application_number="PCT/US2018/063576",
                    ),
                    _patent(
                        "WO2019112939A4",
                        title="Refrigerator assembly",
                        application_number="PCT/US2018/063576",
                    ),
                    _patent(
                        "WO2019112939A2",
                        title="Refrigerator assembly",
                        application_number="PCT/US2018/063576",
                    ),
                ]
            )
        ]
    )
    groups, duplicates = group_candidates(raw)
    assert len(raw) == 3
    assert len(groups) == 1
    assert len(duplicates) == 2
    assert (
        groups[0]["representative_document"]["publication_number"]
        == "WO2019112939A2"
    )
    assert "application:PCTUS2018063576" in groups[0]["identity_keys"]


def test_same_document_across_queries_keeps_all_query_lineage() -> None:
    document = _patent(
        "US20190000001A1",
        title="Optical sensing device with filter",
        application_number="US16/000001",
    )
    raw = build_raw_candidates(
        [
            _search([document], run_id="run-1"),
            _search([document], run_id="run-2"),
        ]
    )
    groups, duplicates = group_candidates(raw)
    assert len(groups) == 1
    assert len(duplicates) == 1
    assert {item["search_run_id"] for item in groups[0]["source_lineage"]} == {
        "run-1",
        "run-2",
    }


def test_reliable_family_id_does_not_merge_conflicting_same_language_titles() -> None:
    left = _patent(
        "US20200000001A1",
        title="Water nozzle pressure valve assembly",
    )
    right = _patent(
        "EP3000001A1",
        title="Refrigerator shelf temperature controller",
    )
    left["raw_metadata"]["family_id"] = "FAMILY-1"
    right["raw_metadata"]["family_id"] = "FAMILY-1"
    raw = build_raw_candidates([_search([left, right])])
    groups, duplicates = group_candidates(raw)
    assert len(groups) == 2
    assert duplicates == []


def test_strong_target_anchor_cannot_be_overridden_by_model_exclusion() -> None:
    raw = build_raw_candidates(
        [
            _search(
                [
                    _patent(
                        "US20190000001A1",
                        title="Optical sensing device with filter",
                    )
                ]
            )
        ]
    )
    groups, _duplicates = group_candidates(raw)
    representative_id = groups[0]["representative_candidate_id"]
    result = apply_filter_decisions(
        groups,
        target_context=_target_context(),
        model_decisions={
            representative_id: {
                "status": "exclude_obvious_unrelated",
                "reason": "incorrect model suggestion",
            }
        },
    )
    assert len(result["fetch_candidates"]) == 1
    assert not result["excluded_candidates"]
    assert (
        result["fetch_candidates"][0]["decision_guard"]
        == "strong_positive_anchor"
    )
    assert (
        result["fetch_candidates"][0]["filter_contract_version"]
        == "i3-candidate-filter-v1"
    )


def test_missing_metadata_fails_open_even_if_model_wants_to_exclude() -> None:
    raw = build_raw_candidates(
        [
            _search(
                [
                    _patent(
                        "US20190000002A1",
                        title="Device",
                    )
                ]
            )
        ]
    )
    groups, _duplicates = group_candidates(raw)
    representative_id = groups[0]["representative_candidate_id"]
    result = apply_filter_decisions(
        groups,
        target_context=_target_context(),
        model_decisions={
            representative_id: {
                "status": "exclude_obvious_unrelated",
                "reason": "too little data",
            }
        },
    )
    assert result["fetch_candidates"][0]["filter_status"] == "keep_uncertain"
    assert (
        result["fetch_candidates"][0]["decision_guard"]
        == "insufficient_metadata_fail_open"
    )


def test_model_failure_keeps_every_non_deduplicated_representative() -> None:
    raw = build_raw_candidates(
        [
            _search(
                [
                    _patent(
                        "US20190000003A1",
                        title="Refrigerator shelf adjustment assembly",
                    ),
                    _patent(
                        "US20190000004A1",
                        title="Cleaning apparatus for optical surfaces",
                    ),
                ]
            )
        ]
    )
    groups, _duplicates = group_candidates(raw)
    result = apply_filter_decisions(
        groups,
        target_context=_target_context(),
        model_failure="MultimodalModelError",
    )
    assert len(result["fetch_candidates"]) == 2
    assert not result["excluded_candidates"]
    assert {
        item["decision_guard"] for item in result["fetch_candidates"]
    } <= {"model_failure_fail_open", "target_technical_term_guard"}
    assert any(
        item["decision_guard"] == "model_failure_fail_open"
        for item in result["fetch_candidates"]
    )


def test_exclusion_requires_complete_high_confidence_no_analogy_checks() -> None:
    raw = build_raw_candidates(
        [
            _search(
                [
                    _patent(
                        "US20190000005A1",
                        title="Pistol grip hose nozzle",
                    )
                ]
            )
        ]
    )
    groups, _duplicates = group_candidates(raw)
    representative_id = groups[0]["representative_candidate_id"]
    incomplete = apply_filter_decisions(
        groups,
        target_context=_target_context(),
        model_decisions={
            representative_id: {
                "status": "exclude_obvious_unrelated",
                "confidence": "high",
                "reason": "different title",
            }
        },
    )
    assert incomplete["fetch_candidates"][0]["decision_guard"] == (
        "incomplete_exclusion_basis_fail_open"
    )

    analogous = apply_filter_decisions(
        groups,
        target_context=_target_context(),
        model_decisions={
            representative_id: {
                "status": "exclude_obvious_unrelated",
                "confidence": "high",
                "reason": "different product",
                "exclusion_checks": {
                    "same_or_related_object": False,
                    "shared_medium_or_energy": True,
                    "shared_component_role": True,
                    "shared_operation_or_control": False,
                    "analogous_mechanism_possible": True,
                },
            }
        },
    )
    assert analogous["fetch_candidates"][0]["decision_guard"] == (
        "analogous_mechanism_guard"
    )

    unrelated = apply_filter_decisions(
        groups,
        target_context=_target_context(),
        model_decisions={
            representative_id: {
                "status": "exclude_obvious_unrelated",
                "confidence": "high",
                "reason": "no shared technical basis",
                "exclusion_checks": {
                    "same_or_related_object": False,
                    "shared_medium_or_energy": False,
                    "shared_component_role": False,
                    "shared_operation_or_control": False,
                    "analogous_mechanism_possible": False,
                },
            }
        },
    )
    assert len(unrelated["excluded_candidates"]) == 1


def test_target_derived_technical_word_overlap_forces_recall() -> None:
    raw = build_raw_candidates(
        [
            _search(
                [
                    _patent(
                        "US20190000006A1",
                        title="Water current control apparatus",
                    )
                ]
            )
        ]
    )
    groups, _duplicates = group_candidates(raw)
    representative_id = groups[0]["representative_candidate_id"]
    context = _target_context()
    context["protected_subject"] = "water gun"
    result = apply_filter_decisions(
        groups,
        target_context=context,
        model_decisions={
            representative_id: {
                "status": "exclude_obvious_unrelated",
                "confidence": "high",
                "reason": "different product",
                "exclusion_checks": {
                    "same_or_related_object": False,
                    "shared_medium_or_energy": False,
                    "shared_component_role": False,
                    "shared_operation_or_control": False,
                    "analogous_mechanism_possible": False,
                },
            }
        },
    )
    assert result["fetch_candidates"][0]["decision_guard"] == (
        "target_technical_term_guard"
    )
    assert "water" in result["fetch_candidates"][0]["matched_technical_terms"]


def test_generic_coupling_word_does_not_protect_an_unrelated_field() -> None:
    context = {
        "protected_subject": "water gun",
        "subject_synonyms_zh": [],
        "subject_synonyms_en": [],
        "classification_anchors": [],
        "concepts": [
            {
                "text": "position selective coupling device",
                "synonyms_zh": [],
                "synonyms_en": [],
            }
        ],
        "mechanism_elements": [],
    }
    document = _patent(
        "US20060210222A1",
        title="Connector device for coupling optical fibres",
    )
    assert matched_technical_terms(document, context) == []
