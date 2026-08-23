"""Conservative pre-fetch cleanup for patent and NPL search candidates.

The module deliberately separates deterministic identity grouping from semantic
technical relevance.  Identity rules may merge duplicate publications; a model
may only exclude a unique representative when the available bibliographic
metadata makes an unrelated technical field obvious.  Missing or ambiguous
metadata always fails open to retrieval.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Mapping, Sequence


FILTER_CONTRACT_VERSION = "i3-candidate-filter-v1"
IDENTITY_RULE_VERSION = "candidate-identity-v1"
SEMANTIC_RULE_VERSION = "candidate-semantic-recall-v1"

FILTER_STATUSES = frozenset(
    {
        "keep_relevant",
        "keep_uncertain",
        "exclude_obvious_unrelated",
    }
)

_GENERIC_ANCHORS = frozenset(
    {
        "apparatus",
        "assembly",
        "component",
        "connect",
        "connected",
        "connecting",
        "connection",
        "connector",
        "couple",
        "coupled",
        "coupling",
        "device",
        "equipment",
        "method",
        "module",
        "system",
        "unit",
        "with",
        "within",
        "装置",
        "设备",
        "系统",
        "组件",
        "模块",
        "方法",
        "结构",
    }
)
_PATENT_KIND_RE = re.compile(
    r"^(?P<authority>WO|US|EP|CN|AU|CA|GB|JP|KR|DE|FR)"
    r"(?P<number>\d+)(?P<kind>[A-Z]\d?|[A-Z])$"
)
_SUBSTANTIVE_KIND_PRIORITY = {
    "A1": 0,
    "A2": 1,
    "B1": 2,
    "B2": 3,
    "U": 4,
    "U1": 4,
    "Y": 5,
    "A": 6,
    "A3": 20,
    "A4": 21,
    "A8": 22,
    "A9": 23,
}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _compact_id(value: Any) -> str:
    return re.sub(r"[^A-Z0-9]", "", _text(value).upper())


def _normal_text(value: Any) -> str:
    return re.sub(r"\s+", " ", _text(value)).strip().lower()


def stable_json_sha256(value: Any) -> str:
    """Hash a JSON-compatible candidate-filter snapshot deterministically."""

    import json

    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _metadata(document: Mapping[str, Any]) -> Mapping[str, Any]:
    value = document.get("raw_metadata")
    return value if isinstance(value, Mapping) else {}


def _compact_document(document: Mapping[str, Any]) -> dict[str, Any]:
    """Copy only retrieval identity and screening metadata from the search run.

    The immutable source search run remains the audit source of truth.  Avoid
    multiplying its large provider provenance/artifact payload three times in a
    filter output.
    """

    allowed = (
        "provider",
        "source_type",
        "external_id",
        "title",
        "source_url",
        "publication_number",
        "authority",
        "language",
        "publication_date",
        "filing_date",
        "priority_date",
        "abstract",
        "snippet",
        "description",
        "pdf_url",
        "image_urls",
    )
    result = {
        key: document.get(key)
        for key in allowed
        if document.get(key) not in (None, "", [], {})
    }
    raw = _metadata(document)
    raw_allowed = (
        "patent_id",
        "application_number",
        "applicationNumber",
        "application_no",
        "family_id",
        "familyId",
        "simple_family_id",
        "inpadoc_family_id",
        "doi",
        "DOI",
        "arxiv_id",
        "arxivId",
        "ipc",
        "ipc_codes",
        "ipc_classifications",
        "cpc",
        "cpc_codes",
        "classification",
        "classifications",
    )
    compact_raw = {
        key: raw.get(key)
        for key in raw_allowed
        if raw.get(key) not in (None, "", [], {})
    }
    if compact_raw:
        result["raw_metadata"] = compact_raw
    return result


def _first_metadata(document: Mapping[str, Any], *keys: str) -> str:
    metadata = _metadata(document)
    for key in keys:
        value = metadata.get(key)
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
            value = next((_text(item) for item in value if _text(item)), "")
        if _text(value):
            return _text(value)
    return ""


def publication_identity(value: Any) -> tuple[str, str | None]:
    """Return a kind-code-free publication lineage and the removed kind code."""

    compact = _compact_id(value)
    match = _PATENT_KIND_RE.fullmatch(compact)
    if not match:
        return compact, None
    return (
        f"{match.group('authority')}{match.group('number')}",
        match.group("kind"),
    )


def candidate_identity_keys(
    document: Mapping[str, Any],
    *,
    provider_kind: str,
) -> list[str]:
    """Build auditable deterministic identities without title similarity."""

    provider = _normal_text(document.get("provider")) or "unknown"
    keys: list[str] = []
    external_id = _compact_id(document.get("external_id"))
    if external_id:
        keys.append(f"external:{provider}:{external_id}")

    if provider_kind == "npl":
        doi = _compact_id(
            _first_metadata(document, "doi", "DOI")
            or (
                document.get("external_id")
                if "doi.org/" in _normal_text(document.get("source_url"))
                else ""
            )
        )
        arxiv_id = _compact_id(_first_metadata(document, "arxiv_id", "arxivId"))
        if doi:
            keys.append(f"doi:{doi}")
        if arxiv_id:
            keys.append(f"arxiv:{arxiv_id}")
        return list(dict.fromkeys(keys))

    application = _compact_id(
        _first_metadata(
            document,
            "application_number",
            "applicationNumber",
            "application_no",
        )
        or document.get("application_number")
    )
    if application:
        keys.append(f"application:{application}")

    publication = (
        document.get("publication_number")
        or _first_metadata(
            document,
            "publication_number",
            "publicationNumber",
            "patent_number",
        )
        or document.get("external_id")
    )
    lineage, _kind = publication_identity(publication)
    if lineage:
        keys.append(f"publication_lineage:{lineage}")

    family = _compact_id(
        _first_metadata(
            document,
            "family_id",
            "familyId",
            "simple_family_id",
            "inpadoc_family_id",
        )
    )
    if family:
        keys.append(f"family:{family}")
    return list(dict.fromkeys(keys))


def build_raw_candidates(
    search_outputs: Sequence[Mapping[str, Any]],
    *,
    top_n_per_search: int = 10,
) -> list[dict[str, Any]]:
    """Flatten persisted search outputs while retaining every query lineage."""

    result: list[dict[str, Any]] = []
    for search in search_outputs:
        documents = search.get("documents")
        if not isinstance(documents, Sequence) or isinstance(
            documents, (str, bytes)
        ):
            continue
        search_run_id = _text(search.get("search_run_id"))
        query_id = _text(search.get("query_id"))
        query_text = _text(search.get("query_text"))
        provider_kind = _normal_text(search.get("provider_kind")) or "patent"
        for rank, value in enumerate(documents[:top_n_per_search], start=1):
            if not isinstance(value, Mapping):
                continue
            document = _compact_document(value)
            external_id = (
                _text(document.get("external_id"))
                or _text(document.get("publication_number"))
                or f"rank-{rank}"
            )
            seed = "|".join(
                [search_run_id, query_id, str(rank), external_id, provider_kind]
            )
            candidate_id = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:20]
            result.append(
                {
                    "candidate_id": candidate_id,
                    "search_run_id": search_run_id,
                    "query_id": query_id,
                    "query_text": query_text,
                    "provider_kind": provider_kind,
                    "source_rank": rank,
                    "document": document,
                    "identity_keys": candidate_identity_keys(
                        document,
                        provider_kind=provider_kind,
                    ),
                }
            )
    return result


def _representative_score(candidate: Mapping[str, Any]) -> tuple[Any, ...]:
    document = (
        candidate.get("document")
        if isinstance(candidate.get("document"), Mapping)
        else {}
    )
    publication = document.get("publication_number") or document.get("external_id")
    _lineage, kind = publication_identity(publication)
    raw = _metadata(document)
    has_retrieval_identity = bool(
        _text(document.get("source_url"))
        and (
            _text(document.get("publication_number"))
            or _text(document.get("external_id"))
            or _text(raw.get("patent_id"))
        )
    )
    metadata_richness = sum(
        bool(_text(value))
        for value in (
            document.get("abstract"),
            document.get("snippet"),
            document.get("publication_date"),
            document.get("filing_date"),
            _first_metadata(document, "application_number", "family_id"),
        )
    )
    return (
        0 if has_retrieval_identity else 1,
        _SUBSTANTIVE_KIND_PRIORITY.get(_text(kind).upper(), 10),
        -metadata_richness,
        int(candidate.get("source_rank") or 9999),
        _text(candidate.get("candidate_id")),
    )


def _representative_selection_reason(candidate: Mapping[str, Any]) -> str:
    document = (
        candidate.get("document")
        if isinstance(candidate.get("document"), Mapping)
        else {}
    )
    publication = document.get("publication_number") or document.get("external_id")
    _lineage, kind = publication_identity(publication)
    raw = _metadata(document)
    reasons = []
    if _text(document.get("source_url")) and (
        _text(document.get("publication_number"))
        or _text(document.get("external_id"))
        or _text(raw.get("patent_id"))
    ):
        reasons.append("具有可执行取文标识")
    if _text(kind).upper() in {"A1", "A2", "B1", "B2", "U", "U1", "Y", "A"}:
        reasons.append(f"优先实质公开文本 kind code {_text(kind).upper()}")
    if any(
        _text(value)
        for value in (
            document.get("abstract"),
            document.get("snippet"),
            document.get("publication_date"),
            document.get("filing_date"),
        )
    ):
        reasons.append("题录信息较完整")
    reasons.append(f"原始排名 {int(candidate.get('source_rank') or 0)}")
    return "；".join(reasons)


def _family_titles_compatible(
    left: Mapping[str, Any],
    right: Mapping[str, Any],
) -> bool:
    """Reject a family merge only when same-language titles visibly conflict."""

    left_title = _normal_text(
        (left.get("document") or {}).get("title")
        if isinstance(left.get("document"), Mapping)
        else ""
    )
    right_title = _normal_text(
        (right.get("document") or {}).get("title")
        if isinstance(right.get("document"), Mapping)
        else ""
    )
    if not left_title or not right_title or left_title == right_title:
        return True
    left_ascii = left_title.isascii()
    right_ascii = right_title.isascii()
    if left_ascii != right_ascii:
        # Translated family titles often share no literal terms.
        return True
    if left_ascii:
        left_terms = {
            term
            for term in re.findall(r"[a-z][a-z0-9-]{3,}", left_title)
            if term not in _GENERIC_ANCHORS
        }
        right_terms = {
            term
            for term in re.findall(r"[a-z][a-z0-9-]{3,}", right_title)
            if term not in _GENERIC_ANCHORS
        }
    else:
        left_terms = set(re.findall(r"[\u4e00-\u9fff]{2,}", left_title))
        right_terms = set(re.findall(r"[\u4e00-\u9fff]{2,}", right_title))
    if not left_terms or not right_terms:
        return True
    return bool(left_terms & right_terms)


def group_candidates(
    raw_candidates: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Union candidates sharing a stable identity and choose one representative."""

    count = len(raw_candidates)
    parents = list(range(count))

    def find(index: int) -> int:
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    def union(left: int, right: int) -> None:
        left_root = find(left)
        right_root = find(right)
        if left_root != right_root:
            parents[right_root] = left_root

    owners: dict[str, list[int]] = {}
    for index, candidate in enumerate(raw_candidates):
        for identity in candidate.get("identity_keys") or []:
            key = _text(identity)
            if not key:
                continue
            prior_indexes = owners.setdefault(key, [])
            for prior_index in prior_indexes:
                if key.startswith("family:") and not _family_titles_compatible(
                    candidate,
                    raw_candidates[prior_index],
                ):
                    continue
                union(index, prior_index)
            prior_indexes.append(index)

    buckets: dict[int, list[dict[str, Any]]] = {}
    for index, candidate in enumerate(raw_candidates):
        buckets.setdefault(find(index), []).append(dict(candidate))

    groups: list[dict[str, Any]] = []
    duplicates: list[dict[str, Any]] = []
    for members in buckets.values():
        members.sort(key=_representative_score)
        representative = members[0]
        representative_id = _text(representative.get("candidate_id"))
        group_id = hashlib.sha256(
            "|".join(
                sorted(
                    {
                        _text(key)
                        for member in members
                        for key in member.get("identity_keys") or []
                        if _text(key)
                    }
                    or {representative_id}
                )
            ).encode("utf-8")
        ).hexdigest()[:20]
        identity_keys = list(
            dict.fromkeys(
                _text(key)
                for member in members
                for key in member.get("identity_keys") or []
                if _text(key)
            )
        )
        lineage = [
            {
                "candidate_id": _text(member.get("candidate_id")),
                "search_run_id": _text(member.get("search_run_id")),
                "query_id": _text(member.get("query_id")),
                "query_text": _text(member.get("query_text")),
                "source_rank": int(member.get("source_rank") or 0),
            }
            for member in members
        ]
        groups.append(
            {
                "group_id": group_id,
                "identity_keys": identity_keys,
                "representative_candidate_id": representative_id,
                "representative_document": dict(
                    representative.get("document") or {}
                ),
                "representative_selection_reason": (
                    _representative_selection_reason(representative)
                ),
                "provider_kind": _text(representative.get("provider_kind"))
                or "patent",
                "member_candidate_ids": [
                    _text(member.get("candidate_id")) for member in members
                ],
                "source_lineage": lineage,
            }
        )
        shared_reason = next(
            (
                key
                for key in identity_keys
                if key.startswith(
                    ("application:", "publication_lineage:", "family:")
                )
            ),
            identity_keys[0] if identity_keys else "stable_identity",
        )
        for duplicate in members[1:]:
            duplicates.append(
                {
                    **dict(duplicate),
                    "group_id": group_id,
                    "merged_into_candidate_id": representative_id,
                    "merge_reason": shared_reason,
                }
            )
    groups.sort(
        key=lambda item: min(
            (
                int(lineage.get("source_rank") or 9999)
                for lineage in item.get("source_lineage") or []
            ),
            default=9999,
        )
    )
    return groups, duplicates


