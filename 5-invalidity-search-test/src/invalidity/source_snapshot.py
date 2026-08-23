from __future__ import annotations

import base64
import hashlib
import ipaddress
import mimetypes
import socket
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence
from urllib.parse import urljoin, urlsplit
import uuid

import httpx
from sqlalchemy.exc import SQLAlchemyError

from .artifacts import ArtifactStore
from .config import Settings
from .contracts import CreateInvestigationRequest, PatentSnapshot
from .module1 import Module1Adapter, PatentSnapshotError


_INVESTIGATION_NAMESPACE = uuid.UUID("25a4a24a-4891-4da1-987d-6fc30584bdf1")
_SAFE_SUFFIXES = {
    ".bin",
    ".doc",
    ".docx",
    ".htm",
    ".html",
    ".jpeg",
    ".jpg",
    ".pdf",
    ".png",
    ".tif",
    ".tiff",
    ".txt",
}
_SOURCE_MIME_TYPES = frozenset(
    {
        "application/msword",
        "application/pdf",
        "application/rtf",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "image/jpeg",
        "image/png",
        "image/tiff",
        "text/html",
        "text/plain",
    }
)
_IMAGE_MIME_TYPES = frozenset({"image/jpeg", "image/png", "image/tiff"})
_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})


class SourceSnapshotError(RuntimeError):
    def __init__(self, code: str, public_message: str, *, status_code: int) -> None:
        super().__init__(public_message)
        self.code = code
        self.public_message = public_message
        self.status_code = status_code


@dataclass(frozen=True, slots=True)
class FetchedResource:
    content: bytes
    final_url: str
    media_type: str


def _default_resolver(hostname: str) -> list[str]:
    try:
        records = socket.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise SourceSnapshotError(
            "SOURCE_URL_DNS_FAILED",
            "目标 URL 主机名无法解析",
            status_code=422,
        ) from exc
    return list(dict.fromkeys(str(record[4][0]) for record in records))


