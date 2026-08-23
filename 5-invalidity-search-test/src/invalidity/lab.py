"""Durable handlers for the isolated module laboratory.

Fixture runs are deterministic and never touch the network or model.  Manual
and live runs execute the same date, provider, and multimodal analysis adapters
used by the workflow, but keep their inputs and outputs inside a module run.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import shutil
from typing import Any, Callable, Mapping, Sequence
from urllib.parse import urlsplit

from pydantic import ValidationError

from .artifacts import ArtifactStore
from .candidate_filter import (
    FILTER_CONTRACT_VERSION,
    IDENTITY_RULE_VERSION,
    SEMANTIC_RULE_VERSION,
    apply_filter_decisions,
    build_raw_candidates,
    group_candidates,
    invoke_semantic_candidate_filter,
    matched_positive_anchors,
    matched_technical_terms,
    metadata_is_sufficient,
    stable_json_sha256,
    target_filter_context,
)
from .analysis import (
    AnalysisValidationError,
    DistinguishingFeature,
    InventiveStepAssessment,
    InvalidityAnalysisEngine,
    ObviousnessPrecheckAssessment,
    QueryPlan,
    I2_DEFAULT_MAX_QUERIES,
    apply_obviousness_routes_to_gap_decision,
    bind_gap_search_strategy,
    build_distinguishing_features,
    evaluate_existing_corpus_gap_coverage,
    gap_search_strategy_metadata,
    select_closest_prior_art,
    select_large_claim_chart_documents,
    target_patent_text_for_analysis,
)
from .config import Settings
from .contracts import (
    DateChannel,
    DisclosureStatus,
    DocumentComparison,
    FeatureDisclosure,
    GapType,
    I4S_PROMPT_VERSION,
    I4S_RULE_VERSION,
    InventionSearchProfile,
    Limitation,
    PatentSnapshot,
    QueryRole,
    QueryVariant,
    SearchObjective,
    SearchConcept,
    SearchQuery as ContractSearchQuery,
    SearchScope,
)
from .date_rules import classify_date_eligibility, resolve_critical_date
from .llm import MultimodalModelError, VisionLLMClient
from .module1 import Module1Adapter, PatentSnapshotError
from .providers import (
    ArxivProvider,
    CompositeNplProvider,
    CrossrefProvider,
    EpoOpsProvider,
    EvidenceRecord,
    EvidenceStage,
    GooglePatentsProvider,
    ManualProvider,
    OpenAlexProvider,
    ProviderSearchBatch,
    ProviderContentError,
    ProviderRequestError,
    QueryValidationError,
    RetrievedArtifact,
    SearchQuery as ProviderSearchQuery,
    WebEvidenceProvider,
)
from .patsnap import PatsnapProvider
from .patent_retrieval import EpoFirstPatsnapPdfRetrievalProvider
from .report import (
    ReportDataV1,
    build_inventive_step_narrative,
    build_report_data,
    public_json,
)
from .readable_patent import (
    NORMALIZATION_VERSION,
    load_cached_readable_document,
    normalize_epo_bundle,
    normalize_pdf,
    persist_readable_document,
)
from .source_snapshot import (
    SourceSnapshotError,
    ensure_patent_snapshot,
    read_allowed_file,
)
from .worker import JobContext, JobHandler, PermanentJobError, RetryableJobError


LAB_MODULE_CODES = (
    "I1_TARGET_SNAPSHOT",
    "I1_5_CLAIM_DATES",
    "I2_INVENTIVE_PROFILE",
    "I2_QUERY_PLAN",
    "I2_GAP_QUERY_PLAN",
    "I3_PATENT_SEARCH",
    "I3_NPL_SEARCH",
    "I3_CANDIDATE_FILTER",
    "I3_FETCH",
    "I3_QUALIFY",
    "I4_S_SINGLE_REFERENCE",
    "I4_C_CLOSEST_PRIOR_ART",
    "I4_O_OBVIOUSNESS_PRECHECK",
    "I4_I_INVENTIVE_STEP",
    "I5_REPORT",
)
_PATENT_PROVIDER_NAMES = frozenset({"google_patents", "epo_ops", "patsnap"})
_SEARCH_MODALITIES = frozenset({"text", "image_single", "image_multiple"})
_IMAGE_SEARCH_MODALITIES = frozenset({"image_single", "image_multiple"})
_FIXED_FIRST_ROUND_QUERY_VARIANTS = frozenset(
    {
        QueryVariant.APPLICANT_PLUS_OBJECT.value,
        QueryVariant.TITLE_OBJECT_PLUS_DESC_INVENTIVE.value,
        QueryVariant.CLASSIFICATION_PLUS_DESC_INVENTIVE.value,
        QueryVariant.DESC_OBJECT_PLUS_DESC_INVENTIVE_PLUS_EFFECT.value,
        QueryVariant.TITLE_KEYWORD_OBJECT_PLUS_DESC_FUNCTION.value,
    }
)

DEFAULT_FIXTURE_ID = "invalidity-module-lab-v1"


def _dedupe_strings(values: Sequence[Any]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value).strip()
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return result


def _publication_base_key(value: Any) -> str:
    """Normalize a publication identity while ignoring A/B/U/S kind codes."""

    normalized = re.sub(r"[^A-Z0-9]", "", str(value or "").upper())
    match = re.fullmatch(r"([A-Z]{2,3}\d+)(?:[A-Z]\d?)?", normalized)
    return match.group(1) if match else normalized


@dataclass(frozen=True, slots=True)
class LabModeRule:
    """One auditable row in the module-lab truth matrix."""

    simulated: bool
    human_supplied: bool
    requires_persisted_investigation: bool
    permits_manual_provider: bool
    permits_client_documents: bool


_MODE_RULES: dict[str, LabModeRule] = {
    "fixture": LabModeRule(
        simulated=True,
        human_supplied=False,
        requires_persisted_investigation=False,
        permits_manual_provider=True,
        permits_client_documents=False,
    ),
    "manual": LabModeRule(
        simulated=False,
        human_supplied=True,
        requires_persisted_investigation=False,
        permits_manual_provider=True,
        permits_client_documents=True,
    ),
    "live": LabModeRule(
        simulated=False,
        human_supplied=False,
        requires_persisted_investigation=True,
        permits_manual_provider=False,
        permits_client_documents=False,
    ),
}

# A module-indexed matrix makes the supported boundary reviewable in one place.
# All public modules support the same three named modes; handlers below add
# only module-specific requirements (for example I5's persisted snapshot).
LAB_MODE_MATRIX: dict[str, dict[str, LabModeRule]] = {
    code: dict(_MODE_RULES) for code in LAB_MODULE_CODES
}


@dataclass(frozen=True, slots=True)
class LabRequest:
    requested_mode: str
    effective_mode: str
    data: dict[str, Any]
    source: Mapping[str, Any]
    rule: LabModeRule
    investigation_id: str | None = None
    investigation: Mapping[str, Any] | None = None
    repository: Any | None = None


_FIXTURE_INPUT = {"fixture": "default"}
_FIXTURE_MARKERS = {
    "fixture",
    "fixture_id",
    "fixture_output",
    "simulated",
}
_LIVE_FORBIDDEN_KEYS = {
    *_FIXTURE_MARKERS,
    "actual_provider",
    "date_qualifications",
    "documents",
    "effective_mode",
    "eligibility",
    "human_supplied",
    "input_mode",
    "model",
    "model_version",
    "provider",
    "provider_mode",
    "requested_mode",
    "stage",
}


def _input_violation(
    value: Any,
    *,
    forbidden_keys: set[str],
    path: str = "input",
    live: bool = False,
) -> str | None:
    if isinstance(value, Mapping):
        for raw_key, child in value.items():
            key = str(raw_key).strip().lower()
            child_path = f"{path}.{raw_key}"
            if key in forbidden_keys:
                return child_path
            if live and (
                key == "hash"
                or key.endswith("_hash")
                or key.endswith("_sha256")
                or key.endswith("_eligible")
                or key.endswith("_eligibility")
            ):
                return child_path
            if live and "verified" in key and isinstance(child, bool):
                return child_path
            nested = _input_violation(
                child,
                forbidden_keys=forbidden_keys,
                path=child_path,
                live=live,
            )
            if nested:
                return nested
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for index, child in enumerate(value):
            nested = _input_violation(
                child,
                forbidden_keys=forbidden_keys,
                path=f"{path}[{index}]",
                live=live,
            )
            if nested:
                return nested
    return None


def _configured_patent_provider(settings: Settings) -> bool:
    return _canonical_provider_name(settings.patent_provider) in _PATENT_PROVIDER_NAMES


def _configured_npl_provider(settings: Settings) -> bool:
    return settings.npl_provider.strip().lower() in {
        "arxiv",
        "arxiv_atom",
        "composite_npl",
        "arxiv_openalex_crossref",
        "arxiv_openalex_crossref_web",
    }


def _request(context: JobContext) -> LabRequest:
    snapshot = context.module_run.get("input_snapshot")
    if not isinstance(snapshot, Mapping):
        raise PermanentJobError("LAB_INPUT_INVALID", "模块实验缺少输入快照")
    request = snapshot.get("request")
    if not isinstance(request, Mapping):
        raise PermanentJobError("LAB_INPUT_INVALID", "模块实验输入快照不完整")
    mode = str(request.get("input_mode") or "")
    module_code = str(context.module_run.get("module_code") or "")
    rule = LAB_MODE_MATRIX.get(module_code, {}).get(mode)
    if rule is None:
        raise PermanentJobError("LAB_INPUT_MODE_INVALID", "模块实验输入模式无效")
    value = request.get("input")
    if not isinstance(value, Mapping):
        raise PermanentJobError("LAB_INPUT_INVALID", "模块实验 input 必须是对象")
    data = dict(value)

    if mode == "fixture":
        if data != _FIXTURE_INPUT:
            raise PermanentJobError(
                "LAB_FIXTURE_INPUT_REJECTED",
                "fixture 模式只允许版本化 default 固定夹具",
            )
    elif mode == "manual":
        violation = _input_violation(data, forbidden_keys=_FIXTURE_MARKERS)
        if violation:
            raise PermanentJobError(
                "LAB_MANUAL_FIXTURE_REJECTED",
                f"manual 模式拒绝 fixture 字段: {violation}",
            )
    else:
        validation_value: Mapping[str, Any] = data
        search_modality = str(data.get("search_modality") or "text").strip().lower()
        if (
            module_code == "I3_PATENT_SEARCH"
            and search_modality in _IMAGE_SEARCH_MODALITIES
        ):
            # ``model`` is normally forbidden client-asserted execution truth.
            # For an image-search lab run it is instead the documented
            # Patsnap algorithm selector, and only this top-level occurrence
            # may pass the forged-truth guard.
            validation_value = dict(data)
            validation_value.pop("model", None)
        violation = _input_violation(
            validation_value,
            forbidden_keys=_LIVE_FORBIDDEN_KEYS,
            live=True,
        )
        if violation:
            raise PermanentJobError(
                "LAB_LIVE_INPUT_FORGED",
                f"live 模式拒绝客户端伪造字段: {violation}",
            )

    investigation_id = str(request.get("investigation_id") or "").strip() or None
    investigation: Mapping[str, Any] | None = None
    if rule.requires_persisted_investigation:
        if investigation_id is None:
            raise PermanentJobError(
                "LAB_LIVE_INVESTIGATION_REQUIRED",
                "live 模式必须显式关联既有持久化调查",
            )
        stored_id = str(context.module_run.get("investigation_id") or "").strip()
        if stored_id and stored_id != investigation_id:
            raise PermanentJobError(
                "LAB_LIVE_INVESTIGATION_MISMATCH",
                "live 模块运行与请求调查不一致",
            )
        getter = getattr(context.repository, "get_investigation", None)
        investigation = getter(investigation_id) if callable(getter) else None
        if not isinstance(investigation, Mapping):
            raise PermanentJobError(
                "LAB_LIVE_INVESTIGATION_NOT_FOUND",
                "live 模式关联的持久化调查不存在",
            )

    return LabRequest(
        requested_mode=mode,
        effective_mode=mode,
        data=data,
        source=request,
        rule=rule,
        investigation_id=investigation_id,
        investigation=investigation,
        repository=context.repository,
    )


def _completed(
    context: JobContext,
    request: LabRequest,
    output: Any,
    *,
    actual_provider: str | None = None,
    model: str | None = None,
    network_used: bool = False,
    used_target_images: int | None = None,
    used_document_images: int | None = None,
) -> dict[str, Any]:
    if hasattr(output, "model_dump"):
        try:
            output = output.model_dump(mode="json")
        except TypeError:
            output = output.model_dump()
    if isinstance(output, Mapping):
        payload = dict(output)
    else:
        payload = {"result": output}

    if request.rule.simulated:
        # Fixture models retain >=1 schema guards used by the real workflow.
        # The lab envelope records actual use and must never invent images.
        for key in (
            "used_target_images",
            "used_document_images",
            "target_image_count",
            "document_image_count",
        ):
            if key in payload:
                payload[key] = 0
        used_target_images = 0 if used_target_images is not None else used_target_images
        used_document_images = (
            0 if used_document_images is not None else used_document_images
        )
        actual_provider = "fixture"
        model = "fixture-no-model"
        network_used = False

    if used_target_images is not None:
        payload["used_target_images"] = max(0, int(used_target_images))
    if used_document_images is not None:
        payload["used_document_images"] = max(0, int(used_document_images))

    audit = {
        "requested_mode": request.requested_mode,
        "effective_mode": request.effective_mode,
        "actual_provider": actual_provider,
        "model": model,
        "network_used": bool(network_used),
        "fixture_id": DEFAULT_FIXTURE_ID if request.rule.simulated else None,
        "simulated": request.rule.simulated,
        "human_supplied": request.rule.human_supplied,
    }
    payload.update(audit)
    return {
        "status": "completed",
        "module_code": str(context.module_run.get("module_code") or ""),
        "input_mode": request.requested_mode,
        **audit,
        "output": payload,
    }


def _engine(settings: Settings) -> InvalidityAnalysisEngine:
    return InvalidityAnalysisEngine(
        VisionLLMClient(
            base_url=settings.llm_base_url,
            api_key=settings.llm_api_key,
            model=settings.llm_model,
            timeout_seconds=settings.llm_timeout_seconds,
            direct_attempt_timeout_seconds=(
                settings.llm_direct_attempt_timeout_seconds
            ),
            direct_probe_timeout_seconds=settings.llm_direct_probe_timeout_seconds,
        ),
        i2_timeout_seconds=settings.llm_i2_timeout_seconds,
        i2_direct_attempt_timeout_seconds=(
            settings.llm_i2_direct_attempt_timeout_seconds
        ),
    )


def _canonical_provider_name(value: Any) -> str:
    name = str(value or "").strip().lower().replace("-", "_")
    if name == "arxiv_atom":
        return "arxiv"
    return name


def _live_provider_name(
    settings: Settings,
    *,
    provider_kind: str,
) -> str:
    kind = provider_kind.strip().lower()
    if kind == "patent":
        if not _configured_patent_provider(settings):
            raise PermanentJobError(
                "PATENT_PROVIDER_UNSUPPORTED",
                "live 专利模块只允许已配置的专利 provider",
            )
        return _canonical_provider_name(settings.patent_provider)
    if kind == "npl":
        if not _configured_npl_provider(settings):
            raise PermanentJobError(
                "NPL_PROVIDER_UNSUPPORTED",
                "live 非专利模块只允许已配置的 arXiv/开放学术复合检索源",
            )
        name = settings.npl_provider.strip().lower()
        return "arxiv" if name == "arxiv_atom" else name
    raise PermanentJobError(
        "PROVIDER_KIND_REQUIRED",
        "live 文献模块必须声明 patent 或 npl provider_kind",
    )


def _live_patent_provider(
    settings: Settings,
    *,
    provider_name: str | None = None,
) -> Any:
    name = (
        _canonical_provider_name(provider_name)
        if provider_name is not None
        else _live_provider_name(settings, provider_kind="patent")
    )
    if provider_name is not None and name != "patsnap":
        raise PermanentJobError(
            "LAB_SEARCH_PROVIDER_UNSUPPORTED",
            "模块实验 search_provider 当前只允许 patsnap",
        )
    if name == "google_patents":
        return GooglePatentsProvider()
    if name == "epo_ops":
        if not settings.epo_ops_consumer_key or not settings.epo_ops_consumer_secret:
            raise PermanentJobError(
                "EPO_OPS_CREDENTIALS_MISSING",
                "EPO OPS 缺少当前环境 OAuth 凭据",
            )
        return EpoOpsProvider(
            consumer_key=settings.epo_ops_consumer_key,
            consumer_secret=settings.epo_ops_consumer_secret,
        )
    if name == "patsnap":
        if not settings.patsnap_api_key:
            raise PermanentJobError(
                "PATSNAP_CREDENTIALS_MISSING",
                "智慧芽缺少当前环境 REST API Key",
            )
        return PatsnapProvider(
            api_key=settings.patsnap_api_key,
            base_url=settings.patsnap_base_url,
            count_path=settings.patsnap_count_path,
            search_path=settings.patsnap_search_path,
        )
    raise PermanentJobError(
        "PATENT_PROVIDER_UNSUPPORTED",
        "live 专利模块 provider 未实现",
    )


def _live_patent_retrieval_provider(settings: Settings) -> Any:
    """Use OPS first and P020 only for an exact-document no-coverage fallback."""

    discovery_provider = _live_provider_name(settings, provider_kind="patent")
    if discovery_provider == "patsnap":
        if not settings.epo_ops_consumer_key or not settings.epo_ops_consumer_secret:
            raise PermanentJobError(
                "EPO_OPS_CREDENTIALS_MISSING",
                "智慧芽候选按公开号取回原文需要当前环境的 EPO OPS OAuth 凭据",
            )
        if not settings.patsnap_api_key:
            raise PermanentJobError(
                "PATSNAP_CREDENTIALS_MISSING",
                "智慧芽 P020 fallback 缺少当前环境 REST API Key",
            )
        return EpoFirstPatsnapPdfRetrievalProvider(
            epo_provider=EpoOpsProvider(
                consumer_key=settings.epo_ops_consumer_key,
                consumer_secret=settings.epo_ops_consumer_secret,
            ),
            patsnap_provider=PatsnapProvider(
                api_key=settings.patsnap_api_key,
                base_url=settings.patsnap_base_url,
                count_path=settings.patsnap_count_path,
                search_path=settings.patsnap_search_path,
            ),
            close_patsnap=True,
        )
    return _live_patent_provider(settings)


def _live_npl_provider(settings: Settings) -> Any:
    """Build the same explicitly configured NPL surface as the real workflow."""

    name = _live_provider_name(settings, provider_kind="npl")
    if name == "arxiv":
        return ArxivProvider()
    providers: list[Any] = [
        ArxivProvider(),
        OpenAlexProvider(),
        CrossrefProvider(),
    ]
    if name in {"composite_npl", "arxiv_openalex_crossref_web"}:
        providers.append(WebEvidenceProvider())
    return CompositeNplProvider(providers)


def _approved_npl_record_provider(settings: Settings, provider: str) -> bool:
    configured = _live_provider_name(settings, provider_kind="npl")
    if configured == "arxiv":
        return provider == "arxiv"
    allowed = {"arxiv", "openalex", "crossref"}
    if configured in {"composite_npl", "arxiv_openalex_crossref_web"}:
        allowed.add("web_search")
    return provider in allowed


def _lab_search_provider(
    settings: Settings,
    data: Mapping[str, Any],
    *,
    provider_kind: str,
    mode: str,
) -> tuple[str | None, bool]:
    """Resolve an I3 lab-only provider override without mutating Settings."""

    raw_override = data.get("search_provider")
    has_override = raw_override is not None
    if has_override and mode != "live":
        raise PermanentJobError(
            "LAB_SEARCH_PROVIDER_LIVE_ONLY",
            "search_provider 只允许用于 live 模块实验",
        )
    if not has_override:
        return None, False
    if provider_kind != "patent":
        raise PermanentJobError(
            "LAB_SEARCH_PROVIDER_UNSUPPORTED",
            "非专利检索模块不接受 patent search_provider",
        )
    override = _canonical_provider_name(raw_override)
    if override != "patsnap":
        raise PermanentJobError(
            "LAB_SEARCH_PROVIDER_UNSUPPORTED",
            "模块实验 search_provider 当前只允许 patsnap",
        )
    if not settings.patsnap_api_key:
        raise PermanentJobError(
            "PATSNAP_CREDENTIALS_MISSING",
            "智慧芽缺少当前测试环境 REST API Key",
        )
    return override, True


def _search_modality(data: Mapping[str, Any]) -> str:
    value = str(data.get("search_modality") or "text").strip().lower()
    if value not in _SEARCH_MODALITIES:
        raise PermanentJobError(
            "LAB_SEARCH_MODALITY_INVALID",
            "search_modality 必须是 text、image_single 或 image_multiple",
        )
    return value


def _image_search_url(value: Any, *, name: str) -> str:
    if not isinstance(value, str):
        raise PermanentJobError(
            "LAB_IMAGE_SEARCH_URL_INVALID",
            f"{name} 必须是智慧芽可访问的 HTTPS 图片 URL",
        )
    candidate = value.strip()
    try:
        parsed = urlsplit(candidate)
        port = parsed.port
    except ValueError as exc:
        raise PermanentJobError(
            "LAB_IMAGE_SEARCH_URL_INVALID",
            f"{name} 必须是智慧芽可访问的 HTTPS 图片 URL",
        ) from exc
    if (
        not candidate
        or parsed.scheme.lower() != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
    ):
        raise PermanentJobError(
            "LAB_IMAGE_SEARCH_URL_INVALID",
            f"{name} 必须是智慧芽可访问的 HTTPS 图片 URL，禁止本机路径",
        )
    return candidate


def _required_image_search_int(
    data: Mapping[str, Any],
    name: str,
) -> int:
    if name not in data or type(data.get(name)) is not int:
        raise PermanentJobError(
            "LAB_IMAGE_SEARCH_INPUT_INVALID",
            f"图片检索必须显式提供整数 {name}",
        )
    return data[name]


def _image_search_parameters(
    data: Mapping[str, Any],
    *,
    modality: str,
) -> tuple[list[str], str, int, int, int]:
    patent_type = str(data.get("patent_type") or "").strip()
    if not patent_type:
        raise PermanentJobError(
            "LAB_IMAGE_SEARCH_INPUT_INVALID",
            "图片检索必须显式提供 patent_type",
        )
    model = _required_image_search_int(data, "model")
    maximum = _required_image_search_int(data, "max_results")
    offset = (
        _required_image_search_int(data, "offset")
        if "offset" in data
        else 0
    )
    if modality == "image_single":
        if "image_url" not in data:
            raise PermanentJobError(
                "LAB_IMAGE_SEARCH_INPUT_INVALID",
                "单图检索必须显式提供 image_url",
            )
        urls = [_image_search_url(data.get("image_url"), name="image_url")]
    else:
        raw_urls = data.get("image_urls")
        if isinstance(raw_urls, (str, bytes)) or not isinstance(
            raw_urls, Sequence
        ):
            raise PermanentJobError(
                "LAB_IMAGE_SEARCH_INPUT_INVALID",
                "多图检索必须显式提供 2–4 个 image_urls",
            )
        if not 2 <= len(raw_urls) <= 4:
            raise PermanentJobError(
                "LAB_IMAGE_SEARCH_INPUT_INVALID",
                "多图检索必须显式提供 2–4 个 image_urls",
            )
        urls = [
            _image_search_url(item, name=f"image_urls[{index}]")
            for index, item in enumerate(raw_urls)
        ]
    return urls, patent_type, model, maximum, offset


def _freeze_live_search_response(
    settings: Settings,
    context: JobContext,
    provider: Any,
    records: Sequence[EvidenceRecord],
) -> tuple[list[EvidenceRecord], list[dict[str, Any]]]:
    artifact = getattr(provider, "last_search_artifact", None)
    provider_name = _canonical_provider_name(
        getattr(provider, "provider_name", "")
    )
    if not isinstance(artifact, RetrievedArtifact):
        if provider_name in {"epo_ops", "patsnap"}:
            raise ProviderContentError(f"{provider_name} 未提供可冻结的原始检索响应")
        return list(records), []
    allowed_kinds = {
        "provider_search_response",
        "provider_image_search_response",
    }
    if artifact.kind not in allowed_kinds or not artifact.content:
        raise ProviderContentError("provider 检索响应工件类型无效")
    run_id = str(context.module_run.get("id") or "search")
    store = ArtifactStore(settings.artifact_root, settings.environment)
    media_type = str(artifact.media_type or "").lower()
    suffix = ".json" if "json" in media_type else ".xml" if "xml" in media_type else ".bin"
    stored = store.write_bytes(
        f"lab-{run_id}",
        (
            f"search-responses/{provider_name or 'provider'}/{artifact.kind}/"
            f"{artifact.content_sha256}{suffix}"
        ),
        artifact.content,
        artifact_type=artifact.kind,
        mime_type=artifact.media_type,
    )
    if stored.sha256 != artifact.content_sha256:
        raise ProviderContentError("检索响应快照哈希与 provider 响应不一致")
    summary = {
        **stored.model_dump(),
        "kind": artifact.kind,
        "provider": provider_name,
        "source_url": artifact.source_url,
        "retrieved_at": artifact.retrieved_at,
        "content_sha256": stored.sha256,
        "content_length": stored.byte_size,
        "local_immutable_snapshot": True,
        "is_primary_source": False,
    }
    return (
        [
            replace(
                record,
                artifacts=(*record.artifacts, summary),
                provenance={
                    **dict(record.provenance),
                    "search_snapshot_sha256": stored.sha256,
                    "search_snapshot_uri": stored.uri,
                    "search_snapshot_retrieved_at": artifact.retrieved_at,
                },
            )
            for record in records
        ],
        [summary],
    )


def _freeze_failed_live_search_response(
    settings: Settings,
    context: JobContext,
    provider: Any,
) -> dict[str, Any] | None:
    """Freeze an already-received provider response before surfacing its error.

    Provider adapters validate the response after assigning
    ``last_search_artifact``.  A content/schema failure therefore still has
    replayable bytes, but the successful-output path above is never reached.
    Persist those bytes and a credential-safe audit row without retrying the
    provider request or changing the original failure classification.
    """

    artifact = getattr(provider, "last_search_artifact", None)
    if not isinstance(artifact, RetrievedArtifact) or not artifact.content:
        return None
    if artifact.kind not in {
        "provider_search_response",
        "provider_image_search_response",
    }:
        return None

    provider_name = (
        _canonical_provider_name(getattr(provider, "provider_name", ""))
        or "provider"
    )
    run_id = str(context.module_run.get("id") or "search")
    media_type = str(artifact.media_type or "").lower()
    suffix = (
        ".json"
        if "json" in media_type
        else ".xml"
        if "xml" in media_type
        else ".bin"
    )
    store = ArtifactStore(settings.artifact_root, settings.environment)
    stored = store.write_bytes(
        f"lab-{run_id}",
        (
            f"search-responses/{provider_name}/{artifact.kind}/"
            f"{artifact.content_sha256}{suffix}"
        ),
        artifact.content,
        artifact_type="provider_search_failure_response",
        mime_type=artifact.media_type,
    )
    if stored.sha256 != artifact.content_sha256:
        raise ProviderContentError("失败检索响应快照哈希与 provider 响应不一致")

    safe_metadata = public_json(
        {
            "kind": artifact.kind,
            "provider": provider_name,
            "source_url": artifact.source_url,
            "retrieved_at": artifact.retrieved_at,
            "content_sha256": stored.sha256,
            "failure_response": True,
            "request_attempt_log": list(artifact.request_attempt_log),
        }
    )
    artifact_id: str | None = None
    insert_artifact = getattr(context.repository, "insert_artifact", None)
    investigation_id = context.module_run.get("investigation_id")
    if callable(insert_artifact) and investigation_id:
        row = insert_artifact(
            investigation_id=investigation_id,
            module_run_id=context.module_run.get("id"),
            artifact_type="provider_search_failure_response",
            uri=stored.uri,
            sha256=stored.sha256,
            mime_type=stored.mime_type,
            byte_size=stored.byte_size,
            metadata=safe_metadata,
            actor=context.worker_id,
        )
        if isinstance(row, Mapping) and row.get("id"):
            artifact_id = str(row["id"])

    return {
        "artifact_id": artifact_id,
        "sha256": stored.sha256,
        "byte_size": stored.byte_size,
        "mime_type": stored.mime_type,
        "kind": artifact.kind,
        "provider": provider_name,
    }


def _attach_failed_search_audit(
    exc: ProviderContentError | QueryValidationError,
    *,
    settings: Settings,
    context: JobContext,
    provider: Any,
) -> None:
    try:
        audit = _freeze_failed_live_search_response(settings, context, provider)
    except Exception:
        # The original provider/content error is already permanent.  Failure
        # audit persistence must never turn it into a retryable exception that
        # could replay a billable POST.
        setattr(exc, "failure_artifact_persist_failed", True)
        return
    if audit is None:
        return
    setattr(exc, "failure_artifact_sha256", audit["sha256"])
    if audit.get("artifact_id"):
        setattr(exc, "failure_artifact_id", audit["artifact_id"])


def _freeze_live_retrieved_document(
    settings: Settings,
    context: JobContext,
    record: EvidenceRecord,
) -> EvidenceRecord:
    """Persist provider bytes before a live I3-FETCH result is serialised."""

    if record.stage is EvidenceStage.LEAD:
        return record
    artifact = record.primary_artifact
    if not isinstance(artifact, RetrievedArtifact) or not artifact.content:
        raise ProviderContentError(
            "live provider 已声明取回文献，但没有可冻结的原始响应字节"
        )
    run_id = str(context.module_run.get("id") or "fetch")
    provider_name = _canonical_provider_name(record.provider) or "provider"
    media_type = str(artifact.media_type or "").lower()
    if "json" in media_type:
        suffix = ".json"
    elif "xml" in media_type:
        suffix = ".xml"
    elif media_type == "application/pdf":
        suffix = ".pdf"
    elif "html" in media_type:
        suffix = ".html"
    elif media_type.startswith("text/"):
        suffix = ".txt"
    else:
        suffix = ".bin"
    stem = hashlib.sha256(record.external_id.encode("utf-8")).hexdigest()[:24]
    store = ArtifactStore(settings.artifact_root, settings.environment)
    stored = store.write_bytes(
        f"lab-{run_id}",
        f"retrieved/{provider_name}/{stem}/source{suffix}",
        artifact.content,
        artifact_type="prior_art_primary_source",
        mime_type=artifact.media_type,
    )
    if stored.sha256 != artifact.content_sha256:
        raise ProviderContentError("live provider 原始响应快照哈希不一致")
    primary = {
        **stored.model_dump(),
        "kind": artifact.kind,
        "provider": provider_name,
        "source_url": artifact.source_url,
        "retrieved_at": artifact.retrieved_at,
        "content_sha256": stored.sha256,
        "content_length": stored.byte_size,
        "local_immutable_snapshot": True,
        "is_primary_source": True,
    }
    full_text = record.full_text
    image_urls = list(record.image_urls)
    derived: list[dict[str, Any]] = []
    parse_issue: str | None = None
    source_metadata = artifact.source_metadata_artifact
    investigation_id = str(
        context.module_run.get("investigation_id") or f"lab-{run_id}"
    )
    readable_document = load_cached_readable_document(
        store,
        investigation_id,
        stored.sha256,
    )
    if readable_document is None:
        try:
            epo_bundle = (
                source_metadata.content
                if source_metadata is not None
                and source_metadata.content
                and "epo.ops.bundle" in source_metadata.media_type.lower()
                else artifact.content
                if "epo.ops.bundle" in artifact.media_type.lower()
                else None
            )
            readable_document = normalize_epo_bundle(
                epo_bundle,
                source_sha256=stored.sha256,
                title=record.title,
                abstract=record.abstract,
            )
            if readable_document is None and artifact.media_type == "application/pdf":
                readable_document = normalize_pdf(
                    artifact.content,
                    source_sha256=stored.sha256,
                    title=record.title,
                    abstract=record.abstract,
                )
            if readable_document is None:
                substantive = "\n\n".join(
                    str(value or "").strip()
                    for value in (record.description, record.claims)
                    if str(value or "").strip()
                )
                ready = len("".join(substantive.split())) >= 200
                readable_document = {
                    "schema_version": NORMALIZATION_VERSION,
                    "source_sha256": stored.sha256,
                    "source_kind": "provider_structured_text",
                    "normalization_status": "completed" if ready else "incomplete",
                    "analysis_ready": ready,
                    "analysis_readiness_reason": (
                        "provider supplied substantive description/claims text"
                        if ready
                        else "provider did not supply a complete readable document"
                    ),
                    "page_count": None,
                    "processed_page_count": None,
                    "text_page_count": None,
                    "ocr_page_count": 0,
                    "failed_pages": [],
                    "sections": [
                        {
                            "section": key,
                            "anchor": key,
                            "kind": "provider_text",
                            "text": str(value).strip(),
                        }
                        for key, value in (
                            ("description", record.description),
                            ("claims", record.claims),
                        )
                        if str(value or "").strip()
                    ],
                    "full_text": record.full_text or substantive,
                }
            json_artifact, markdown_artifact = persist_readable_document(
                store,
                investigation_id,
                stored.sha256,
                readable_document,
            )
            derived.extend(
                [
                    {
                        **json_artifact.model_dump(),
                        "kind": "readable_patent_json",
                        "is_primary_source": False,
                    },
                    {
                        **markdown_artifact.model_dump(),
                        "kind": "readable_patent_markdown",
                        "is_primary_source": False,
                    },
                ]
            )
        except Exception as exc:
            parse_issue = (
                f"{type(exc).__name__}: 可读全文标准化或扫描页 OCR 失败"
            )
            readable_document = {
                "schema_version": NORMALIZATION_VERSION,
                "source_sha256": stored.sha256,
                "source_kind": "normalization_failed",
                "normalization_status": "failed",
                "analysis_ready": False,
                "analysis_readiness_reason": parse_issue,
                "sections": [],
                "full_text": "",
            }
    else:
        derived.append(
            {
                "kind": "readable_patent_cache_hit",
                "artifact_type": "readable_patent_cache_hit",
                "sha256": stored.sha256,
                "mime_type": "application/json",
                "is_primary_source": False,
            }
        )
    if isinstance(readable_document, Mapping):
        normalized_text = str(readable_document.get("full_text") or "").strip()
        # Never replace structured EPO/OCR content with a lower-quality empty
        # PDF extraction.  The normalized document is the only analysis input.
        full_text = normalized_text or full_text
    if artifact.media_type == "application/pdf":
        try:
            pages = store.render_pdf_images(
                f"lab-{run_id}",
                stored.uri,
                f"retrieved/{provider_name}/{stem}/pages",
                max_images=8,
                max_rendered_pixels=64_000_000,
                operation_timeout_seconds=90.0,
            )
            derived.extend(
                [
                {
                    **item.model_dump(),
                    "kind": "rendered_page",
                    "is_primary_source": False,
                }
                for item in pages
                ]
            )
            image_urls.extend(item.uri for item in pages)
        except Exception as exc:
            page_issue = f"{type(exc).__name__}: PDF 页面渲染失败"
            parse_issue = f"{parse_issue}；{page_issue}" if parse_issue else page_issue
    elif artifact.media_type.startswith("text/") and not full_text:
        full_text = artifact.content.decode("utf-8", errors="replace")
    existing = tuple(
        {**dict(item), "is_primary_source": False}
        if item.get("is_primary_source")
        else dict(item)
        for item in record.artifacts
    )
    metadata_artifacts: list[dict[str, Any]] = []
    if source_metadata is not None and source_metadata.content:
        metadata_suffix = (
            ".json"
            if "json" in source_metadata.media_type.lower()
            else ".xml"
            if "xml" in source_metadata.media_type.lower()
            else ".bin"
        )
        metadata_stored = store.write_bytes(
            f"lab-{run_id}",
            (
                f"retrieved/{provider_name}/{stem}/metadata/"
                f"{source_metadata.kind}{metadata_suffix}"
            ),
            source_metadata.content,
            artifact_type=source_metadata.kind,
            mime_type=source_metadata.media_type,
        )
        if metadata_stored.sha256 != source_metadata.content_sha256:
            raise ProviderContentError("live provider 元数据快照哈希不一致")
        metadata_artifacts.append(
            {
                **metadata_stored.model_dump(),
                "kind": source_metadata.kind,
                "provider": source_metadata.provider,
                "source_url": source_metadata.source_url,
                "retrieved_at": source_metadata.retrieved_at,
                "content_sha256": metadata_stored.sha256,
                "content_length": metadata_stored.byte_size,
                "local_immutable_snapshot": True,
                "is_primary_source": False,
            }
        )
    return replace(
        record,
        full_text=full_text,
        image_urls=tuple(dict.fromkeys(image_urls)),
        content_sha256=stored.sha256,
        retrieved_at=artifact.retrieved_at,
        artifacts=(*existing, *metadata_artifacts, primary, *derived),
        provenance={
            **dict(record.provenance),
            "primary_snapshot_verified": True,
            "primary_snapshot_uri": stored.uri,
            "primary_snapshot_sha256": stored.sha256,
            **({"lab_pdf_parse_issue": parse_issue} if parse_issue else {}),
            "readable_document_version": NORMALIZATION_VERSION,
            "readable_document_cache_key": stored.sha256,
        },
        readable_document=readable_document,
        analysis_ready=bool(
            isinstance(readable_document, Mapping)
            and readable_document.get("analysis_ready")
        ),
        analysis_readiness_reason=(
            str(readable_document.get("analysis_readiness_reason") or "")
            if isinstance(readable_document, Mapping)
            else "readable document was not created"
        ),
        primary_artifact=None,
    )


def _freeze_live_retrieval_trace_artifacts(
    settings: Settings,
    context: JobContext,
    record: EvidenceRecord,
    *,
    provider: Any,
) -> EvidenceRecord:
    """Freeze every provider response used by an EPO→P020 retrieval chain."""

    run_id = str(context.module_run.get("id") or "fetch")
    store = ArtifactStore(settings.artifact_root, settings.environment)
    existing_hashes = {
        str(item.get("content_sha256") or item.get("sha256") or "")
        for item in record.artifacts
    }
    additions: list[dict[str, Any]] = []
    for artifact in tuple(
        getattr(provider, "last_retrieval_artifacts", ()) or ()
    ):
        if (
            not isinstance(artifact, RetrievedArtifact)
            or not artifact.kind.startswith("provider_")
            or artifact.content_sha256 in existing_hashes
        ):
            continue
        media_type = artifact.media_type.lower()
        suffix = (
            ".json"
            if "json" in media_type
            else ".xml"
            if "xml" in media_type
            else ".bin"
        )
        stored = store.write_bytes(
            f"lab-{run_id}",
            (
                "retrieved/retrieval-chain/"
                f"{artifact.kind}-{artifact.content_sha256}{suffix}"
            ),
            artifact.content,
            artifact_type=artifact.kind,
            mime_type=artifact.media_type,
        )
        if stored.sha256 != artifact.content_sha256:
            raise ProviderContentError("live 取文链响应快照哈希不一致")
        additions.append(
            {
                **stored.model_dump(),
                "kind": artifact.kind,
                "provider": artifact.provider,
                "source_url": artifact.source_url,
                "retrieved_at": artifact.retrieved_at,
                "content_sha256": stored.sha256,
                "content_length": stored.byte_size,
                "request_attempt_log": [
                    dict(item) for item in artifact.request_attempt_log
                ],
                "local_immutable_snapshot": True,
                "is_primary_source": False,
            }
        )
        existing_hashes.add(artifact.content_sha256)
    if not additions:
        return record
    return replace(
        record,
        artifacts=(*record.artifacts, *additions),
        provenance={
            **dict(record.provenance),
            "retrieval_attempt_artifact_hashes": [
                item["content_sha256"] for item in additions
            ],
        },
    )


def _trusted_evidence(value: Any, *, provider: str) -> EvidenceRecord:
    """Hydrate a server-trusted provider DTO without touching ManualProvider."""

    if isinstance(value, EvidenceRecord):
        if _canonical_provider_name(value.provider) != provider:
            raise ValueError("持久文献 provider 与已批准 provider 不一致")
        return value
    if not isinstance(value, Mapping):
        raise ValueError("document 必须是对象")
    artifacts = value.get("artifacts") or ()
    if not isinstance(artifacts, Sequence) or isinstance(artifacts, (str, bytes)):
        raise ValueError("document.artifacts 必须是数组")
    stage_value = str(value.get("stage") or EvidenceStage.LEAD.value)
    stage = EvidenceStage(stage_value)
    return EvidenceRecord(
        provider=provider,
        source_type=str(value.get("source_type") or "patent"),
        external_id=str(value.get("external_id") or value.get("id") or ""),
        title=str(value.get("title") or value.get("external_id") or ""),
        source_url=str(value.get("source_url") or value.get("url") or ""),
        stage=stage,
        publication_number=(
            str(value["publication_number"]) if value.get("publication_number") else None
        ),
        authority=str(value["authority"]) if value.get("authority") else None,
        language=str(value["language"]) if value.get("language") else None,
        publication_date=(
            str(value["publication_date"]) if value.get("publication_date") else None
        ),
        filing_date=str(value["filing_date"]) if value.get("filing_date") else None,
        priority_date=(
            str(value["priority_date"]) if value.get("priority_date") else None
        ),
        abstract=str(value["abstract"]) if value.get("abstract") else None,
        snippet=str(value["snippet"]) if value.get("snippet") else None,
        description=str(value["description"]) if value.get("description") else None,
        claims=str(value["claims"]) if value.get("claims") else None,
        full_text=str(value["full_text"]) if value.get("full_text") else None,
        pdf_url=str(value["pdf_url"]) if value.get("pdf_url") else None,
        image_urls=tuple(str(item) for item in value.get("image_urls") or ()),
        content_sha256=(
            str(value["content_sha256"]) if value.get("content_sha256") else None
        ),
        retrieved_at=str(value["retrieved_at"]) if value.get("retrieved_at") else None,
        artifacts=tuple(item for item in artifacts if isinstance(item, Mapping)),
        raw_metadata=(
            dict(value["raw_metadata"])
            if isinstance(value.get("raw_metadata"), Mapping)
            else {}
        ),
        provenance=(
            dict(value["provenance"])
            if isinstance(value.get("provenance"), Mapping)
            else {}
        ),
        eligibility=(
            dict(value["eligibility"])
            if isinstance(value.get("eligibility"), Mapping)
            else None
        ),
        qualification_issues=tuple(value.get("qualification_issues") or ()),
        readable_document=(
            dict(value["readable_document"])
            if isinstance(value.get("readable_document"), Mapping)
            else None
        ),
        analysis_ready=bool(value.get("analysis_ready", False)),
        analysis_readiness_reason=(
            str(value["analysis_readiness_reason"])
            if value.get("analysis_readiness_reason")
            else None
        ),
    )


def _live_lead(value: Any, *, provider: str) -> EvidenceRecord:
    """Create an untrusted live lead while discarding any client stage/hash claims."""

    if not isinstance(value, Mapping):
        raise ValueError("document 必须是对象")
    external_id = str(value.get("external_id") or value.get("id") or "").strip()
    source_url = str(value.get("source_url") or value.get("url") or "").strip()
    if not external_id or not source_url:
        raise ValueError("live document 必须包含 external_id 和 source_url")
    return EvidenceRecord(
        provider=provider,
        source_type=str(value.get("source_type") or "patent"),
        external_id=external_id,
        title=str(value.get("title") or external_id),
        source_url=source_url,
        stage=EvidenceStage.LEAD,
        publication_number=(
            str(value["publication_number"]) if value.get("publication_number") else None
        ),
        authority=str(value["authority"]) if value.get("authority") else None,
        language=str(value["language"]) if value.get("language") else None,
        publication_date=(
            str(value["publication_date"]) if value.get("publication_date") else None
        ),
        filing_date=str(value["filing_date"]) if value.get("filing_date") else None,
        priority_date=(
            str(value["priority_date"]) if value.get("priority_date") else None
        ),
        abstract=str(value["abstract"]) if value.get("abstract") else None,
        snippet=str(value["snippet"]) if value.get("snippet") else None,
        pdf_url=str(value["pdf_url"]) if value.get("pdf_url") else None,
        image_urls=tuple(str(item) for item in value.get("image_urls") or ()),
        raw_metadata={
            "application_number": (
                str(value["application_number"])
                if value.get("application_number")
                else (
                    str(value.get("raw_metadata", {}).get("application_number"))
                    if isinstance(value.get("raw_metadata"), Mapping)
                    and value.get("raw_metadata", {}).get("application_number")
                    else None
                )
            )
        },
        provenance={
            "lab_live_input": "untrusted_lead_only",
            "discovery_provider": provider,
        },
    )


def _lab_output(run: Mapping[str, Any]) -> Mapping[str, Any] | None:
    snapshot = run.get("output_snapshot")
    if not isinstance(snapshot, Mapping):
        return None
    output = snapshot.get("output")
    return output if isinstance(output, Mapping) else None


def _run_matches_claim(
    run: Mapping[str, Any],
    *,
    claim_id: str,
    claim_investigation_id: str,
) -> bool:
    run_claim_investigation_id = str(
        run.get("claim_investigation_id") or ""
    ).strip()
    if run_claim_investigation_id:
        return run_claim_investigation_id == claim_investigation_id
    snapshot = run.get("input_snapshot")
    request = snapshot.get("request") if isinstance(snapshot, Mapping) else None
    if not isinstance(request, Mapping):
        return False
    requested_claim_investigation_id = str(
        request.get("claim_investigation_id") or ""
    ).strip()
    if requested_claim_investigation_id:
        return requested_claim_investigation_id == claim_investigation_id
    data = request.get("input")
    requested_claim_id = (
        str(data.get("claim_id") or "").strip()
        if isinstance(data, Mapping)
        else ""
    )
    return bool(requested_claim_id and requested_claim_id == claim_id)


def _run_module6_batch_id(run: Mapping[str, Any]) -> str:
    snapshot = run.get("input_snapshot")
    request = snapshot.get("request") if isinstance(snapshot, Mapping) else None
    data = request.get("input") if isinstance(request, Mapping) else None
    return (
        str(data.get("module6_batch_id") or "").strip()
        if isinstance(data, Mapping)
        else ""
    )


def _run_module9_batch_id(run: Mapping[str, Any]) -> str:
    snapshot = run.get("input_snapshot")
    request = snapshot.get("request") if isinstance(snapshot, Mapping) else None
    data = request.get("input") if isinstance(request, Mapping) else None
    return (
        str(data.get("module9_batch_id") or "").strip()
        if isinstance(data, Mapping)
        else ""
    )


def _version_binding_text(value: Any) -> str:
    """Return an actual version value, never a serialized null sentinel."""

    result = str(value or "").strip()
    if result.casefold() in {"", "none", "null", "undefined", "n/a", "na"}:
        return ""
    return result


def _normalised_feature_substance(value: Any) -> str:
    """Normalise only typography; never reduce a feature to its sequence."""

    return re.sub(r"\s+", "", str(value or "")).strip().casefold()


def _lab_limitation_set_sha256(values: Sequence[Any]) -> str:
    limitations = sorted(
        (Limitation.model_validate(item) for item in values),
        key=lambda item: (item.sequence, item.feature_id),
    )
    return stable_json_sha256(
        [item.model_dump(mode="json") for item in limitations]
    )


def _comparison_matches_current_feature_substance(
    output: Mapping[str, Any],
    *,
    limitations: Sequence[Any],
) -> tuple[bool, str]:
    """Require exact current feature identities *and* their substantive text.

    Feature sequence numbers are deliberately ignored.  A disclosure row from
    another target cannot be reused merely because it was once called feature
    3 or feature 4.
    """

    expected: dict[str, str] = {}
    for raw in limitations:
        try:
            limitation = Limitation.model_validate(raw)
        except ValidationError:
            return False, "当前权利要求的技术特征结构无效"
        feature_id = str(limitation.feature_id or "").strip()
        if not feature_id or feature_id in expected:
            return False, "当前权利要求存在空白或重复的技术特征标识"
        expected[feature_id] = _normalised_feature_substance(limitation.text)

    disclosures = output.get("disclosures")
    if not isinstance(disclosures, Sequence) or isinstance(
        disclosures, (str, bytes)
    ):
        return False, "单篇比对没有逐特征披露矩阵"
    actual: dict[str, str] = {}
    for raw in disclosures:
        if not isinstance(raw, Mapping):
            return False, "单篇比对含无效逐特征行"
        feature_id = str(raw.get("feature_id") or "").strip()
        feature_text = _normalised_feature_substance(raw.get("feature_text"))
        if not feature_id or feature_id in actual:
            return False, "单篇比对含空白或重复的技术特征标识"
        actual[feature_id] = feature_text

    if set(actual) != set(expected):
        return False, "单篇比对的技术特征集合不属于当前权利要求"
    for feature_id, expected_text in expected.items():
        if not expected_text or actual.get(feature_id) != expected_text:
            return False, f"技术特征 {feature_id} 的实质内容与当前权利要求不一致"
    return True, ""


def _comparison_binding_matches_current_target(
    output: Mapping[str, Any],
    *,
    request: LabRequest,
    claim: Mapping[str, Any],
    limitations: Sequence[Any],
) -> tuple[bool, str]:
    """Validate new lab I4-S target/claim hashes; legacy rows still need the
    exact batch, claim and feature-substance gates above.
    """

    binding = output.get("_input_binding")
    if binding is None:
        return True, ""
    if not isinstance(binding, Mapping):
        return False, "单篇比对的目标绑定结构无效"
    source = (
        request.investigation.get("source_snapshot")
        if isinstance(request.investigation, Mapping)
        else None
    )
    patent_snapshot = (
        source.get("patent_snapshot") if isinstance(source, Mapping) else None
    )
    target_source_sha256 = str(
        patent_snapshot.get("source_sha256")
        if isinstance(patent_snapshot, Mapping)
        else ""
    ).strip()
    expected = {
        "target_source_sha256": target_source_sha256,
        "claim_id": str(claim.get("claim_id") or "").strip(),
        "claim_investigation_id": str(
            claim.get("claim_investigation_id") or ""
        ).strip(),
        "limitation_set_sha256": _lab_limitation_set_sha256(limitations),
        "prompt_version": I4S_PROMPT_VERSION,
        "analysis_rule_version": I4S_RULE_VERSION,
    }
    for key, value in expected.items():
        if not value or str(binding.get(key) or "").strip() != value:
            return False, f"单篇比对的当前目标绑定不一致：{key}"
    return True, ""


def _run_matches_requested_lab_scope(
    run: Mapping[str, Any],
    *,
    module6_batch_id: str,
    module9_batch_id: str,
) -> bool:
    module_code = str(run.get("module_code") or "").strip()
    if module_code == "I4_S_SINGLE_REFERENCE":
        if not module6_batch_id and not module9_batch_id:
            return True
        return bool(
            (module6_batch_id and _run_module6_batch_id(run) == module6_batch_id)
            or (module9_batch_id and _run_module9_batch_id(run) == module9_batch_id)
        )
    if module_code in {
        "I4_C_CLOSEST_PRIOR_ART",
        "I4_O_OBVIOUSNESS_PRECHECK",
    }:
        return bool(
            not module6_batch_id
            or _run_module6_batch_id(run) == module6_batch_id
        )
    if module_code == "I4_I_INVENTIVE_STEP":
        return bool(
            (not module6_batch_id or _run_module6_batch_id(run) == module6_batch_id)
            and (not module9_batch_id or _run_module9_batch_id(run) == module9_batch_id)
        )
    return True


def _successful_live_lab_runs(
    request: LabRequest,
    *,
    claim: Mapping[str, Any],
) -> list[Mapping[str, Any]]:
    if request.effective_mode != "live" or request.investigation_id is None:
        return []
    lister = getattr(request.repository, "list_module_runs", None)
    if not callable(lister):
        return []
    try:
        rows = lister(
            investigation_id=request.investigation_id,
            status="succeeded",
            limit=1_000,
        )
    except (TypeError, ValueError):
        rows = lister(investigation_id=request.investigation_id, limit=1_000)
    if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)):
        return []
    claim_id = str(claim.get("claim_id") or "").strip()
    claim_investigation_id = str(
        claim.get("claim_investigation_id") or ""
    ).strip()
    requested_module6_batch_id = str(
        request.data.get("module6_batch_id") or ""
    ).strip()
    requested_module9_batch_id = str(
        request.data.get("module9_batch_id") or ""
    ).strip()
    return [
        row
        for row in rows
        if isinstance(row, Mapping)
        and str(row.get("status") or "") == "succeeded"
        and str(row.get("effective_mode") or "live") == "live"
        and _run_matches_claim(
            row,
            claim_id=claim_id,
            claim_investigation_id=claim_investigation_id,
        )
        and _run_matches_requested_lab_scope(
            row,
            module6_batch_id=requested_module6_batch_id,
            module9_batch_id=requested_module9_batch_id,
        )
    ]


def _required_live_lineage_run(
    request: LabRequest,
    *,
    claim: Mapping[str, Any],
    input_key: str,
    module_code: str,
    public_label: str,
) -> tuple[Mapping[str, Any], Mapping[str, Any]]:
    """Resolve one exact upstream run from the current business execution.

    Selecting merely the newest successful row is unsafe in the module lab:
    the same investigation may contain several independently tested Module-6
    batches and several D1/precheck attempts.  A downstream business run must
    therefore name the exact upstream run it consumes.
    """

    requested_run_id = str(request.data.get(input_key) or "").strip()
    if not requested_run_id:
        raise PermanentJobError(
            "LAB_CURRENT_LINEAGE_REQUIRED",
            f"{public_label}必须绑定本次前序模块运行，禁止读取历史运行",
        )
    for row in _successful_live_lab_runs(request, claim=claim):
        if str(row.get("id") or "").strip() != requested_run_id:
            continue
        if str(row.get("module_code") or "").strip() != module_code:
            raise PermanentJobError(
                "LAB_CURRENT_LINEAGE_MISMATCH",
                f"{public_label}绑定的前序运行类型不一致",
            )
        output = _lab_output(row)
        if output is None:
            raise PermanentJobError(
                "LAB_CURRENT_LINEAGE_MISMATCH",
                f"{public_label}绑定的前序运行没有可用输出",
            )
        return row, output
    raise PermanentJobError(
        "LAB_CURRENT_LINEAGE_MISMATCH",
        f"{public_label}绑定的前序运行不属于本次案件、独立权利要求或模块6批次",
    )


def _current_module6_reuse_keys(
    claim: Mapping[str, Any],
    *,
    comparisons: Sequence[DocumentComparison],
    module6_batch_id: str,
) -> dict[str, dict[str, str]]:
    """Build reuse identities only for comparisons in the named Module-6 batch."""

    documents = claim.get("documents") or {}
    comparison_values = claim.get("comparisons") or {}
    qualifications = claim.get("date_qualifications") or {}
    if not all(
        isinstance(value, Mapping)
        for value in (documents, comparison_values, qualifications)
    ):
        return {}
    result: dict[str, dict[str, str]] = {}
    for comparison in comparisons:
        document_id = comparison.document_id
        raw_comparison = comparison_values.get(document_id)
        if not isinstance(raw_comparison, Mapping):
            continue
        if str(raw_comparison.get("_module6_batch_id") or "").strip() != (
            module6_batch_id
        ):
            continue
        stored = documents.get(document_id) or {}
        record = stored.get("record") if isinstance(stored, Mapping) else {}
        binding = raw_comparison.get("_input_binding") or {}
        qualification = qualifications.get(document_id) or {}
        values = {
            "document_version_id": _version_binding_text(
                (
                    stored.get("repository_document_version_id")
                    if isinstance(stored, Mapping)
                    else None
                )
                or (
                    binding.get("document_version_id")
                    if isinstance(binding, Mapping)
                    else None
                )
            ),
            "content_sha256": _version_binding_text(
                (
                    record.get("content_sha256")
                    if isinstance(record, Mapping)
                    else None
                )
                or (
                    binding.get("document_content_sha256")
                    if isinstance(binding, Mapping)
                    else None
                )
            ),
            "limitation_version_id": _version_binding_text(
                binding.get("limitation_set_sha256")
                if isinstance(binding, Mapping)
                else None
            ),
            "date_qualification_revision": _version_binding_text(
                (
                    qualification.get("repository_qualification_revision")
                    if isinstance(qualification, Mapping)
                    else None
                )
                or (
                    qualification.get("assessment_version")
                    if isinstance(qualification, Mapping)
                    else None
                )
                or (
                    qualification.get("repository_qualification_id")
                    if isinstance(qualification, Mapping)
                    else None
                )
            ),
            "i4s_run_id": _version_binding_text(
                raw_comparison.get("_i4s_module_run_id")
            ),
            "i4s_rule_version": _version_binding_text(
                raw_comparison.get("analysis_rule_version")
            ),
        }
        if all(values.values()):
            result[document_id] = values
    return result


def _overlay_lab_lineage(
    request: LabRequest,
    claim: Mapping[str, Any],
) -> Mapping[str, Any]:
    """Compose successful lab outputs without mutating the I0 checkpoint."""

    requested_module6_batch_id = str(
        request.data.get("module6_batch_id") or ""
    ).strip()
    requested_module9_batch_id = str(
        request.data.get("module9_batch_id") or ""
    ).strip()
    rows = _successful_live_lab_runs(request, claim=claim)
    if not rows and not requested_module6_batch_id and not requested_module9_batch_id:
        return claim
    result = dict(claim)
    limitations = list(result.get("limitations") or [])
    if not limitations:
        for row in rows:
            if str(row.get("module_code") or "") != "I2_QUERY_PLAN":
                continue
            output = _lab_output(row)
            values = output.get("limitations") if isinstance(output, Mapping) else None
            if isinstance(values, Sequence) and not isinstance(values, (str, bytes)):
                limitations = [
                    dict(item) for item in values if isinstance(item, Mapping)
                ]
            if limitations:
                break
    documents = dict(result.get("documents") or {})
    qualifications = dict(result.get("date_qualifications") or {})
    comparisons = (
        {}
        if requested_module6_batch_id or requested_module9_batch_id
        else {
            str(document_id): dict(value)
            for document_id, value in dict(
                result.get("comparisons") or {}
            ).items()
            if isinstance(value, Mapping)
            and str(value.get("analysis_rule_version") or "")
            == I4S_RULE_VERSION
            and str(value.get("structural_review_status") or "not_needed")
            != "model_error"
        }
    )
    closest = None
    closest_candidates: list[tuple[dict[str, Any], str]] = []
    lineage_run_ids: list[str] = []
    rejected_comparisons: list[dict[str, str]] = []

    # list_module_runs is newest-first. setdefault therefore preserves the
    # latest successful output for each document while retaining canonical I0
    # facts whenever they already exist.
    for row in rows:
        output = _lab_output(row)
        if output is None:
            continue
        module_code = str(row.get("module_code") or "")
        run_id = str(row.get("id") or "")
        if module_code == "I2_QUERY_PLAN" and not limitations:
            values = output.get("limitations")
            if isinstance(values, Sequence) and not isinstance(values, (str, bytes)):
                limitations = [
                    dict(item) for item in values if isinstance(item, Mapping)
                ]
                if limitations:
                    lineage_run_ids.append(run_id)
        elif module_code == "I3_FETCH":
            external_id = str(
                output.get("external_id")
                or output.get("publication_number")
                or ""
            ).strip()
            if external_id and external_id not in documents:
                documents[external_id] = {
                    "repository_document_id": external_id,
                    "record": dict(output),
                    "lab_module_run_id": run_id,
                }
                lineage_run_ids.append(run_id)
        elif module_code == "I3_QUALIFY":
            document_id = str(
                output.get("external_id")
                or output.get("publication_number")
                or ""
            ).strip()
            eligibility = output.get("eligibility")
            if document_id and isinstance(eligibility, Mapping):
                qualifications.setdefault(document_id, dict(eligibility))
                lineage_run_ids.append(run_id)
        elif module_code == "I4_S_SINGLE_REFERENCE":
            document_id = str(output.get("document_id") or "").strip()
            matches_features, mismatch_reason = (
                _comparison_matches_current_feature_substance(
                    output,
                    limitations=limitations,
                )
            )
            matches_target, target_mismatch_reason = (
                _comparison_binding_matches_current_target(
                    output,
                    request=request,
                    claim=result,
                    limitations=limitations,
                )
            )
            current_i4s = (
                str(row.get("prompt_version") or "") == I4S_PROMPT_VERSION
                and str(row.get("rule_version") or "") == I4S_RULE_VERSION
                and str(output.get("analysis_rule_version") or "")
                == I4S_RULE_VERSION
                and str(
                    output.get("structural_review_status") or "not_needed"
                )
                != "model_error"
                and matches_features
                and matches_target
            )
            if document_id and current_i4s and document_id not in comparisons:
                comparison = dict(output)
                comparison["_i4s_module_run_id"] = run_id
                comparison["_module6_batch_id"] = _run_module6_batch_id(row)
                comparison["_module9_batch_id"] = _run_module9_batch_id(row)
                comparisons[document_id] = comparison
                lineage_run_ids.append(run_id)
            elif document_id and not current_i4s:
                rejected_comparisons.append(
                    {
                        "document_id": document_id,
                        "module_run_id": run_id,
                        "reason": mismatch_reason
                        or target_mismatch_reason
                        or "I4-S 版本或结构复核状态无效",
                    }
                )
        elif module_code == "I4_C_CLOSEST_PRIOR_ART":
            if output.get("document_id"):
                closest_candidates.append((dict(output), run_id))

    for candidate, run_id in closest_candidates:
        if str(candidate.get("document_id") or "") in comparisons:
            closest = candidate
            lineage_run_ids.append(run_id)
            break

    result["limitations"] = limitations
    result["documents"] = documents
    result["date_qualifications"] = qualifications
    result["comparisons"] = comparisons
    if isinstance(closest, Mapping):
        result["current_closest_prior_art"] = closest
    result["lab_lineage_run_ids"] = list(dict.fromkeys(lineage_run_ids))
    result["module6_batch_id"] = requested_module6_batch_id or None
    result["module9_batch_id"] = requested_module9_batch_id or None
    result["rejected_lab_comparisons"] = rejected_comparisons
    return result


def _workflow_claim(
    request: LabRequest,
    *,
    selector: str | None = None,
) -> Mapping[str, Any]:
    investigation = request.investigation
    if not isinstance(investigation, Mapping):
        raise PermanentJobError(
            "LAB_PERSISTED_CONTEXT_REQUIRED",
            "模块需要既有持久化调查上下文",
        )
    workflow_state = investigation.get("workflow_state")
    checkpoint = (
        workflow_state.get("invalidity_workflow")
        if isinstance(workflow_state, Mapping)
        else None
    )
    claims = checkpoint.get("claims") if isinstance(checkpoint, Mapping) else None
    if not isinstance(claims, Mapping) or not claims:
        raise PermanentJobError(
            "LAB_PERSISTED_CONTEXT_REQUIRED",
            "调查没有可重放的无效工作流 claim checkpoint",
        )
    wanted = str(
        selector
        or request.source.get("claim_investigation_id")
        or request.data.get("claim_id")
        or ""
    ).strip()
    if wanted:
        for key, value in claims.items():
            if not isinstance(value, Mapping):
                continue
            if wanted in {
                str(key),
                str(value.get("claim_id") or ""),
                str(value.get("claim_investigation_id") or ""),
            }:
                return _overlay_lab_lineage(request, value)
        raise PermanentJobError(
            "LAB_PERSISTED_CLAIM_NOT_FOUND",
            "持久化调查中不存在指定权利要求",
        )
    values = [value for value in claims.values() if isinstance(value, Mapping)]
    if len(values) != 1:
        raise PermanentJobError(
            "LAB_PERSISTED_CLAIM_REQUIRED",
            "调查包含多项权利要求，必须明确 claim_investigation_id",
        )
    return _overlay_lab_lineage(request, values[0])


def _persisted_document(
    request: LabRequest,
    *,
    document_id: str,
) -> tuple[str, Mapping[str, Any], Mapping[str, Any]]:
    claim = _workflow_claim(request)
    documents = claim.get("documents")
    if not isinstance(documents, Mapping):
        raise PermanentJobError(
            "LAB_PERSISTED_DOCUMENT_NOT_FOUND",
            "持久化 claim checkpoint 没有文献",
        )
    wanted = document_id.strip()
    for key, raw in documents.items():
        if not isinstance(raw, Mapping):
            continue
        record = raw.get("record") if isinstance(raw.get("record"), Mapping) else raw
        identifiers = {
            str(key),
            str(raw.get("repository_document_id") or ""),
            str(record.get("external_id") or ""),
            str(record.get("publication_number") or ""),
        }
        if wanted and wanted in identifiers:
            return str(key), raw, claim
    raise PermanentJobError(
        "LAB_PERSISTED_DOCUMENT_NOT_FOUND",
        "指定文献不在调查的持久化 checkpoint 中",
    )


def _target_images(request: LabRequest) -> list[str]:
    if request.effective_mode != "live":
        return [str(item) for item in request.data.get("target_images") or []]
    source = (
        request.investigation.get("source_snapshot")
        if isinstance(request.investigation, Mapping)
        else None
    )
    images = source.get("target_images") if isinstance(source, Mapping) else None
    if not isinstance(images, Sequence) or isinstance(images, (str, bytes)):
        raise PermanentJobError(
            "LAB_PERSISTED_IMAGES_REQUIRED",
            "live 图文模块缺少调查创建时冻结的目标附图",
        )
    result = [str(item) for item in images if str(item).strip()]
    if not result:
        raise PermanentJobError(
            "LAB_PERSISTED_IMAGES_REQUIRED",
            "live 图文模块没有可用的冻结目标附图",
        )
    return result


def _persisted_limitations(claim: Mapping[str, Any]) -> list[Limitation]:
    values = claim.get("limitations") or []
    result = _limitations(values)
    if not result:
        raise PermanentJobError(
            "LAB_PERSISTED_LIMITATIONS_REQUIRED",
            "持久化 claim checkpoint 缺少技术特征",
        )
    return result


def _persisted_comparisons(claim: Mapping[str, Any]) -> list[DocumentComparison]:
    values = claim.get("comparisons")
    if not isinstance(values, Mapping):
        raise PermanentJobError(
            "LAB_PERSISTED_COMPARISONS_REQUIRED",
            "持久化 claim checkpoint 缺少单文献矩阵",
        )
    result = _comparisons(list(values.values()))
    if not result:
        raise PermanentJobError(
            "LAB_PERSISTED_COMPARISONS_REQUIRED",
            "持久化 claim checkpoint 没有可用单文献矩阵",
        )
    return result


_PATENT_CONTEXT_OVERLAY_KEYS = frozenset(
    {
        "patent_number",
        "application_number",
        "title",
        "holder",
        "abstract",
        "application_date",
        "priority_date",
        "publication_date",
        "grant_date",
        "bibliographic_data",
        "specification",
        "claims",
        "independent_claims",
        "figure_overview",
        "source_sha256",
        "source_format",
        "page_count",
        "used_ocr",
        "parser_version",
    }
)


def _persisted_patent_context(request: LabRequest) -> dict[str, Any]:
    """Load frozen target facts, overlaid only by a same-source successful I1 run."""

    source = (
        request.investigation.get("source_snapshot")
        if isinstance(request.investigation, Mapping)
        else None
    )
    frozen = source.get("patent_snapshot") if isinstance(source, Mapping) else None
    if not isinstance(frozen, Mapping):
        raise PermanentJobError(
            "LAB_TARGET_SNAPSHOT_MISSING",
            "模块必须读取当前案件冻结的完整专利快照",
        )
    patent_context = dict(frozen)
    lister = getattr(request.repository, "list_module_runs", None)
    if not callable(lister) or not request.investigation_id:
        return patent_context
    try:
        target_runs = lister(
            investigation_id=request.investigation_id,
            module_code="I1_TARGET_SNAPSHOT",
            status="succeeded",
            limit=20,
        )
    except (TypeError, ValueError):
        return patent_context
    frozen_sha = str(patent_context.get("source_sha256") or "").strip()
    if not frozen_sha:
        raise PermanentJobError(
            "LAB_TARGET_SNAPSHOT_SHA_REQUIRED",
            "冻结目标专利快照缺少 source_sha256，不能接受后续解析覆盖",
        )
    for target_run in target_runs:
        if not isinstance(target_run, Mapping):
            continue
        if str(target_run.get("effective_mode") or "live") != "live":
            continue
        target_output = _lab_output(target_run)
        if not isinstance(target_output, Mapping):
            continue
        output_sha = str(target_output.get("source_sha256") or "").strip()
        if not output_sha or output_sha != frozen_sha:
            continue
        patent_context.update(
            {
                key: target_output[key]
                for key in _PATENT_CONTEXT_OVERLAY_KEYS
                if key in target_output
                and target_output[key] not in (None, "", [], {})
            }
        )
        patent_context["lab_source_overlay_run_id"] = str(
            target_run.get("id") or ""
        )
        break
    return patent_context


def _persisted_expanded_claim(
    request: LabRequest,
    claim: Mapping[str, Any],
    *,
    patent_context: Mapping[str, Any] | None = None,
) -> str:
    claim_id = str(claim.get("claim_id") or "")
    patent = patent_context or _persisted_patent_context(request)
    claims = patent.get("claims") if isinstance(patent, Mapping) else None
    if isinstance(claims, Sequence) and not isinstance(claims, (str, bytes)):
        for value in claims:
            if not isinstance(value, Mapping):
                continue
            if str(value.get("claim_id") or "") == claim_id:
                text = str(
                    value.get("expanded_claim_text") or value.get("claim_text") or ""
                ).strip()
                if text:
                    return text
    raise PermanentJobError(
        "LAB_PERSISTED_CLAIM_TEXT_REQUIRED",
        "持久化调查缺少完整展开权利要求文本",
    )


def _record_text(value: Mapping[str, Any]) -> str:
    readable = value.get("readable_document")
    if not isinstance(readable, Mapping) or not readable.get("analysis_ready"):
        return ""
    return str(readable.get("full_text") or "").strip()


def _record_images(value: Mapping[str, Any]) -> list[str]:
    images = [str(item) for item in value.get("image_urls") or () if str(item).strip()]
    for artifact in value.get("artifacts") or ():
        if not isinstance(artifact, Mapping):
            continue
        kind = str(artifact.get("kind") or artifact.get("artifact_type") or "").lower()
        mime = str(artifact.get("media_type") or artifact.get("mime_type") or "").lower()
        if "image" not in kind and not mime.startswith("image/"):
            continue
        uri = artifact.get("uri") or artifact.get("local_path") or artifact.get("source_url")
        if uri:
            images.append(str(uri))
    deduplicated = list(dict.fromkeys(images))
    local_snapshots = [
        item
        for item in deduplicated
        if not item.lower().startswith(("http://", "https://"))
    ]
    # EPO image endpoints require provider authentication. Passing those URLs
    # to GLM makes the model fetch a protected resource and the whole request is
    # rejected (HTTP 400 / provider code 1210), even when locally frozen page
    # renders already exist. Prefer immutable local renders and keep the
    # multimodal payload bounded; only fall back to remote images when no local
    # snapshot exists at all.
    return (local_snapshots or deduplicated)[:4]


def _approved_persisted_record(
    settings: Settings,
    stored: Mapping[str, Any],
) -> tuple[EvidenceRecord, Mapping[str, Any], str]:
    values = stored.get("record") if isinstance(stored.get("record"), Mapping) else stored
    provider = _canonical_provider_name(values.get("provider"))
    kind = "patent" if provider in _PATENT_PROVIDER_NAMES else "npl"
    if kind == "patent":
        discovery_provider = _live_provider_name(settings, provider_kind="patent")
        approved = provider == discovery_provider
        if (
            not approved
            and discovery_provider == "patsnap"
            and provider == "epo_ops"
        ):
            provenance = (
                values.get("provenance")
                if isinstance(values.get("provenance"), Mapping)
                else {}
            )
            identity = (
                provenance.get("retrieval_identity")
                if isinstance(provenance.get("retrieval_identity"), Mapping)
                else {}
            )
            publication_number = "".join(
                character
                for character in str(values.get("publication_number") or "").upper()
                if character.isalnum()
            )
            requested = "".join(
                character
                for character in str(
                    identity.get("requested_publication_number") or ""
                ).upper()
                if character.isalnum()
            )
            returned = "".join(
                character
                for character in str(
                    identity.get("returned_publication_number") or ""
                ).upper()
                if character.isalnum()
            )
            approved = bool(
                provenance.get("discovery_provider") == "patsnap"
                and provenance.get("retrieval_provider") == "epo_ops"
                and identity.get("match") == "exact"
                and publication_number
                and publication_number == requested == returned
            )
    else:
        approved = _approved_npl_record_provider(settings, provider)
    if not approved:
        raise PermanentJobError(
            "PROVIDER_UNSUPPORTED",
            "持久化文献不是已批准的 live provider 结果",
        )
    return _trusted_evidence(values, provider=provider), values, provider


def _fixture_report_data() -> dict[str, Any]:
    investigation_id = "00000000-0000-0000-0000-000000000001"
    claim_investigation_id = "fixture-claim-1"
    return build_report_data(
        {
            "investigation": {
                "id": investigation_id,
                "analysis_session_id": "fixture-invalidity-report-v1",
                "environment": "test",
                "status": "partial",
                "pipeline_version": "fixture-v1",
                "state_version": 1,
                "created_at": "2026-07-19T00:00:00Z",
                "source_snapshot": {
                    "patent_snapshot": {
                        "patent_number": "CN209999999U",
                        "title": "一种光学检测装置",
                        "application_date": "2020-01-02",
                        "publication_date": "2020-08-01",
                        "claims": [
                            {
                                "claim_id": "1",
                                "claim_type": "INDEPENDENT",
                                "claim_text": (
                                    "一种光学检测装置，包括密闭壳体、光源、"
                                    "光学传感器以及位于光源与传感器之间的滤光件。"
                                ),
                                "expanded_claim_text": (
                                    "一种光学检测装置，包括密闭壳体、光源、"
                                    "光学传感器以及位于光源与传感器之间的滤光件。"
                                ),
                            },
                            {
                                "claim_id": "2",
                                "claim_type": "DEPENDENT",
                                "claim_text": "根据权利要求1所述的装置，滤光件可拆卸。",
                                "parent_claim_ids": ["1"],
                            },
                        ],
                        "figures": [
                            {
                                "figure_id": "figure-1",
                                "figure_description": "光源、滤光件和传感器位置示意图",
                                "page_number": 6,
                            }
                        ],
                    }
                },
            },
            "claim_investigations": [
                {
                    "id": claim_investigation_id,
                    "investigation_id": investigation_id,
                    "claim_id": "1",
                    "source_claim_text": (
                        "一种光学检测装置，包括密闭壳体、光源、"
                        "光学传感器以及位于光源与传感器之间的滤光件。"
                    ),
                    "expanded_claim_text": (
                        "一种光学检测装置，包括密闭壳体、光源、"
                        "光学传感器以及位于光源与传感器之间的滤光件。"
                    ),
                    "status": "partial",
                    "critical_date": "2020-01-02",
                    "critical_date_basis": "application_date",
                }
            ],
            "claim_limitations": [
                {
                    "id": "fixture-L1",
                    "claim_investigation_id": claim_investigation_id,
                    "feature_key": "F1",
                    "sequence_no": 1,
                    "limitation_text": "光学传感器设置于密闭壳体内",
                    "origin_claim_id": "1",
                },
                {
                    "id": "fixture-L2",
                    "claim_investigation_id": claim_investigation_id,
                    "feature_key": "F2",
                    "sequence_no": 2,
                    "limitation_text": "滤光件位于光源与传感器之间",
                    "origin_claim_id": "1",
                },
            ],
            "documents": [
                {
                    "id": "fixture-D1",
                    "investigation_id": investigation_id,
                    "canonical_key": "US20180123456A1",
                    "document_type": "patent",
                    "evidence_level": "qualified",
                    "title": "Sealed optical sensor enclosure",
                    "language": "en",
                    "identifiers": {"publication_number": "US20180123456A1"},
                    "dates": {"publication_date": "2018-01-02"},
                },
                {
                    "id": "fixture-D2",
                    "investigation_id": investigation_id,
                    "canonical_key": "fixture-npl-2017",
                    "document_type": "journal_article",
                    "evidence_level": "qualified",
                    "title": "Stray-light suppression in optical detectors",
                    "language": "en",
                    "identifiers": {"doi": "10.0000/fixture.2017.1"},
                    "dates": {"public_availability_date": "2017-06-01"},
                },
            ],
            "document_qualifications": [
                {
                    "id": "fixture-Q1",
                    "document_id": "fixture-D1",
                    "claim_investigation_id": claim_investigation_id,
                    "critical_date": "2020-01-02",
                    "publication_date": "2018-01-02",
                    "eligibility_type": "ordinary_prior_art",
                    "novelty_eligible": True,
                    "inventive_step_eligible": True,
                    "verification_status": "verified",
                    "verification_reason": "官方公开文本早于临界日",
                },
                {
                    "id": "fixture-Q2",
                    "document_id": "fixture-D2",
                    "claim_investigation_id": claim_investigation_id,
                    "critical_date": "2020-01-02",
                    "public_availability_date": "2017-06-01",
                    "eligibility_type": "ordinary_prior_art",
                    "novelty_eligible": True,
                    "inventive_step_eligible": True,
                    "verification_status": "verified",
                    "verification_reason": "期刊公开页和原文均早于临界日",
                },
            ],
            "feature_disclosures": [
                {
                    "id": "fixture-FD11",
                    "claim_investigation_id": claim_investigation_id,
                    "limitation_id": "fixture-L1",
                    "document_id": "fixture-D1",
                    "disclosure_status": "explicit",
                    "excerpt": "The optical sensor is mounted inside a sealed enclosure.",
                    "locator": "D1 第[0010]段",
                    "analysis": "D1直接公开传感器位于密闭壳体内。",
                },
                {
                    "id": "fixture-FD12",
                    "claim_investigation_id": claim_investigation_id,
                    "limitation_id": "fixture-L2",
                    "document_id": "fixture-D1",
                    "disclosure_status": "not_disclosed",
                    "excerpt": "",
                    "locator": "D1全文复核",
                    "analysis": "D1没有公开滤光件位于光源与传感器之间。",
                },
                {
                    "id": "fixture-FD21",
                    "claim_investigation_id": claim_investigation_id,
                    "limitation_id": "fixture-L1",
                    "document_id": "fixture-D2",
                    "disclosure_status": "not_disclosed",
                    "excerpt": "",
                    "locator": "D2全文复核",
                    "analysis": "D2着重滤光位置，没有公开密闭壳体结构。",
                },
                {
                    "id": "fixture-FD22",
                    "claim_investigation_id": claim_investigation_id,
                    "limitation_id": "fixture-L2",
                    "document_id": "fixture-D2",
                    "disclosure_status": "explicit",
                    "excerpt": (
                        "A filter is positioned between the illumination source "
                        "and the detector."
                    ),
                    "locator": "D2 第4页第2段",
                    "analysis": "D2直接公开滤光件的相对位置关系。",
                },
            ],
            "closest_prior_art_versions": [
                {
                    "id": "fixture-CPA1",
                    "claim_investigation_id": claim_investigation_id,
                    "document_id": "fixture-D1",
                    "version_no": 1,
                    "metrics": {
                        "explicit_feature_count": 1,
                        "total_feature_count": 2,
                    },
                    "rationale": "D1与权利要求的装置主题和F1最接近。",
                    "selected_by": "fixture-rule",
                    "is_current": True,
                }
            ],
            "combinations": [
                {
                    "id": "fixture-C1",
                    "claim_investigation_id": claim_investigation_id,
                    "closest_prior_art_version_id": "fixture-CPA1",
                    "document_ids": ["fixture-D1", "fixture-D2"],
                    "status": "supported",
                    "coverage_complete": True,
                    "motivation_status": "supported",
                    "analysis": {
                        "technical_problem": "降低杂散光对密闭传感器的影响",
                        "motivation": "D2明确教导在光源与传感器之间设置滤光件",
                        "conclusion": "D1+D2具备进一步审查创造性的证据基础",
                    },
                }
            ],
        },
        preview=True,
        generated_at=datetime(2026, 7, 19, tzinfo=timezone.utc),
    )


def _target_snapshot_summary(
    patent: PatentSnapshot,
    *,
    snapshot_state: str,
    target_image_count: int,
) -> dict[str, Any]:
    independent_claims = [
        claim for claim in patent.claims if claim.claim_type == "INDEPENDENT"
    ]
    dependent_claims = [
        claim for claim in patent.claims if claim.claim_type == "DEPENDENT"
    ]
    first_independent = independent_claims[0] if independent_claims else None
    claim_rows = [
        {
            "claim_id": claim.claim_id,
            "claim_type": claim.claim_type,
            "claim_text": claim.claim_text,
            "expanded_claim_text": claim.expanded_claim_text,
            "parent_claim_ids": list(claim.parent_claim_ids),
            "dependency_uncertain": claim.dependency_uncertain,
            "sentence_units": list(claim.sentence_units),
        }
        for claim in patent.claims
    ]
    independent_claim_rows = [
        claim for claim in claim_rows if claim["claim_type"] == "INDEPENDENT"
    ]
    figure_overview = [
        {
            key: value
            for key, value in dict(figure).items()
            if key
            in {
                "figure_id",
                "figure_description",
                "page_number",
                "selection_role",
                "selection_score",
                "selection_reasons",
                "mime_type",
                "file_size",
                "file_sha256",
                "artifact_index",
                "lab_asset_index",
            }
        }
        for figure in patent.figures
        if isinstance(figure, Mapping)
    ]
    return {
        "snapshot_state": snapshot_state,
        "source_sha256": patent.source_sha256,
        "source_format": patent.source_format,
        "source_byte_size": patent.source_byte_size,
        "page_count": patent.page_count,
        "used_ocr": patent.used_ocr,
        "parser_version": patent.parser_version,
        "patent_number": patent.patent_number,
        "application_number": patent.application_number,
        "title": patent.title,
        "holder": patent.holder,
        "abstract": patent.abstract,
        "application_date": patent.application_date,
        "priority_date": patent.priority_date,
        "publication_date": patent.publication_date,
        "grant_date": patent.grant_date,
        "bibliographic_data": dict(patent.bibliographic_data),
        "specification": dict(patent.specification),
        "specification_section_count": len(
            [key for key in patent.specification if key != "全文"]
        ),
        "claim_count": len(patent.claims),
        "independent_claim_count": len(independent_claims),
        "dependent_claim_count": len(dependent_claims),
        "figure_count": len(patent.figures),
        "target_image_count": max(0, int(target_image_count)),
        "parser_error_count": len(patent.parser_errors),
        "parser_errors": list(patent.parser_errors),
        "claims": claim_rows,
        "independent_claims": independent_claim_rows,
        "figure_overview": figure_overview,
        "first_independent_claim_id": (
            first_independent.claim_id if first_independent is not None else None
        ),
    }


def _fixture_target_snapshot_summary() -> dict[str, Any]:
    patent = PatentSnapshot.model_validate(
        {
            "source_sha256": "f" * 64,
            "source_uri": "fixture://target-patent",
            "patent_number": "CN209999999U",
            "application_number": "202020999999.9",
            "title": "一种光学检测装置",
            "holder": "示例科技有限公司",
            "abstract": "本示例公开一种带有密闭检测腔和滤光件的光学检测装置。",
            "application_date": "2020-01-02",
            "publication_date": "2020-08-01",
            "source_format": "pdf",
            "source_byte_size": 128000,
            "page_count": 8,
            "parser_version": "fixture-neutral-parser-v2",
            "bibliographic_data": {
                "application_number": "202020999999.9",
                "publication_number": "CN209999999U",
                "applicants": ["示例科技有限公司"],
                "patent_holders": ["示例科技有限公司"],
                "inventors": ["张三", "李四"],
                "classifications": ["G01N 21/00"],
                "date_events": [
                    {"code": "22", "label": "申请日", "date": "2020-01-02"},
                    {"code": "45", "label": "授权公告日", "date": "2020-08-01"},
                ],
            },
            "specification": {
                "技术领域": "本示例涉及光学检测设备领域。",
                "背景技术": "现有装置容易受到杂散光干扰。",
                "实用新型内容": "本示例提供一种密闭光学检测装置。",
                "附图说明": "图1为装置结构示意图。",
                "具体实施方式": "检测腔内设置光源、传感器和滤光件。",
                "全文": "本示例涉及光学检测设备领域……",
            },
            "claims": [
                {
                    "claim_id": "1",
                    "claim_type": "INDEPENDENT",
                    "claim_text": "一种光学检测装置。",
                    "expanded_claim_text": "一种光学检测装置。",
                },
                {
                    "claim_id": "2",
                    "claim_type": "DEPENDENT",
                    "claim_text": "根据权利要求1所述的装置。",
                    "parent_claim_ids": ["1"],
                    "expanded_claim_text": (
                        "一种光学检测装置；根据权利要求1所述的装置。"
                    ),
                },
            ],
            "figures": [
                {
                    "figure_id": "摘要附图",
                    "figure_description": "摘要附图",
                    "page_number": 1,
                    "selection_role": "abstract_figure",
                },
                {
                    "figure_id": "图1",
                    "figure_description": "装置结构示意图",
                    "page_number": 8,
                    "selection_role": "specification_drawing",
                },
            ],
        }
    )
    return _target_snapshot_summary(
        patent,
        snapshot_state="fixture_patent_snapshot",
        target_image_count=0,
    )


def _fresh_lab_target_snapshot(
    settings: Settings,
    context: JobContext,
    request: LabRequest,
) -> PatentSnapshot | None:
    source = (
        request.investigation.get("source_snapshot")
        if isinstance(request.investigation, Mapping)
        else None
    )
    frozen = source.get("frozen_source") if isinstance(source, Mapping) else None
    if not isinstance(frozen, Mapping) or frozen.get("kind") not in {
        "local_file",
        "source_url",
    }:
        return None
    source_path = str(frozen.get("uri") or "").strip()
    if not source_path:
        return None
    investigation_id = str(request.investigation_id or "").strip()
    module_run_id = str(context.module_run.get("id") or "").strip()
    if not investigation_id or not module_run_id:
        raise PermanentJobError(
            "LAB_TARGET_SNAPSHOT_CONTEXT_MISSING",
            "模块一测试缺少调查或运行编号",
        )
    artifact_dir = (
        ArtifactStore(settings.artifact_root, settings.environment)
        .investigation_dir(investigation_id)
        / "lab"
        / "module1"
        / module_run_id
    )
    adapter = Module1Adapter(
        module1_api_url=settings.module1_api_url,
        database_url=settings.database_url,
        api_token=settings.module1_api_token,
        allowed_local_roots=(
            *settings.allowed_source_roots,
            settings.artifact_root,
        ),
    )
    try:
        patent = adapter.snapshot(
            source_path=source_path,
            artifact_dir=artifact_dir,
        )
    except (PatentSnapshotError, OSError, ValueError) as exc:
        raise PermanentJobError(
            "LAB_TARGET_SNAPSHOT_PARSE_FAILED",
            "模块一未能完整读取当前案件的源文件",
        ) from exc
    expected_sha256 = str(frozen.get("sha256") or "").strip()
    if expected_sha256 and patent.source_sha256 != expected_sha256:
        raise PermanentJobError(
            "LAB_TARGET_SNAPSHOT_HASH_MISMATCH",
            "模块一读取的源文件与当前案件冻结文件不一致",
        )
    figure_dir = artifact_dir / "figures"
    figure_dir.mkdir(parents=True, exist_ok=True)
    materialized: list[dict[str, Any]] = []
    for figure in patent.figures:
        source_value = str(
            figure.get("file_path") or figure.get("figure_url") or ""
        ).strip()
        if not source_value or source_value.startswith(("http://", "https://", "data:")):
            continue
        source_figure = Path(source_value).expanduser().resolve()
        if not source_figure.is_file():
            continue
        suffix = source_figure.suffix.lower() or ".bin"
        index = len(materialized)
        destination = figure_dir / f"figure-{index:03d}{suffix}"
        shutil.copyfile(source_figure, destination)
        content = destination.read_bytes()
        row = {
            key: value
            for key, value in dict(figure).items()
            if key not in {"figure_url", "file_path", "storage_key"}
        }
        row.update(
            {
                "figure_url": str(destination),
                "file_path": str(destination),
                "file_size": len(content),
                "file_sha256": hashlib.sha256(content).hexdigest(),
                "lab_asset_index": index,
            }
        )
        materialized.append(row)
    patent.figures = materialized
    (artifact_dir / "target_snapshot.json").write_text(
        patent.model_dump_json(indent=2),
        encoding="utf-8",
    )
    return patent


def _persisted_report_data(context: JobContext, request: LabRequest) -> dict[str, Any]:
    investigation_id = request.investigation_id
    if not investigation_id:
        raise PermanentJobError(
            "LAB_REPORT_INVESTIGATION_REQUIRED",
            "manual/live I5 必须显式关联既有调查",
        )
    getter = getattr(context.repository, "get_investigation", None)
    investigation = getter(investigation_id) if callable(getter) else None
    if not isinstance(investigation, Mapping):
        raise PermanentJobError(
            "LAB_REPORT_INVESTIGATION_NOT_FOUND",
            "manual/live I5 关联的调查不存在",
        )

    report_snapshot_id = str(request.data.get("report_snapshot_id") or "").strip() or None
    snapshot_getter = getattr(context.repository, "get_report_snapshot", None)
    row = (
        snapshot_getter(investigation_id, report_snapshot_id)
        if callable(snapshot_getter)
        else None
    )
    payload: Any = None
    persisted_hash: Any = None
    if isinstance(row, Mapping):
        payload = row.get("report_snapshot")
        persisted_hash = row.get("report_sha256")
        if str(row.get("contract_version") or "v1") != "v1":
            raise PermanentJobError(
                "LAB_REPORT_CONTRACT_UNSUPPORTED",
                "持久化报告快照 contract_version 不受支持",
            )
    else:
        # Compatibility for repositories that expose an already-materialised
        # ReportDataV1 directly. Raw report tables are never upgraded here.
        report_getter = getattr(context.repository, "get_report_data", None)
        candidate = report_getter(investigation_id) if callable(report_getter) else None
        if isinstance(candidate, Mapping) and candidate.get("contract_version") == "v1":
            payload = candidate
            persisted_hash = candidate.get("snapshot_sha256")

    if not isinstance(payload, Mapping):
        raise PermanentJobError(
            "LAB_REPORT_SNAPSHOT_REQUIRED",
            "manual/live I5 只能读取既有 ReportDataV1 持久快照",
        )
    try:
        report = ReportDataV1.model_validate(payload)
    except ValidationError as exc:
        raise PermanentJobError(
            "LAB_REPORT_CONTRACT_INVALID",
            "持久化报告快照不符合 ReportDataV1",
        ) from exc
    if report.is_preview or report.report_snapshot_id is None:
        raise PermanentJobError(
            "LAB_REPORT_SNAPSHOT_REQUIRED",
            "manual/live I5 拒绝 preview 或未固化报告",
        )
    if not isinstance(persisted_hash, str) or len(persisted_hash) != 64:
        raise PermanentJobError(
            "LAB_REPORT_HASH_REQUIRED",
            "持久化报告快照缺少可审计 SHA-256",
        )
    report_investigation_id = str(report.investigation.get("id") or "")
    if report_investigation_id and report_investigation_id != investigation_id:
        raise PermanentJobError(
            "LAB_REPORT_INVESTIGATION_MISMATCH",
            "持久化报告快照不属于指定调查",
        )
    return report.model_dump(mode="json")


def _limitations(values: Any) -> list[Limitation]:
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
        raise ValueError("limitations 必须是数组")
    return [Limitation.model_validate(item) for item in values]


def _comparisons(values: Any) -> list[DocumentComparison]:
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
        raise ValueError("comparisons 必须是数组")
    return [DocumentComparison.model_validate(item) for item in values]


def _fixture_limitations() -> list[Limitation]:
    return [
        Limitation(
            feature_id="F1",
            claim_id="1",
            sequence=1,
            text="光学传感器设置于密闭壳体内",
            technical_subject="光学检测装置",
        ),
        Limitation(
            feature_id="F2",
            claim_id="1",
            sequence=2,
            text="滤光件位于光源与传感器之间",
            technical_subject="光学检测装置",
        ),
    ]


def _fixture_query_plan() -> QueryPlan:
    limitations = _fixture_limitations()
    profile = InventionSearchProfile(
        protected_subject="光学检测装置",
        subject_synonyms_zh=["光学传感装置"],
        subject_synonyms_en=["optical sensing device"],
        classification_anchors=["G01J1/00", "G02B5/20"],
        classification_anchor_sources={
            "G01J1/00": "parsed_fact",
            "G02B5/20": "model_suggested",
        },
        classification_anchor_roles={
            "G01J1/00": "subject",
            "G02B5/20": "inventive_point",
        },
        common_context_features=[
            SearchConcept(
                concept_id="fixture-context-1",
                text="光学传感器",
                feature_ids=["F1"],
                synonyms_zh=["传感器"],
                synonyms_en=["optical sensor"],
                source_reference="权利要求1",
                rationale="同类光学检测装置的常见检测构件",
                preferred_scope=SearchScope.CLAIMS,
            ),
            SearchConcept(
                concept_id="fixture-context-2",
                text="滤光件",
                feature_ids=["F1", "F2"],
                synonyms_zh=["光学滤片"],
                synonyms_en=["optical filter"],
                source_reference="权利要求1",
                rationale="同类光路中的常见滤光构件",
                preferred_scope=SearchScope.CLAIMS,
            ),
        ],
        inventive_point_features=[
            SearchConcept(
                concept_id="fixture-inventive-1",
                text="滤光件位于光源与传感器之间",
                feature_ids=["F2"],
                synonyms_zh=["光源与传感器之间的滤光件"],
                synonyms_en=["filter between light source and sensor"],
                source_reference="权利要求1",
                rationale="具体的光路位置关系体现发明点",
                preferred_scope=SearchScope.FULL_TEXT,
            )
        ],
        invention_summary="在密闭光学检测结构中限定滤光件的具体光路位置。",
    )
    return QueryPlan(
        claim_id="1",
        iteration_number=1,
        round_kind="initial",
        technical_subject="光学检测装置",
        invention_search_profile=profile,
        limitations=limitations,
        queries=[
            ContractSearchQuery(
                query_id="fixture-q1",
                provider_kind="patent",
                purpose="initial",
                technical_subject="光学检测装置",
                feature_ids=["F1", "F2"],
                expression="光学检测装置 AND 光学传感器 AND 滤光件位于光源与传感器之间",
                language="zh",
                query_role=QueryRole.INVENTIVE_POINT_PRECISION,
                query_variant=QueryVariant.MAXIMAL_SIMILARITY_PRECISION,
                search_scope=SearchScope.FULL_TEXT,
                scope_reason="具体位置关系更可能在说明书中完整出现",
                subject_terms=["光学检测装置"],
                feature_terms=["光学传感器", "滤光件位于光源与传感器之间"],
                concept_ids=["fixture-context-1", "fixture-inventive-1"],
                provider_expression=(
                    "TACD:(光学检测装置 AND 光学传感器 AND 滤光件位于光源与传感器之间)"
                ),
                rationale="保护客体与发明点位置关系组合的固定夹具",
                allow_zero_results=True,
                compact_fallback_allowed=False,
            ),
            ContractSearchQuery(
                query_id="fixture-q2",
                provider_kind="patent",
                purpose="initial",
                technical_subject="光学检测装置",
                feature_ids=["F1", "F2"],
                expression="光学检测装置 AND 光学传感器 AND 滤光件",
                language="zh",
                query_role=QueryRole.CLAIM_CONTEXT_RECALL,
                query_variant=QueryVariant.SYSTEM_ARCHITECTURE_RECALL,
                search_scope=SearchScope.CLAIMS,
                scope_reason="类别构件通常直接写入权利要求",
                subject_terms=["光学检测装置"],
                feature_terms=["光学传感器", "滤光件"],
                concept_ids=["fixture-context-1", "fixture-context-2"],
                provider_expression=(
                    "CLMS:(光学检测装置 AND 光学传感器 AND 滤光件)"
                ),
                rationale="保护客体与两个类别技术语境组组合的固定夹具",
            ),
            ContractSearchQuery(
                query_id="fixture-q3",
                provider_kind="patent",
                purpose="initial",
                technical_subject="光学检测装置",
                feature_ids=["F2"],
                expression="光学检测装置 AND 滤光件位于光源与传感器之间",
                language="zh",
                query_role=QueryRole.INVENTIVE_POINT_PRECISION,
                query_variant=QueryVariant.OBJECT_PLUS_INVENTIVE_POINT,
                search_scope=SearchScope.FULL_TEXT,
                scope_reason="发明点位置关系更可能在说明书中完整出现",
                subject_terms=["光学检测装置"],
                feature_terms=["滤光件位于光源与传感器之间"],
                concept_ids=["fixture-inventive-1"],
                provider_expression=(
                    "TACD:(光学检测装置 AND 滤光件位于光源与传感器之间)"
                ),
                rationale="客体加一个发明点",
            ),
            ContractSearchQuery(
                query_id="fixture-q4",
                provider_kind="patent",
                purpose="initial",
                technical_subject="光学检测装置",
                feature_ids=["F2"],
                expression="滤光件位于光源与传感器之间",
                language="zh",
                query_role=QueryRole.INVENTIVE_POINT_PRECISION,
                query_variant=QueryVariant.SUBJECT_CLASSIFICATION_PLUS_INVENTIVE_POINT,
                search_scope=SearchScope.FULL_TEXT,
                scope_reason="客体分类号限定类别，全文寻找发明点",
                feature_terms=["滤光件位于光源与传感器之间"],
                concept_ids=["fixture-inventive-1"],
                classification_anchors=["G01J1/00"],
                classification_anchor_sources={"G01J1/00": "parsed_fact"},
                classification_anchor_roles={"G01J1/00": "subject"},
                provider_expression=(
                    "(IPC:(G01J1/00)) AND (TACD:(滤光件位于光源与传感器之间))"
                ),
                rationale="客体分类号加发明点",
            ),
            ContractSearchQuery(
                query_id="fixture-q5",
                provider_kind="patent",
                purpose="initial",
                technical_subject="光学检测装置",
                feature_ids=[],
                expression="光学检测装置",
                language="zh",
                query_role=QueryRole.TITLE_ABSTRACT_CONCEPT,
                query_variant=QueryVariant.OBJECT_PLUS_INVENTIVE_CLASSIFICATION,
                search_scope=SearchScope.TITLE_ABSTRACT,
                scope_reason="客体通常出现在标题摘要",
                subject_terms=["光学检测装置"],
                classification_anchors=["G02B5/20"],
                classification_anchor_sources={"G02B5/20": "model_suggested"},
                classification_anchor_roles={"G02B5/20": "inventive_point"},
                provider_expression=(
                    "(IPC:(G02B5/20)) AND ((TTL:(光学检测装置) OR ABST:(光学检测装置)))"
                ),
                rationale="客体加发明点分类号",
            ),
            ContractSearchQuery(
                query_id="fixture-q6",
                provider_kind="patent",
                purpose="initial",
                technical_subject="光学检测装置",
                feature_ids=["F1", "F2"],
                expression="光学检测装置 AND 光学传感器 AND 滤光件位于光源与传感器之间",
                language="zh",
                query_role=QueryRole.INVENTIVE_POINT_PRECISION,
                query_variant=QueryVariant.OBJECT_PLUS_TWO_INVENTIVE_POINTS,
                search_scope=SearchScope.FULL_TEXT,
                scope_reason="两个结构锚点在说明书全文共同出现",
                subject_terms=["光学检测装置"],
                feature_terms=["光学传感器", "滤光件位于光源与传感器之间"],
                concept_ids=["fixture-context-1", "fixture-inventive-1"],
                provider_expression=(
                    "TACD:(光学检测装置 AND 光学传感器 AND 滤光件位于光源与传感器之间)"
                ),
                rationale="客体加两个发明/机构锚点",
            ),
        ],
        model="fixture-no-model",
        used_target_images=1,
        generation_source="fixture",
    )


def _fixture_inventive_profile() -> QueryPlan:
    """Profile-only fixture: same frozen profile/limitations, no queries."""

    return _fixture_query_plan().model_copy(
        update={"queries": [], "rejected_queries": []}
    )


def _fixture_comparison(document_id: str = "D1") -> DocumentComparison:
    return DocumentComparison(
        document_id=document_id,
        disclosures=[
            FeatureDisclosure(
                feature_id="F1",
                feature_text="传感器安装在密闭壳体中",
                status=DisclosureStatus.EXPLICIT,
                evidence_quote="传感器安装在密闭壳体中",
                evidence_location="第 10 段",
                reasoning="固定夹具中的直接披露",
                confidence=0.95,
            ),
            FeatureDisclosure(
                feature_id="F2",
                feature_text="滤光件位于传感器的入光路径上",
                status=DisclosureStatus.NOT_DISCLOSED,
                evidence_location="全文复核",
                reasoning="固定夹具中未披露滤光件的位置关系",
                confidence=0.95,
            ),
        ],
        field_alignment=0.9,
        purpose_alignment=0.8,
        effect_alignment=0.7,
        evidence_completeness=1.0,
        model="fixture-no-model",
        used_target_images=1,
        used_document_images=1,
    )


def _fixture_document(*, source_type: str = "patent") -> dict[str, Any]:
    return {
        "provider": "manual",
        "source_type": source_type,
        "external_id": "fixture-D1",
        "title": "Optical sensor enclosure with an intermediate filter",
        "source_url": "manual://fixture-D1",
        "publication_number": "US20180123456A1" if source_type == "patent" else None,
        "publication_date": "2018-01-02",
        "filing_date": "2017-01-02" if source_type == "patent" else None,
        "full_text": (
            "optical sensor enclosure filter light source sensor sealed housing "
            "光学检测装置 密闭壳体 光源 传感器 滤光件"
        ),
        "public_date_evidence": "fixture metadata",
    }


def _evidence(value: Any) -> EvidenceRecord:
    if isinstance(value, EvidenceRecord):
        value = value.model_dump()
    if not isinstance(value, Mapping):
        raise ValueError("document 必须是对象")
    # Only descriptive facts and raw content cross the manual trust boundary.
    # Stage, hashes, timestamps, artifacts, eligibility and provider identity
    # are always recomputed by the server-side handlers.
    allowed = {
        "source_type",
        "external_id",
        "id",
        "title",
        "source_url",
        "url",
        "publication_number",
        "authority",
        "language",
        "publication_date",
        "public_availability_date",
        "filing_date",
        "priority_date",
        "abstract",
        "snippet",
        "description",
        "claims",
        "full_text",
        "pdf_url",
        "image_urls",
        "content",
        "public_date_evidence",
        "raw_metadata",
        "provenance",
    }
    values = {key: item for key, item in value.items() if key in allowed}
    if isinstance(values.get("provenance"), Mapping):
        provenance = dict(values["provenance"])
        for key in (
            "human_confirmed",
            "human_supplied",
            "primary_snapshot_verified",
            "primary_snapshot_uri",
            "primary_snapshot_sha256",
        ):
            provenance.pop(key, None)
        values["provenance"] = provenance
    return ManualProvider().ingest(values)


def _manual_fetch_record(
    settings: Settings,
    context: JobContext,
    value: Any,
) -> EvidenceRecord:
    if isinstance(value, EvidenceRecord):
        raw_values: dict[str, Any] = value.model_dump()
    elif isinstance(value, Mapping):
        raw_values = dict(value)
    else:
        raise ValueError("document 必须是对象")

    source_path = str(raw_values.get("source_path") or "").strip()
    if source_path:
        try:
            content, media_type, resolved_source = read_allowed_file(
                source_path,
                allowed_roots=settings.allowed_source_roots,
                max_bytes=settings.source_max_bytes,
                code_prefix="MANUAL_EVIDENCE",
            )
        except SourceSnapshotError as exc:
            raise PermanentJobError(exc.code, exc.public_message) from exc
        suffix = resolved_source.suffix.lower() or ".bin"
    else:
        content_value = raw_values.get("content")
        if content_value is None:
            content_value = raw_values.get("full_text")
        if isinstance(content_value, str):
            content = content_value.encode("utf-8")
        elif isinstance(content_value, bytes):
            content = content_value
        else:
            raise PermanentJobError(
                "LAB_MANUAL_CONTENT_REQUIRED",
                "manual 取文必须提供 source_path、content 或 full_text",
            )
        if len(content) > settings.source_max_bytes:
            raise PermanentJobError(
                "LAB_MANUAL_CONTENT_TOO_LARGE",
                "manual 取文内容超过字节上限",
            )
        if content.lstrip().startswith(b"%PDF"):
            media_type, suffix = "application/pdf", ".pdf"
        elif content.startswith(b"\x89PNG\r\n\x1a\n"):
            media_type, suffix = "image/png", ".png"
        elif content.startswith(b"\xff\xd8\xff"):
            media_type, suffix = "image/jpeg", ".jpg"
        else:
            try:
                content.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise PermanentJobError(
                    "LAB_MANUAL_MIME_NOT_ALLOWED",
                    "manual 内联内容只允许 UTF-8 文本、PDF、PNG 或 JPEG",
                ) from exc
            media_type, suffix = "text/plain", ".txt"

    record = _evidence({**raw_values, "content": content})
    run_id = str(context.module_run.get("id") or "manual")
    namespace = f"lab-{run_id}"
    stem = hashlib.sha256(record.external_id.encode("utf-8")).hexdigest()[:24]
    store = ArtifactStore(settings.artifact_root, settings.environment)
    stored = store.write_bytes(
        namespace,
        f"manual-evidence/{stem}/source{suffix}",
        content,
        artifact_type="manual_prior_art_primary_source",
        mime_type=media_type,
    )
    retrieved_at = datetime.now(timezone.utc).isoformat()
    primary = {
        **stored.model_dump(),
        "kind": (
            "pdf"
            if media_type == "application/pdf"
            else "image"
            if media_type.startswith("image/")
            else "text"
        ),
        "source_url": str(raw_values.get("source_url") or "manual://import"),
        "media_type": media_type,
        "content_sha256": stored.sha256,
        "content_length": stored.byte_size,
        "retrieved_at": retrieved_at,
        "is_primary_source": True,
        "local_immutable_snapshot": True,
    }
    derived: list[dict[str, Any]] = []
    image_urls: list[str] = []
    full_text = record.full_text
    if media_type == "application/pdf":
        full_text = store.extract_pdf_text(content) or full_text
        pages = store.render_pdf_images(
            namespace,
            stored.uri,
            f"manual-evidence/{stem}/pages",
            max_images=2,
        )
        derived = [item.model_dump() for item in pages]
        image_urls = [item.uri for item in pages]
    elif media_type.startswith("image/"):
        image_urls = [stored.uri]

    return replace(
        record,
        provider="manual",
        stage=EvidenceStage.RETRIEVED,
        full_text=full_text,
        image_urls=tuple(image_urls),
        content_sha256=stored.sha256,
        retrieved_at=retrieved_at,
        artifacts=(primary, *derived),
        eligibility=None,
        provenance={
            **dict(record.provenance),
            "human_supplied": True,
            "primary_snapshot_verified": True,
            "primary_snapshot_uri": stored.uri,
            "primary_snapshot_sha256": stored.sha256,
        },
        qualification_issues=("date_qualification_pending",),
    )


def _date_handler(context: JobContext) -> Mapping[str, Any]:
    request = _request(context)
    mode = request.effective_mode
    data = dict(request.data)
    if mode == "fixture":
        data = {
            "application_date": "2020-01-02",
            "priority_date": "2019-01-02",
            "priority_verified": True,
            "candidates": [_fixture_document()],
        }
    elif mode == "live":
        investigation = request.investigation or {}
        source = investigation.get("source_snapshot") or {}
        settings = investigation.get("settings") or {}
        patent = source.get("patent_snapshot") or {}
        if not all(isinstance(item, Mapping) for item in (source, settings, patent)):
            raise PermanentJobError(
                "LAB_PERSISTED_DATE_FACTS_REQUIRED",
                "live 日期模块缺少冻结的专利日期事实",
            )
        data = {
            "application_date": patent.get("application_date"),
            "priority_date": patent.get("priority_date"),
            "priority_verified": source.get(
                "priority_verified", settings.get("priority_verified")
            ),
            "application_date_verified": bool(
                settings.get("application_date_verified", True)
            ),
            "candidates": [],
        }
    critical = resolve_critical_date(
        application_date=data.get("application_date"),
        priority_date=data.get("priority_date"),
        priority_verified=data.get("priority_verified"),
        application_date_verified=bool(data.get("application_date_verified", True)),
    )
    candidates = data.get("candidates") or []
    if not isinstance(candidates, list):
        raise ValueError("candidates 必须是数组")
    qualifications = []
    for candidate in candidates:
        if not isinstance(candidate, Mapping):
            raise ValueError("candidate 必须是对象")
        qualifications.append(
            {
                "external_id": candidate.get("external_id") or candidate.get("id"),
                "eligibility": classify_date_eligibility(
                    candidate,
                    critical_date=critical.critical_date,
                    critical_date_verified=not critical.requires_human_review,
                ).model_dump(),
            }
        )
    return _completed(
        context,
        request,
        {"critical_date": critical.model_dump(), "candidate_eligibility": qualifications},
        actual_provider="manual" if mode == "manual" else None,
    )


def _target_snapshot_handler(
    settings: Settings,
    context: JobContext,
) -> Mapping[str, Any]:
    request = _request(context)
    mode = request.effective_mode
    if mode == "fixture":
        return _completed(
            context,
            request,
            _fixture_target_snapshot_summary(),
            actual_provider="fixture",
            network_used=False,
        )
    if mode == "manual":
        summary = request.data.get("summary")
        if not isinstance(summary, Mapping):
            raise PermanentJobError(
                "LAB_TARGET_SNAPSHOT_MANUAL_SUMMARY_REQUIRED",
                "manual 目标快照模块必须提供 summary 对象",
            )
        allowed_summary_fields = {
            "application_date",
            "claim_count",
            "dependent_claim_count",
            "figure_count",
            "figure_overview",
            "first_independent_claim_id",
            "independent_claims",
            "independent_claim_count",
            "parser_error_count",
            "patent_number",
            "priority_date",
            "publication_date",
            "title",
        }
        return _completed(
            context,
            request,
            {
                "snapshot_state": "human_supplied_summary",
                "summary": {
                    key: value
                    for key, value in summary.items()
                    if key in allowed_summary_fields
                },
                "persisted_snapshot_created": False,
            },
            actual_provider="manual",
            network_used=False,
        )
    if request.data:
        raise PermanentJobError(
            "LAB_TARGET_SNAPSHOT_INPUT_MUST_BE_EMPTY",
            "live 目标快照模块只接受空 input，解析事实必须来自关联调查",
        )
    if request.investigation_id is None:
        raise PermanentJobError(
            "LAB_LIVE_INVESTIGATION_REQUIRED",
            "live 模式必须显式关联既有持久化调查",
        )
    existing_source = (
        request.investigation.get("source_snapshot")
        if isinstance(request.investigation, Mapping)
        else None
    )
    snapshot_was_already_frozen = bool(
        isinstance(existing_source, Mapping)
        and isinstance(existing_source.get("patent_snapshot"), Mapping)
    )
    fresh_patent = _fresh_lab_target_snapshot(settings, context, request)
    if fresh_patent is not None:
        patent = fresh_patent
        target_image_count = min(2, len(patent.figures))
        snapshot_state = "module_test_snapshot"
        actual_provider = "module1_parser"
        network_used = True
    else:
        source = ensure_patent_snapshot(
            settings=settings,
            repository=context.repository,
            investigation_id=request.investigation_id,
        )
        patent_value = source.get("patent_snapshot")
        if not isinstance(patent_value, Mapping):
            raise PermanentJobError(
                "LAB_TARGET_SNAPSHOT_MISSING",
                "模块一未返回可验证的目标专利快照",
            )
        patent = PatentSnapshot.model_validate(patent_value)
        target_images = source.get("target_images")
        target_image_count = (
            len(target_images)
            if isinstance(target_images, Sequence)
            and not isinstance(target_images, (str, bytes))
            else 0
        )
        snapshot_state = str(
            source.get("snapshot_state") or "patent_snapshot_frozen"
        )
        actual_provider = (
            "persisted_target_snapshot"
            if snapshot_was_already_frozen
            else "module1_parser"
        )
        network_used = not snapshot_was_already_frozen
    return _completed(
        context,
        request,
        _target_snapshot_summary(
            patent,
            snapshot_state=snapshot_state,
            target_image_count=target_image_count,
        ),
        actual_provider=actual_provider,
        network_used=network_used,
    )


def _query_plan_handler(settings: Settings, context: JobContext) -> Mapping[str, Any]:
    request = _request(context)
    mode = request.effective_mode
    data = request.data
    if mode == "fixture":
        return _completed(context, request, _fixture_query_plan())
    target_images = _target_images(request)
    patent_context = data.get("patent_context")
    engine = _engine(settings)
    manual_profile = data.get("inventive_profile_plan")
    if mode == "manual" and isinstance(manual_profile, Mapping):
        source_plan_run_id = str(data.get("source_plan_run_id") or "").strip()
        if not source_plan_run_id:
            raise ValueError(
                "manual 模块4使用模块3画像时必须提供 source_plan_run_id"
            )
        try:
            result = engine.generate_queries_from_inventive_profile(
                previous_plan=manual_profile,
                source_plan_run_id=source_plan_run_id,
                source_sha256=(
                    str(data.get("source_sha256") or "").strip() or None
                ),
                max_queries=int(
                    data.get("max_queries") or I2_DEFAULT_MAX_QUERIES
                ),
            )
        except Exception:
            _persist_lab_i2_model_response(settings, context, engine)
            raise
        audit_artifact = _persist_lab_i2_model_response(
            settings, context, engine
        )
        return _completed(
            context,
            request,
            {
                **result.model_dump(mode="json"),
                "model_response_audit": audit_artifact,
            },
            actual_provider="manual",
            model=result.model,
            network_used=True,
            used_target_images=0,
        )
    if mode == "live":
        patent_context = _persisted_patent_context(request)
        # 2026-08-05 用户决定：模块4「生成检索关键词」的输入仍是模块3
        # 「总结核心发明点」的最新成功输出（发明画像 + 技术特征），但生成
        # 检索词时必须重新调用 GLM 在该画像之上分析和生成，不再免模型确定性
        # 重编译。冻结快照门控保留（上方 _persisted_patent_context），但画像
        # 生成检索词不再接收专利全文。
        claim = {
            "claim_id": str(data.get("claim_id") or "").strip(),
            "claim_investigation_id": str(
                request.source.get("claim_investigation_id")
                or data.get("claim_investigation_id")
                or ""
            ).strip(),
        }
        profile_row = next(
            (
                row
                for row in _successful_live_lab_runs(request, claim=claim)
                if str(row.get("module_code") or "") == "I2_INVENTIVE_PROFILE"
                and isinstance(_lab_output(row), Mapping)
            ),
            None,
        )
        if profile_row is None:
            raise PermanentJobError(
                "LAB_I2_PROFILE_REQUIRED",
                "模块4需要先在模块3总结核心发明点：当前案件该独立权利要求还没有成功的模块3运行",
            )
        profile_output = _lab_output(profile_row)
        try:
            result = engine.generate_queries_from_inventive_profile(
                previous_plan=profile_output,
                source_plan_run_id=str(profile_row.get("id") or ""),
                source_sha256=(
                    str(profile_output.get("source_sha256") or "").strip() or None
                ),
                max_queries=int(data.get("max_queries") or I2_DEFAULT_MAX_QUERIES),
            )
        except Exception:
            _persist_lab_i2_model_response(settings, context, engine)
            raise
        # 这条显式「模块3画像输出 -> 模块4 GLM 生成检索词」路径每次都会真实
        # 调用模型，model_response_audit 持久化完整调用审计；它不受下方
        # LAB_I2_CACHED_PROFILE_FORBIDDEN 守门约束（该守门只拦 plan_queries
        # 分支的引擎内部缓存复用）。
        audit_artifact = _persist_lab_i2_model_response(settings, context, engine)
        return _completed(
            context,
            request,
            {
                **result.model_dump(mode="json"),
                "model_response_audit": audit_artifact,
            },
            actual_provider=None,
            model=result.model,
            network_used=True,
            used_target_images=len(target_images),
        )
    try:
        result = engine.plan_queries(
            claim_id=str(data.get("claim_id") or ""),
            expanded_claim_text=str(data.get("expanded_claim_text") or ""),
            target_images=target_images,
            patent_context=patent_context if isinstance(patent_context, Mapping) else None,
            iteration_number=int(data.get("iteration_number") or 1),
            round_kind=str(data.get("round_kind") or "initial"),
            existing_limitations=_limitations(data.get("existing_limitations") or []),
            gap_feature_ids=list(data.get("gap_feature_ids") or []),
            closest_prior_art_context=(
                data.get("closest_prior_art_context")
                if isinstance(data.get("closest_prior_art_context"), Mapping)
                else None
            ),
            max_queries=int(data.get("max_queries") or I2_DEFAULT_MAX_QUERIES),
        )
    except Exception:
        _persist_lab_i2_model_response(settings, context, engine)
        raise
    if (
        settings.environment == "test"
        and result.generation_source == "same_source_cached_profile"
    ):
        # 2026-08-02 用户决定：测试阶段每次点击都必须重新生成检索方案，
        # 任何情况下不得复用同案缓存画像。守门 fail closed，防止旧代码
        # 或未来改动把缓存结果再次带进测试环境。该守门只约束本 plan_queries
        # 分支（manual/旧 live 全量生成）的引擎内部缓存复用；live 模式下
        # 律师显式的「模块3画像输出 -> 模块4确定性重编译」路径已在上方
        # 提前返回，不受此守门拦截。
        raise PermanentJobError(
            "LAB_I2_CACHED_PROFILE_FORBIDDEN",
            "测试环境禁止复用同案缓存画像，每次运行必须重新生成检索方案",
        )
    audit_artifact = _persist_lab_i2_model_response(settings, context, engine)
    return _completed(
        context,
        request,
        {
            **result.model_dump(mode="json"),
            "model_response_audit": audit_artifact,
        },
        actual_provider="manual" if mode == "manual" else None,
        model=result.model,
        network_used=True,
        used_target_images=len(target_images),
    )


def _inventive_profile_handler(
    settings: Settings, context: JobContext
) -> Mapping[str, Any]:
    request = _request(context)
    mode = request.effective_mode
    data = request.data
    if mode == "fixture":
        return _completed(context, request, _fixture_inventive_profile())
    target_images = _target_images(request)
    patent_context = data.get("patent_context")
    if mode == "live":
        patent_context = _persisted_patent_context(request)
    engine = _engine(settings)
    try:
        # max_queries is accepted for input compatibility with I2_QUERY_PLAN
        # but deliberately ignored: this module never compiles queries.
        result = engine.generate_inventive_profile(
            claim_id=str(data.get("claim_id") or ""),
            expanded_claim_text=str(data.get("expanded_claim_text") or ""),
            target_images=target_images,
            patent_context=patent_context if isinstance(patent_context, Mapping) else None,
            iteration_number=int(data.get("iteration_number") or 1),
            round_kind=str(data.get("round_kind") or "initial"),
            existing_limitations=_limitations(data.get("existing_limitations") or []),
            gap_feature_ids=list(data.get("gap_feature_ids") or []),
            closest_prior_art_context=(
                data.get("closest_prior_art_context")
                if isinstance(data.get("closest_prior_art_context"), Mapping)
                else None
            ),
        )
    except Exception:
        _persist_lab_i2_model_response(
            settings, context, engine, stage="I2_INVENTIVE_PROFILE"
        )
        raise
    if (
        settings.environment == "test"
        and result.generation_source == "same_source_cached_profile"
    ):
        # 与 I2 相同的 fail-closed 防线：画像模块每次都重新调用模型，
        # 正常路径不会触发；保留守门防止未来改动把缓存画像带进测试环境。
        raise PermanentJobError(
            "LAB_I2_CACHED_PROFILE_FORBIDDEN",
            "测试环境禁止复用同案缓存画像，每次运行必须重新生成发明画像",
        )
    audit_artifact = _persist_lab_i2_model_response(
        settings, context, engine, stage="I2_INVENTIVE_PROFILE"
    )
    return _completed(
        context,
        request,
        {
            **result.model_dump(mode="json"),
            "model_response_audit": audit_artifact,
        },
        actual_provider="manual" if mode == "manual" else None,
        model=result.model,
        network_used=True,
        used_target_images=len(target_images),
    )


def _persist_lab_i2_model_response(
    settings: Settings,
    context: JobContext,
    engine: InvalidityAnalysisEngine,
    *,
    stage: str = "I2_QUERY_PLAN",
) -> dict[str, Any] | None:
    audit = getattr(engine, "last_invocation_audit", None)
    if not isinstance(audit, Mapping):
        return None
    run_id = str(context.module_run.get("id") or "")
    investigation_id = str(
        context.module_run.get("investigation_id") or f"lab-{run_id}"
    )
    artifact_dir = (
        "model-responses/i2-profile"
        if stage == "I2_INVENTIVE_PROFILE"
        else "model-responses/i2"
    )
    store = ArtifactStore(settings.artifact_root, settings.environment)
    artifact = store.write_json(
        investigation_id,
        f"{artifact_dir}/lab-{run_id}.json",
        dict(audit),
        artifact_type="i2_model_response",
    )
    artifact_id: str | None = None
    inserter = getattr(context.repository, "insert_artifact", None)
    if callable(inserter):
        row = inserter(
            investigation_id=investigation_id,
            module_run_id=run_id or None,
            artifact_type=artifact.artifact_type,
            uri=artifact.uri,
            sha256=artifact.sha256,
            mime_type=artifact.mime_type,
            byte_size=artifact.byte_size,
            metadata={
                "stage": stage,
                "model": str(audit.get("model") or "unknown"),
                "normalization": dict(audit.get("normalization") or {}),
                "attempt_count": int(audit.get("attempt_count") or 1),
                "successful_attempt": audit.get("successful_attempt"),
                "module_lab": True,
            },
            actor="MODULE_LAB",
        )
        artifact_id = str(row.get("id") or "") or None
    return {
        "artifact_id": artifact_id,
        "artifact_type": artifact.artifact_type,
        "sha256": artifact.sha256,
        "byte_size": artifact.byte_size,
        "normalization": dict(audit.get("normalization") or {}),
        "attempt_count": int(audit.get("attempt_count") or 1),
        "successful_attempt": audit.get("successful_attempt"),
    }


def _search_handler(
    settings: Settings, context: JobContext, *, provider_kind: str
) -> Mapping[str, Any]:
    request = _request(context)
    mode = request.effective_mode
    data = dict(request.data)
    if mode == "fixture":
        data = {
            "query": {
                "text": "optical sensor filter",
                "subject_terms": ["optical sensor"],
                "feature_terms": ["filter"],
            },
            "documents": [
                _fixture_document(
                    source_type="patent" if provider_kind == "patent" else "preprint"
                )
            ],
        }
    modality = _search_modality(data)
    provider_override, has_provider_override = _lab_search_provider(
        settings,
        data,
        provider_kind=provider_kind,
        mode=mode,
    )
    if modality in _IMAGE_SEARCH_MODALITIES:
        if provider_kind != "patent":
            raise PermanentJobError(
                "LAB_IMAGE_SEARCH_PATENT_ONLY",
                "图片相似检索只允许用于 I3_PATENT_SEARCH",
            )
        if mode != "live":
            raise PermanentJobError(
                "LAB_IMAGE_SEARCH_LIVE_ONLY",
                "图片相似检索只允许 live 模块实验",
            )
        if not has_provider_override or provider_override != "patsnap":
            raise PermanentJobError(
                "LAB_IMAGE_SEARCH_PATSNAP_REQUIRED",
                "图片相似检索必须显式设置 search_provider=patsnap",
            )
        image_urls, patent_type, image_model, maximum, image_offset = (
            _image_search_parameters(data, modality=modality)
        )
        query = None
    else:
        query = data.get("query")
        if query is None:
            raise ValueError("search 模块缺少 query")
        maximum = int(data.get("max_results") or settings.max_candidates_per_query)
        image_urls = []
        patent_type = ""
        image_model = 0
        image_offset = 0
    subject_terms = list(data.get("subject_terms") or []) or None
    feature_terms = list(data.get("feature_terms") or []) or None
    frozen_plan_query_verified = False
    frozen_plan_run_id: str | None = None
    frozen_plan_query_id: str | None = None
    if (
        mode == "live"
        and provider_kind == "patent"
        and modality == "text"
        and (data.get("query_plan_run_id") or data.get("query_plan_query_id"))
    ):
        trusted = _trusted_fixed_plan_query(request, data)
        query = ProviderSearchQuery(
            text=trusted["provider_expression"],
            trusted_fixed_plan=True,
        )
        # The successful I2 run already checked applicant/subject/feature/
        # classification sufficiency and atomized the frozen expression. Do
        # not make the generic adapter re-interpret long pre-atomization terms.
        subject_terms = None
        feature_terms = None
        data["excluded_publication"] = trusted["excluded_publication"] or None
        if trusted["date_channel"] == "cn_conflicting_application":
            # A conflicting CN application may publish after the target's
            # critical date.  Restrict its filing date, not its publication
            # date, and leave the final legal classification to I3-QUALIFY.
            data.pop("server_before", None)
            data["filing_before"] = trusted["critical_date"]
        else:
            data.pop("filing_before", None)
            data["server_before"] = trusted["critical_date"]
        frozen_plan_query_verified = True
        frozen_plan_run_id = trusted["query_plan_run_id"]
        frozen_plan_query_id = trusted["query_id"]
    actual_provider: str
    network_used = False
    search_artifacts: list[dict[str, Any]] = []
    providers_attempted: list[str] = []
    providers_succeeded: list[str] = []
    provider_errors: list[str] = []
    search_complete = True
    if mode in {"fixture", "manual"}:
        documents = data.get("documents") or []
        if not isinstance(documents, list):
            raise ValueError("documents 必须是数组")
        provider = ManualProvider(documents)
        records = provider.search(
            query,
            max_results=maximum,
            subject_terms=subject_terms,
            feature_terms=feature_terms,
        )
        if mode == "manual":
            records = [
                replace(
                    record,
                    stage=EvidenceStage.LEAD,
                    full_text=None,
                    content_sha256=None,
                    retrieved_at=None,
                    artifacts=(),
                    eligibility=None,
                    qualification_issues=("document_not_fetched",),
                )
                for record in records
            ]
        actual_provider = "fixture" if mode == "fixture" else "manual"
        providers_attempted = [actual_provider]
        providers_succeeded = [actual_provider]
    elif provider_kind == "patent":
        actual_provider = (
            provider_override
            if has_provider_override
            else _live_provider_name(settings, provider_kind="patent")
        )
        with _live_patent_provider(
            settings,
            provider_name=provider_override if has_provider_override else None,
        ) as provider:
            try:
                if modality == "image_single":
                    records = provider.search_single_image(
                        image_urls[0],
                        patent_type=patent_type,
                        model=image_model,
                        max_results=maximum,
                        offset=image_offset,
                    )
                elif modality == "image_multiple":
                    records = provider.search_multiple_images(
                        image_urls,
                        patent_type=patent_type,
                        model=image_model,
                        max_results=maximum,
                        offset=image_offset,
                    )
                else:
                    records = provider.search(
                        query,
                        max_results=maximum,
                        language=str(data.get("language") or "en"),
                        country=str(data.get("country") or "") or None,
                        server_before=data.get("server_before")
                        or data.get("critical_date"),
                        server_after=data.get("server_after"),
                        filing_before=data.get("filing_before"),
                        subject_terms=subject_terms,
                        feature_terms=feature_terms,
                    )
                if modality in _IMAGE_SEARCH_MODALITIES and any(
                    not isinstance(record, EvidenceRecord)
                    or record.stage is not EvidenceStage.LEAD
                    for record in records
                ):
                    raise ProviderContentError("智慧芽图片检索只能返回 lead")
                records, search_artifacts = _freeze_live_search_response(
                    settings,
                    context,
                    provider,
                    records,
                )
            except (ProviderContentError, QueryValidationError) as exc:
                _attach_failed_search_audit(
                    exc,
                    settings=settings,
                    context=context,
                    provider=provider,
                )
                raise
        network_used = True
        providers_attempted = [actual_provider]
        providers_succeeded = [actual_provider]
    else:
        actual_provider = _live_provider_name(settings, provider_kind="npl")
        provider = _live_npl_provider(settings)
        try:
            if isinstance(provider, ArxivProvider):
                search_result: Any = provider.search(
                    query,
                    max_results=maximum,
                    start=int(data.get("start") or 0),
                    subject_terms=subject_terms,
                    feature_terms=feature_terms,
                )
            else:
                search_result = provider.search(
                    query,
                    max_results=maximum,
                    subject_terms=subject_terms,
                    feature_terms=feature_terms,
                )
            if isinstance(search_result, ProviderSearchBatch):
                records = list(search_result.records)
                search_complete = search_result.complete
                providers_attempted = list(search_result.providers_attempted)
                providers_succeeded = list(search_result.providers_succeeded)
                provider_errors = list(search_result.errors)
            else:
                records = list(search_result)
                provider_name = _canonical_provider_name(
                    getattr(provider, "provider_name", actual_provider)
                )
                providers_attempted = [provider_name]
                providers_succeeded = [provider_name]
        finally:
            close = getattr(provider, "close", None)
            if callable(close):
                close()
        network_used = True
    query_text = (
        str(query.get("text") or query.get("expression") or "")
        if isinstance(query, Mapping)
        else str(query or "")
    )
    excluded_publication = str(data.get("excluded_publication") or "").strip()
    excluded_target_documents: list[dict[str, Any]] = []
    if provider_kind == "patent" and excluded_publication:
        excluded_key = _publication_base_key(excluded_publication)
        kept_records: list[EvidenceRecord] = []
        for record in records:
            if (
                excluded_key
                and _publication_base_key(record.publication_number) == excluded_key
            ):
                excluded_target_documents.append(
                    {
                        "external_id": record.external_id,
                        "publication_number": record.publication_number,
                        "reason": "target_publication_excluded_locally",
                    }
                )
            else:
                kept_records.append(record)
        records = kept_records
    output = {
        "provider_kind": provider_kind,
        "query_text": query_text,
        "query_strategy": str(data.get("query_strategy") or "").strip() or None,
        "count": len(records),
        "complete": search_complete,
        "providers_attempted": providers_attempted,
        "providers_succeeded": providers_succeeded,
        "provider_errors": provider_errors,
        "zero_result_reason": (
            "检索源对该精确查询返回 0 条；这只表示本次查询未命中，不等于不存在现有技术。"
            if not records
            else None
        ),
        "documents": [record.model_dump() for record in records],
        "excluded_publication": excluded_publication or None,
        "target_exclusion_applied": bool(excluded_publication),
        "excluded_target_documents": excluded_target_documents,
        "frozen_plan_query_verified": frozen_plan_query_verified,
        "query_plan_run_id": frozen_plan_run_id,
        "query_plan_query_id": frozen_plan_query_id,
        "date_channel": (
            trusted["date_channel"] if frozen_plan_query_verified else None
        ),
        "search_artifacts": [
            {
                "artifact_type": item.get("artifact_type"),
                "kind": item.get("kind"),
                "provider": item.get("provider"),
                "source_url": item.get("source_url"),
                "sha256": item.get("sha256"),
                "byte_size": item.get("byte_size"),
                "mime_type": item.get("mime_type"),
                "retrieved_at": item.get("retrieved_at"),
            }
            for item in search_artifacts
        ],
    }
    if modality in _IMAGE_SEARCH_MODALITIES:
        output.update(
            {
                "search_modality": modality,
                "input_image_count": len(image_urls),
            }
        )
    return _completed(
        context,
        request,
        output,
        actual_provider=actual_provider,
        network_used=network_used,
    )


def _normalized_frozen_expression(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _trusted_fixed_plan_query(
    request: LabRequest,
    data: Mapping[str, Any],
) -> dict[str, str]:
    """Resolve one exact fixed-lane query from same-claim persisted I2 output."""

    plan_run_id = str(data.get("query_plan_run_id") or "").strip()
    query_id = str(data.get("query_plan_query_id") or "").strip()
    if not plan_run_id or not query_id:
        raise PermanentJobError(
            "LAB_FIXED_QUERY_LINEAGE_REQUIRED",
            "固定五组真实检索必须同时引用检索方案运行和该方案内的 query_id",
        )
    claim = _workflow_claim(request)
    plan_run = next(
        (
            row
            for row in _successful_live_lab_runs(request, claim=claim)
            if str(row.get("id") or "") == plan_run_id
            and str(row.get("module_code") or "")
            in {"I2_QUERY_PLAN", "I2_GAP_QUERY_PLAN"}
        ),
        None,
    )
    if not isinstance(plan_run, Mapping):
        raise PermanentJobError(
            "LAB_FIXED_QUERY_PLAN_INVALID",
            "指定检索方案不存在、未成功或不属于当前独立权利要求",
        )
    output = _lab_output(plan_run)
    queries = output.get("queries") if isinstance(output, Mapping) else None
    frozen = next(
        (
            item
            for item in queries
            if isinstance(item, Mapping)
            and str(item.get("query_id") or "").strip() == query_id
        ),
        None,
    ) if isinstance(queries, Sequence) and not isinstance(queries, (str, bytes)) else None
    if not isinstance(frozen, Mapping):
        raise PermanentJobError(
            "LAB_FIXED_QUERY_NOT_FOUND",
            "指定 query_id 不属于该检索方案",
        )
    module_code = str(plan_run.get("module_code") or "")
    variant = str(frozen.get("query_variant") or "").strip()
    date_channel = str(
        frozen.get("date_channel") or "ordinary_prior_art"
    ).strip()
    is_fixed_first_round = (
        module_code == "I2_QUERY_PLAN"
        and variant in _FIXED_FIRST_ROUND_QUERY_VARIANTS
        and date_channel == "ordinary_prior_art"
        and str(
            frozen.get("search_objective") or "full_claim_single_reference"
        )
        == "full_claim_single_reference"
    )
    is_gap_query = (
        module_code == "I2_GAP_QUERY_PLAN"
        and str(frozen.get("query_role") or "") == "gap_followup"
        and variant == "gap_followup"
        and date_channel
        in {"ordinary_prior_art", "cn_conflicting_application"}
        and str(frozen.get("search_objective") or "")
        in {"gap_or_combination", "common_knowledge_evidence"}
    )
    if (
        str(frozen.get("provider_kind") or "") != "patent"
        or not (is_fixed_first_round or is_gap_query)
    ):
        raise PermanentJobError(
            "LAB_FIXED_QUERY_VARIANT_INVALID",
            "该查询不是当前固定五组普通现有技术专利检索线",
        )
    provider_expression = _normalized_frozen_expression(
        frozen.get("provider_expression")
    )
    supplied_query = data.get("query")
    supplied_expression = _normalized_frozen_expression(
        supplied_query.get("text") or supplied_query.get("expression")
        if isinstance(supplied_query, Mapping)
        else supplied_query
    )
    if not provider_expression or supplied_expression != provider_expression:
        raise PermanentJobError(
            "LAB_FIXED_QUERY_EXPRESSION_MISMATCH",
            "本次检索式与检索方案冻结的供应商表达式不一致",
        )
    critical_date = str(claim.get("critical_date") or "").strip()
    if not critical_date:
        raise PermanentJobError(
            "LAB_FIXED_QUERY_DATE_REQUIRED",
            "当前独立权利要求缺少已持久化关键日",
        )
    return {
        "query_plan_run_id": plan_run_id,
        "query_id": query_id,
        "provider_expression": provider_expression,
        "excluded_publication": str(
            frozen.get("excluded_publication") or ""
        ).strip(),
        "critical_date": critical_date,
        "date_channel": date_channel,
    }


def _module_run_request_data(run: Mapping[str, Any]) -> Mapping[str, Any]:
    snapshot = run.get("input_snapshot")
    request = snapshot.get("request") if isinstance(snapshot, Mapping) else None
    data = request.get("input") if isinstance(request, Mapping) else None
    return data if isinstance(data, Mapping) else {}


def _candidate_filter_live_sources(
    request: LabRequest,
) -> tuple[list[dict[str, Any]], Mapping[str, Any], str]:
    claim = _workflow_claim(request)
    rows = _successful_live_lab_runs(request, claim=claim)
    by_id = {
        str(row.get("id") or ""): row
        for row in rows
        if str(row.get("id") or "")
    }
    requested_ids = request.data.get("search_run_ids")
    if not isinstance(requested_ids, Sequence) or isinstance(
        requested_ids, (str, bytes)
    ):
        raise PermanentJobError(
            "LAB_CANDIDATE_FILTER_SEARCH_RUNS_REQUIRED",
            "取文前候选清理必须引用本案已经完成的检索运行",
        )
    search_run_ids = list(
        dict.fromkeys(_text for item in requested_ids if (_text := str(item).strip()))
    )
    if not search_run_ids:
        raise PermanentJobError(
            "LAB_CANDIDATE_FILTER_SEARCH_RUNS_REQUIRED",
            "取文前候选清理没有收到检索运行编号",
        )
    search_outputs: list[dict[str, Any]] = []
    for run_id in search_run_ids:
        row = by_id.get(run_id)
        if not isinstance(row, Mapping):
            raise PermanentJobError(
                "LAB_CANDIDATE_FILTER_SOURCE_INVALID",
                "指定检索运行不存在、未成功或不属于当前权利要求",
            )
        module_code = str(row.get("module_code") or "")
        if module_code not in {"I3_PATENT_SEARCH", "I3_NPL_SEARCH"}:
            raise PermanentJobError(
                "LAB_CANDIDATE_FILTER_SOURCE_INVALID",
                "候选清理只接受 I3 专利或论文检索运行",
            )
        output = _lab_output(row)
        if not isinstance(output, Mapping):
            raise PermanentJobError(
                "LAB_CANDIDATE_FILTER_SOURCE_INVALID",
                "指定检索运行没有可读取的持久化输出",
            )
        source_input = _module_run_request_data(row)
        query = source_input.get("query")
        query_id = (
            str(query.get("query_id") or "").strip()
            if isinstance(query, Mapping)
            else ""
        )
        search_outputs.append(
            {
                "search_run_id": run_id,
                "query_id": query_id,
                "query_text": str(output.get("query_text") or ""),
                "provider_kind": str(
                    output.get("provider_kind")
                    or ("npl" if module_code == "I3_NPL_SEARCH" else "patent")
                ),
                "documents": list(output.get("documents") or []),
            }
        )

    plan_run_id = str(request.data.get("query_plan_run_id") or "").strip()
    plan_row: Mapping[str, Any] | None = None
    if plan_run_id:
        candidate = by_id.get(plan_run_id)
        if isinstance(candidate, Mapping) and str(
            candidate.get("module_code") or ""
        ) in {"I2_QUERY_PLAN", "I2_GAP_QUERY_PLAN"}:
            plan_row = candidate
        else:
            raise PermanentJobError(
                "LAB_CANDIDATE_FILTER_PLAN_INVALID",
                "指定检索方案不存在、未成功或不属于当前权利要求",
            )
    else:
        plan_row = next(
            (
                row
                for row in rows
                if str(row.get("module_code") or "") == "I2_QUERY_PLAN"
                and isinstance(_lab_output(row), Mapping)
            ),
            None,
        )
    plan = _lab_output(plan_row) if isinstance(plan_row, Mapping) else None
    has_profile = isinstance(plan, Mapping) and isinstance(
        plan.get("invention_search_profile"), Mapping
    )
    plan_limitations = plan.get("limitations") if isinstance(plan, Mapping) else None
    has_frozen_gap_facts = (
        isinstance(plan, Mapping)
        and str(plan_row.get("module_code") or "") == "I2_GAP_QUERY_PLAN"
        and bool(str(plan.get("technical_subject") or "").strip())
        and isinstance(plan_limitations, Sequence)
        and not isinstance(plan_limitations, (str, bytes))
        and any(isinstance(item, Mapping) for item in plan_limitations)
    )
    if not has_profile and not has_frozen_gap_facts:
        raise PermanentJobError(
            "LAB_CANDIDATE_FILTER_PLAN_REQUIRED",
            "候选清理需要 I2 发明画像，或 I2-G 已冻结的技术主题与区别特征",
        )
    return search_outputs, plan, str(plan_row.get("id") or "")


def _candidate_filter_fetch_source(
    request: LabRequest,
) -> tuple[str, str, Mapping[str, Any]]:
    """Resolve a retained lead only from a successful same-claim filter run."""

    claim = _workflow_claim(request)
    rows = _successful_live_lab_runs(request, claim=claim)
    filter_run_id = str(
        request.data.get("candidate_filter_run_id") or ""
    ).strip()
    candidate_id = str(request.data.get("candidate_id") or "").strip()
    if not filter_run_id or not candidate_id:
        raise PermanentJobError(
            "LAB_FILTERED_CANDIDATE_REQUIRED",
            "live 取文必须引用本案候选清理运行和保留候选编号",
        )
    row = next(
        (
            value
            for value in rows
            if str(value.get("id") or "") == filter_run_id
            and str(value.get("module_code") or "") == "I3_CANDIDATE_FILTER"
        ),
        None,
    )
    output = _lab_output(row) if isinstance(row, Mapping) else None
    if not isinstance(output, Mapping):
        raise PermanentJobError(
            "LAB_FILTERED_CANDIDATE_INVALID",
            "候选清理运行不存在、未成功或不属于当前独立权利要求",
        )
    candidates = output.get("fetch_candidates")
    if not isinstance(candidates, Sequence) or isinstance(
        candidates, (str, bytes)
    ):
        raise PermanentJobError(
            "LAB_FILTERED_CANDIDATE_INVALID",
            "候选清理运行没有可执行取文计划",
        )
    candidate = next(
        (
            value
            for value in candidates
            if isinstance(value, Mapping)
            and str(value.get("candidate_id") or "") == candidate_id
        ),
        None,
    )
    document = (
        candidate.get("document")
        if isinstance(candidate, Mapping)
        and isinstance(candidate.get("document"), Mapping)
        else None
    )
    if not isinstance(document, Mapping):
        raise PermanentJobError(
            "LAB_FILTERED_CANDIDATE_INVALID",
            "该候选未进入后端持久化的取文清单",
        )
    provider_kind = str(candidate.get("provider_kind") or "patent").strip().lower()
    if provider_kind not in {"patent", "npl"}:
        raise PermanentJobError(
            "LAB_FILTERED_CANDIDATE_INVALID",
            "候选清理结果中的来源类型无效",
        )
    source_provider = _canonical_provider_name(document.get("provider"))
    return provider_kind, source_provider, document


@dataclass(frozen=True, slots=True)
class _ReusablePatentFetch:
    """One exact-publication, hash-verified historical I3-FETCH result.

    Only the public patent source/readable document is reusable.  The current
    filter run, date qualification and I4-S comparison remain separate facts.
    """

    module_run_id: str
    record: EvidenceRecord
    primary_artifact: Mapping[str, Any]
    primary_content: bytes
    readable_document_current: bool


def _strict_publication_identity(value: Any) -> str:
    """Return an exact publication identity, including the kind code.

    A/B/U/S variants can contain different public text.  They therefore must
    never be collapsed here, even though candidate-family grouping may treat
    them as related during pre-fetch cleanup.
    """

    normalized = re.sub(r"[^A-Z0-9]", "", str(value or "").upper())
    if not re.fullmatch(r"[A-Z]{2,3}[A-Z0-9]*\d[A-Z]\d?", normalized):
        return ""
    return normalized


def _record_publication_identity(value: Mapping[str, Any]) -> str:
    candidates: list[Any] = [value.get("publication_number")]
    raw_metadata = value.get("raw_metadata")
    if isinstance(raw_metadata, Mapping):
        candidates.extend(
            raw_metadata.get(key)
            for key in (
                "publication_number",
                "publicationNumber",
                "publication_no",
                "pn",
            )
        )
    # Some approved providers use the publication number as external_id.  A
    # Patsnap UUID or another opaque provider ID does not pass the strict form.
    candidates.append(value.get("external_id"))
    return next(
        (
            normalized
            for candidate in candidates
            if (normalized := _strict_publication_identity(candidate))
        ),
        "",
    )


def _verified_cached_primary_source(
    settings: Settings,
    record: EvidenceRecord,
) -> tuple[Mapping[str, Any], bytes] | None:
    expected = str(record.content_sha256 or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{64}", expected):
        return None
    try:
        artifact_root = settings.artifact_root.expanduser().resolve(strict=True)
    except OSError:
        return None
    for artifact in record.artifacts:
        if not artifact.get("is_primary_source"):
            continue
        if not artifact.get("local_immutable_snapshot"):
            continue
        digest = str(
            artifact.get("sha256") or artifact.get("content_sha256") or ""
        ).strip().lower()
        if digest != expected:
            continue
        raw_uri = str(artifact.get("uri") or "").strip()
        if not raw_uri or raw_uri.lower().startswith(("http://", "https://")):
            continue
        try:
            path = Path(raw_uri).expanduser().resolve(strict=True)
        except OSError:
            continue
        if not path.is_file() or not (
            path == artifact_root or artifact_root in path.parents
        ):
            continue
        try:
            content = path.read_bytes()
        except OSError:
            continue
        if hashlib.sha256(content).hexdigest() != expected:
            continue
        declared_size = artifact.get("byte_size") or artifact.get("content_length")
        if declared_size is not None:
            try:
                if int(declared_size) != len(content):
                    continue
            except (TypeError, ValueError):
                continue
        return {**dict(artifact), "uri": str(path)}, content
    return None


def _cached_readable_document_is_current(
    settings: Settings,
    record: EvidenceRecord,
) -> bool:
    readable = record.readable_document
    if not record.analysis_ready or not isinstance(readable, Mapping):
        return False
    if (
        readable.get("schema_version") != NORMALIZATION_VERSION
        or readable.get("analysis_ready") is not True
        or str(readable.get("source_sha256") or "").strip().lower()
        != str(record.content_sha256 or "").strip().lower()
        or len(re.sub(r"\s+", "", str(readable.get("full_text") or ""))) < 200
    ):
        return False
    page_count = readable.get("page_count")
    if page_count is not None and not (
        isinstance(page_count, int)
        and page_count > 0
        and readable.get("processed_page_count") == page_count
        and isinstance(readable.get("failed_pages"), list)
        and not readable.get("failed_pages")
    ):
        return False

    # The database output is an index, not the content authority.  Reuse OCR
    # text only when its own immutable JSON artifact still hashes to the row's
    # readable_document value.  If this artifact is absent/stale, rebuild from
    # the separately verified source PDF rather than trusting a copied blob.
    try:
        artifact_root = settings.artifact_root.expanduser().resolve(strict=True)
    except OSError:
        return False
    for artifact in record.artifacts:
        kind = str(
            artifact.get("kind") or artifact.get("artifact_type") or ""
        ).lower()
        if kind != "readable_patent_json":
            continue
        uri = str(artifact.get("uri") or "").strip()
        digest = str(
            artifact.get("sha256") or artifact.get("content_sha256") or ""
        ).strip().lower()
        if not uri or not re.fullmatch(r"[0-9a-f]{64}", digest):
            continue
        try:
            path = Path(uri).expanduser().resolve(strict=True)
            content = path.read_bytes()
        except OSError:
            continue
        if not path.is_file() or not (
            path == artifact_root or artifact_root in path.parents
        ):
            continue
        if hashlib.sha256(content).hexdigest() != digest:
            continue
        try:
            frozen_readable = json.loads(content.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        if isinstance(frozen_readable, Mapping) and stable_json_sha256(
            frozen_readable
        ) == stable_json_sha256(readable):
            return True
    return False


def _safe_cached_artifacts(
    settings: Settings,
    record: EvidenceRecord,
    primary: Mapping[str, Any],
) -> tuple[Mapping[str, Any], ...]:
    """Keep only immutable local cache artifacts whose bytes still verify."""

    try:
        artifact_root = settings.artifact_root.expanduser().resolve(strict=True)
    except OSError:
        return (dict(primary),)
    retained: list[Mapping[str, Any]] = [dict(primary)]
    primary_uri = str(primary.get("uri") or "")
    for artifact in record.artifacts:
        uri = str(artifact.get("uri") or "").strip()
        if not uri or uri == primary_uri or uri.lower().startswith(("http://", "https://")):
            continue
        try:
            path = Path(uri).expanduser().resolve(strict=True)
        except OSError:
            continue
        if not path.is_file() or not (
            path == artifact_root or artifact_root in path.parents
        ):
            continue
        digest = str(
            artifact.get("sha256") or artifact.get("content_sha256") or ""
        ).strip().lower()
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            continue
        try:
            if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                continue
        except OSError:
            continue
        retained.append({**dict(artifact), "uri": str(path)})
    return tuple(retained)


def _historical_fetch_rows(
    repository: Any,
    *,
    publication_number: str,
) -> Sequence[Mapping[str, Any]]:
    finder = getattr(repository, "find_reusable_patent_fetches", None)
    if callable(finder):
        try:
            rows = finder(publication_number=publication_number, limit=20)
        except (TypeError, ValueError):
            rows = []
        if isinstance(rows, Sequence) and not isinstance(rows, (str, bytes)):
            return [item for item in rows if isinstance(item, Mapping)]
    lister = getattr(repository, "list_module_runs", None)
    if not callable(lister):
        return []
    try:
        rows = lister(module_code="I3_FETCH", status="succeeded", limit=1_000)
    except (TypeError, ValueError):
        return []
    if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)):
        return []
    return [item for item in rows if isinstance(item, Mapping)]


def _find_reusable_live_patent_fetch(
    settings: Settings,
    context: JobContext,
    current_lead: EvidenceRecord,
) -> _ReusablePatentFetch | None:
    publication_number = _record_publication_identity(current_lead.model_dump())
    if not publication_number:
        return None
    current_run_id = str(context.module_run.get("id") or "")
    for row in _historical_fetch_rows(
        context.repository,
        publication_number=publication_number,
    ):
        run_id = str(row.get("id") or "")
        if not run_id or run_id == current_run_id:
            continue
        if (
            str(row.get("module_code") or "") != "I3_FETCH"
            or str(row.get("status") or "") != "succeeded"
            or str(row.get("effective_mode") or "live") != "live"
        ):
            continue
        output = _lab_output(row)
        if not isinstance(output, Mapping):
            continue
        if bool(output.get("simulated")) or str(
            output.get("effective_mode") or "live"
        ) != "live":
            continue
        if _record_publication_identity(output) != publication_number:
            continue
        try:
            cached_record, _values, _provider = _approved_persisted_record(
                settings,
                {"record": output},
            )
        except (PermanentJobError, TypeError, ValueError):
            continue
        if (
            cached_record.source_type != "patent"
            or cached_record.stage is EvidenceStage.LEAD
        ):
            continue
        primary = _verified_cached_primary_source(settings, cached_record)
        if primary is None:
            continue
        primary_artifact, primary_content = primary
        readable_current = _cached_readable_document_is_current(
            settings,
            cached_record,
        )
        media_type = str(
            primary_artifact.get("mime_type")
            or primary_artifact.get("media_type")
            or ""
        ).lower()
        # A stale readable view may be rebuilt without network only when the
        # frozen source itself contains the complete document.  Metadata-only
        # or arbitrary HTML caches fall back to the approved provider.
        if not readable_current and media_type not in {
            "application/pdf",
            "application/vnd.epo.ops.bundle+json",
        }:
            continue
        return _ReusablePatentFetch(
            module_run_id=run_id,
            record=cached_record,
            primary_artifact=primary_artifact,
            primary_content=primary_content,
            readable_document_current=readable_current,
        )
    return None


def _reuse_live_patent_fetch(
    settings: Settings,
    context: JobContext,
    request: LabRequest,
    current_lead: EvidenceRecord,
    cached: _ReusablePatentFetch,
) -> EvidenceRecord:
    cached_record = cached.record
    publication_number = _record_publication_identity(current_lead.model_dump())
    cache_audit = {
        "retrieval_cache_hit": True,
        "retrieval_cache_source_run_id": cached.module_run_id,
        "retrieval_cache_publication_number": publication_number,
        "retrieval_cache_content_sha256": cached_record.content_sha256,
        "retrieval_cache_readable_reused": cached.readable_document_current,
        "retrieval_cache_validation": (
            "exact_publication_kind+verified_source_sha256+current_readable_contract"
            if cached.readable_document_current
            else "exact_publication_kind+verified_source_sha256+readable_rebuilt"
        ),
        "current_candidate_filter_run_id": str(
            request.data.get("candidate_filter_run_id") or ""
        ),
        "current_candidate_id": str(request.data.get("candidate_id") or ""),
        "current_candidate_external_id": current_lead.external_id,
    }
    provenance = {
        **dict(cached_record.provenance),
        **cache_audit,
        # The current filter lineage remains the discovery source; the old
        # retrieval provider/identity still proves where the bytes came from.
        "discovery_provider": current_lead.provider,
    }
    if cached.readable_document_current:
        readable = dict(cached_record.readable_document or {})
        safe_artifacts = _safe_cached_artifacts(
            settings,
            cached_record,
            cached.primary_artifact,
        )
        safe_image_urls = tuple(
            str(item.get("uri"))
            for item in safe_artifacts
            if (
                str(item.get("mime_type") or item.get("media_type") or "")
                .lower()
                .startswith("image/")
            )
        )
        return replace(
            cached_record,
            external_id=current_lead.external_id,
            title=current_lead.title or cached_record.title,
            publication_number=publication_number,
            authority=current_lead.authority or cached_record.authority,
            language=current_lead.language or cached_record.language,
            raw_metadata={
                **dict(cached_record.raw_metadata),
                **dict(current_lead.raw_metadata),
            },
            stage=EvidenceStage.RETRIEVED_DOCUMENT,
            full_text=str(readable.get("full_text") or cached_record.full_text or ""),
            image_urls=safe_image_urls,
            artifacts=safe_artifacts,
            provenance=provenance,
            eligibility=None,
            qualification_issues=(),
            readable_document=readable,
            analysis_ready=True,
            primary_artifact=None,
        )

    media_type = str(
        cached.primary_artifact.get("mime_type")
        or cached.primary_artifact.get("media_type")
        or "application/octet-stream"
    )
    source_artifact = RetrievedArtifact(
        provider=cached_record.provider,
        kind=str(cached.primary_artifact.get("kind") or "cached_patent_source"),
        source_url=cached_record.source_url,
        media_type=media_type,
        content=cached.primary_content,
        retrieved_at=str(cached_record.retrieved_at or ""),
    )
    rebuilt = replace(
        cached_record,
        external_id=current_lead.external_id,
        title=current_lead.title or cached_record.title,
        publication_number=publication_number,
        authority=current_lead.authority or cached_record.authority,
        language=current_lead.language or cached_record.language,
        raw_metadata={
            **dict(cached_record.raw_metadata),
            **dict(current_lead.raw_metadata),
        },
        stage=EvidenceStage.RETRIEVED_DOCUMENT,
        full_text=None,
        image_urls=(),
        artifacts=(),
        provenance=provenance,
        eligibility=None,
        qualification_issues=(),
        readable_document=None,
        analysis_ready=False,
        analysis_readiness_reason=None,
        primary_artifact=source_artifact,
    )
    return _freeze_live_retrieved_document(settings, context, rebuilt)


def _candidate_filter_model(
    settings: Settings,
    *,
    target_context: Mapping[str, Any],
    candidates: Sequence[Mapping[str, Any]],
    target_images: Sequence[str],
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    client = VisionLLMClient(
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key,
        model=settings.llm_model,
        timeout_seconds=settings.llm_timeout_seconds,
        direct_attempt_timeout_seconds=settings.llm_direct_attempt_timeout_seconds,
        direct_probe_timeout_seconds=settings.llm_direct_probe_timeout_seconds,
    )
    return invoke_semantic_candidate_filter(
        client,
        target_context=target_context,
        candidates=candidates,
        target_images=target_images,
        request_timeout_seconds=min(120.0, settings.llm_timeout_seconds),
        direct_attempt_timeout_seconds=min(
            90.0, settings.llm_direct_attempt_timeout_seconds
        ),
    )


def _persist_candidate_filter_audit(
    settings: Settings,
    context: JobContext,
    audit: Mapping[str, Any],
) -> dict[str, Any]:
    run_id = str(context.module_run.get("id") or "")
    investigation_id = str(
        context.module_run.get("investigation_id") or f"lab-{run_id}"
    )
    store = ArtifactStore(settings.artifact_root, settings.environment)
    artifact = store.write_json(
        investigation_id,
        f"model-responses/i3-candidate-filter/lab-{run_id}.json",
        dict(audit),
        artifact_type="i3_candidate_filter_audit",
    )
    artifact_id: str | None = None
    inserter = getattr(context.repository, "insert_artifact", None)
    if callable(inserter):
        row = inserter(
            investigation_id=investigation_id,
            module_run_id=run_id or None,
            artifact_type=artifact.artifact_type,
            uri=artifact.uri,
            sha256=artifact.sha256,
            mime_type=artifact.mime_type,
            byte_size=artifact.byte_size,
            metadata={
                "stage": "I3_CANDIDATE_FILTER",
                "prompt_sha256": str(audit.get("prompt_sha256") or ""),
                "model": str(audit.get("model") or ""),
                "candidate_count": int(audit.get("candidate_count") or 0),
                "module_lab": True,
            },
            actor="MODULE_LAB",
        )
        artifact_id = str(row.get("id") or "") or None
    return {
        "artifact_id": artifact_id,
        "artifact_type": artifact.artifact_type,
        "sha256": artifact.sha256,
        "byte_size": artifact.byte_size,
        "prompt_sha256": str(audit.get("prompt_sha256") or ""),
    }


def _candidate_filter_handler(
    settings: Settings, context: JobContext
) -> Mapping[str, Any]:
    request = _request(context)
    mode = request.effective_mode
    if mode == "fixture":
        relevant = {
            **_fixture_document(),
            "external_id": "WO2026000001A2",
            "publication_number": "WO2026000001A2",
            "title": "Optical sensing device with filter",
            "raw_metadata": {"application_number": "PCT/CN2025/000001"},
        }
        duplicate = {
            **relevant,
            "external_id": "WO2026000001A3",
            "publication_number": "WO2026000001A3",
        }
        unrelated = {
            **_fixture_document(),
            "external_id": "US2026000002A1",
            "publication_number": "US2026000002A1",
            "title": "Refrigerator shelf assembly",
        }
        search_outputs = [
            {
                "search_run_id": "fixture-search-1",
                "query_id": "fixture-q1",
                "query_text": "optical sensor filter",
                "provider_kind": "patent",
                "documents": [relevant, duplicate, unrelated],
            }
        ]
        plan: Mapping[str, Any] = _fixture_query_plan().model_dump(mode="json")
        fixture_decisions: dict[str, dict[str, str]] | None = None
    elif mode == "manual":
        documents = request.data.get("documents")
        if not isinstance(documents, Sequence) or isinstance(
            documents, (str, bytes)
        ):
            raise ValueError("manual 候选清理需要 documents 数组")
        search_outputs = [
            {
                "search_run_id": "manual-search",
                "query_id": str(request.data.get("query_id") or "manual-query"),
                "query_text": str(request.data.get("query_text") or ""),
                "provider_kind": str(
                    request.data.get("provider_kind") or "patent"
                ),
                "documents": list(documents),
            }
        ]
        supplied_plan = request.data.get("query_plan")
        plan = (
            supplied_plan
            if isinstance(supplied_plan, Mapping)
            else _fixture_query_plan().model_dump(mode="json")
        )
        fixture_decisions = None
    else:
        search_outputs, plan, plan_run_id = _candidate_filter_live_sources(
            request
        )
        fixture_decisions = None

    raw_candidates = build_raw_candidates(search_outputs, top_n_per_search=10)
    groups, duplicates = group_candidates(raw_candidates)
    target_context = target_filter_context(plan)
    classifiable_groups = [
        group
        for group in groups
        if isinstance(group.get("representative_document"), Mapping)
        and metadata_is_sufficient(group["representative_document"])
        and not matched_positive_anchors(
            group["representative_document"], target_context
        )
        and not matched_technical_terms(
            group["representative_document"], target_context
        )
    ]
    model_attempted = False
    model_failure: str | None = None
    model_failure_detail: dict[str, Any] | None = None
    used_target_image_count: int | None = None
    audit_artifact: Mapping[str, Any] | None = None
    decisions: dict[str, dict[str, Any]] = {}
    if mode == "fixture":
        fixture_decisions = {
            str(group.get("representative_candidate_id") or ""): {
                "status": (
                    "exclude_obvious_unrelated"
                    if str(
                        (group.get("representative_document") or {}).get("title")
                        or ""
                    )
                    == "Refrigerator shelf assembly"
                    else "keep_relevant"
                ),
                "reason": "固定夹具的版本化预设判断",
                "confidence": "high",
                "exclusion_checks": {
                    "same_or_related_object": False,
                    "shared_medium_or_energy": False,
                    "shared_component_role": False,
                    "shared_operation_or_control": False,
                    "analogous_mechanism_possible": False,
                },
            }
            for group in groups
        }
        decisions = fixture_decisions
    elif classifiable_groups:
        model_attempted = True
        try:
            model_target_images = _target_images(request)[:1]
            used_target_image_count = len(model_target_images)
            decisions, audit = _candidate_filter_model(
                settings,
                target_context=target_context,
                candidates=classifiable_groups,
                target_images=model_target_images,
            )
            audit_artifact = _persist_candidate_filter_audit(
                settings, context, audit
            )
        except (MultimodalModelError, TypeError, ValueError) as exc:
            model_failure = (
                str(getattr(exc, "reason_code", "") or "").strip()
                or type(exc).__name__
            )
            model_failure_detail = {
                "failure_type": type(exc).__name__,
                "reason_code": str(
                    getattr(exc, "reason_code", "") or ""
                ).strip()
                or None,
                "http_status": getattr(exc, "http_status", None),
                "retryable": getattr(exc, "retryable", None),
                "message": str(exc)[:300],
            }
            audit_artifact = _persist_candidate_filter_audit(
                settings,
                context,
                {
                    "prompt_sha256": "",
                    "model": settings.llm_model,
                    "candidate_count": len(classifiable_groups),
                    **model_failure_detail,
                    "fail_open": True,
                },
            )

    screened = apply_filter_decisions(
        groups,
        target_context=target_context,
        model_decisions=decisions,
        model_failure=model_failure,
    )
    fetch_candidates = list(screened["fetch_candidates"])
    excluded_candidates = list(screened["excluded_candidates"])
    warnings = []
    if model_failure:
        warnings.append("语义筛选调用失败；本批未确定候选均已保留取文。")
    if not raw_candidates:
        warnings.append("引用的检索运行没有候选文献。")
    input_sha256 = stable_json_sha256(
        {
            "source_search_run_ids": [
                str(item.get("search_run_id") or "") for item in search_outputs
            ],
            "query_plan_run_id": plan_run_id if mode == "live" else None,
            "raw_candidates": raw_candidates,
            "target_context": target_context,
        }
    )
    decision_sha256 = stable_json_sha256(
        {
            "candidate_groups": screened["candidate_groups"],
            "duplicate_candidates": duplicates,
            "excluded_candidates": excluded_candidates,
            "fetch_candidates": fetch_candidates,
        }
    )
    model_prompt_sha256 = (
        str(audit_artifact.get("prompt_sha256") or "")
        if isinstance(audit_artifact, Mapping)
        else ""
    )
    audit_sha256 = stable_json_sha256(
        {
            "filter_contract_version": FILTER_CONTRACT_VERSION,
            "identity_rule_version": IDENTITY_RULE_VERSION,
            "semantic_rule_version": SEMANTIC_RULE_VERSION,
            "input_sha256": input_sha256,
            "decision_sha256": decision_sha256,
            "model_prompt_sha256": model_prompt_sha256,
            "model_failure": model_failure,
        }
    )
    output = {
        "filter_contract_version": FILTER_CONTRACT_VERSION,
        "identity_rule_version": IDENTITY_RULE_VERSION,
        "semantic_rule_version": SEMANTIC_RULE_VERSION,
        "source_search_run_ids": [
            str(item.get("search_run_id") or "") for item in search_outputs
        ],
        "query_plan_run_id": (
            plan_run_id if mode == "live" else None
        ),
        "raw_candidate_count": len(raw_candidates),
        "unique_group_count": len(groups),
        "duplicate_count": len(duplicates),
        "excluded_count": len(excluded_candidates),
        "fetch_candidate_count": len(fetch_candidates),
        "estimated_fetch_requests_saved": len(raw_candidates)
        - len(fetch_candidates),
        "raw_candidates": raw_candidates,
        "candidate_groups": screened["candidate_groups"],
        "duplicate_candidates": duplicates,
        "excluded_candidates": excluded_candidates,
        "fetch_candidates": fetch_candidates,
        "target_context": target_context,
        "model_attempted": model_attempted,
        "model_failure": model_failure,
        "model_failure_detail": model_failure_detail,
        "model_audit_artifact": audit_artifact,
        "model_prompt_sha256": model_prompt_sha256 or None,
        "input_sha256": input_sha256,
        "decision_sha256": decision_sha256,
        "audit_sha256": audit_sha256,
        "warnings": warnings,
    }
    return _completed(
        context,
        request,
        output,
        actual_provider=(
            "fixture"
            if mode == "fixture"
            else (
                "glm_semantic_candidate_filter"
                if model_attempted and not model_failure
                else (
                    "deterministic_fail_open"
                    if model_failure
                    else "deterministic_candidate_filter"
                )
            )
        ),
        model=(
            "fixture-no-model"
            if mode == "fixture"
            else settings.llm_model if model_attempted else None
        ),
        network_used=model_attempted,
        used_target_images=(
            0
            if mode == "fixture"
            else used_target_image_count if model_attempted else None
        ),
    )


def _fetch_handler(settings: Settings, context: JobContext) -> Mapping[str, Any]:
    request = _request(context)
    mode = request.effective_mode
    data = dict(request.data)
    result_is_frozen = False
    if mode == "fixture":
        data = {"document": _fixture_document()}
    if mode == "fixture":
        record = _evidence(data.get("document"))
        provider_name = "fixture"
        result = ManualProvider().retrieve(record)
        network_used = False
    elif mode == "manual":
        provider_name = "manual"
        result = _manual_fetch_record(settings, context, data.get("document"))
        network_used = False
    else:
        kind, document_provider, document = _candidate_filter_fetch_source(
            request
        )
        configured_provider = _live_provider_name(settings, provider_kind=kind)
        if kind == "npl":
            if not _approved_npl_record_provider(settings, document_provider):
                raise PermanentJobError(
                    "PROVIDER_UNSUPPORTED",
                    "该非专利文献不是当前复合检索源返回的候选",
                )
            provider_name = document_provider
        else:
            if document_provider and document_provider != configured_provider:
                raise PermanentJobError(
                    "PROVIDER_UNSUPPORTED",
                    "该专利候选不是当前智慧芽检索源返回的候选",
                )
            provider_name = configured_provider
        record = _live_lead(document, provider=provider_name)
        network_used = True
    if mode == "live" and provider_name in _PATENT_PROVIDER_NAMES:
        cached = _find_reusable_live_patent_fetch(settings, context, record)
        if cached is not None:
            result = _reuse_live_patent_fetch(
                settings,
                context,
                request,
                record,
                cached,
            )
            provider_name = "patent_text_cache"
            network_used = False
            result_is_frozen = True
        else:
            with _live_patent_retrieval_provider(settings) as provider:
                result = provider.retrieve(record)
                result = _freeze_live_retrieval_trace_artifacts(
                    settings,
                    context,
                    result,
                    provider=provider,
                )
                provider_name = _canonical_provider_name(
                    result.provenance.get("retrieval_provider")
                    or getattr(provider, "provider_name", provider_name)
                )
    elif mode == "live":
        provider = _live_npl_provider(settings)
        try:
            if isinstance(provider, CompositeNplProvider):
                adapter, retrieval_record = provider.retrieval_plan_for(record)
            else:
                adapter, retrieval_record = provider, record
            retrieve = getattr(adapter, "retrieve", None)
            if not callable(retrieve):
                raise PermanentJobError(
                    "LAB_NPL_RETRIEVAL_UNAVAILABLE",
                    "该候选目前只有题录线索，尚无可核验全文取文通道",
                )
            result = retrieve(retrieval_record)
            if result.provider != provider_name:
                result = replace(
                    result,
                    provider=provider_name,
                    provenance={
                        **dict(result.provenance),
                        "retrieved_via": str(
                            getattr(adapter, "provider_name", "unknown")
                        ),
                    },
                )
        finally:
            close = getattr(provider, "close", None)
            if callable(close):
                close()
    if mode == "live" and not result_is_frozen:
        result = _freeze_live_retrieved_document(settings, context, result)
    return _completed(
        context,
        request,
        result,
        actual_provider=provider_name,
        network_used=network_used,
    )


def _qualify_handler(context: JobContext) -> Mapping[str, Any]:
    request = _request(context)
    mode = request.effective_mode
    data = dict(request.data)
    if mode == "fixture":
        data = {
            "document": _fixture_document(),
            "critical_date": "2020-01-02",
            "content_relevance_verified": True,
            "source_chain_verified": True,
            "public_date_verified": True,
            "public_date_evidence": "fixture metadata",
        }
    if mode == "live":
        document_id = str(data.get("document_id") or "").strip()
        if not document_id:
            raise PermanentJobError(
                "LAB_PERSISTED_DOCUMENT_REQUIRED",
                "live 资格模块必须选择持久化 document_id",
            )
        document_key, stored, claim = _persisted_document(
            request, document_id=document_id
        )
        record, record_values, provider_name = _approved_persisted_record(
            context.settings, stored
        )
        qualifications = claim.get("date_qualifications")
        qualification = (
            qualifications.get(document_key)
            if isinstance(qualifications, Mapping)
            else None
        )
        if isinstance(qualification, Mapping):
            result = replace(record, eligibility=dict(qualification))
        else:
            critical_date = str(claim.get("critical_date") or "").strip()
            source = (
                request.investigation.get("source_snapshot")
                if isinstance(request.investigation, Mapping)
                else None
            )
            patent = (
                source.get("patent_snapshot")
                if isinstance(source, Mapping)
                else None
            )
            if not critical_date and isinstance(patent, Mapping):
                resolution = resolve_critical_date(
                    application_date=patent.get("application_date"),
                    priority_date=patent.get("priority_date"),
                    priority_verified=bool(
                        source.get("priority_verified")
                        if isinstance(source, Mapping)
                        else False
                    ),
                    application_date_verified=True,
                )
                critical_date = str(resolution.critical_date or "")
            if not critical_date:
                raise PermanentJobError(
                    "LAB_PERSISTED_CRITICAL_DATE_REQUIRED",
                    "当前独立权利要求缺少可计算的临界日",
                )
            provenance = dict(record.provenance)
            source_chain_verified = bool(
                provenance.get("primary_snapshot_verified")
            )
            public_date_evidence = str(
                provenance.get("public_date_evidence") or ""
            ).strip()
            result = ManualProvider().qualify(
                record,
                critical_date=critical_date,
                content_relevance_verified=False,
                source_chain_verified=source_chain_verified,
                public_date_verified=bool(
                    record.publication_date
                    and source_chain_verified
                    and (
                        public_date_evidence
                        or provider_name in _PATENT_PROVIDER_NAMES
                    )
                ),
                public_date_evidence=public_date_evidence or None,
                target_publication_date=(
                    patent.get("publication_date")
                    if isinstance(patent, Mapping)
                    else None
                ),
                target_publication_date_verified=bool(
                    isinstance(patent, Mapping)
                    and patent.get("publication_date")
                ),
            )
        return _completed(
            context,
            request,
            result,
            actual_provider=provider_name,
            network_used=False,
        )
    record = (
        _evidence(data.get("document"))
        if mode == "fixture"
        else _manual_fetch_record(context.settings, context, data.get("document"))
    )
    provider = ManualProvider()
    result = provider.qualify(
        record,
        critical_date=data.get("critical_date"),
        # I3 only performs deterministic date qualification.  Technical
        # disclosure is promoted after I4-S, never from a client checkbox.
        content_relevance_verified=(
            bool(data.get("content_relevance_verified", False))
            if mode == "fixture"
            else False
        ),
        source_chain_verified=(
            bool(data.get("source_chain_verified", False))
            if mode == "fixture"
            else True
        ),
        public_date_verified=bool(data.get("public_date_verified", False)),
        public_date_evidence=(
            str(data.get("public_date_evidence"))
            if data.get("public_date_evidence")
            else None
        ),
        cn_application_scope=data.get("cn_application_scope"),
        candidate_filing_date_verified=bool(
            data.get("candidate_filing_date_verified", True)
        ),
        strict=(bool(data.get("strict", False)) if mode == "fixture" else False),
    )
    return _completed(
        context,
        request,
        result,
        actual_provider="fixture" if mode == "fixture" else "manual",
        network_used=False,
    )


def _single_reference_handler(
    settings: Settings, context: JobContext
) -> Mapping[str, Any]:
    request = _request(context)
    mode = request.effective_mode
    data = request.data
    comparison_input_binding: dict[str, Any] | None = None
    if mode == "fixture":
        return _completed(context, request, _fixture_comparison())
    if mode == "live":
        document_id = str(data.get("document_id") or "").strip()
        if not document_id:
            raise PermanentJobError(
                "LAB_PERSISTED_DOCUMENT_REQUIRED",
                "live 单文献比对必须选择持久化 document_id",
            )
        document_key, stored, claim = _persisted_document(
            request, document_id=document_id
        )
        _record, record_values, provider_name = _approved_persisted_record(
            settings, stored
        )
        if not bool(record_values.get("analysis_ready")):
            reason = str(
                record_values.get("analysis_readiness_reason")
                or "全文尚未完成结构化/OCR处理"
            ).strip()
            raise PermanentJobError(
                "LAB_DOCUMENT_NOT_ANALYSIS_READY",
                f"该文献尚不能进入实体比对：{reason}",
            )
        limitations = _persisted_limitations(claim)
        claim_id = str(claim.get("claim_id") or "")
        patent_context = _persisted_patent_context(request)
        expanded_claim_text = _persisted_expanded_claim(
            request,
            claim,
            patent_context=patent_context,
        )
        target_patent_text = target_patent_text_for_analysis(patent_context)
        document_text = _record_text(record_values)
        if not document_text:
            raise PermanentJobError(
                "LAB_DOCUMENT_NOT_ANALYSIS_READY",
                "该文献没有通过可读全文门槛，禁止用标题、摘要或题录代替全文比对",
            )
        # I4-S receives the complete readable text as its evidence source.  Keep
        # the visual context bounded so an old scanned patent cannot turn one
        # comparison into a multi-minute image upload/inference request.  This
        # is also the audited image budget used by structural reconciliation.
        document_images = _record_images(record_values)[:2]
        target_images = _target_images(request)[:1]
        document_id = document_key
        document_title = str(record_values.get("title") or "").strip()
        publication_number = str(
            record_values.get("publication_number") or ""
        ).strip()
        comparison_input_binding = {
            "target_source_sha256": str(
                patent_context.get("source_sha256") or ""
            ).strip(),
            "claim_id": claim_id,
            "claim_investigation_id": str(
                claim.get("claim_investigation_id") or ""
            ).strip(),
            "expanded_claim_sha256": hashlib.sha256(
                expanded_claim_text.encode("utf-8")
            ).hexdigest(),
            "limitation_set_sha256": _lab_limitation_set_sha256(limitations),
            "document_id": str(
                stored.get("repository_document_id") or document_key
            ).strip(),
            "document_version_id": str(
                stored.get("repository_document_version_id") or ""
            ).strip()
            or None,
            "document_content_sha256": str(
                record_values.get("content_sha256") or ""
            ).strip()
            or None,
            "prompt_version": I4S_PROMPT_VERSION,
            "analysis_rule_version": I4S_RULE_VERSION,
        }
    else:
        limitations = _limitations(data.get("limitations") or [])
        claim_id = str(data.get("claim_id") or "")
        expanded_claim_text = str(data.get("expanded_claim_text") or "")
        target_patent_text = str(data.get("target_patent_text") or "").strip()
        if not target_patent_text:
            target_patent_text = target_patent_text_for_analysis(
                data.get("patent_context")
            )
        if not target_patent_text:
            raise PermanentJobError(
                "LAB_TARGET_PATENT_TEXT_REQUIRED",
                "manual 单文献比对必须提供目标专利全文或完整 patent_context",
            )
        document_id = str(data.get("document_id") or "")
        document_text = str(data.get("document_text") or "")
        target_images = [
            str(item) for item in data.get("target_images") or []
        ][:1]
        document_images = [
            str(item) for item in data.get("document_images") or []
        ][:2]
        provider_name = "manual"
        document_title = str(data.get("document_title") or "").strip()
        publication_number = str(data.get("publication_number") or "").strip()
    result = _engine(settings).compare_single_reference(
        claim_id=claim_id,
        expanded_claim_text=expanded_claim_text,
        target_patent_text=target_patent_text,
        limitations=limitations,
        document_id=document_id,
        document_text=document_text,
        target_images=target_images,
        document_images=document_images,
    )
    result = result.model_copy(
        update={
            "document_title": document_title,
            "publication_number": publication_number,
        }
    )
    output = result.model_dump(mode="json")
    if comparison_input_binding is not None:
        output["_input_binding"] = comparison_input_binding
        output["_i4s_module_run_id"] = str(
            context.module_run.get("id") or ""
        ).strip()
        output["_module6_batch_id"] = str(
            data.get("module6_batch_id") or ""
        ).strip()
    return _completed(
        context,
        request,
        output,
        actual_provider=provider_name,
        model=result.model,
        network_used=True,
        used_target_images=len(target_images),
        used_document_images=len(document_images),
    )


def _closest_handler(context: JobContext) -> Mapping[str, Any]:
    request = _request(context)
    mode = request.effective_mode
    data = request.data
    if mode == "fixture":
        limitations = _fixture_limitations()
        comparisons = [_fixture_comparison()]
        qualifications: Mapping[str, Any] = {
            "D1": {"inventive_step_eligible": True}
        }
        actual_provider = "fixture"
    elif mode == "live":
        module6_batch_id = str(data.get("module6_batch_id") or "").strip()
        if not module6_batch_id:
            raise PermanentJobError(
                "LAB_MODULE6_BATCH_REQUIRED",
                "模块7必须绑定刚完成的模块6批次，禁止读取历史工作流或其他批次的单篇比对",
            )
        claim = _workflow_claim(request)
        limitations = _persisted_limitations(claim)
        comparisons = _persisted_comparisons(claim)
        qualifications = claim.get("date_qualifications") or {}
        if not isinstance(qualifications, Mapping):
            raise PermanentJobError(
                "LAB_PERSISTED_QUALIFICATION_REQUIRED",
                "live D1 选择缺少持久化日期资格",
            )
        actual_provider = "persisted_workflow"
    else:
        limitations = _limitations(data.get("limitations") or [])
        comparisons = _comparisons(data.get("comparisons") or [])
        qualifications = data.get("date_qualifications") or {}
        if not isinstance(qualifications, Mapping):
            raise ValueError("date_qualifications 必须是对象")
        actual_provider = "manual"
    result = select_closest_prior_art(
        limitations=limitations,
        comparisons=comparisons,
        date_qualifications=qualifications,
    )
    comparison_by_id = {item.document_id: item for item in comparisons}
    d1_comparison = comparison_by_id[result.document_id]
    differences = build_distinguishing_features(
        limitations=limitations,
        d1_comparison=d1_comparison,
    )
    large_chart = select_large_claim_chart_documents(
        limitations=limitations,
        closest_document_id=result.document_id,
        comparisons=comparisons,
        date_qualifications=qualifications,
        difference_feature_ids=[item.feature_id for item in differences],
    )
    return _completed(
        context,
        request,
        {
            **result.model_dump(mode="json"),
            "distinguishing_features": [
                item.model_dump(mode="json") for item in differences
            ],
            "large_claim_chart_document_ids": [
                item.document_id for item in large_chart
            ],
            **(
                {
                    "source_module6_batch_id": module6_batch_id,
                    "source_i4s_module_run_ids": [
                        str(
                            dict(claim.get("comparisons") or {})
                            .get(item.document_id, {})
                            .get("_i4s_module_run_id")
                            or ""
                        )
                        for item in comparisons
                    ],
                    "source_document_ids": [
                        item.document_id for item in comparisons
                    ],
                    "rejected_comparisons": list(
                        claim.get("rejected_lab_comparisons") or []
                    ),
                }
                if mode == "live"
                else {}
            ),
        },
        actual_provider=actual_provider,
        network_used=False,
    )


def _fixture_obviousness_precheck() -> ObviousnessPrecheckAssessment:
    return ObviousnessPrecheckAssessment.model_validate(
        {
            "closest_document_id": "D1",
            "feature_groups": [
                {
                    "feature_group_id": "G01",
                    "feature_ids": ["F2"],
                    "feature_texts": ["filter positioned before the sensor"],
                    "objective_technical_problem": "reduce stray light before sensing",
                    "d1_teaching": {
                        "status": "not_supported",
                        "reasoning": "fixture D1 does not state this direction",
                        "confidence": 0.9,
                    },
                    "routine_means": {
                        "status": "not_supported",
                        "reasoning": "fixture has no common-knowledge evidence",
                        "confidence": 0.9,
                    },
                    "modification_motivation": {
                        "status": "uncertain",
                        "reasoning": "requires follow-up evidence",
                        "confidence": 0.6,
                    },
                    "teaching_away": {
                        "status": "absent",
                        "reasoning": "no contrary teaching in fixture D1",
                        "confidence": 0.8,
                    },
                    "technical_effect": {
                        "status": "predictable",
                        "reasoning": "the claimed effect follows from the arrangement",
                        "confidence": 0.8,
                    },
                    "search_route": "search_direct_feature_evidence",
                    "ordinary_structural_search_required": True,
                    "reasoning": "D1 does not provide the modification direction",
                }
            ],
            "resolved_feature_ids": [],
            "unresolved_feature_ids": ["F2"],
            "search_targets": [
                {
                    "feature_group_id": "G01",
                    "feature_ids": ["F2"],
                    "route": "search_direct_feature_evidence",
                    "target_gap_type": "feature_gap",
                    "search_anchor": "filter positioned before sensor",
                    "rationale": "D1 does not provide the modification direction",
                }
            ],
            "ordinary_structural_search_required": True,
            "evidence_complete": False,
            "model": "fixture-no-model",
            "used_target_images": 1,
            "used_document_images": 1,
        }
    )


def _obviousness_precheck_handler(
    settings: Settings, context: JobContext
) -> Mapping[str, Any]:
    request = _request(context)
    mode = request.effective_mode
    data = request.data
    if mode == "fixture":
        return _completed(context, request, _fixture_obviousness_precheck())
    if mode == "live":
        module6_batch_id = str(data.get("module6_batch_id") or "").strip()
        if not module6_batch_id:
            raise PermanentJobError(
                "LAB_MODULE6_BATCH_REQUIRED",
                "模块8必须绑定本次模块6批次，禁止读取历史比对",
            )
        claim = _workflow_claim(request)
        limitations = _persisted_limitations(claim)
        comparisons = _persisted_comparisons(claim)
        _closest_run, closest = _required_live_lineage_run(
            request,
            claim=claim,
            input_key="closest_prior_art_run_id",
            module_code="I4_C_CLOSEST_PRIOR_ART",
            public_label="模块8",
        )
        if str(closest.get("source_module6_batch_id") or "").strip() != (
            module6_batch_id
        ):
            raise PermanentJobError(
                "LAB_CURRENT_LINEAGE_MISMATCH",
                "模块8绑定的模块7结果不属于本次模块6批次",
            )
        d1_document_id = str(closest.get("document_id") or "").strip()
        if not d1_document_id:
            raise PermanentJobError(
                "LAB_PERSISTED_D1_REQUIRED",
                "live 显而易见性预分析需要先完成 D1 选择",
            )
        d1_comparison = next(
            (item for item in comparisons if item.document_id == d1_document_id),
            None,
        )
        if d1_comparison is None:
            raise PermanentJobError(
                "LAB_PERSISTED_D1_REQUIRED", "D1 不在当前 I4-S 比对集合中"
            )
        differences = build_distinguishing_features(
            limitations=limitations, d1_comparison=d1_comparison
        )
        _key, stored, _claim = _persisted_document(
            request, document_id=d1_document_id
        )
        _record, values, provider = _approved_persisted_record(settings, stored)
        document_text = _record_text(values)
        document_images = _record_images(values)
        expanded_claim_text = _persisted_expanded_claim(request, claim)
        target_images = _target_images(request)
        actual_provider = provider
    else:
        limitations = _limitations(data.get("limitations") or [])
        comparisons = _comparisons(data.get("comparisons") or [])
        d1_document_id = str(data.get("closest_document_id") or "").strip()
        d1_comparison = next(
            (item for item in comparisons if item.document_id == d1_document_id),
            None,
        )
        if d1_comparison is None:
            raise ValueError("closest_document_id 不在 comparisons 中")
        raw_differences = data.get("distinguishing_features")
        differences = (
            [DistinguishingFeature.model_validate(item) for item in raw_differences]
            if isinstance(raw_differences, list) and raw_differences
            else build_distinguishing_features(
                limitations=limitations, d1_comparison=d1_comparison
            )
        )
        document_text = str(data.get("document_text") or "")
        document_images = [str(item) for item in data.get("document_images") or []]
        expanded_claim_text = str(data.get("expanded_claim_text") or "")
        target_images = [str(item) for item in data.get("target_images") or []]
        actual_provider = "manual"
    result = _engine(settings).analyze_obviousness_precheck(
        differences=differences,
        closest_document_id=d1_document_id,
        expanded_claim_text=expanded_claim_text,
        target_images=target_images,
        document_text=document_text,
        document_images=document_images,
    )
    output = result.model_dump(mode="json")
    if mode == "live":
        output.update(
            {
                "module_run_id": str(context.module_run.get("id") or "").strip(),
                "source_module6_batch_id": module6_batch_id,
                "source_closest_prior_art_run_id": str(
                    data.get("closest_prior_art_run_id") or ""
                ).strip(),
            }
        )
    return _completed(
        context,
        request,
        output,
        actual_provider=actual_provider,
        model=result.model,
        network_used=True,
        used_target_images=len(target_images),
        used_document_images=len(document_images),
    )


def _gap_query_plan_handler(
    settings: Settings, context: JobContext
) -> Mapping[str, Any]:
    """Freeze D1 differences, reuse eligible I4-S evidence, and own gap queries."""

    request = _request(context)
    mode = request.effective_mode
    data = request.data
    if mode == "fixture":
        limitations = _fixture_limitations()
        comparisons = [_fixture_comparison()]
        qualifications: Mapping[str, Any] = {
            "D1": {"inventive_step_eligible": True}
        }
        d1_document_id = "D1"
        reuse_keys: Mapping[str, Any] = {}
        precheck = _fixture_obviousness_precheck()
        actual_provider = "fixture"
    elif mode == "live":
        module6_batch_id = str(data.get("module6_batch_id") or "").strip()
        if not module6_batch_id:
            raise PermanentJobError(
                "LAB_MODULE6_BATCH_REQUIRED",
                "模块9必须绑定本次模块6批次，禁止读取历史比对",
            )
        claim = _workflow_claim(request)
        limitations = _persisted_limitations(claim)
        comparisons = _persisted_comparisons(claim)
        qualifications = claim.get("date_qualifications") or {}
        if not isinstance(qualifications, Mapping):
            raise PermanentJobError(
                "LAB_PERSISTED_QUALIFICATION_REQUIRED",
                "live 差异检索计划缺少持久化日期资格",
            )
        _closest_run, closest = _required_live_lineage_run(
            request,
            claim=claim,
            input_key="closest_prior_art_run_id",
            module_code="I4_C_CLOSEST_PRIOR_ART",
            public_label="模块9",
        )
        if str(closest.get("source_module6_batch_id") or "").strip() != (
            module6_batch_id
        ):
            raise PermanentJobError(
                "LAB_CURRENT_LINEAGE_MISMATCH",
                "模块9绑定的模块7结果不属于本次模块6批次",
            )
        d1_document_id = str(closest.get("document_id") or "").strip()
        if not d1_document_id:
            raise PermanentJobError(
                "LAB_PERSISTED_D1_REQUIRED",
                "live 差异检索计划需要先完成 D1 选择",
            )
        reuse_keys = _current_module6_reuse_keys(
            claim,
            comparisons=comparisons,
            module6_batch_id=module6_batch_id,
        )
        _precheck_run, precheck_payload = _required_live_lineage_run(
            request,
            claim=claim,
            input_key="obviousness_precheck_run_id",
            module_code="I4_O_OBVIOUSNESS_PRECHECK",
            public_label="模块9",
        )
        if (
            str(precheck_payload.get("source_module6_batch_id") or "").strip()
            != module6_batch_id
            or str(
                precheck_payload.get("source_closest_prior_art_run_id") or ""
            ).strip()
            != str(data.get("closest_prior_art_run_id") or "").strip()
        ):
            raise PermanentJobError(
                "LAB_CURRENT_LINEAGE_MISMATCH",
                "模块9绑定的模块8结果不属于本次模块6/模块7谱系",
            )
        precheck = ObviousnessPrecheckAssessment.model_validate(precheck_payload)
        actual_provider = "persisted_workflow"
    else:
        limitations = _limitations(data.get("limitations") or [])
        comparisons = _comparisons(data.get("comparisons") or [])
        qualifications = data.get("date_qualifications") or {}
        if not isinstance(qualifications, Mapping):
            raise ValueError("date_qualifications 必须是对象")
        d1_document_id = str(data.get("d1_document_id") or "").strip()
        reuse_keys = data.get("reuse_keys") or {}
        if not isinstance(reuse_keys, Mapping):
            raise ValueError("reuse_keys 必须是对象")
        precheck_value = data.get("obviousness_precheck")
        if not isinstance(precheck_value, Mapping):
            raise ValueError("obviousness_precheck 必须是模块 I4-O 的输出对象")
        precheck = ObviousnessPrecheckAssessment.model_validate(precheck_value)
        actual_provider = "manual"
    comparison_by_id = {item.document_id: item for item in comparisons}
    d1_comparison = comparison_by_id.get(d1_document_id)
    if d1_comparison is None:
        raise PermanentJobError(
            "LAB_PERSISTED_D1_REQUIRED",
            "D1 不在当前版本的 I4-S 比对集合中",
        )
    profile_value = data.get("invention_search_profile")
    profile = (
        InventionSearchProfile.model_validate(profile_value)
        if isinstance(profile_value, Mapping)
        else None
    )
    if profile is None and mode == "live":
        # Durable Module 9 requests intentionally carry only cursors, not a
        # browser-supplied profile.  Reuse the newest successful same-claim I2
        # vocabulary/profile as frozen search input, especially for legacy
        # limitations whose persisted rows predate bilingual synonyms.  This
        # is not evidence reuse and cannot close a feature; it only supplies
        # target-derived query language to the current I2-G compiler.
        known_feature_ids = {item.feature_id for item in limitations}
        for row in _successful_live_lab_runs(request, claim=claim):
            if str(row.get("module_code") or "") not in {
                "I2_GAP_QUERY_PLAN",
                "I2_QUERY_PLAN",
                "I2_INVENTIVE_PROFILE",
            }:
                continue
            prior_output = _lab_output(row)
            prior_profile_value = (
                prior_output.get("invention_search_profile")
                if isinstance(prior_output, Mapping)
                else None
            )
            if not isinstance(prior_profile_value, Mapping):
                continue
            try:
                candidate_profile = InventionSearchProfile.model_validate(
                    prior_profile_value
                )
            except Exception:
                continue
            bound_feature_ids = {
                feature_id
                for concept in (
                    *candidate_profile.common_context_features,
                    *candidate_profile.inventive_point_features,
                )
                for feature_id in concept.feature_ids
            }
            if bound_feature_ids.intersection(known_feature_ids):
                profile = candidate_profile
                break
    differences = build_distinguishing_features(
        limitations=limitations,
        d1_comparison=d1_comparison,
        invention_search_profile=profile,
    )
    decision = evaluate_existing_corpus_gap_coverage(
        differences=differences,
        comparisons=comparisons,
        date_qualifications=qualifications,
        reuse_keys=reuse_keys,
        gap_search_iteration=int(data.get("gap_search_iteration") or 1),
        iteration_completed=bool(data.get("iteration_completed", False)),
        previous_iteration_failure_reason=str(
            data.get("previous_iteration_failure_reason") or ""
        ),
    )
    if precheck.closest_document_id != d1_document_id:
        raise PermanentJobError(
            "LAB_OBVIOUSNESS_PRECHECK_D1_MISMATCH",
            "显而易见性预分析与当前 D1 版本不一致，必须重算 I4-O",
        )
    route_by_feature = {
        feature_id: target.route
        for target in precheck.search_targets
        for feature_id in target.feature_ids
    }
    (
        decision,
        allowed_gap_feature_ids,
        human_review_feature_ids,
    ) = apply_obviousness_routes_to_gap_decision(
        decision=decision,
        precheck=precheck,
    )
    requested_gap_feature_ids = _dedupe_strings(
        str(item)
        for item in data.get("gap_feature_ids") or []
        if str(item).strip()
    )
    if requested_gap_feature_ids:
        requested_gap_set = set(requested_gap_feature_ids)
        # A durable Module-9 cursor may only shrink as citable evidence closes
        # distinguishing features.  Re-fetching or re-analysing the frozen D1
        # must not reopen an already-resolved feature merely because a newer
        # I4-S row for the same document is less certain.
        allowed_gap_feature_ids = [
            feature_id
            for feature_id in allowed_gap_feature_ids
            if feature_id in requested_gap_set
        ]
        human_review_feature_ids = [
            feature_id
            for feature_id in human_review_feature_ids
            if feature_id in requested_gap_set
        ]
        covered_requested = [
            feature_id
            for feature_id in decision.covered_difference_feature_ids
            if feature_id in requested_gap_set
        ]
        if not allowed_gap_feature_ids:
            stop_reason = "all_differences_covered"
        elif decision.gap_search_exhausted:
            stop_reason = "gap_search_exhausted"
        else:
            stop_reason = "search_remaining_differences"
        decision = decision.model_copy(
            update={
                "covered_difference_feature_ids": covered_requested,
                "uncovered_difference_feature_ids": allowed_gap_feature_ids,
                "search_required": bool(allowed_gap_feature_ids),
                "stop_reason": stop_reason,
            }
        )
    difference_payload = [item.model_dump(mode="json") for item in differences]
    decision_payload = decision.model_dump(mode="json")
    iteration_number = decision.gap_search_iteration + 1
    gap_strategy = gap_search_strategy_metadata(decision.gap_search_iteration)
    reuse_evidence = [
        item.model_copy(
            update={
                "source_module6_batch_id": (
                    module6_batch_id if mode == "live" else ""
                )
            }
        ).model_dump(mode="json")
        for item in decision.coverage_evidence
    ]
    decision_payload["coverage_evidence"] = reuse_evidence
    audit_artifact = None
    network_used = False
    if mode == "fixture":
        base_plan = _fixture_query_plan()
        feature_ids = list(allowed_gap_feature_ids)
        limitation_by_id = {item.feature_id: item for item in limitations}
        queries = []
        if decision.search_required:
            for sequence, feature_id in enumerate(feature_ids, start=1):
                feature_text = limitation_by_id[feature_id].text
                expression = " AND ".join(
                    [base_plan.technical_subject, feature_text]
                )
                route = route_by_feature.get(feature_id)
                queries.append(ContractSearchQuery(
                    query_id=(
                        f"fixture-gap-q{decision.gap_search_iteration}-{sequence}"
                    ),
                    provider_kind="patent",
                    purpose="feature_uncovered",
                    technical_subject=base_plan.technical_subject,
                    feature_ids=[feature_id],
                    expression=expression,
                    language="zh",
                    query_role=QueryRole.GAP_FOLLOWUP,
                    query_variant=QueryVariant.GAP_FOLLOWUP,
                    search_scope=SearchScope.FULL_TEXT,
                    scope_reason="固定夹具只检索尚未覆盖的 D1 区别特征",
                    subject_terms=[base_plan.technical_subject],
                    feature_terms=[feature_text],
                    rationale="固定夹具区别特征检索线",
                    search_objective=SearchObjective.GAP_OR_COMBINATION,
                    date_channel=DateChannel.ORDINARY_PRIOR_ART,
                    target_gap_type=(
                        GapType.COMBINATION
                        if route == "search_combination_evidence"
                        else (
                            GapType.EVIDENCE
                            if route == "search_common_knowledge_evidence"
                            else GapType.FEATURE
                        )
                    ),
                    gap_feature_ids=[feature_id],
                    anchor_document_id=d1_document_id,
                    gap_search_iteration=decision.gap_search_iteration,
                    gap_search_strategy=gap_strategy["code"],
                    gap_search_strategy_label=gap_strategy["label"],
                    covered_difference_feature_ids=(
                        decision.covered_difference_feature_ids
                    ),
                    uncovered_difference_feature_ids=feature_ids,
                    existing_corpus_reuse=reuse_evidence,
                    previous_iteration_failure_reason=(
                        decision.previous_iteration_failure_reason
                    ),
                ))
        plan = base_plan.model_copy(
            update={
                "iteration_number": iteration_number,
                "round_kind": "gap",
                "queries": queries,
                "rejected_queries": [],
                "gap_search_iteration": decision.gap_search_iteration,
                "gap_search_strategy": gap_strategy["code"],
                "gap_search_strategy_label": gap_strategy["label"],
                "gap_search_strategy_description": gap_strategy[
                    "description"
                ],
                "covered_difference_feature_ids": (
                    decision.covered_difference_feature_ids
                ),
                "uncovered_difference_feature_ids": (
                    decision.uncovered_difference_feature_ids
                ),
                "existing_corpus_reuse": reuse_evidence,
                "previous_iteration_failure_reason": (
                    decision.previous_iteration_failure_reason
                ),
            }
        )
    elif not decision.search_required:
        profile_value = data.get("invention_search_profile")
        plan = QueryPlan(
            claim_id=str(data.get("claim_id") or limitations[0].claim_id),
            iteration_number=iteration_number,
            round_kind="gap",
            technical_subject=limitations[0].technical_subject,
            invention_search_profile=(
                InventionSearchProfile.model_validate(profile_value)
                if isinstance(profile_value, Mapping)
                else None
            ),
            limitations=limitations,
            queries=[],
            model="deterministic-obviousness-precheck",
            used_target_images=1,
            generation_source=(
                "obviousness_precheck_resolved"
                if precheck.resolved_feature_ids or human_review_feature_ids
                else "existing_corpus_reuse"
            ),
            gap_search_iteration=decision.gap_search_iteration,
            gap_search_strategy=gap_strategy["code"],
            gap_search_strategy_label=gap_strategy["label"],
            gap_search_strategy_description=gap_strategy["description"],
            covered_difference_feature_ids=(
                decision.covered_difference_feature_ids
            ),
            uncovered_difference_feature_ids=(
                decision.uncovered_difference_feature_ids
            ),
            existing_corpus_reuse=reuse_evidence,
            previous_iteration_failure_reason=(
                decision.previous_iteration_failure_reason
            ),
        )
    else:
        patent_context = (
            _persisted_patent_context(request)
            if mode == "live"
            else data.get("patent_context")
        )
        if not isinstance(patent_context, Mapping):
            raise PermanentJobError(
                "LAB_TARGET_SNAPSHOT_MISSING",
                "区别特征检索计划缺少冻结目标专利快照",
            )
        claim_id = str(
            data.get("claim_id")
            or (claim.get("claim_id") if mode == "live" else "")
            or limitations[0].claim_id
        ).strip()
        expanded_claim_text = str(
            data.get("expanded_claim_text") or ""
        ).strip()
        if not expanded_claim_text:
            for raw_claim in patent_context.get("claims") or []:
                if not isinstance(raw_claim, Mapping):
                    continue
                if str(raw_claim.get("claim_id") or "") != claim_id:
                    continue
                expanded_claim_text = str(
                    raw_claim.get("expanded_claim_text")
                    or raw_claim.get("claim_text")
                    or ""
                ).strip()
                break
        if not expanded_claim_text:
            raise PermanentJobError(
                "LAB_PERSISTED_CLAIM_REQUIRED",
                "区别特征检索计划缺少当前独立权利要求全文",
            )
        target_images = _target_images(request)
        previous_query_expressions = [
            str(item)
            for item in data.get("previous_query_expressions") or []
            if str(item).strip()
        ]
        engine = _engine(settings)
        force_deterministic = bool(data.get("force_deterministic_gap_plan", False))
        try:
            if force_deterministic:
                plan = engine.deterministic_gap_query_plan(
                    claim_id=claim_id,
                    iteration_number=iteration_number,
                    existing_limitations=limitations,
                    gap_feature_ids=allowed_gap_feature_ids,
                    gap_types=_dedupe_strings(
                        "combination_gap"
                        if route_by_feature.get(feature_id)
                        == "search_combination_evidence"
                        else (
                            "evidence_gap"
                            if route_by_feature.get(feature_id)
                            == "search_common_knowledge_evidence"
                            else "feature_gap"
                        )
                        for feature_id in allowed_gap_feature_ids
                    ),
                    invention_search_profile=profile,
                    closest_prior_art_context={"document_id": d1_document_id},
                    covered_difference_feature_ids=(
                        decision.covered_difference_feature_ids
                    ),
                    existing_corpus_reuse=reuse_evidence,
                    previous_iteration_failure_reason=(
                        decision.previous_iteration_failure_reason
                    ),
                    previous_query_expressions=previous_query_expressions,
                    max_queries=int(
                        data.get("max_queries") or I2_DEFAULT_MAX_QUERIES
                    ),
                    warning=(
                        "前三次模型计划均未完成；第 4 次仅依据冻结事实"
                        "确定性编译，不再调用模型。"
                    ),
                )
            else:
                plan = engine.plan_queries(
                    claim_id=claim_id,
                    expanded_claim_text=expanded_claim_text,
                    target_images=target_images,
                    patent_context=patent_context,
                    iteration_number=iteration_number,
                    round_kind="gap",
                    existing_limitations=limitations,
                    invention_search_profile=profile,
                    gap_feature_ids=allowed_gap_feature_ids,
                    gap_types=_dedupe_strings(
                        "combination_gap"
                        if route_by_feature.get(feature_id)
                        == "search_combination_evidence"
                        else (
                            "evidence_gap"
                            if route_by_feature.get(feature_id)
                            == "search_common_knowledge_evidence"
                            else "feature_gap"
                        )
                        for feature_id in allowed_gap_feature_ids
                    ),
                    closest_prior_art_context={
                        "document_id": d1_document_id,
                        "distinguishing_features": difference_payload,
                        "gap_reuse_decision": decision_payload,
                        "obviousness_precheck": precheck.model_dump(mode="json"),
                    },
                    covered_difference_feature_ids=(
                        decision.covered_difference_feature_ids
                    ),
                    existing_corpus_reuse=reuse_evidence,
                    previous_iteration_failure_reason=(
                        decision.previous_iteration_failure_reason
                    ),
                    previous_query_expressions=previous_query_expressions,
                    max_queries=int(
                        data.get("max_queries") or I2_DEFAULT_MAX_QUERIES
                    ),
                )
        except MultimodalModelError as exc:
            plan = engine.deterministic_gap_query_plan(
                claim_id=claim_id,
                iteration_number=iteration_number,
                existing_limitations=limitations,
                gap_feature_ids=allowed_gap_feature_ids,
                gap_types=_dedupe_strings(
                    "combination_gap"
                    if route_by_feature.get(feature_id)
                    == "search_combination_evidence"
                    else (
                        "evidence_gap"
                        if route_by_feature.get(feature_id)
                        == "search_common_knowledge_evidence"
                        else "feature_gap"
                    )
                    for feature_id in allowed_gap_feature_ids
                ),
                invention_search_profile=profile,
                closest_prior_art_context={"document_id": d1_document_id},
                covered_difference_feature_ids=(
                    decision.covered_difference_feature_ids
                ),
                existing_corpus_reuse=reuse_evidence,
                previous_iteration_failure_reason=(
                    decision.previous_iteration_failure_reason
                ),
                previous_query_expressions=previous_query_expressions,
                max_queries=int(data.get("max_queries") or I2_DEFAULT_MAX_QUERIES),
                warning=(
                    f"模型请求失败（{exc.reason_code or 'MODEL_REQUEST_FAILED'}）；"
                    "已仅依据冻结事实确定性编译本轮 gap 查询。"
                ),
            )
        except Exception:
            _persist_lab_i2_model_response(settings, context, engine)
            raise
        audit_artifact = _persist_lab_i2_model_response(
            settings, context, engine
        )
        network_used = not force_deterministic
    plan = bind_gap_search_strategy(
        plan,
        gap_search_iteration=decision.gap_search_iteration,
    )
    return _completed(
        context,
        request,
        {
            **plan.model_dump(mode="json"),
            "closest_document_id": d1_document_id,
            **(
                {
                    "source_module6_batch_id": module6_batch_id,
                    "source_closest_prior_art_run_id": str(
                        data.get("closest_prior_art_run_id") or ""
                    ).strip(),
                    "source_obviousness_precheck_run_id": str(
                        data.get("obviousness_precheck_run_id") or ""
                    ).strip(),
                }
                if mode == "live"
                else {}
            ),
            "distinguishing_features": difference_payload,
            "gap_reuse_decision": decision_payload,
            "obviousness_precheck": precheck.model_dump(mode="json"),
            "covered_difference_feature_ids": (
                decision.covered_difference_feature_ids
            ),
            "uncovered_difference_feature_ids": (
                decision.uncovered_difference_feature_ids
            ),
            "allowed_gap_feature_ids": allowed_gap_feature_ids,
            "human_review_feature_ids": human_review_feature_ids,
            "gap_query_matrix_id": str(
                data.get("gap_query_matrix_id") or ""
            ).strip()
            or None,
            "gap_query_matrix_version": int(
                data.get("gap_query_matrix_version") or 0
            )
            or None,
            "model_response_audit": audit_artifact,
        },
        actual_provider=actual_provider,
        model=plan.model,
        network_used=network_used,
        used_target_images=plan.used_target_images,
    )


def _fixture_inventive_step() -> InventiveStepAssessment:
    return InventiveStepAssessment.model_validate(
        {
            "closest_document_id": "D1",
            "considered_document_ids": ["D1", "D2"],
            "combination_document_ids": ["D1", "D2"],
            "feature_coverage": [
                {"feature_id": "F1", "disclosed_by_document_ids": ["D1"], "covered": True},
                {"feature_id": "F2", "disclosed_by_document_ids": ["D2"], "covered": True},
            ],
            "distinguishing_feature_analysis": [
                {
                    "feature_id": "F2",
                    "feature_text": "filter positioned before the sensor",
                    "d1_disclosure_status": "not_disclosed",
                    "objective_technical_problem": "reduce stray light before sensing",
                    "supporting_document_ids": ["D2"],
                    "disclosure_complete": True,
                    "same_role_and_effect": {
                        "status": "supported",
                        "evidence_quote": "place the filter before the sensor",
                        "evidence_location": "D2 paragraph 12",
                        "reasoning": "same filtering role",
                        "confidence": 0.9,
                    },
                    "technical_teaching": {
                        "status": "supported",
                        "evidence_quote": "place the filter before the sensor",
                        "evidence_location": "D2 paragraph 12",
                        "reasoning": "explicit placement teaching",
                        "confidence": 0.9,
                    },
                    "modification_motivation": {
                        "status": "supported",
                        "evidence_quote": "reduces stray light",
                        "evidence_location": "D2 paragraph 13",
                        "reasoning": "reduce stray light",
                        "confidence": 0.9,
                    },
                    "modification_path": "place D2's filter before D1's sensor",
                    "teaching_away": {
                        "status": "absent",
                        "reasoning": "no contrary teaching",
                        "confidence": 0.9,
                    },
                    "technical_effect": {
                        "status": "predictable",
                        "evidence_quote": "reduces stray light",
                        "evidence_location": "D2 paragraph 13",
                        "reasoning": "stated effect follows from placement",
                        "confidence": 0.9,
                    },
                    "evidence_chain_complete": True,
                    "unresolved_reasons": [],
                }
            ],
            "combined_feature_coverage_complete": True,
            "all_documents_date_eligible": True,
            "related_technical_problem": {
                "status": "same_or_related",
                "evidence_quote": "same sensing problem",
                "evidence_location": "D2 paragraph 8",
                "reasoning": "fixed fixture",
                "confidence": 0.9,
            },
            "combination_motivation": {
                "status": "supported",
                "evidence_quote": "place the filter before the sensor",
                "evidence_location": "D2 paragraph 12",
                "reasoning": "fixed fixture",
                "confidence": 0.9,
            },
            "teaching_away": {
                "status": "absent",
                "reasoning": "fixed fixture contains no contrary teaching",
                "confidence": 0.9,
            },
            "technical_effect": {
                "status": "predictable",
                "evidence_quote": "reduces stray light",
                "evidence_location": "D2 paragraph 13",
                "reasoning": "fixed fixture",
                "confidence": 0.9,
            },
            "evidence_complete": True,
            "gaps": [],
            "conclusion": "lack_of_inventive_step_evidence_complete",
            "conclusion_text": "现有证据已形成缺乏创造性的完整证据链（供律师复核）",
            "model": "fixture-no-model",
            "used_target_images": 1,
            "used_document_images": 2,
        }
    )


def _inventive_step_handler(
    settings: Settings, context: JobContext
) -> Mapping[str, Any]:
    request = _request(context)
    mode = request.effective_mode
    data = request.data
    if mode == "fixture":
        return _completed(context, request, _fixture_inventive_step())
    if mode == "live":
        module6_batch_id = str(data.get("module6_batch_id") or "").strip()
        module9_batch_id = str(data.get("module9_batch_id") or "").strip()
        if not module6_batch_id or not module9_batch_id:
            raise PermanentJobError(
                "LAB_CURRENT_LINEAGE_REQUIRED",
                "模块10必须绑定本次模块6批次和已经收口的模块9批次",
            )
        upstream_gap_search_errors = [
            dict(item)
            for item in data.get("module9_failure_ledger", [])
            if isinstance(item, Mapping)
        ]
        claim = _workflow_claim(request)
        limitations = _persisted_limitations(claim)
        comparisons = _persisted_comparisons(claim)
        date_qualifications = claim.get("date_qualifications") or {}
        if not isinstance(date_qualifications, Mapping):
            raise PermanentJobError(
                "LAB_PERSISTED_QUALIFICATION_REQUIRED",
                "live 创造性分析缺少持久化日期资格",
            )
        _closest_run, closest = _required_live_lineage_run(
            request,
            claim=claim,
            input_key="closest_prior_art_run_id",
            module_code="I4_C_CLOSEST_PRIOR_ART",
            public_label="模块10",
        )
        if str(closest.get("source_module6_batch_id") or "").strip() != (
            module6_batch_id
        ):
            raise PermanentJobError(
                "LAB_CURRENT_LINEAGE_MISMATCH",
                "模块10绑定的模块7结果不属于本次模块6批次",
            )
        closest_document_id = str(closest.get("document_id") or "").strip()
        if not closest_document_id:
            raise PermanentJobError(
                "LAB_PERSISTED_D1_REQUIRED",
                "live 创造性分析缺少持久化 D1",
            )
        raw_differences = (
            closest.get("distinguishing_features")
            if isinstance(closest, Mapping)
            else None
        )
        distinguishing_features = [
            DistinguishingFeature.model_validate(item)
            for item in (
                raw_differences
                if isinstance(raw_differences, Sequence)
                and not isinstance(raw_differences, (str, bytes))
                else []
            )
            if isinstance(item, Mapping)
        ]
        if not distinguishing_features:
            d1_comparison = next(
                (
                    item
                    for item in comparisons
                    if item.document_id == closest_document_id
                ),
                None,
            )
            if d1_comparison is None:
                raise PermanentJobError(
                    "LAB_PERSISTED_D1_REQUIRED",
                    "live 创造性分析的 D1 不在当前 I4-S 语料中",
                )
            distinguishing_features = build_distinguishing_features(
                limitations=limitations,
                d1_comparison=d1_comparison,
            )
        _precheck_run, precheck_output = _required_live_lineage_run(
            request,
            claim=claim,
            input_key="obviousness_precheck_run_id",
            module_code="I4_O_OBVIOUSNESS_PRECHECK",
            public_label="模块10",
        )
        if (
            str(precheck_output.get("source_module6_batch_id") or "").strip()
            != module6_batch_id
            or str(
                precheck_output.get("source_closest_prior_art_run_id") or ""
            ).strip()
            != str(data.get("closest_prior_art_run_id") or "").strip()
        ):
            raise PermanentJobError(
                "LAB_CURRENT_LINEAGE_MISMATCH",
                "模块10绑定的模块8结果不属于本次模块6/模块7谱系",
            )
        obviousness_precheck = ObviousnessPrecheckAssessment.model_validate(
            precheck_output
        )
        expanded_claim_text = _persisted_expanded_claim(request, claim)
        target_images = _target_images(request)
        document_texts: dict[str, str] = {}
        document_images: dict[str, list[str]] = {}
        providers: set[str] = set()
        documents = claim.get("documents") or {}
        if not isinstance(documents, Mapping):
            raise PermanentJobError(
                "LAB_PERSISTED_DOCUMENT_REQUIRED",
                "live 创造性分析缺少持久化文献",
            )
        for comparison in comparisons:
            _key, stored, _claim = _persisted_document(
                request, document_id=comparison.document_id
            )
            _record, values, provider = _approved_persisted_record(settings, stored)
            providers.add(provider)
            document_texts[comparison.document_id] = _record_text(values)
            document_images[comparison.document_id] = _record_images(values)
        actual_provider = ",".join(sorted(providers)) or None
    else:
        document_images = data.get("document_images") or {}
        document_texts = data.get("document_texts") or {}
        date_qualifications = data.get("date_qualifications") or {}
        if not all(
            isinstance(value, Mapping)
            for value in (document_images, document_texts, date_qualifications)
        ):
            raise ValueError(
                "document_images/document_texts/date_qualifications 必须是对象"
            )
        limitations = _limitations(data.get("limitations") or [])
        comparisons = _comparisons(data.get("comparisons") or [])
        closest_document_id = str(data.get("closest_document_id") or "")
        expanded_claim_text = str(data.get("expanded_claim_text") or "")
        target_images = [str(item) for item in data.get("target_images") or []]
        document_texts = {
            str(key): str(value) for key, value in document_texts.items()
        }
        document_images = {
            str(key): list(value) if isinstance(value, Sequence) else []
            for key, value in document_images.items()
        }
        actual_provider = "manual"
        raw_precheck = data.get("obviousness_precheck")
        obviousness_precheck = (
            ObviousnessPrecheckAssessment.model_validate(raw_precheck)
            if isinstance(raw_precheck, Mapping)
            else None
        )
        raw_differences = data.get("distinguishing_features")
        distinguishing_features = [
            DistinguishingFeature.model_validate(item)
            for item in (
                raw_differences
                if isinstance(raw_differences, Sequence)
                and not isinstance(raw_differences, (str, bytes))
                else []
            )
            if isinstance(item, Mapping)
        ]
        upstream_gap_search_errors = [
            dict(item)
            for item in data.get("module9_failure_ledger", [])
            if isinstance(item, Mapping)
        ]
    result = _engine(settings).analyze_inventive_step(
        limitations=limitations,
        closest_document_id=closest_document_id,
        comparisons=comparisons,
        date_qualifications=date_qualifications,
        expanded_claim_text=expanded_claim_text,
        target_images=target_images,
        document_texts=document_texts,
        document_images=document_images,
        obviousness_precheck=obviousness_precheck,
        distinguishing_features=distinguishing_features or None,
        upstream_gap_search_errors=upstream_gap_search_errors,
    )
    available_document_images = sum(
        len(items) for items in document_images.values()
    )
    used_document_images = min(
        available_document_images,
        max(0, int(result.used_document_images)),
    )
    output = result.model_dump(mode="json")
    if mode == "live":
        output.update(
            {
                "source_module6_batch_id": module6_batch_id,
                "source_module9_batch_id": module9_batch_id,
                "source_closest_prior_art_run_id": str(
                    data.get("closest_prior_art_run_id") or ""
                ).strip(),
                "source_obviousness_precheck_run_id": str(
                    data.get("obviousness_precheck_run_id") or ""
                ).strip(),
                "source_i4s_module_run_ids": [
                    str(
                        dict(claim.get("comparisons") or {})
                        .get(item.document_id, {})
                        .get("_i4s_module_run_id")
                        or ""
                    )
                    for item in comparisons
                ],
            }
        )
    return _completed(
        context,
        request,
        output,
        actual_provider=actual_provider,
        model=result.model,
        network_used=True,
        used_target_images=len(target_images),
        used_document_images=used_document_images,
    )


def _report_handler(context: JobContext) -> Mapping[str, Any]:
    request = _request(context)
    if request.effective_mode == "fixture":
        result = _fixture_report_data()
        provider = "fixture"
        appendix = None
    else:
        result = _persisted_report_data(context, request)
        provider = "persistent_report_snapshot"
        appendix = None
        if request.effective_mode == "live":
            try:
                claim = _workflow_claim(request)
            except PermanentJobError as exc:
                if exc.error_code not in {
                    "LAB_PERSISTED_CONTEXT_REQUIRED",
                    "LAB_PERSISTED_CLAIM_REQUIRED",
                }:
                    raise
            else:
                inventive_step: Mapping[str, Any] | None = None
                obviousness_precheck: Mapping[str, Any] | None = None
                for row in _successful_live_lab_runs(request, claim=claim):
                    module_code = str(row.get("module_code") or "")
                    if module_code == "I4_I_INVENTIVE_STEP" and inventive_step is None:
                        inventive_step = _lab_output(row)
                    if (
                        module_code == "I4_O_OBVIOUSNESS_PRECHECK"
                        and obviousness_precheck is None
                    ):
                        obviousness_precheck = _lab_output(row)
                    if inventive_step is not None and obviousness_precheck is not None:
                        break
                closest = claim.get("current_closest_prior_art")
                closest = closest if isinstance(closest, Mapping) else {}
                claim_documents = claim.get("documents")
                claim_documents = (
                    claim_documents if isinstance(claim_documents, Mapping) else {}
                )
                narrative = (
                    build_inventive_step_narrative(
                        inventive_step,
                        claim_investigation_id=str(
                            claim.get("claim_investigation_id") or ""
                        ),
                        claim_id=str(claim.get("claim_id") or "") or None,
                        closest_prior_art_version_id=str(
                            closest.get("repository_selection_id")
                            or closest.get("closest_prior_art_version_id")
                            or ""
                        )
                        or None,
                        d1_document_id=str(
                            closest.get("document_id")
                            or inventive_step.get("closest_document_id")
                            or ""
                        )
                        or None,
                        documents={
                            str(key): (
                                dict(value.get("record") or value)
                                if isinstance(value, Mapping)
                                else {}
                            )
                            for key, value in claim_documents.items()
                        },
                    )
                    if inventive_step is not None
                    else None
                )
                appendix = public_json(
                    {
                        "scope": "module_lab_only",
                        "claim_id": claim.get("claim_id"),
                        "claim_investigation_id": claim.get(
                            "claim_investigation_id"
                        ),
                        "limitations": claim.get("limitations") or [],
                        "documents": claim.get("documents") or {},
                        "date_qualifications": claim.get(
                            "date_qualifications"
                        )
                        or {},
                        "comparisons": claim.get("comparisons") or {},
                        "closest_prior_art": claim.get(
                            "current_closest_prior_art"
                        ),
                        "obviousness_precheck": obviousness_precheck,
                        "inventive_step": inventive_step,
                        "inventive_step_narrative": narrative,
                        "lineage_run_ids": claim.get("lab_lineage_run_ids") or [],
                        "notice": (
                            "本附录仅汇总同一独立权利要求下已成功的模块实验输出，"
                            "不回写或替代正式调查报告快照。"
                        ),
                    }
                )
    return _completed(
        context,
        request,
        {
            **result,
            "report_data": result,
            **({"lab_analysis_appendix": appendix} if appendix else {}),
        },
        actual_provider=provider,
        network_used=False,
    )


def _guarded(handler: Callable[[JobContext], Mapping[str, Any]]) -> JobHandler:
    def execute(context: JobContext) -> Mapping[str, Any]:
        try:
            context.heartbeat()
            result = handler(context)
            context.heartbeat()
            return result
        except (PermanentJobError, RetryableJobError):
            raise
        except ProviderRequestError as exc:
            if not bool(getattr(exc, "retryable", True)):
                if getattr(exc, "error_code", None) == 67200004:
                    raise PermanentJobError(
                        "PATSNAP_PERMISSION_DENIED",
                        "智慧芽账号无权限或套餐额度已用尽（67200004），本次不会自动重试",
                    ) from exc
                if getattr(exc, "error_code", None) == 67200005:
                    raise PermanentJobError(
                        "PATSNAP_BALANCE_INSUFFICIENT",
                        "智慧芽账号余额不足（67200005），本次检索未执行，不会记为零命中",
                    ) from exc
                raise PermanentJobError(
                    "LAB_DEPENDENCY_REJECTED",
                    "模块实验的外部依赖拒绝了请求，本次不会自动重试",
                ) from exc
            raise RetryableJobError(
                "LAB_DEPENDENCY_FAILED",
                "模块实验的外部依赖暂时失败",
                retry_delay_seconds=5,
            ) from exc
        except MultimodalModelError as exc:
            message = str(
                public_json({"message": str(exc)}).get("message") or ""
            )[:500]
            if not bool(getattr(exc, "retryable", True)):
                raise PermanentJobError(
                    "GLM_REQUEST_REJECTED",
                    message or "GLM 拒绝了本次图文分析请求，本次不会自动重试",
                ) from exc
            reason_code = str(getattr(exc, "reason_code", None) or "")
            if reason_code == "glm_output_truncated":
                raise RetryableJobError(
                    "GLM_OUTPUT_TRUNCATED",
                    message or "GLM 输出达到长度上限且未形成完整 JSON",
                    retry_delay_seconds=5,
                ) from exc
            if reason_code == "glm_output_invalid_json":
                raise RetryableJobError(
                    "GLM_OUTPUT_INVALID_JSON",
                    message or "GLM 返回的结构化结果格式无效",
                    retry_delay_seconds=5,
                ) from exc
            raise RetryableJobError(
                "GLM_TEMPORARY_UNAVAILABLE",
                message or "GLM 图文分析服务暂时不可用",
                retry_delay_seconds=5,
            ) from exc
        except QueryValidationError as exc:
            message = str(public_json({"message": str(exc)}).get("message") or "")
            raise PermanentJobError(
                "LAB_QUERY_INVALID",
                message[:500] or "模块实验检索式不符合契约",
            ) from exc
        except ProviderContentError as exc:
            message = str(public_json({"message": str(exc)}).get("message") or "")
            artifact_sha256 = str(
                getattr(exc, "failure_artifact_sha256", "") or ""
            )
            if artifact_sha256:
                message = (
                    f"{message[:400]}；失败响应已冻结，"
                    f"SHA-256={artifact_sha256}"
                )
            elif getattr(exc, "failure_artifact_persist_failed", False):
                message = f"{message[:430]}；失败响应审计冻结失败"
            raise PermanentJobError(
                "LAB_PROVIDER_CONTENT_INVALID",
                message[:500] or "provider 返回内容不符合模块实验契约",
            ) from exc
        except AnalysisValidationError as exc:
            message = str(public_json({"message": str(exc)}).get("message") or "")
            raise PermanentJobError(
                "LAB_ANALYSIS_INVALID",
                message[:500] or "模块实验分析结果未通过确定性守门",
            ) from exc
        except (
            ValidationError,
            TypeError,
            ValueError,
        ) as exc:
            raise PermanentJobError(
                "LAB_INPUT_INVALID", "模块实验输入或输出不符合契约"
            ) from exc

    return execute


def build_module_lab_handlers(
    *, settings: Settings, repository: Any
) -> dict[str, JobHandler]:
    """Return a concrete durable handler for every public lab module code."""

    del repository  # repository is available through JobContext at execution time.
    raw_handlers: dict[str, Callable[[JobContext], Mapping[str, Any]]] = {
        "I1_TARGET_SNAPSHOT": lambda context: _target_snapshot_handler(
            settings, context
        ),
        "I1_5_CLAIM_DATES": _date_handler,
        "I2_INVENTIVE_PROFILE": lambda context: _inventive_profile_handler(
            settings, context
        ),
        "I2_QUERY_PLAN": lambda context: _query_plan_handler(settings, context),
        "I2_GAP_QUERY_PLAN": lambda context: _gap_query_plan_handler(
            settings, context
        ),
        "I3_PATENT_SEARCH": lambda context: _search_handler(
            settings, context, provider_kind="patent"
        ),
        "I3_NPL_SEARCH": lambda context: _search_handler(
            settings, context, provider_kind="npl"
        ),
        "I3_CANDIDATE_FILTER": lambda context: _candidate_filter_handler(
            settings, context
        ),
        "I3_FETCH": lambda context: _fetch_handler(settings, context),
        "I3_QUALIFY": _qualify_handler,
        "I4_S_SINGLE_REFERENCE": lambda context: _single_reference_handler(
            settings, context
        ),
        "I4_C_CLOSEST_PRIOR_ART": _closest_handler,
        "I4_O_OBVIOUSNESS_PRECHECK": lambda context: _obviousness_precheck_handler(
            settings, context
        ),
        "I4_I_INVENTIVE_STEP": lambda context: _inventive_step_handler(
            settings, context
        ),
        "I5_REPORT": _report_handler,
    }
    return {code: _guarded(raw_handlers[code]) for code in LAB_MODULE_CODES}


__all__ = [
    "DEFAULT_FIXTURE_ID",
    "LAB_MODE_MATRIX",
    "LAB_MODULE_CODES",
    "build_module_lab_handlers",
]