def target_filter_context(plan: Mapping[str, Any]) -> dict[str, Any]:
    """Extract only the I2 facts needed by the relevance classifier."""

    profile = plan.get("invention_search_profile")
    profile = profile if isinstance(profile, Mapping) else {}
    mechanism = profile.get("mechanism_model")
    mechanism = mechanism if isinstance(mechanism, Mapping) else {}
    concepts: list[dict[str, Any]] = []
    for role, key in (
        ("common_context", "common_context_features"),
        ("inventive_point", "inventive_point_features"),
    ):
        values = profile.get(key)
        if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
            continue
        for value in values:
            if not isinstance(value, Mapping):
                continue
            concepts.append(
                {
                    "role": role,
                    "text": _text(value.get("text")),
                    "synonyms_zh": list(value.get("synonyms_zh") or []),
                    "synonyms_en": list(value.get("synonyms_en") or []),
                }
            )
    elements = []
    values = mechanism.get("elements")
    if isinstance(values, Sequence) and not isinstance(values, (str, bytes)):
        for value in values:
            if isinstance(value, Mapping):
                elements.append(
                    {
                        "kind": _text(value.get("kind")),
                        "text": _text(value.get("text")),
                        "original_terms": list(value.get("original_terms") or []),
                        "role_equivalent_terms": list(
                            value.get("role_equivalent_terms") or []
                        ),
                        "operation_terms": list(value.get("operation_terms") or []),
                    }
                )
    limitations = []
    values = plan.get("limitations")
    if isinstance(values, Sequence) and not isinstance(values, (str, bytes)):
        limitations = [
            {
                "feature_id": _text(value.get("feature_id")),
                "text": _text(value.get("text")),
                "technical_subject": _text(value.get("technical_subject")),
            }
            for value in values
            if isinstance(value, Mapping)
        ]
    return {
        "protected_subject": _text(
            profile.get("protected_subject") or plan.get("technical_subject")
        ),
        "subject_synonyms_zh": list(profile.get("subject_synonyms_zh") or []),
        "subject_synonyms_en": list(profile.get("subject_synonyms_en") or []),
        "classification_anchors": list(
            profile.get("classification_anchors") or []
        ),
        "invention_summary": _text(profile.get("invention_summary")),
        "mechanism_summary": _text(mechanism.get("summary")),
        "concepts": concepts,
        "mechanism_elements": elements,
        "limitations": limitations,
    }


