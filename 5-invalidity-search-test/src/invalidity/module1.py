from __future__ import annotations

import hashlib
import json
import re
import shutil
import uuid
from pathlib import Path
from typing import Any, Iterable

import fitz
import httpx
from sqlalchemy import create_engine, text

from invalidity.contracts import ClaimSnapshot, PatentSnapshot


class PatentSnapshotError(RuntimeError):
    pass


PATENT_NUMBER_RE = re.compile(r"\b(CN\s*[_-]?\s*\d{7,12}\s*[A-Z]\d?)\b", re.IGNORECASE)
LEADING_CLAIM_NUMBER_RE = re.compile(r"^\s*\d+\s*[\.、:]?\s*")
DEPENDENCY_PREFIX_RE = re.compile(
    r"^\s*(?:(?:根据|如|按照)\s*)?权利要求\s*([0-9一二三四五六七八九十、,，或和至到\-~～]+?)"
    r"\s*(?:(?:中)?任(?:一|意一)(?:项)?\s*)?(?:所述|记载|的)",
    re.IGNORECASE,
)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def infer_patent_number(*values: str | None) -> str | None:
    for value in values:
        if not value:
            continue
        compact = str(value).replace("_", "").replace("-", "").replace(" ", "")
        match = PATENT_NUMBER_RE.search(compact)
        if match:
            return re.sub(r"[\s_-]", "", match.group(1)).upper()
    return None


def _chinese_small_number(value: str) -> int | None:
    mapping = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
    if value in mapping:
        return mapping[value]
    if value.startswith("十") and len(value) == 2:
        return 10 + mapping.get(value[1], 0)
    if value.endswith("十") and len(value) == 2:
        return mapping.get(value[0], 0) * 10
    if "十" in value and len(value) == 3:
        return mapping.get(value[0], 0) * 10 + mapping.get(value[2], 0)
    return None


def _parse_reference_atom(value: str) -> int | None:
    value = value.strip()
    if value.isdigit():
        return int(value)
    return _chinese_small_number(value)


def extract_parent_claim_ids(claim_text: str) -> tuple[list[str], bool]:
    prefix = re.sub(
        r"\s+", "", LEADING_CLAIM_NUMBER_RE.sub("", claim_text, count=1)[:180]
    )
    match = DEPENDENCY_PREFIX_RE.search(prefix)
    if not match:
        return [], False
    expression = match.group(1)
    uncertain = bool(re.search(r"任一|或|、|,|，|和|至|到|[-~～]", expression))
    parents: list[int] = []
    range_match = re.search(r"([0-9一二三四五六七八九十]+)\s*(?:至|到|[-~～])\s*([0-9一二三四五六七八九十]+)", expression)
    if range_match:
        start = _parse_reference_atom(range_match.group(1))
        end = _parse_reference_atom(range_match.group(2))
        if start is not None and end is not None and 0 < start <= end <= 200:
            parents.extend(range(start, end + 1))
        else:
            uncertain = True
    for atom in re.findall(r"[0-9]+|[一二三四五六七八九十]+", expression):
        number = _parse_reference_atom(atom)
        if number and number not in parents:
            parents.append(number)
    return [str(number) for number in parents], uncertain


def normalize_claims(raw_claims: Iterable[dict[str, Any]]) -> list[ClaimSnapshot]:
    normalized: list[ClaimSnapshot] = []
    for index, raw in enumerate(raw_claims, start=1):
        claim_id = str(raw.get("claim_id") or index).strip()
        claim_text = str(raw.get("claim_text") or "").strip()
        if not claim_text:
            raise PatentSnapshotError(f"权利要求 {claim_id} 没有原文")
        parents, uncertain = extract_parent_claim_ids(claim_text)
        claim_type = "DEPENDENT" if parents else "INDEPENDENT"
        sentence_units = [
            str(item).strip()
            for item in (raw.get("sentence_units") or [])
            if str(item).strip()
        ]
        normalized.append(
            ClaimSnapshot(
                claim_id=claim_id,
                claim_type=claim_type,
                claim_text=claim_text,
                parent_claim_ids=parents,
                dependency_uncertain=uncertain,
                sentence_units=sentence_units,
            )
        )
    _validate_claims(normalized)
    by_id = {claim.claim_id: claim for claim in normalized}
    for claim in normalized:
        claim.expanded_claim_text = _expand_claim(claim, by_id, set())
    return normalized


