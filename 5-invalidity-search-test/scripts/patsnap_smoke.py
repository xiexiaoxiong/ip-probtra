#!/usr/bin/env python3
"""Read-only Patsnap REST smoke test for the isolated test environment."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
from typing import Any

from dotenv import dotenv_values


SERVICE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVICE_ROOT / "src"))

from invalidity.config import Settings  # noqa: E402
from invalidity.patsnap import (  # noqa: E402
    PATSNAP_BIBLIOGRAPHY_PATH,
    PATSNAP_CLAIMS_PATH,
    PATSNAP_DESCRIPTION_PATH,
    PatsnapApiError,
    PatsnapProvider,
)
from invalidity.providers import EvidenceRecord, ProviderError  # noqa: E402


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="按显式选项验证智慧芽 P001、P002 或 P012/P018/P019 链路",
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        default=SERVICE_ROOT / ".env.test.local",
        help="显式测试配置文件（默认 .env.test.local）",
    )
    parser.add_argument("--query", help="PatSnap 检索式或技术主题+特征组合")
    parser.add_argument(
        "--skip-count",
        action="store_true",
        help="不调用当前账号无权限的 P001；P002 和详情链路仍可独立执行",
    )
    parser.add_argument(
        "--search-limit",
        type=int,
        default=0,
        help="执行 P002 并返回的 lead 数；0 表示不做 P002",
    )
    parser.add_argument(
        "--patent-id",
        help="直接验证详情链路的智慧芽 patent_id，须与 --publication-number 同时提供",
    )
    parser.add_argument(
        "--publication-number",
        help="直接验证详情链路的公开号，须与 --patent-id 同时提供",
    )
    parser.add_argument(
        "--image-url",
        action="append",
        default=[],
        help="显式执行图片检索的公开 HTTPS 图片 URL；提供 1 次调用 P060 beta，2–4 次调用 P061",
    )
    parser.add_argument(
        "--image-patent-type",
        help="图片检索的智慧芽 patent_type；使用接口文档允许的显式值",
    )
    parser.add_argument(
        "--image-model",
        type=int,
        help="图片检索的智慧芽 model；使用接口文档允许的显式值",
    )
    parser.add_argument(
        "--image-limit",
        type=int,
        default=10,
        help="图片检索返回 lead 数（默认 10，最大 100）",
    )
    parser.add_argument(
        "--retrieve-first",
        action="store_true",
        help="取回 P002 第一条 lead，或由 --patent-id 指定文献的 P012/P018/P019",
    )
    parser.add_argument(
        "--probe-detail-endpoint",
        action="append",
        choices=("P012", "P018", "P019"),
        default=[],
        help="仅探测指定详情接口并输出权限/结构摘要；可重复提供，且不会输出正文",
    )
    parser.add_argument(
        "--download-pdf",
        action="store_true",
        help=(
            "显式调用 P020 并下载/校验 PDF（会增加 API 调用量）；"
            "提供直接 patent_id+公开号时不依赖 P012/P018/P019"
        ),
    )
    parser.add_argument(
        "--download-first-image",
        action="store_true",
        help="显式调用 P042 并下载/校验第一张附图（会增加 API 调用量）",
    )
    return parser.parse_args()


def main() -> int:
    args = _arguments()
    if args.search_limit < 0 or args.search_limit > 20:
        raise SystemExit("--search-limit 必须在 0..20 之间")
    if args.image_limit < 1 or args.image_limit > 100:
        raise SystemExit("--image-limit 必须在 1..100 之间")
    if len(args.image_url) > 4:
        raise SystemExit("--image-url 只允许提供 1–4 次")
    if args.image_url and (
        not args.image_patent_type or args.image_model is None
    ):
        raise SystemExit(
            "图片检索必须显式提供 --image-patent-type 和 --image-model"
        )
    if not args.image_url and (
        args.image_patent_type is not None or args.image_model is not None
    ):
        raise SystemExit("图片检索参数必须与 --image-url 一起使用")
    direct_reference = bool(args.patent_id or args.publication_number)
    detail_probes = tuple(dict.fromkeys(args.probe_detail_endpoint))
    if direct_reference and not (args.patent_id and args.publication_number):
        raise SystemExit("--patent-id 与 --publication-number 必须同时提供")
    if direct_reference and not (
        args.retrieve_first
        or detail_probes
        or args.download_pdf
        or args.download_first_image
    ):
        raise SystemExit(
            "直接详情验证必须显式提供 --retrieve-first 或 --probe-detail-endpoint"
        )
    if detail_probes and not direct_reference:
        raise SystemExit("--probe-detail-endpoint 必须提供直接文献标识")
    if detail_probes and args.retrieve_first:
        raise SystemExit("详情探测与完整 retrieve 不得同时执行，以免重复调用")
    if args.retrieve_first and args.search_limit < 1 and not direct_reference:
        raise SystemExit(
            "--retrieve-first 必须与 --search-limit 或直接文献标识一起使用"
        )
    if not args.query and (not args.skip_count or args.search_limit):
        raise SystemExit("P001/P002 调用必须提供 --query")
    if (
        args.skip_count
        and not args.search_limit
        and not direct_reference
        and not args.image_url
    ):
        raise SystemExit("--skip-count 后至少要执行 P002、详情或图片检索")
    if (
        (args.download_pdf or args.download_first_image)
        and not args.retrieve_first
        and not direct_reference
    ):
        raise SystemExit(
            "媒体下载必须与 --retrieve-first 一起使用，"
            "或直接提供 --patent-id 与 --publication-number"
        )
    env_path = args.env_file.expanduser().resolve()
    if not env_path.is_file():
        raise SystemExit(f"配置文件不存在: {env_path}")
    environment = {
        str(key): str(value)
        for key, value in dotenv_values(env_path).items()
        if value is not None
    }
    settings = Settings.from_environment(environment, load_dotenv_files=False)
    if settings.environment != "test":
        raise SystemExit("smoke 工具只允许 INVALIDITY_ENV=test")
    if not settings.patsnap_api_key:
        raise SystemExit(
            "缺少 INVALIDITY_TEST_PATSNAP_API_KEY；请只在 .env.test.local 中填写"
        )

    output: dict[str, Any] = {
        "environment": settings.environment,
        "base_url": settings.patsnap_base_url,
        "count_path": settings.patsnap_count_path,
        "search_path": settings.patsnap_search_path,
        "count_skipped": args.skip_count,
    }
    probe_failed = False
    try:
        with PatsnapProvider(
            api_key=settings.patsnap_api_key,
            base_url=settings.patsnap_base_url,
            count_path=settings.patsnap_count_path,
            search_path=settings.patsnap_search_path,
        ) as provider:
            if not args.skip_count:
                count = provider.count(args.query)
                output["count"] = count.model_dump()
            leads: list[EvidenceRecord] = []
            if args.search_limit:
                leads = provider.search(args.query, max_results=args.search_limit)
                output["search"] = {
                    "returned": len(leads),
                    "artifact": (
                        provider.last_search_artifact.model_dump()
                        if provider.last_search_artifact
                        else None
                    ),
                    "leads": [
                        {
                            "patent_id": lead.external_id,
                            "publication_number": lead.publication_number,
                            "title": lead.title,
                            "publication_date": lead.publication_date,
                            "filing_date": lead.filing_date,
                            "stage": lead.stage.value,
                        }
                        for lead in leads
                    ],
                }
            if args.image_url:
                if len(args.image_url) == 1:
                    image_leads = provider.search_single_image(
                        args.image_url[0],
                        patent_type=args.image_patent_type,
                        model=args.image_model,
                        max_results=args.image_limit,
                    )
                    image_endpoint = "P060-beta"
                else:
                    image_leads = provider.search_multiple_images(
                        args.image_url,
                        patent_type=args.image_patent_type,
                        model=args.image_model,
                        max_results=args.image_limit,
                    )
                    image_endpoint = "P061"
                output["image_search"] = {
                    "endpoint": image_endpoint,
                    "returned": len(image_leads),
                    "artifact": (
                        provider.last_search_artifact.model_dump()
                        if provider.last_search_artifact
                        else None
                    ),
                    "leads": [
                        {
                            "patent_id": lead.external_id,
                            "publication_number": lead.publication_number,
                            "title": lead.title,
                            "publication_date": lead.publication_date,
                            "filing_date": lead.filing_date,
                            "score": lead.raw_metadata.get(
                                "image_similarity_score"
                            ),
                            "img_id": lead.raw_metadata.get("matched_image_id"),
                            "stage": lead.stage.value,
                        }
                        for lead in image_leads
                    ],
                }
            if detail_probes:
                probe_paths = {
                    "P012": PATSNAP_BIBLIOGRAPHY_PATH,
                    "P018": PATSNAP_CLAIMS_PATH,
                    "P019": PATSNAP_DESCRIPTION_PATH,
                }
                output["detail_permission_probe"] = {}
                for endpoint_name in detail_probes:
                    params: dict[str, Any] = {"patent_id": args.patent_id}
                    if endpoint_name in {"P018", "P019"}:
                        params["replace_by_related"] = 0
                    try:
                        payload, raw, audit = provider._request_json(  # noqa: SLF001
                            "GET",
                            probe_paths[endpoint_name],
                            params=params,
                        )
                        data = payload.get("data")
                        output["detail_permission_probe"][endpoint_name] = {
                            "status": "success",
                            "data_shape": (
                                "array"
                                if isinstance(data, list)
                                else "object"
                                if isinstance(data, dict)
                                else type(data).__name__
                            ),
                            "item_count": len(data) if isinstance(data, list) else None,
                            "response_bytes": len(raw),
                            "response_sha256": hashlib.sha256(raw).hexdigest(),
                            "request_attempts": audit.get("request_attempts"),
                            "correlation_id": audit.get("correlation_id"),
                            "openapi_amount": audit.get("openapi_amount"),
                        }
                    except PatsnapApiError as exc:
                        probe_failed = True
                        artifact = exc.raw_response_artifact
                        output["detail_permission_probe"][endpoint_name] = {
                            "status": "failed",
                            "http_status": exc.status_code,
                            "error_code": exc.error_code,
                            "correlation_id": exc.correlation_id,
                            "response_bytes": (
                                len(artifact.content) if artifact is not None else None
                            ),
                            "response_sha256": (
                                artifact.content_sha256
                                if artifact is not None
                                else None
                            ),
                        }
            retrieval_lead: EvidenceRecord | None = None
            if direct_reference:
                retrieval_lead = EvidenceRecord(
                    provider="patsnap",
                    source_type="patent",
                    external_id=args.patent_id,
                    title="Direct detail smoke target",
                    source_url=(
                        f"{settings.patsnap_base_url}"
                        "/basic-patent-data/bibliography"
                    ),
                    publication_number=args.publication_number,
                )
            elif args.retrieve_first and leads:
                retrieval_lead = leads[0]
            if args.retrieve_first and retrieval_lead is not None:
                document = provider.retrieve(retrieval_lead)
                output["retrieve_first"] = {
                    "patent_id": document.external_id,
                    "publication_number": document.publication_number,
                    "stage": document.stage.value,
                    "publication_date": document.publication_date,
                    "filing_date": document.filing_date,
                    "priority_date": document.priority_date,
                    "has_claims": bool(document.claims),
                    "has_description": bool(document.description),
                    "retrieval_artifacts": [
                        artifact.model_dump()
                        for artifact in provider.last_retrieval_artifacts
                    ],
                    "pdf_verification": "not_requested",
                    "first_image_verification": "not_requested",
                    "content_sha256": document.content_sha256,
                }
                if args.download_pdf:
                    pdf = provider.download_pdf(document)
                    output["retrieve_first"]["pdf_verification"] = {
                        "status": "verified",
                        "media_type": pdf.media_type,
                        "byte_size": len(pdf.content),
                        "sha256": pdf.content_sha256,
                    }
                if args.download_first_image:
                    image = provider.download_image(document, index=0)
                    output["retrieve_first"]["first_image_verification"] = {
                        "status": "verified",
                        "media_type": image.media_type,
                        "byte_size": len(image.content),
                        "sha256": image.content_sha256,
                    }
            elif retrieval_lead is not None and (
                args.download_pdf or args.download_first_image
            ):
                output["direct_media"] = {
                    "patent_id": retrieval_lead.external_id,
                    "publication_number": retrieval_lead.publication_number,
                    "pdf_verification": "not_requested",
                    "first_image_verification": "not_requested",
                }
                if args.download_pdf:
                    pdf = provider.download_pdf(retrieval_lead)
                    output["direct_media"]["pdf_verification"] = {
                        "status": "verified",
                        "media_type": pdf.media_type,
                        "byte_size": len(pdf.content),
                        "sha256": pdf.content_sha256,
                    }
                if args.download_first_image:
                    image = provider.download_image(retrieval_lead, index=0)
                    output["direct_media"]["first_image_verification"] = {
                        "status": "verified",
                        "media_type": image.media_type,
                        "byte_size": len(image.content),
                        "sha256": image.content_sha256,
                    }
    except (PatsnapApiError, ProviderError) as exc:
        print(str(exc), file=sys.stderr)
        return 2

    # API keys and Authorization headers are never included in this output.
    print(json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True))
    return 2 if probe_failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