def _anchor_terms(context: Mapping[str, Any]) -> list[str]:
    terms = [
        _text(context.get("protected_subject")),
        *[_text(item) for item in context.get("subject_synonyms_zh") or []],
        *[_text(item) for item in context.get("subject_synonyms_en") or []],
    ]
    for concept in context.get("concepts") or []:
        if not isinstance(concept, Mapping):
            continue
        terms.extend(
            [
                _text(concept.get("text")),
                *[_text(item) for item in concept.get("synonyms_zh") or []],
                *[_text(item) for item in concept.get("synonyms_en") or []],
            ]
        )
    return list(dict.fromkeys(term for term in terms if term))


def matched_technical_terms(
    document: Mapping[str, Any],
    context: Mapping[str, Any],
) -> list[str]:
    """Find target-derived technical words shared by sparse candidate metadata."""

    target_terms = list(_anchor_terms(context))
    for element in context.get("mechanism_elements") or []:
        if not isinstance(element, Mapping):
            continue
        target_terms.extend(
            [
                _text(element.get("text")),
                *[_text(item) for item in element.get("original_terms") or []],
                *[
                    _text(item)
                    for item in element.get("role_equivalent_terms") or []
                ],
                *[_text(item) for item in element.get("operation_terms") or []],
            ]
        )
    target_words = {
        word
        for term in target_terms
        for word in re.findall(r"[a-z][a-z0-9-]{3,}", _normal_text(term))
        if word not in _GENERIC_ANCHORS
    }
    candidate_words = set(
        re.findall(
            r"[a-z][a-z0-9-]{3,}",
            _normal_text(
                " ".join(
                    _text(document.get(key))
                    for key in ("title", "abstract", "snippet", "description")
                )
            ),
        )
    )
    return sorted(target_words & candidate_words)