def _validate_claims(claims: list[ClaimSnapshot]) -> None:
    if not claims:
        raise PatentSnapshotError("模块一没有解析出任何权利要求")
    numeric_ids = [int(item.claim_id) for item in claims if item.claim_id.isdigit()]
    if len(numeric_ids) == len(claims):
        expected = list(range(1, max(numeric_ids) + 1))
        if sorted(numeric_ids) != expected:
            raise PatentSnapshotError(
                f"权利要求编号不连续，实际 {sorted(numeric_ids)}，期望 {expected}"
            )
    if len({item.claim_id for item in claims}) != len(claims):
        raise PatentSnapshotError("权利要求编号重复")
    for claim in claims:
        if len(claim.claim_text) < 20:
            raise PatentSnapshotError(f"权利要求 {claim.claim_id} 文本异常短")
        if re.match(r"^\s*\d+[.、]\s*(技术领域|相关技术|背景技术)", claim.claim_text):
            raise PatentSnapshotError(f"权利要求 {claim.claim_id} 疑似误收说明书小节")


def _expand_claim(
    claim: ClaimSnapshot,
    by_id: dict[str, ClaimSnapshot],
    visiting: set[str],
) -> str:
    if claim.claim_id in visiting:
        raise PatentSnapshotError(f"权利要求依赖存在循环: {claim.claim_id}")
    if not claim.parent_claim_ids:
        return claim.claim_text
    visiting = {*visiting, claim.claim_id}
    inherited: list[str] = []
    for parent_id in claim.parent_claim_ids:
        parent = by_id.get(parent_id)
        if parent is None:
            raise PatentSnapshotError(
                f"权利要求 {claim.claim_id} 引用了不存在的权利要求 {parent_id}"
            )
        inherited.append(_expand_claim(parent, by_id, visiting))
    return "\n【继承限制】" + "\n【或】".join(inherited) + "\n【新增限制】" + claim.claim_text


