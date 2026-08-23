"""Deterministic date eligibility rules for PRC patent invalidity searches.

The rules in this module deliberately do not attempt to decide whether a
document actually discloses a claim.  They only decide which legal analysis
lanes a candidate *may* enter based on verified dates and its source type.

All comparisons are strict date comparisons.  In particular, a publication
on the claim's critical date is not automatically treated as public before the
filing event because a calendar date alone cannot establish the time ordering.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime
from enum import StrEnum
import re
from typing import Any, Mapping, TypeAlias


DateLike: TypeAlias = date | datetime | str | None


class DateCategory(StrEnum):
    """The four date lanes used by the invalidity workflow."""

    ORDINARY_PRIOR_ART = "ordinary_prior_art"
    CONFLICTING_APPLICATION_CANDIDATE = "conflicting_application_candidate"
    POST_DATE_LEAD = "post_date_lead"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class CriticalDateResult:
    """Resolution result for a claim-specific critical date."""

    critical_date: date | None
    basis: str
    requires_human_review: bool
    reason_codes: tuple[str, ...] = ()

    def model_dump(self) -> dict[str, Any]:
        result = asdict(self)
        result["critical_date"] = _iso(self.critical_date)
        result["reason_codes"] = list(self.reason_codes)
        return result


@dataclass(frozen=True, slots=True)
class DateEligibilityResult:
    """A deterministic date classification and its permitted analysis lanes."""

    category: DateCategory
    novelty_eligible: bool
    inventive_step_eligible: bool
    requires_human_review: bool
    critical_date: date | None
    public_availability_date: date | None
    candidate_filing_date: date | None
    candidate_priority_date: date | None
    target_publication_date: date | None
    discovery_date_channel: str
    reason_codes: tuple[str, ...]
    explanation: str

    @property
    def inventive_eligible(self) -> bool:
        """Short alias used by a few API clients."""

        return self.inventive_step_eligible

    def model_dump(self) -> dict[str, Any]:
        result = asdict(self)
        result["category"] = self.category.value
        for key in (
            "critical_date",
            "public_availability_date",
            "candidate_filing_date",
            "candidate_priority_date",
            "target_publication_date",
        ):
            result[key] = _iso(result[key])
        result["reason_codes"] = list(self.reason_codes)
        result["inventive_eligible"] = self.inventive_step_eligible
        return result


def parse_date(value: DateLike) -> date | None:
    """Parse common provider date representations without guessing partial dates.

    Year-only and year/month inputs are intentionally rejected.  Treating them
    as the first or last day of a period could silently change legal eligibility.
    """

    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        return None

    raw = value.strip()
    if not raw:
        return None

    # ISO timestamps are frequent in Atom and metadata responses.
    iso_candidate = raw.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(iso_candidate).date()
    except ValueError:
        pass

    for pattern, fmt in (
        (r"\d{8}", "%Y%m%d"),
        (r"\d{4}/\d{1,2}/\d{1,2}", "%Y/%m/%d"),
        (r"\d{4}\.\d{1,2}\.\d{1,2}", "%Y.%m.%d"),
    ):
        if re.fullmatch(pattern, raw):
            try:
                return datetime.strptime(raw, fmt).date()
            except ValueError:
                return None
    return None


def resolve_critical_date(
    *,
    application_date: DateLike,
    priority_date: DateLike = None,
    priority_verified: bool | None = None,
    application_date_verified: bool = True,
) -> CriticalDateResult:
    """Resolve the patent-level critical date applied to every claim.

    Charter decision 2026-07-25: the MVP determines one critical date per
    patent — the earliest parseable priority date, otherwise the application
    date — and applies it to all claims without per-claim human stops.  Human
    review is required only when no patent-level date can be resolved at all.

    ``priority_verified`` has three meanings:

    - ``True``: the priority was explicitly confirmed; the priority date is
      used with a ``verified_priority_date`` basis;
    - ``False``: priority was reviewed and is not available, so the
      application date is used instead;
    - ``None``: no explicit review exists; the MVP still uses the parseable
      priority date as the patent-level critical date with a
      ``patent_level_priority_date`` basis instead of blocking the workflow.

    Per-subject-matter priority refinement for multi-priority patents is
    intentionally deferred until the owner explicitly restores it.
    """

    application = parse_date(application_date)
    priority = parse_date(priority_date)

    if priority is not None and priority_verified is not False:
        if priority_verified:
            return CriticalDateResult(
                critical_date=priority,
                basis="verified_priority_date",
                requires_human_review=False,
                reason_codes=("verified_priority_used",),
            )
        return CriticalDateResult(
            critical_date=priority,
            basis="patent_level_priority_date",
            requires_human_review=False,
            reason_codes=("patent_level_priority_used",),
        )

    reason_codes: list[str] = []
    if priority_date is not None:
        reason_codes.append(
            "invalid_priority_date" if priority is None else "priority_reviewed_not_available"
        )

    if not application_date_verified:
        reason_codes.append("application_date_not_verified")
        return CriticalDateResult(
            critical_date=None,
            basis="unknown",
            requires_human_review=True,
            reason_codes=tuple(reason_codes),
        )
    if application is None:
        reason_codes.append("missing_or_invalid_application_date")
        return CriticalDateResult(
            critical_date=None,
            basis="unknown",
            requires_human_review=True,
            reason_codes=tuple(reason_codes),
        )

    if not reason_codes:
        reason_codes.append("no_claimed_priority")
    return CriticalDateResult(
        critical_date=application,
        basis="application_date",
        requires_human_review=False,
        reason_codes=tuple(reason_codes),
    )


def classify_date_eligibility(
    data: Mapping[str, Any] | None = None,
    /,
    *,
    critical_date: DateLike = None,
    public_availability_date: DateLike = None,
    publication_date: DateLike = None,
    candidate_filing_date: DateLike = None,
    candidate_priority_date: DateLike = None,
    target_publication_date: DateLike = None,
    source_type: str = "patent",
    publication_number: str | None = None,
    authority: str | None = None,
    cn_application_scope: bool | None = None,
    critical_date_verified: bool = True,
    public_date_verified: bool = True,
    candidate_filing_date_verified: bool = True,
    candidate_priority_date_verified: bool = True,
    target_publication_date_verified: bool = False,
    date_channel: str = "ordinary_prior_art",
) -> DateEligibilityResult:
    """Classify one candidate using deterministic, fail-closed date rules.

    ``data`` may be a plain provider dictionary.  Explicit keyword arguments
    override dictionary values, which keeps this function easy to call from
    fixture tests and adapter code.

    A post-critical-date patent is only promoted to a conflicting-application
    *candidate* when it is within the Chinese application scope and has a
    verified Chinese filing date strictly earlier than the claim's critical
    date.  Technical identity and the other substantive requirements remain for
    the comparison workflow; this function never decides them.
    """

    values: dict[str, Any] = dict(data or {})
    explicit_values = {
        "critical_date": critical_date,
        "public_availability_date": public_availability_date,
        "publication_date": publication_date,
        "candidate_filing_date": candidate_filing_date,
        "candidate_priority_date": candidate_priority_date,
        "target_publication_date": target_publication_date,
        "publication_number": publication_number,
        "authority": authority,
        "cn_application_scope": cn_application_scope,
    }
    for key, value in explicit_values.items():
        if value is not None:
            values[key] = value

    # These defaults should still be overrideable by dictionaries.
    if "source_type" not in values or source_type != "patent":
        values["source_type"] = source_type
    if "critical_date_verified" not in values or critical_date_verified is not True:
        values["critical_date_verified"] = critical_date_verified
    if "public_date_verified" not in values or public_date_verified is not True:
        values["public_date_verified"] = public_date_verified
    if (
        "candidate_filing_date_verified" not in values
        or candidate_filing_date_verified is not True
    ):
        values["candidate_filing_date_verified"] = candidate_filing_date_verified
    if (
        "candidate_priority_date_verified" not in values
        or candidate_priority_date_verified is not True
    ):
        values["candidate_priority_date_verified"] = candidate_priority_date_verified
    if (
        "target_publication_date_verified" not in values
        or target_publication_date_verified is not False
    ):
        values["target_publication_date_verified"] = target_publication_date_verified
    if "date_channel" not in values or date_channel != "ordinary_prior_art":
        values["date_channel"] = date_channel

    critical_raw = values.get("critical_date")
    public_raw = values.get("public_availability_date") or values.get("publication_date")
    filing_raw = values.get("candidate_filing_date", values.get("filing_date"))
    priority_raw = values.get("candidate_priority_date", values.get("priority_date"))
    target_publication_raw = values.get("target_publication_date")

    critical = parse_date(critical_raw)
    public = parse_date(public_raw)
    filing = parse_date(filing_raw)
    priority = parse_date(priority_raw)
    target_publication = parse_date(target_publication_raw)
    discovery_channel = str(values.get("date_channel") or "ordinary_prior_art").strip()

    invalid_reasons: list[str] = []
    if not bool(values.get("critical_date_verified", True)):
        invalid_reasons.append("critical_date_not_verified")
    if critical is None:
        invalid_reasons.append("missing_or_invalid_critical_date")
    if not bool(values.get("public_date_verified", True)):
        invalid_reasons.append("public_date_not_verified")
    if public is None:
        invalid_reasons.append("missing_or_invalid_public_date")
    if discovery_channel not in {
        "ordinary_prior_art",
        "cn_conflicting_application",
    }:
        invalid_reasons.append("invalid_date_channel")

    if invalid_reasons:
        return _result(
            DateCategory.UNKNOWN,
            critical,
            public,
            filing,
            priority,
            target_publication,
            discovery_channel,
            invalid_reasons,
            "关键日或公开可得日期缺失、格式无效或尚未核验，需人工复核。",
        )

    assert critical is not None and public is not None
    if public < critical:
        return _result(
            DateCategory.ORDINARY_PRIOR_ART,
            critical,
            public,
            filing,
            priority,
            target_publication,
            discovery_channel,
            ("public_before_critical_date",),
            "材料在该权利要求关键日前已公开，可进入新颖性和创造性分析。",
        )

    normalized_source_type = str(values.get("source_type") or "").strip().lower()
    is_patent_source = normalized_source_type in {
        "patent",
        "patent_application",
        "application",
        "utility_model",
    }
    inferred_cn_scope = _is_cn_application(
        publication_number=values.get("publication_number"),
        authority=values.get("authority"),
    )
    explicit_cn_scope = values.get("cn_application_scope")
    is_cn_scope = bool(explicit_cn_scope) if explicit_cn_scope is not None else inferred_cn_scope

    if is_patent_source and is_cn_scope:
        target_verified = bool(values.get("target_publication_date_verified", False))
        if not target_verified or target_publication is None:
            reasons = [
                "target_publication_date_not_verified"
                if not target_verified
                else "missing_or_invalid_target_publication_date",
                "conflicting_application_window_not_established",
            ]
            return _result(
                DateCategory.UNKNOWN,
                critical,
                public,
                filing,
                priority,
                target_publication,
                discovery_channel,
                reasons,
                "中国抵触申请候选通道缺少用户确认的目标公开日上界。",
            )
        if target_publication <= critical:
            return _result(
                DateCategory.UNKNOWN,
                critical,
                public,
                filing,
                priority,
                target_publication,
                discovery_channel,
                ("target_publication_not_after_critical_date",),
                "目标公开日未晚于关键日，无法建立抵触申请检索窗口。",
            )
        if public >= target_publication:
            return _result(
                DateCategory.POST_DATE_LEAD,
                critical,
                public,
                filing,
                priority,
                target_publication,
                discovery_channel,
                (
                    "not_public_before_critical_date",
                    "not_before_target_publication_date",
                    "outside_conflicting_application_window",
                ),
                "材料在目标专利公开日当日或之后才公开，不进入抵触申请候选窗口。",
            )

        filing_verified = bool(values.get("candidate_filing_date_verified", True))
        priority_verified = bool(values.get("candidate_priority_date_verified", True))
        verified_earlier_dates = [
            value
            for value, verified in (
                (filing, filing_verified),
                (priority, priority_verified),
            )
            if verified and value is not None
        ]
        if not verified_earlier_dates:
            reasons = [
                "candidate_filing_date_not_verified"
                if not filing_verified
                else "missing_or_invalid_candidate_filing_date",
                "post_critical_publication_may_be_conflicting_application",
            ]
            return _result(
                DateCategory.UNKNOWN,
                critical,
                public,
                filing,
                priority,
                target_publication,
                discovery_channel,
                reasons,
                "中国专利材料在关键日当日或之后公开，但其在先申请日尚不能核验。",
            )
        if min(verified_earlier_dates) < critical:
            return _result(
                DateCategory.CONFLICTING_APPLICATION_CANDIDATE,
                critical,
                public,
                filing,
                priority,
                target_publication,
                discovery_channel,
                (
                    "cn_application_or_priority_before_critical_date",
                    "published_on_or_after_critical_date",
                    "published_before_target_publication_date",
                    "novelty_only_date_lane",
                ),
                "中国在先申请、关键日当日或之后公开；仅作为抵触申请候选进入新颖性分析。",
            )

    reasons: list[str] = ["not_public_before_critical_date"]
    if public == critical:
        reasons.append("same_day_order_not_proven")
    if is_patent_source and is_cn_scope and filing is not None and filing >= critical:
        reasons.append("candidate_filing_not_before_critical_date")
    else:
        reasons.append("conflicting_application_lane_not_established")
    return _result(
        DateCategory.POST_DATE_LEAD,
        critical,
        public,
        filing,
        priority,
        target_publication,
        discovery_channel,
        reasons,
        "材料未证明在关键日前公开，且不满足确定的抵触申请日期入口，只能作为追溯线索。",
    )


def _result(
    category: DateCategory,
    critical: date | None,
    public: date | None,
    filing: date | None,
    priority: date | None,
    target_publication: date | None,
    discovery_channel: str,
    reasons: list[str] | tuple[str, ...],
    explanation: str,
) -> DateEligibilityResult:
    novelty = category in {
        DateCategory.ORDINARY_PRIOR_ART,
        DateCategory.CONFLICTING_APPLICATION_CANDIDATE,
    }
    inventive = category is DateCategory.ORDINARY_PRIOR_ART
    return DateEligibilityResult(
        category=category,
        novelty_eligible=novelty,
        inventive_step_eligible=inventive,
        requires_human_review=category is DateCategory.UNKNOWN,
        critical_date=critical,
        public_availability_date=public,
        candidate_filing_date=filing,
        candidate_priority_date=priority,
        target_publication_date=target_publication,
        discovery_date_channel=discovery_channel,
        reason_codes=tuple(reasons),
        explanation=explanation,
    )


def _is_cn_application(*, publication_number: Any, authority: Any) -> bool:
    normalized_authority = re.sub(r"[^A-Z]", "", str(authority or "").upper())
    if normalized_authority in {"CN", "CNIPA", "CHINA"}:
        return True
    normalized_number = re.sub(r"[^A-Z0-9]", "", str(publication_number or "").upper())
    return normalized_number.startswith("CN")


def _iso(value: date | None) -> str | None:
    return value.isoformat() if value is not None else None


__all__ = [
    "CriticalDateResult",
    "DateCategory",
    "DateEligibilityResult",
    "classify_date_eligibility",
    "parse_date",
    "resolve_critical_date",
]