def _classification_values(document: Mapping[str, Any]) -> list[str]:
    metadata = _metadata(document)
    result: list[str] = []
    for key in (
        "ipc",
        "ipc_codes",
        "ipc_classifications",
        "cpc",
        "cpc_codes",
        "classification",
        "classifications",
    ):
        value = metadata.get(key)
        values = (
            value
            if isinstance(value, Sequence) and not isinstance(value, (str, bytes))
            else [value]
        )
        result.extend(_text(item).upper() for item in values if _text(item))
    return list(dict.fromkeys(result))


def matched_positive_anchors(
    document: Mapping[str, Any],
    context: Mapping[str, Any],
) -> list[str]:
    """Protect candidates that explicitly name the target or a distinctive point."""

    haystack = _normal_text(
        " ".join(
            _text(document.get(key))
            for key in ("title", "abstract", "snippet", "description")
        )
    )
    matches: list[str] = []
    for raw in _anchor_terms(context):
        term = _normal_text(raw)
        compact = re.sub(r"\s+", "", term)
        if (
            not term
            or term in _GENERIC_ANCHORS
            or (term.isascii() and len(compact) < 5)
            or (not term.isascii() and len(compact) < 2)
        ):
            continue
        if term in haystack:
            matches.append(raw)
    target_classes = {
        _compact_id(item)[:4]
        for item in context.get("classification_anchors") or []
        if len(_compact_id(item)) >= 4
    }
    candidate_classes = {
        _compact_id(item)[:4]
        for item in _classification_values(document)
        if len(_compact_id(item)) >= 4
    }
    matches.extend(
        f"classification:{value}"
        for value in sorted(target_classes & candidate_classes)
    )
    return list(dict.fromkeys(matches))