class Module1Adapter:
    def __init__(
        self,
        *,
        module1_api_url: str,
        database_url: str,
        api_token: str | None = None,
        allowed_local_roots: Iterable[Path] = (),
        timeout_seconds: float = 1200,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.module1_api_url = module1_api_url
        self.database_url = database_url
        self.api_token = str(api_token or "").strip()
        self.allowed_local_roots = tuple(
            Path(root).expanduser().resolve() for root in allowed_local_roots
        )
        self.timeout_seconds = timeout_seconds
        self.transport = transport

    def snapshot(
        self,
        *,
        source_path: str | None = None,
        source_url: str | None = None,
        patent_record_id: int | None = None,
        artifact_dir: Path,
    ) -> PatentSnapshot:
        artifact_dir.mkdir(parents=True, exist_ok=True)
        source_uri, source_hash = self._source_identity(source_path, source_url, patent_record_id)
        record_id = patent_record_id
        parser_task_id: str | None = None
        response: dict[str, Any] = {}
        if record_id is None:
            parser_task_id = f"invalidity_snapshot_{uuid.uuid4().hex}"
            response = self._call_module1(source_uri, parser_task_id)
            record_id = self._extract_record_id(response)
        if record_id is not None:
            data = self._load_record(record_id)
        else:
            data = self._normalize_response(response)
        claims = normalize_claims(data.get("claims") or [])
        metadata = data.get("metadata") or {}
        patent_number = infer_patent_number(
            metadata.get("patent_number"),
            Path(source_uri).name,
        )
        snapshot = PatentSnapshot(
            source_sha256=source_hash,
            source_uri=source_uri,
            source_format=data.get("source_format"),
            source_byte_size=data.get("source_byte_size"),
            page_count=data.get("page_count"),
            used_ocr=bool(data.get("used_ocr")),
            parser_version=data.get("parser_version"),
            patent_record_id=record_id,
            parser_task_id=parser_task_id or data.get("task_id"),
            patent_number=patent_number,
            application_number=metadata.get("application_number"),
            title=metadata.get("title"),
            holder=metadata.get("patent_holder"),
            abstract=metadata.get("abstract") or metadata.get("abstract_text"),
            application_date=metadata.get("application_date"),
            priority_date=metadata.get("priority_date"),
            publication_date=metadata.get("publication_date"),
            grant_date=metadata.get("grant_date"),
            bibliographic_data=metadata.get("bibliographic_data") or {},
            claims_section_text=data.get("claims_section_text"),
            page_texts=data.get("page_texts") or [],
            specification=data.get("specification") or {},
            claims=claims,
            figures=data.get("figures") or [],
            parser_errors=data.get("errors") or [],
        )
        (artifact_dir / "target_snapshot.json").write_text(
            snapshot.model_dump_json(indent=2), encoding="utf-8"
        )
        self.ensure_target_images(snapshot, artifact_dir / "target-images")
        return snapshot

    def _source_identity(
        self,
        source_path: str | None,
        source_url: str | None,
        patent_record_id: int | None,
    ) -> tuple[str, str]:
        if source_path:
            path = Path(source_path).expanduser().resolve()
            if not path.is_file():
                raise PatentSnapshotError(f"目标专利文件不存在: {path}")
            data = path.read_bytes()
            return str(path), sha256_bytes(data)
        if source_url:
            raise PatentSnapshotError(
                "远程目标专利必须先经安全抓取器冻结为隔离工件，再交给模块一"
            )
        if patent_record_id:
            marker = f"legacy-record:{patent_record_id}"
            return marker, sha256_bytes(marker.encode())
        raise PatentSnapshotError("没有可用的目标专利来源")

    def _call_module1(self, source_uri: str, task_id: str) -> dict[str, Any]:
        payload = {
            "patent_file": {"url": source_uri, "file_type": "image"},
            "task_id": task_id,
        }
        try:
            with httpx.Client(
                timeout=self.timeout_seconds,
                follow_redirects=True,
                transport=self.transport,
                trust_env=False,
            ) as client:
                headers = (
                    {"Authorization": f"Bearer {self.api_token}"}
                    if self.api_token
                    else None
                )
                response = client.post(
                    self.module1_api_url, json=payload, headers=headers
                )
                response.raise_for_status()
                data = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise PatentSnapshotError(f"模块一解析失败: {type(exc).__name__}") from exc
        if not isinstance(data, dict):
            raise PatentSnapshotError("模块一响应不是 JSON 对象")
        if data.get("status") in {"failed", "timeout", "cancelled"}:
            raise PatentSnapshotError(f"模块一未完成: {data.get('status')}")
        return data

    @staticmethod
    def _extract_record_id(response: dict[str, Any]) -> int | None:
        candidates = [
            response.get("db_record_id"),
            (response.get("final_output") or {}).get("db_record_id")
            if isinstance(response.get("final_output"), dict)
            else None,
        ]
        for candidate in candidates:
            try:
                if candidate is not None and int(candidate) > 0:
                    return int(candidate)
            except (TypeError, ValueError):
                pass
        return None

    def _load_record(self, record_id: int) -> dict[str, Any]:
        engine = create_engine(self.database_url, pool_pre_ping=True)
        try:
            with engine.connect() as connection:
                record = connection.execute(
                    text(
                        """
                        SELECT id, task_id, patent_number, patent_holder, title,
                               abstract_text, application_date, priority_date,
                               specification, parse_errors
                        FROM public.patent_parse_records WHERE id = :id
                        """
                    ),
                    {"id": record_id},
                ).mappings().one_or_none()
                if record is None:
                    raise PatentSnapshotError(f"找不到模块一记录 {record_id}")
                claims = connection.execute(
                    text(
                        """
                        SELECT claim_id, claim_type, claim_text, parent_claim_id, sentence_units
                        FROM public.patent_claims WHERE record_id = :id
                        ORDER BY CASE WHEN claim_id ~ '^[0-9]+$' THEN claim_id::int ELSE 2147483647 END,
                                 claim_id
                        """
                    ),
                    {"id": record_id},
                ).mappings().all()
                figures = connection.execute(
                    text(
                        """
                        SELECT figure_id, figure_url, figure_description, storage_key,
                               file_path, mime_type, file_size, file_sha256
                        FROM public.patent_figures WHERE record_id = :id ORDER BY id
                        """
                    ),
                    {"id": record_id},
                ).mappings().all()
        finally:
            engine.dispose()
        return {
            "task_id": record["task_id"],
            "source_format": None,
            "source_byte_size": None,
            "page_count": None,
            "used_ocr": False,
            "parser_version": "shared-module1-database-record",
            "metadata": {
                "patent_number": record["patent_number"],
                "patent_holder": record["patent_holder"],
                "title": record["title"],
                "abstract": record["abstract_text"],
                "application_date": record["application_date"],
                "priority_date": record["priority_date"],
            },
            "specification": record["specification"] or {},
            "errors": record["parse_errors"] or [],
            "claims": [dict(item) for item in claims],
            "figures": [dict(item) for item in figures],
        }

    @staticmethod
    def _normalize_response(response: dict[str, Any]) -> dict[str, Any]:
        output = response.get("final_output") if isinstance(response.get("final_output"), dict) else response
        if not isinstance(output, dict):
            raise PatentSnapshotError("模块一响应缺少 final_output")
        return {
            "task_id": output.get("task_id"),
            "source_format": output.get("source_format"),
            "source_byte_size": output.get("source_byte_size"),
            "page_count": output.get("page_count"),
            "used_ocr": output.get("used_ocr"),
            "parser_version": output.get("parser_version"),
            "metadata": output.get("metadata") or {},
            "claims_section_text": output.get("claims_section_text"),
            "page_texts": output.get("page_texts") or [],
            "specification": output.get("specification") or {},
            "errors": output.get("errors") or [],
            "claims": output.get("claims") or [],
            "figures": output.get("figures") or [],
        }

    def ensure_target_images(
        self,
        snapshot: PatentSnapshot,
        output_dir: Path,
        max_images: int = 2,
    ) -> list[str]:
        existing: list[str] = []
        for figure in snapshot.figures:
            for key in ("file_path", "figure_url"):
                value = str(figure.get(key) or "").strip()
                if not value:
                    continue
                if value.startswith(("http://", "https://", "data:")):
                    existing.append(value)
                    break
                path = Path(value).expanduser().resolve()
                if not path.is_file() or not self._local_path_allowed(path):
                    continue
                output_dir.mkdir(parents=True, exist_ok=True)
                suffix = path.suffix.lower() if path.suffix else ".bin"
                destination = output_dir / f"figure-{len(existing) + 1}{suffix}"
                if path != destination.resolve():
                    shutil.copyfile(path, destination)
                existing.append(str(destination.resolve()))
                break
            if len(existing) >= max_images:
                return existing
        source = Path(snapshot.source_uri)
        if source.suffix.lower() != ".pdf" or not source.is_file():
            return existing
        output_dir.mkdir(parents=True, exist_ok=True)
        document = fitz.open(source)
        try:
            ranked = sorted(
                range(document.page_count),
                key=lambda index: len(document.load_page(index).get_images(full=True)),
                reverse=True,
            )
            for page_index in ranked:
                if len(existing) >= max_images:
                    break
                page = document.load_page(page_index)
                if not page.get_images(full=True) and existing:
                    continue
                destination = output_dir / f"page-{page_index + 1}.png"
                page.get_pixmap(matrix=fitz.Matrix(1.5, 1.5), alpha=False).save(destination)
                existing.append(str(destination))
        finally:
            document.close()
        return existing

    def _local_path_allowed(self, path: Path) -> bool:
        if not self.allowed_local_roots:
            # Direct unit callers may omit roots; production Settings always
            # supplies explicit upload/artifact roots through SourceFreezer.
            return True
        resolved = path.expanduser().resolve()
        return any(
            resolved != root and root in resolved.parents
            for root in self.allowed_local_roots
        )
