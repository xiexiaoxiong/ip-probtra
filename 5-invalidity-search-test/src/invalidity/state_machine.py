from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from invalidity.contracts import ClaimStatus, DisclosureStatus


TERMINAL_CLAIM_STATES = {
    ClaimStatus.NOVELTY_EVIDENCE_COMPLETE,
    ClaimStatus.INVENTIVE_STEP_EVIDENCE_COMPLETE,
    ClaimStatus.EXHAUSTED,
    ClaimStatus.LEGACY_EXHAUSTED,
    ClaimStatus.NEEDS_HUMAN_REVIEW,
    ClaimStatus.PARTIAL,
    ClaimStatus.FAILED,
    ClaimStatus.CANCELLED,
}


ALLOWED_TRANSITIONS: dict[ClaimStatus, set[ClaimStatus]] = {
    ClaimStatus.QUEUED: {
        ClaimStatus.CRITICAL_DATE_REVIEW,
        ClaimStatus.NEEDS_HUMAN_REVIEW,
        ClaimStatus.CANCELLED,
        ClaimStatus.FAILED,
    },
    ClaimStatus.CRITICAL_DATE_REVIEW: {
        ClaimStatus.INITIAL_SEARCH,
        ClaimStatus.NEEDS_HUMAN_REVIEW,
        ClaimStatus.FAILED,
        ClaimStatus.CANCELLED,
    },
    ClaimStatus.INITIAL_SEARCH: {
        ClaimStatus.SINGLE_REFERENCE_REVIEW,
        ClaimStatus.NEEDS_HUMAN_REVIEW,
        ClaimStatus.PARTIAL,
        ClaimStatus.FAILED,
        ClaimStatus.CANCELLED,
    },
    ClaimStatus.GAP_SEARCH: {
        ClaimStatus.SINGLE_REFERENCE_REVIEW,
        ClaimStatus.NEEDS_HUMAN_REVIEW,
        ClaimStatus.PARTIAL,
        ClaimStatus.FAILED,
        ClaimStatus.CANCELLED,
    },
    ClaimStatus.SINGLE_REFERENCE_REVIEW: {
        ClaimStatus.NOVELTY_EVIDENCE_COMPLETE,
        ClaimStatus.CLOSEST_PRIOR_ART_SELECTED,
        ClaimStatus.GAP_SEARCH,
        ClaimStatus.NEEDS_HUMAN_REVIEW,
        ClaimStatus.EXHAUSTED,
        ClaimStatus.PARTIAL,
        ClaimStatus.FAILED,
        ClaimStatus.CANCELLED,
    },
    ClaimStatus.CLOSEST_PRIOR_ART_SELECTED: {
        ClaimStatus.COMBINATION_REVIEW,
        ClaimStatus.GAP_SEARCH,
        ClaimStatus.NEEDS_HUMAN_REVIEW,
        ClaimStatus.EXHAUSTED,
        ClaimStatus.PARTIAL,
        ClaimStatus.FAILED,
        ClaimStatus.CANCELLED,
    },
    ClaimStatus.COMBINATION_REVIEW: {
        ClaimStatus.INVENTIVE_STEP_EVIDENCE_COMPLETE,
        ClaimStatus.GAP_SEARCH,
        ClaimStatus.NEEDS_HUMAN_REVIEW,
        ClaimStatus.EXHAUSTED,
        ClaimStatus.PARTIAL,
        ClaimStatus.FAILED,
        ClaimStatus.CANCELLED,
    },
}


class InvalidTransition(ValueError):
    pass


def assert_transition(current: ClaimStatus | str, target: ClaimStatus | str) -> None:
    current_status = ClaimStatus(current)
    target_status = ClaimStatus(target)
    if current_status in TERMINAL_CLAIM_STATES:
        raise InvalidTransition(f"终态 {current_status} 不能自动转移到 {target_status}")
    if target_status not in ALLOWED_TRANSITIONS.get(current_status, set()):
        raise InvalidTransition(f"非法状态转移: {current_status} -> {target_status}")


@dataclass(frozen=True, slots=True)
class RoundProgress:
    qualified_documents_added: int
    uncovered_before: int
    uncovered_after: int
    combination_gaps_before: int
    combination_gaps_after: int
    closest_prior_art_improved: bool
    provider_complete: bool

    @property
    def no_progress(self) -> bool:
        if not self.provider_complete:
            return False
        return (
            self.qualified_documents_added == 0
            and self.uncovered_after >= self.uncovered_before
            and self.combination_gaps_after >= self.combination_gaps_before
            and not self.closest_prior_art_improved
        )


def aggregate_single_document_novelty(
    mandatory_feature_ids: Iterable[str],
    disclosures: Iterable[dict[str, object]],
    *,
    eligible_for_novelty: bool,
) -> bool:
    """A deterministic one-document aggregation; never combines disclosures across docs."""
    if not eligible_for_novelty:
        return False
    accepted = {
        DisclosureStatus.EXPLICIT.value,
        DisclosureStatus.DIRECT_AND_UNAMBIGUOUS.value,
        DisclosureStatus.NECESSARILY_IMPLICIT.value,
    }
    covered = {
        str(item.get("feature_id", ""))
        for item in disclosures
        if str(item.get("status", "")) in accepted
        and float(item.get("confidence", 0) or 0) >= 0.7
    }
    return set(mandatory_feature_ids).issubset(covered)


def next_round_or_stop(
    *,
    current_round: int,
    max_rounds: int,
    progress: RoundProgress,
    has_complete_inventive_combination: bool,
) -> ClaimStatus:
    if has_complete_inventive_combination:
        return ClaimStatus.INVENTIVE_STEP_EVIDENCE_COMPLETE
    if not progress.provider_complete:
        return ClaimStatus.PARTIAL
    # A no-progress gap iteration is still an auditable failed search attempt;
    # it consumes one of the five budgets but does not itself justify early exit.
    if current_round >= max_rounds:
        return ClaimStatus.EXHAUSTED
    return ClaimStatus.GAP_SEARCH