def metadata_is_sufficient(document: Mapping[str, Any]) -> bool:
    title = _normal_text(document.get("title"))
    detail = _normal_text(
        " ".join(
            _text(document.get(key))
            for key in ("abstract", "snippet", "description")
        )
    )
    classifications = _classification_values(document)
    meaningful_title = len(re.sub(r"[^a-z0-9\u4e00-\u9fff]", "", title)) >= 8
    return bool(meaningful_title or len(detail) >= 24 or classifications)


def apply_filter_decisions(
    groups: Sequence[Mapping[str, Any]],
    *,
    target_context: Mapping[str, Any],
    model_decisions: Mapping[str, Mapping[str, Any]] | None = None,
    model_failure: str | None = None,
) -> dict[str, Any]:
    """Apply model suggestions behind deterministic recall-preserving guards."""

    model_decisions = model_decisions or {}
    fetch_candidates: list[dict[str, Any]] = []
    excluded_candidates: list[dict[str, Any]] = []
    reviewed_groups: list[dict[str, Any]] = []
    for value in groups:
        group = dict(value)
        representative_id = _text(group.get("representative_candidate_id"))
        document = (
            dict(group.get("representative_document") or {})
            if isinstance(group.get("representative_document"), Mapping)
            else {}
        )
        positive = matched_positive_anchors(document, target_context)
        technical_overlap = matched_technical_terms(document, target_context)
        decision = model_decisions.get(representative_id) or {}
        proposed = _text(decision.get("status"))
        reason = _text(decision.get("reason"))
        if positive:
            status = "keep_relevant"
            reason = "候选元数据直接命中目标客体、发明点或分类锚点"
            guard = "strong_positive_anchor"
        elif not metadata_is_sufficient(document):
            status = "keep_uncertain"
            reason = "题录信息不足，不能在取全文前安全排除"
            guard = "insufficient_metadata_fail_open"
        elif technical_overlap:
            status = "keep_uncertain"
            reason = (
                "候选题录仍包含目标发明画像中的技术词，可能存在介质、构件或机理关联"
            )
            guard = "target_technical_term_guard"
        elif model_failure:
            status = "keep_uncertain"
            reason = "语义筛选不可用，按高召回原则保留"
            guard = "model_failure_fail_open"
        elif proposed not in FILTER_STATUSES:
            status = "keep_uncertain"
            reason = "模型没有给出有效判断，按高召回原则保留"
            guard = "missing_model_decision_fail_open"
        elif proposed == "exclude_obvious_unrelated":
            checks = decision.get("exclusion_checks")
            checks = checks if isinstance(checks, Mapping) else {}
            required_checks = (
                "same_or_related_object",
                "shared_medium_or_energy",
                "shared_component_role",
                "shared_operation_or_control",
                "analogous_mechanism_possible",
            )
            complete_checks = all(
                isinstance(checks.get(key), bool) for key in required_checks
            )
            has_shared_basis = any(checks.get(key) is True for key in required_checks)
            high_confidence = _normal_text(decision.get("confidence")) == "high"
            if not complete_checks or not high_confidence:
                status = "keep_uncertain"
                reason = "排除依据未逐项完整确认，按高召回原则保留"
                guard = "incomplete_exclusion_basis_fail_open"
            elif has_shared_basis:
                status = "keep_uncertain"
                reason = "仍存在相同介质、部件作用、操作关系或可类比机理，保留取文"
                guard = "analogous_mechanism_guard"
            else:
                status = proposed
                reason = reason or "模型逐项确认没有可类比技术基础"
                guard = "model_semantic_classification"
        else:
            status = proposed
            reason = reason or "模型依据题录技术语境给出保守分类"
            guard = "model_semantic_classification"
        reviewed = {
            **group,
            "filter_status": status,
            "filter_reason": reason,
            "decision_guard": guard,
            "filter_contract_version": FILTER_CONTRACT_VERSION,
            "identity_rule_version": IDENTITY_RULE_VERSION,
            "semantic_rule_version": SEMANTIC_RULE_VERSION,
            "matched_positive_anchors": positive,
            "matched_technical_terms": technical_overlap,
        }
        reviewed_groups.append(reviewed)
        queue_item = {
            "candidate_id": representative_id,
            "group_id": _text(group.get("group_id")),
            "provider_kind": _text(group.get("provider_kind")) or "patent",
            "document": document,
            "source_lineage": list(group.get("source_lineage") or []),
            "filter_status": status,
            "filter_reason": reason,
            "decision_guard": guard,
            "filter_contract_version": FILTER_CONTRACT_VERSION,
            "identity_rule_version": IDENTITY_RULE_VERSION,
            "semantic_rule_version": SEMANTIC_RULE_VERSION,
            "matched_positive_anchors": positive,
            "matched_technical_terms": technical_overlap,
        }
        if status == "exclude_obvious_unrelated":
            excluded_candidates.append(queue_item)
        else:
            fetch_candidates.append(queue_item)
    return {
        "candidate_groups": reviewed_groups,
        "excluded_candidates": excluded_candidates,
        "fetch_candidates": fetch_candidates,
    }


