from datetime import date

from invalidity.date_rules import (
    DateCategory,
    classify_date_eligibility,
    parse_date,
    resolve_critical_date,
)


def test_ordinary_prior_art_is_eligible_for_novelty_and_inventive_step() -> None:
    result = classify_date_eligibility(
        critical_date="2020-06-15",
        publication_date="2019-12-01",
        source_type="manual_document",
    )

    assert result.category is DateCategory.ORDINARY_PRIOR_ART
    assert result.novelty_eligible is True
    assert result.inventive_step_eligible is True
    assert result.requires_human_review is False
    assert result.model_dump()["public_availability_date"] == "2019-12-01"


def test_cn_earlier_application_later_publication_is_novelty_only_candidate() -> None:
    result = classify_date_eligibility(
        critical_date="2020-06-15",
        publication_date="2021-01-02",
        target_publication_date="2021-06-01",
        target_publication_date_verified=True,
        candidate_filing_date="2020-03-10",
        source_type="patent_application",
        publication_number="CN112345678A",
        date_channel="cn_conflicting_application",
    )

    assert result.category is DateCategory.CONFLICTING_APPLICATION_CANDIDATE
    assert result.novelty_eligible is True
    assert result.inventive_step_eligible is False
    assert "novelty_only_date_lane" in result.reason_codes
    assert result.model_dump()["target_publication_date"] == "2021-06-01"


def test_cn_conflicting_application_window_fails_closed_without_confirmed_target_date() -> None:
    result = classify_date_eligibility(
        critical_date="2020-06-15",
        publication_date="2021-01-02",
        candidate_filing_date="2020-03-10",
        source_type="patent_application",
        publication_number="CN112345678A",
        date_channel="cn_conflicting_application",
    )

    assert result.category is DateCategory.UNKNOWN
    assert result.requires_human_review is True
    assert result.novelty_eligible is False
    assert "target_publication_date_not_verified" in result.reason_codes


def test_cn_document_published_on_or_after_target_publication_is_not_conflicting_art() -> None:
    result = classify_date_eligibility(
        critical_date="2020-06-15",
        publication_date="2021-06-01",
        target_publication_date="2021-06-01",
        target_publication_date_verified=True,
        candidate_filing_date="2020-03-10",
        source_type="patent_application",
        publication_number="CN112345678A",
        date_channel="cn_conflicting_application",
    )

    assert result.category is DateCategory.POST_DATE_LEAD
    assert result.novelty_eligible is False
    assert "not_before_target_publication_date" in result.reason_codes


def test_foreign_post_date_patent_is_only_a_lead() -> None:
    result = classify_date_eligibility(
        critical_date="2020-06-15",
        publication_date="2021-01-02",
        candidate_filing_date="2019-03-10",
        source_type="patent",
        publication_number="US20210001234A1",
    )

    assert result.category is DateCategory.POST_DATE_LEAD
    assert result.novelty_eligible is False
    assert result.inventive_step_eligible is False


def test_same_day_publication_does_not_silently_become_prior_art() -> None:
    result = classify_date_eligibility(
        critical_date="2020-06-15",
        publication_date="2020-06-15",
        source_type="article",
    )

    assert result.category is DateCategory.POST_DATE_LEAD
    assert "same_day_order_not_proven" in result.reason_codes


def test_cn_post_date_document_with_unknown_filing_date_fails_closed() -> None:
    result = classify_date_eligibility(
        critical_date="2020-06-15",
        publication_date="2021-01-02",
        target_publication_date="2021-06-01",
        target_publication_date_verified=True,
        source_type="patent",
        publication_number="CN112345678A",
        date_channel="cn_conflicting_application",
    )

    assert result.category is DateCategory.UNKNOWN
    assert result.requires_human_review is True
    assert result.novelty_eligible is False


def test_missing_or_unverified_public_date_is_unknown() -> None:
    missing = classify_date_eligibility(
        critical_date="2020-06-15",
        publication_date=None,
        source_type="datasheet",
    )
    unverified = classify_date_eligibility(
        critical_date="2020-06-15",
        publication_date="2018-01-01",
        public_date_verified=False,
        source_type="forum",
    )

    assert missing.category is DateCategory.UNKNOWN
    assert unverified.category is DateCategory.UNKNOWN
    assert unverified.inventive_step_eligible is False


def test_plain_mapping_input_is_supported() -> None:
    result = classify_date_eligibility(
        {
            "critical_date": "2022-03-01",
            "public_availability_date": "2020-09-01",
            "source_type": "preprint",
        }
    )

    assert result.category.value == "ordinary_prior_art"


def test_patent_level_critical_date_prefers_priority_then_application() -> None:
    # 宪章 2026-07-25 决定：关键日按专利确定一次，自动适用于全部权利要求；
    # 未核验的优先权不再阻断流程，只有专利级日期全缺才转人工。
    unverified = resolve_critical_date(
        application_date="2020-06-15",
        priority_date="2019-06-15",
        priority_verified=None,
    )
    verified = resolve_critical_date(
        application_date="2020-06-15",
        priority_date="2019-06-15",
        priority_verified=True,
    )
    rejected = resolve_critical_date(
        application_date="2020-06-15",
        priority_date="2019-06-15",
        priority_verified=False,
    )

    assert unverified.critical_date == date(2019, 6, 15)
    assert unverified.requires_human_review is False
    assert unverified.basis == "patent_level_priority_date"
    assert verified.critical_date == date(2019, 6, 15)
    assert verified.basis == "verified_priority_date"
    assert rejected.critical_date == date(2020, 6, 15)
    assert rejected.basis == "application_date"


def test_missing_patent_level_dates_require_human_review() -> None:
    missing = resolve_critical_date(application_date=None, priority_date=None)
    invalid = resolve_critical_date(
        application_date=None,
        priority_date="not-a-date",
    )

    assert missing.critical_date is None
    assert missing.requires_human_review is True
    assert "missing_or_invalid_application_date" in missing.reason_codes
    assert invalid.critical_date is None
    assert invalid.requires_human_review is True
    assert "invalid_priority_date" in invalid.reason_codes


def test_parse_date_rejects_partial_dates_and_accepts_provider_formats() -> None:
    assert parse_date("2020") is None
    assert parse_date("2020-06") is None
    assert parse_date("20200615") == date(2020, 6, 15)
    assert parse_date("2020-06-15T01:02:03Z") == date(2020, 6, 15)