def _validate_public_url(
    value: str,
    resolver: Callable[[str], Sequence[str]],
) -> None:
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
        _ = parsed.port
    except ValueError as exc:
        raise SourceSnapshotError(
            "SOURCE_URL_BLOCKED", "目标 URL 无效", status_code=422
        ) from exc
    if (
        parsed.scheme.lower() not in {"http", "https"}
        or not hostname
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise SourceSnapshotError(
            "SOURCE_URL_BLOCKED",
            "目标 URL 只能是无凭据的 HTTP(S) 公网地址",
            status_code=422,
        )
    if hostname.rstrip(".").lower() in {"localhost", "localhost.localdomain"}:
        raise SourceSnapshotError(
            "SOURCE_URL_BLOCKED", "目标 URL 不得指向本机或私有网络", status_code=422
        )
    try:
        literal = ipaddress.ip_address(hostname)
        addresses = [literal]
    except ValueError:
        addresses = []
        for raw in resolver(hostname):
            try:
                addresses.append(ipaddress.ip_address(str(raw).split("%", 1)[0]))
            except ValueError as exc:
                raise SourceSnapshotError(
                    "SOURCE_URL_DNS_FAILED",
                    "目标 URL 的 DNS 结果无效",
                    status_code=422,
                ) from exc
    if not addresses or any(not address.is_global for address in addresses):
        raise SourceSnapshotError(
            "SOURCE_URL_BLOCKED", "目标 URL 不得指向本机或私有网络", status_code=422
        )


def _normalized_media_type(
    content: bytes,
    *,
    declared: str | None,
    name: str,
) -> str:
    header = str(declared or "").split(";", 1)[0].strip().lower()
    if content.startswith(b"%PDF"):
        detected = "application/pdf"
    elif content.startswith(b"\x89PNG\r\n\x1a\n"):
        detected = "image/png"
    elif content.startswith(b"\xff\xd8\xff"):
        detected = "image/jpeg"
    elif content.startswith((b"II*\x00", b"MM\x00*")):
        detected = "image/tiff"
    elif content.startswith(b"PK\x03\x04") and Path(name).suffix.lower() == ".docx":
        detected = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    else:
        detected = str(mimetypes.guess_type(name)[0] or "").lower()
    if header in {"", "application/octet-stream", "binary/octet-stream"}:
        return detected or "application/octet-stream"
    if header == "application/pdf" and detected != "application/pdf":
        return "application/octet-stream"
    if header.startswith("image/") and detected != header:
        return "application/octet-stream"
    return header


def _validate_media_type(
    content: bytes,
    *,
    declared: str | None,
    name: str,
    allowed: frozenset[str],
    code_prefix: str,
) -> str:
    media_type = _normalized_media_type(content, declared=declared, name=name)
    if media_type not in allowed:
        raise SourceSnapshotError(
            f"{code_prefix}_MIME_NOT_ALLOWED",
            "来源内容类型不在允许的专利文档或图像范围内",
            status_code=422,
        )
    return media_type


def read_allowed_file(
    value: str | Path,
    *,
    allowed_roots: Sequence[Path],
    max_bytes: int,
    allowed_mime_types: frozenset[str] = _SOURCE_MIME_TYPES,
    code_prefix: str = "SOURCE",
) -> tuple[bytes, str, Path]:
    path = Path(value).expanduser().resolve()
    roots = tuple(Path(root).expanduser().resolve() for root in allowed_roots)
    if not any(path != root and root in path.parents for root in roots):
        raise SourceSnapshotError(
            f"{code_prefix}_PATH_NOT_ALLOWED",
            "来源文件不在显式允许的上传或隔离工件目录内",
            status_code=422,
        )
    if not path.is_file():
        raise SourceSnapshotError(
            f"{code_prefix}_FILE_NOT_FOUND",
            "目标专利文件不存在，调查未创建",
            status_code=422,
        )
    try:
        if path.stat().st_size > max_bytes:
            raise SourceSnapshotError(
                f"{code_prefix}_TOO_LARGE", "来源文件超过允许的字节上限", status_code=413
            )
        content = path.read_bytes()
    except SourceSnapshotError:
        raise
    except OSError as exc:
        raise SourceSnapshotError(
            f"{code_prefix}_FILE_READ_FAILED",
            "无法读取目标专利文件，调查未创建",
            status_code=422,
        ) from exc
    if len(content) > max_bytes:
        raise SourceSnapshotError(
            f"{code_prefix}_TOO_LARGE", "来源文件超过允许的字节上限", status_code=413
        )
    media_type = _validate_media_type(
        content,
        declared=mimetypes.guess_type(path.name)[0],
        name=path.name,
        allowed=allowed_mime_types,
        code_prefix=code_prefix,
    )
    return content, media_type, path


class SafeFetcher:
    """Bounded HTTP(S) fetcher with per-hop public-address validation."""

    def __init__(
        self,
        *,
        timeout_seconds: float,
        max_redirects: int,
        transport: httpx.BaseTransport | None = None,
        resolver: Callable[[str], Sequence[str]] | None = None,
    ) -> None:
        self.timeout_seconds = timeout_seconds
        self.max_redirects = max_redirects
        self.transport = transport
        self.resolver = resolver or _default_resolver

    def fetch(
        self,
        url: str,
        *,
        max_bytes: int,
        allowed_mime_types: frozenset[str] = _SOURCE_MIME_TYPES,
        code_prefix: str = "SOURCE",
        user_agent: str = "patent-invalidity/0.1 (bounded-source-fetch)",
    ) -> FetchedResource:
        current_url = url
        try:
            with httpx.Client(
                follow_redirects=False,
                timeout=self.timeout_seconds,
                transport=self.transport,
                trust_env=False,
                headers={"User-Agent": user_agent, "Accept-Encoding": "identity"},
            ) as client:
                for redirect_count in range(self.max_redirects + 1):
                    _validate_public_url(current_url, self.resolver)
                    with client.stream("GET", current_url) as response:
                        if response.status_code in _REDIRECT_STATUSES:
                            location = response.headers.get("location")
                            if not location or redirect_count >= self.max_redirects:
                                raise SourceSnapshotError(
                                    f"{code_prefix}_URL_REDIRECT_LIMIT",
                                    "目标 URL 重定向无效或超过允许上限",
                                    status_code=422,
                                )
                            next_url = urljoin(str(response.url), location)
                            _validate_public_url(next_url, self.resolver)
                            current_url = next_url
                            continue
                        response.raise_for_status()
                        raw_length = response.headers.get("content-length")
                        if raw_length:
                            try:
                                if int(raw_length) > max_bytes:
                                    raise SourceSnapshotError(
                                        f"{code_prefix}_TOO_LARGE",
                                        "远程来源超过允许的字节上限",
                                        status_code=413,
                                    )
                            except ValueError:
                                pass
                        chunks: list[bytes] = []
                        total = 0
                        for chunk in response.iter_bytes():
                            total += len(chunk)
                            if total > max_bytes:
                                raise SourceSnapshotError(
                                    f"{code_prefix}_TOO_LARGE",
                                    "远程来源超过允许的字节上限",
                                    status_code=413,
                                )
                            chunks.append(chunk)
                        content = b"".join(chunks)
                        if not content:
                            raise SourceSnapshotError(
                                f"{code_prefix}_URL_EMPTY",
                                "目标 URL 返回空内容",
                                status_code=502,
                            )
                        media_type = _validate_media_type(
                            content,
                            declared=response.headers.get("content-type"),
                            name=urlsplit(str(response.url)).path,
                            allowed=allowed_mime_types,
                            code_prefix=code_prefix,
                        )
                        return FetchedResource(
                            content=content,
                            final_url=str(response.url),
                            media_type=media_type,
                        )
        except SourceSnapshotError:
            raise
        except httpx.HTTPError as exc:
            raise SourceSnapshotError(
                f"{code_prefix}_URL_FETCH_FAILED",
                "无法取得远程来源",
                status_code=502,
            ) from exc
        raise SourceSnapshotError(
            f"{code_prefix}_URL_REDIRECT_LIMIT",
            "目标 URL 重定向超过允许上限",
            status_code=422,
        )


def stable_investigation_id(
    *, environment: str, analysis_session_id: str, idempotency_key_sha256: str
) -> uuid.UUID:
    return uuid.uuid5(
        _INVESTIGATION_NAMESPACE,
        f"{environment}\0{analysis_session_id}\0{idempotency_key_sha256}",
    )


def _suffix(name: str, content_type: str | None = None) -> str:
    candidate = Path(name).suffix.lower()
    if candidate in _SAFE_SUFFIXES:
        return candidate
    guessed = mimetypes.guess_extension(str(content_type or "").split(";", 1)[0].strip())
    if guessed and guessed.lower() in _SAFE_SUFFIXES:
        return guessed.lower()
    return ".bin"


class SourceFreezer:
    """Freeze user-controlled source bytes before a durable workflow is queued."""

    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.BaseTransport | None = None,
        timeout_seconds: float | None = None,
        resolver: Callable[[str], Sequence[str]] | None = None,
    ) -> None:
        self.settings = settings
        self.store = ArtifactStore(settings.artifact_root, settings.environment)
        self.transport = transport
        self.timeout_seconds = float(
            timeout_seconds
            if timeout_seconds is not None
            else settings.source_fetch_timeout_seconds
        )
        self.fetcher = SafeFetcher(
            timeout_seconds=self.timeout_seconds,
            max_redirects=settings.source_max_redirects,
            transport=transport,
            resolver=resolver,
        )

    def freeze(
        self,
        *,
        request: CreateInvestigationRequest,
        investigation_id: str | uuid.UUID,
        source_snapshot: Mapping[str, Any],
    ) -> dict[str, Any]:
        snapshot = dict(source_snapshot)
        snapshot["declared_critical_date"] = (
            request.declared_critical_date.isoformat()
            if request.declared_critical_date is not None
            else None
        )
        if request.patent_record_id is not None:
            return self._freeze_patent_record(
                request=request,
                investigation_id=str(investigation_id),
                source_snapshot=snapshot,
            )

        if request.source_path:
            content, content_type, source = read_allowed_file(
                request.source_path,
                allowed_roots=self.settings.allowed_source_roots,
                max_bytes=self.settings.source_max_bytes,
            )
            original_uri = str(source)
            filename = f"original{_suffix(source.name)}"
            kind = "local_file"
        else:
            assert request.source_url is not None
            fetched = self.fetcher.fetch(
                request.source_url,
                max_bytes=self.settings.source_max_bytes,
                allowed_mime_types=_SOURCE_MIME_TYPES,
                code_prefix="SOURCE",
                user_agent="patent-invalidity/0.1 (source-snapshot)",
            )
            content = fetched.content
            final_url = fetched.final_url
            content_type = fetched.media_type
            original_uri = request.source_url
            filename = f"original{_suffix(urlsplit(final_url).path, content_type)}"
            kind = "source_url"

        artifact = self.store.write_bytes(
            str(investigation_id),
            f"source/{filename}",
            content,
            artifact_type="target_source_snapshot",
            mime_type=content_type,
        )
        snapshot["frozen_source"] = {
            **artifact.model_dump(),
            "kind": kind,
            "original_uri": original_uri,
            "frozen_before_queue": True,
        }
        snapshot["snapshot_state"] = "source_bytes_frozen"
        return snapshot

    def _freeze_patent_record(
        self,
        *,
        request: CreateInvestigationRequest,
        investigation_id: str,
        source_snapshot: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Copy the shared Module1 record into an immutable test artifact now."""

        assert request.patent_record_id is not None
        artifact_dir = self.store.investigation_dir(investigation_id) / "module1"
        adapter = Module1Adapter(
            module1_api_url=self.settings.module1_api_url,
            database_url=self.settings.database_url,
            api_token=self.settings.module1_api_token,
            allowed_local_roots=(
                *self.settings.allowed_source_roots,
                self.settings.artifact_root,
            ),
        )
        try:
            patent = adapter.snapshot(
                patent_record_id=request.patent_record_id,
                artifact_dir=artifact_dir,
            )
            patent_figures, patent_figure_artifacts = self._freeze_patent_figures(
                investigation_id=investigation_id,
                patent=patent,
            )
            patent.figures = patent_figures
            image_sources = adapter.ensure_target_images(
                patent,
                artifact_dir / "target-images-source",
            )
            target_images, target_image_artifacts = self._freeze_target_images(
                investigation_id=investigation_id,
                image_sources=image_sources,
            )
        except (
            PatentSnapshotError,
            SQLAlchemyError,
            OSError,
            ValueError,
            httpx.HTTPError,
        ) as exc:
            raise SourceSnapshotError(
                "PATENT_RECORD_SNAPSHOT_FAILED",
                "无法在创建调查时冻结模块一专利记录",
                status_code=422,
            ) from exc
        if not target_images:
            raise SourceSnapshotError(
                "PATENT_RECORD_IMAGES_MISSING",
                "模块一专利记录没有可冻结的目标图像，调查未创建",
                status_code=422,
            )

        patent_data = patent.model_dump(mode="json")
        patent_artifact = self.store.write_json(
            investigation_id,
            "source/patent-snapshot.json",
            patent_data,
            artifact_type="target_patent_snapshot",
        )
        return {
            **dict(source_snapshot),
            "frozen_source": {
                "kind": "patent_record_id",
                "patent_record_id": request.patent_record_id,
                "byte_snapshot_applicable": False,
                "record_snapshot_frozen": True,
                "source_sha256": patent.source_sha256,
                "patent_snapshot_artifact": patent_artifact.model_dump(),
            },
            "patent_snapshot": patent_data,
            "patent_figure_artifacts": patent_figure_artifacts,
            "target_images": target_images,
            "target_image_artifacts": target_image_artifacts,
            "snapshot_state": "patent_snapshot_frozen",
        }

    def _freeze_patent_figures(
        self,
        *,
        investigation_id: str,
        patent: PatentSnapshot,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        frozen_figures: list[dict[str, Any]] = []
        artifacts: list[dict[str, Any]] = []
        for index, figure in enumerate(patent.figures, start=1):
            if not isinstance(figure, Mapping):
                continue
            source = next(
                (
                    str(figure.get(key) or "").strip()
                    for key in ("file_path", "figure_url")
                    if str(figure.get(key) or "").strip()
                ),
                "",
            )
            if not source:
                continue
            content, media_type, source_name = self._read_image(source)
            if not content:
                continue
            artifact = self.store.write_bytes(
                investigation_id,
                (
                    f"source/patent-figures/figure-{index:03d}"
                    f"{_suffix(source_name, media_type)}"
                ),
                content,
                artifact_type="target_patent_figure_snapshot",
                mime_type=media_type,
            )
            row = {
                key: value
                for key, value in dict(figure).items()
                if key not in {"figure_url", "file_path", "storage_key"}
            }
            row.update(
                {
                    "figure_url": artifact.uri,
                    "file_path": artifact.uri,
                    "mime_type": artifact.mime_type,
                    "file_size": artifact.byte_size,
                    "file_sha256": artifact.sha256,
                    "artifact_index": index - 1,
                }
            )
            frozen_figures.append(row)
            artifacts.append(artifact.model_dump())
        return frozen_figures, artifacts

    def _freeze_target_images(
        self,
        *,
        investigation_id: str,
        image_sources: list[str],
    ) -> tuple[list[str], list[dict[str, Any]]]:
        frozen: list[str] = []
        artifacts: list[dict[str, Any]] = []
        for index, source in enumerate(image_sources[:2], start=1):
            content, media_type, source_name = self._read_image(source)
            if not content:
                continue
            artifact = self.store.write_bytes(
                investigation_id,
                f"source/target-images/image-{index}{_suffix(source_name, media_type)}",
                content,
                artifact_type="target_patent_image_snapshot",
                mime_type=media_type,
            )
            frozen.append(artifact.uri)
            artifacts.append(artifact.model_dump())
        return frozen, artifacts

    def _read_image(self, source: str) -> tuple[bytes, str | None, str]:
        if source.startswith("data:"):
            try:
                header, encoded = source.split(",", 1)
                media_type = header[5:].split(";", 1)[0] or "application/octet-stream"
                content = (
                    base64.b64decode(encoded, validate=True)
                    if ";base64" in header.lower()
                    else encoded.encode("utf-8")
                )
            except (ValueError, base64.binascii.Error) as exc:
                raise ValueError("目标图像 data URI 无效") from exc
            if len(content) > self.settings.image_max_bytes:
                raise SourceSnapshotError(
                    "IMAGE_TOO_LARGE", "目标图像超过允许的字节上限", status_code=413
                )
            normalized = _validate_media_type(
                content,
                declared=media_type,
                name="target-image",
                allowed=_IMAGE_MIME_TYPES,
                code_prefix="IMAGE",
            )
            return content, normalized, "target-image"
        if source.startswith(("http://", "https://")):
            fetched = self.fetcher.fetch(
                source,
                max_bytes=self.settings.image_max_bytes,
                allowed_mime_types=_IMAGE_MIME_TYPES,
                code_prefix="IMAGE",
                user_agent="patent-invalidity/0.1 (image-snapshot)",
            )
            return (
                fetched.content,
                fetched.media_type,
                urlsplit(fetched.final_url).path,
            )
        content, media_type, path = read_allowed_file(
            source,
            allowed_roots=(*self.settings.allowed_source_roots, self.settings.artifact_root),
            max_bytes=self.settings.image_max_bytes,
            allowed_mime_types=_IMAGE_MIME_TYPES,
            code_prefix="IMAGE",
        )
        return content, media_type, path.name


def ensure_patent_snapshot(
    *,
    settings: Settings,
    repository: Any,
    investigation_id: str,
) -> Mapping[str, Any]:
    """Create Module1's immutable fact snapshot inside the leased I0 job."""

    investigation = repository.get_investigation(investigation_id)
    if investigation is None:
        raise ValueError("调查不存在")
    source = dict(investigation.get("source_snapshot") or {})
    if source.get("patent_snapshot"):
        patent = PatentSnapshot.model_validate(source["patent_snapshot"])
        _verify_patent_snapshot_integrity(
            settings=settings,
            source=source,
            patent=patent,
        )
        return source

    request = source.get("request")
    if not isinstance(request, Mapping):
        raise ValueError("source_snapshot 缺少原始请求")
    frozen = source.get("frozen_source")
    source_path: str | None = None
    source_url: str | None = None
    patent_record_id: int | None = None
    if isinstance(frozen, Mapping) and frozen.get("kind") in {
        "local_file",
        "source_url",
    }:
        _verify_stored_artifact(settings, frozen, label="目标专利源文件")
        source_path = str(frozen.get("uri") or "").strip() or None
    elif isinstance(frozen, Mapping) and frozen.get("kind") == "patent_record_id":
        patent_record_id = int(frozen["patent_record_id"])
    else:
        source_path = str(request.get("source_path") or "").strip() or None
        source_url = str(request.get("source_url") or "").strip() or None
        raw_record_id = request.get("patent_record_id")
        patent_record_id = int(raw_record_id) if raw_record_id is not None else None

    store = ArtifactStore(settings.artifact_root, settings.environment)
    artifact_dir = store.investigation_dir(investigation_id) / "module1"
    adapter = Module1Adapter(
        module1_api_url=settings.module1_api_url,
        database_url=settings.database_url,
        api_token=settings.module1_api_token,
        allowed_local_roots=(*settings.allowed_source_roots, settings.artifact_root),
    )
    patent = adapter.snapshot(
        source_path=source_path,
        source_url=source_url,
        patent_record_id=patent_record_id,
        artifact_dir=artifact_dir,
    )
    expected_source_sha256 = (
        str(frozen.get("sha256") or "")
        if isinstance(frozen, Mapping)
        else ""
    )
    if expected_source_sha256 and patent.source_sha256 != expected_source_sha256:
        raise SourceSnapshotError(
            "SOURCE_SNAPSHOT_INTEGRITY_FAILED",
            "模块一解析的源文件哈希与冻结快照不一致",
            status_code=409,
        )
    freezer = SourceFreezer(settings)
    patent_figures, patent_figure_artifacts = freezer._freeze_patent_figures(
        investigation_id=investigation_id,
        patent=patent,
    )
    patent.figures = patent_figures
    image_sources = adapter.ensure_target_images(
        patent,
        artifact_dir / "target-images",
    )
    target_images, target_image_artifacts = freezer._freeze_target_images(
        investigation_id=investigation_id,
        image_sources=image_sources,
    )
    if not target_images:
        raise ValueError("模块一快照没有可供 GLM-4.6V 使用的目标图像")

    patent_data = patent.model_dump(mode="json")
    patent_artifact = store.write_json(
        investigation_id,
        "source/patent-snapshot.json",
        patent_data,
        artifact_type="target_patent_snapshot",
    )
    updated = {
        **source,
        "patent_snapshot": patent_data,
        "patent_snapshot_artifact": patent_artifact.model_dump(),
        "patent_figure_artifacts": patent_figure_artifacts,
        "target_images": target_images,
        "target_image_artifacts": target_image_artifacts,
        "snapshot_state": "patent_snapshot_frozen",
    }
    repository.update_investigation(
        investigation_id,
        source_snapshot=updated,
        actor="I1-snapshot",
        event_type="investigation.patent_snapshot_frozen",
        event_payload={
            "source_sha256": patent.source_sha256,
            "claim_count": len(patent.claims),
            "target_image_count": len(target_images),
        },
    )
    return updated


def _verify_stored_artifact(
    settings: Settings,
    artifact: Mapping[str, Any],
    *,
    label: str,
) -> bytes:
    uri = str(artifact.get("uri") or "").strip()
    expected_sha256 = str(artifact.get("sha256") or "").strip().lower()
    try:
        expected_size = int(artifact.get("byte_size"))
    except (TypeError, ValueError) as exc:
        raise SourceSnapshotError(
            "SOURCE_SNAPSHOT_INTEGRITY_FAILED",
            f"{label}缺少有效字节数",
            status_code=409,
        ) from exc
    path = Path(uri).expanduser().resolve()
    root = settings.artifact_root.expanduser().resolve()
    if not uri or (path != root and root not in path.parents):
        raise SourceSnapshotError(
            "SOURCE_SNAPSHOT_INTEGRITY_FAILED",
            f"{label}不在隔离工件目录内",
            status_code=409,
        )
    try:
        content = path.read_bytes()
    except OSError as exc:
        raise SourceSnapshotError(
            "SOURCE_SNAPSHOT_INTEGRITY_FAILED",
            f"{label}不存在或不可读取",
            status_code=409,
        ) from exc
    actual_sha256 = hashlib.sha256(content).hexdigest()
    if (
        len(expected_sha256) != 64
        or actual_sha256 != expected_sha256
        or len(content) != expected_size
    ):
        raise SourceSnapshotError(
            "SOURCE_SNAPSHOT_INTEGRITY_FAILED",
            f"{label}字节数或 SHA-256 已变化",
            status_code=409,
        )
    return content


def _verify_patent_snapshot_integrity(
    *,
    settings: Settings,
    source: Mapping[str, Any],
    patent: PatentSnapshot,
) -> None:
    frozen = source.get("frozen_source")
    if isinstance(frozen, Mapping):
        if frozen.get("kind") in {"local_file", "source_url"}:
            _verify_stored_artifact(
                settings,
                frozen,
                label="目标专利源文件",
            )
        expected_source_sha256 = str(
            frozen.get("sha256") or frozen.get("source_sha256") or ""
        )
        if expected_source_sha256 and patent.source_sha256 != expected_source_sha256:
            raise SourceSnapshotError(
                "SOURCE_SNAPSHOT_INTEGRITY_FAILED",
                "PatentSnapshot 源哈希与冻结快照不一致",
                status_code=409,
            )
        nested_patent_artifact = frozen.get("patent_snapshot_artifact")
        if isinstance(nested_patent_artifact, Mapping):
            _verify_stored_artifact(
                settings,
                nested_patent_artifact,
                label="目标专利结构化快照",
            )
    patent_artifact = source.get("patent_snapshot_artifact")
    if isinstance(patent_artifact, Mapping):
        _verify_stored_artifact(
            settings,
            patent_artifact,
            label="目标专利结构化快照",
        )
    figure_artifacts = source.get("patent_figure_artifacts")
    if isinstance(figure_artifacts, list):
        for index, artifact in enumerate(figure_artifacts, start=1):
            if not isinstance(artifact, Mapping):
                raise SourceSnapshotError(
                    "SOURCE_SNAPSHOT_INTEGRITY_FAILED",
                    "目标专利完整附图快照元数据无效",
                    status_code=409,
                )
            _verify_stored_artifact(
                settings,
                artifact,
                label=f"目标专利完整附图 {index}",
            )
    target_artifacts = source.get("target_image_artifacts")
    if not isinstance(target_artifacts, list) or not target_artifacts:
        raise SourceSnapshotError(
            "SOURCE_SNAPSHOT_INTEGRITY_FAILED",
            "目标专利图像快照缺少哈希元数据",
            status_code=409,
        )
    for index, artifact in enumerate(target_artifacts, start=1):
        if not isinstance(artifact, Mapping):
            raise SourceSnapshotError(
                "SOURCE_SNAPSHOT_INTEGRITY_FAILED",
                "目标专利图像快照元数据无效",
                status_code=409,
            )
        _verify_stored_artifact(
            settings,
            artifact,
            label=f"目标专利图像 {index}",
        )


__all__ = [
    "FetchedResource",
    "SafeFetcher",
    "SourceFreezer",
    "SourceSnapshotError",
    "ensure_patent_snapshot",
    "read_allowed_file",
    "stable_investigation_id",
]