def invoke_semantic_candidate_filter(
    llm_client: Any,
    *,
    target_context: Mapping[str, Any],
    candidates: Sequence[Mapping[str, Any]],
    target_images: Sequence[str],
    request_timeout_seconds: float | None = None,
    direct_attempt_timeout_seconds: float | None = None,
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    """Run the conservative I3-F semantic classifier through an injected client.

    The caller owns persistence and fail-open handling.  Keeping prompt and
    response parsing here makes the durable I0 workflow and module laboratory
    execute the same semantic contract.
    """

    candidate_payload: list[dict[str, Any]] = []
    for candidate in candidates:
        document = (
            candidate.get("representative_document")
            if isinstance(candidate.get("representative_document"), Mapping)
            else {}
        )
        raw_metadata = (
            document.get("raw_metadata")
            if isinstance(document.get("raw_metadata"), Mapping)
            else {}
        )
        candidate_payload.append(
            {
                "candidate_id": _text(
                    candidate.get("representative_candidate_id")
                ),
                "provider_kind": _text(candidate.get("provider_kind")),
                "title": _text(document.get("title")),
                "abstract": _text(document.get("abstract")),
                "snippet": _text(document.get("snippet")),
                "publication_number": _text(
                    document.get("publication_number")
                ),
                "application_number": _text(
                    raw_metadata.get("application_number")
                ),
                "classifications": {
                    key: raw_metadata.get(key)
                    for key in (
                        "ipc",
                        "ipc_codes",
                        "ipc_classifications",
                        "cpc",
                        "cpc_codes",
                        "classification",
                        "classifications",
                    )
                    if raw_metadata.get(key)
                },
            }
        )
    task = {
        "target_invention": dict(target_context),
        "candidate_documents": candidate_payload,
    }
    prompt = (
        "你正在执行专利无效检索的‘取全文前候选清理’，不是新颖性或创造性判断。"
        "上游 I2 已经通读目标专利全文和全部附图并形成 target_invention；"
        "请以该结构化理解为准，结合随附的一张目标专利图像作视觉语境核对，"
        "只依据候选的标题、摘要、片段和分类信息，逐篇判断它是否明显属于完全无关且"
        "不可能提供相同或相近作用机理的技术领域。\n"
        "允许的 status 只有：keep_relevant（与目标客体、结构、用途或作用机理相关）、"
        "keep_uncertain（信息不足、跨领域但可能存在可借鉴机理、或不能安全排除）、"
        "exclude_obvious_unrelated（题录已经充分表明技术客体和作用机理均明显不相干）。\n"
        "必须保守：名称不同不等于不相关；零部件、流体控制、联接、阀、位置关系等可能"
        "跨领域借鉴时必须 keep_uncertain。候选只要与目标存在相关客体、相同或相关介质/"
        "能量、相近部件作用、相近操作/控制关系、可类比机理中的任意一项，就不得排除。"
        "只有这五项全部明确为 false 且 confidence=high 才能"
        " exclude_obvious_unrelated。不得判断日期资格、公开技术特征、新颖性、创造性或"
        "无效结论。\n"
        "必须为输入中的每一个 candidate_id 恰好返回一条 decision；即使题名相同或判断"
        "相同也不得合并、概括或省略。checks 固定为五个布尔值，顺序依次是：相关客体、"
        "相同/相关介质或能量、相近部件作用、相近操作/控制关系、可类比机理。\n"
        "返回 JSON：{\"decisions\":[{\"candidate_id\":\"...\","
        "\"status\":\"keep_relevant|keep_uncertain|exclude_obvious_unrelated\","
        "\"confidence\":\"high|medium|low\","
        "\"checks\":[false,false,false,false,false],"
        "\"reason\":\"不超过80字的技术理由\"}]}。\n"
        f"输入数据：{json.dumps(task, ensure_ascii=False, sort_keys=True)}"
    )
    invoke_kwargs: dict[str, Any] = {
        "prompt": prompt,
        "target_images": list(target_images),
        "temperature": 0.0,
        "max_tokens": 8192,
    }
    if request_timeout_seconds is not None:
        invoke_kwargs["request_timeout_seconds"] = request_timeout_seconds
    if direct_attempt_timeout_seconds is not None:
        invoke_kwargs[
            "direct_attempt_timeout_seconds"
        ] = direct_attempt_timeout_seconds
    invocation = llm_client.invoke_json(**invoke_kwargs)

    requested = {
        _text(item.get("candidate_id")) for item in candidate_payload
    }
    decisions: dict[str, dict[str, Any]] = {}
    data = getattr(invocation, "data", {})
    values = data.get("decisions") if isinstance(data, Mapping) else None
    if isinstance(values, Sequence) and not isinstance(values, (str, bytes)):
        for value in values:
            if not isinstance(value, Mapping):
                continue
            candidate_id = _text(value.get("candidate_id"))
            status = _text(value.get("status"))
            if candidate_id not in requested or status not in FILTER_STATUSES:
                continue
            check_keys = (
                "same_or_related_object",
                "shared_medium_or_energy",
                "shared_component_role",
                "shared_operation_or_control",
                "analogous_mechanism_possible",
            )
            raw_checks = value.get("checks")
            if (
                isinstance(raw_checks, Sequence)
                and not isinstance(raw_checks, (str, bytes))
                and len(raw_checks) == len(check_keys)
                and all(isinstance(item, bool) for item in raw_checks)
            ):
                normalized_checks = dict(
                    zip(check_keys, raw_checks, strict=True)
                )
            else:
                mapping_checks = value.get("exclusion_checks")
                normalized_checks = (
                    {
                        key: mapping_checks.get(key)
                        for key in check_keys
                        if isinstance(mapping_checks.get(key), bool)
                    }
                    if isinstance(mapping_checks, Mapping)
                    else {}
                )
            decisions[candidate_id] = {
                "status": status,
                "reason": _text(value.get("reason"))[:240],
                "confidence": _normal_text(value.get("confidence")),
                "exclusion_checks": normalized_checks,
            }
    audit = {
        "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
        "model": _text(
            getattr(invocation, "model", None)
            or getattr(llm_client, "model", None)
        ),
        "target_image_count": int(
            getattr(invocation, "target_image_count", len(target_images)) or 0
        ),
        "candidate_count": len(candidate_payload),
        "decisions": decisions,
        "raw_response": _text(getattr(invocation, "raw_content", "")),
    }
    return decisions, audit


__all__ = [
    "FILTER_CONTRACT_VERSION",
    "FILTER_STATUSES",
    "IDENTITY_RULE_VERSION",
    "SEMANTIC_RULE_VERSION",
    "apply_filter_decisions",
    "build_raw_candidates",
    "candidate_identity_keys",
    "group_candidates",
    "invoke_semantic_candidate_filter",
    "matched_positive_anchors",
    "matched_technical_terms",
    "metadata_is_sufficient",
    "publication_identity",
    "stable_json_sha256",
    "target_filter_context",
]
