from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
import sys
from typing import Any

SERVICE_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = SERVICE_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from invalidity.module1 import normalize_claims
from invalidity.parser_service import parse_source


DEFAULT_SAMPLE_ROOT = Path("/Users/xiexiaoxiong/Documents/Patent项目配套/实例专利")
DEFAULT_ORACLE = SERVICE_ROOT / "fixtures" / "sample-oracle.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sample-root", type=Path, default=DEFAULT_SAMPLE_ROOT)
    parser.add_argument("--oracle", type=Path, default=DEFAULT_ORACLE)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--only", action="append", default=[])
    parser.add_argument("--fail-on-error", action="store_true")
    return parser.parse_args()


def normal(value: Any) -> str:
    return "".join(str(value or "").split()).lower()


def discover_patent_pdfs(sample_root: Path) -> list[Path]:
    if not sample_root.is_dir():
        raise FileNotFoundError(sample_root)
    return sorted(
        (
            path.resolve()
            for path in sample_root.rglob("*")
            if path.is_file() and path.suffix.lower() == ".pdf"
        ),
        key=lambda path: str(path.relative_to(sample_root.resolve())).lower(),
    )


def materialized_figure_checks(figures: list[dict[str, Any]]) -> dict[str, bool]:
    paths = [
        Path(str(item.get("file_path") or ""))
        for item in figures
        if item.get("file_path")
    ]
    return {
        "abstract_figure_present": any(
            item.get("selection_role") == "abstract_figure" for item in figures
        ),
        "technical_drawing_present": any(
            item.get("selection_role") == "specification_drawing"
            for item in figures
        ),
        "figure_files_materialized": bool(paths)
        and all(path.is_file() and path.stat().st_size > 0 for path in paths),
        "figure_hashes_present": bool(figures)
        and all(
            len(str(item.get("file_sha256") or "")) == 64
            for item in figures
            if item.get("file_path")
        ),
        "figure_ids_unique": len(
            {str(item.get("figure_id") or "") for item in figures}
        )
        == len(figures),
    }


def main() -> int:
    args = parse_args()
    oracle = json.loads(args.oracle.read_text(encoding="utf-8"))["patents"]
    selected = set(args.only)
    sample_root = args.sample_root.resolve()
    discovered = discover_patent_pdfs(sample_root)
    selected_matches = {
        value
        for path in discovered
        for value in (path.name, str(path.relative_to(sample_root)))
        if value in selected
    }
    unknown_selected = selected - selected_matches
    if unknown_selected:
        raise SystemExit(f"unknown --only sample(s): {sorted(unknown_selected)}")
    results: list[dict[str, Any]] = []
    for source in discovered:
        relative_name = str(source.relative_to(sample_root))
        if selected and source.name not in selected and relative_name not in selected:
            continue
        expected = dict(oracle.get(relative_name) or oracle.get(source.name) or {})
        started = datetime.now(timezone.utc)
        item: dict[str, Any] = {
            "relative_path": relative_name,
            "file_name": source.name,
            "oracle_status": "matched" if expected else "not_available",
            "started_at": started.isoformat(),
            "status": "failed",
        }
        try:
            parsed = parse_source(str(source))
            claims = normalize_claims(parsed["claims"])
            independent = [claim.claim_id for claim in claims if claim.claim_type == "INDEPENDENT"]
            checks = {
                "claims_present": bool(claims),
                "independent_claim_present": bool(independent),
                "publication_number_present": bool(
                    parsed["metadata"].get("patent_number")
                ),
                "title_present": bool(parsed["metadata"].get("title")),
                "abstract_present": bool(parsed["metadata"].get("abstract")),
                "application_date_present": bool(
                    parsed["metadata"].get("application_date")
                ),
                "publication_date_present": bool(
                    parsed["metadata"].get("publication_date")
                ),
                "claims_section_present": bool(parsed.get("claims_section_text")),
                "page_texts_complete": len(parsed.get("page_texts") or [])
                == int(parsed.get("page_count") or 0),
                "specification_full_present": bool(
                    (parsed.get("specification") or {}).get("全文")
                ),
                "specification_sections_present": any(
                    key != "全文"
                    for key in (parsed.get("specification") or {})
                ),
                **materialized_figure_checks(list(parsed.get("figures") or [])),
            }
            if expected.get("claim_count") is not None:
                checks["claim_count"] = len(claims) == int(expected["claim_count"])
            if expected.get("independent_claim_ids") is not None:
                checks["independent_claim_ids"] = independent == list(
                    expected["independent_claim_ids"]
                )
            if expected.get("publication_number"):
                checks["publication_number"] = (
                    parsed["metadata"].get("patent_number")
                    == expected["publication_number"]
                )
            if expected.get("title"):
                checks["title"] = normal(expected["title"]) in normal(
                    parsed["metadata"].get("title")
                )
            expected_application = expected.get("application_date")
            if expected_application:
                checks["application_date"] = parsed["metadata"].get("application_date") == expected_application
            expected_priority = expected.get("priority_date")
            if expected_priority:
                checks["priority_date"] = parsed["metadata"].get("priority_date") == expected_priority
            expected_critical = expected.get("critical_date")
            if expected_critical:
                actual_critical = parsed["metadata"].get("priority_date") or parsed["metadata"].get("application_date")
                checks["critical_date"] = actual_critical == expected_critical
            expected_publication = expected.get("publication_date")
            if expected_publication:
                checks["publication_date"] = parsed["metadata"].get("publication_date") == expected_publication
            if "requires_ocr" in expected:
                checks["used_ocr"] = bool(parsed.get("used_ocr")) is bool(
                    expected["requires_ocr"]
                )
            item.update(
                {
                    "status": "passed" if all(checks.values()) else "failed",
                    "checks": checks,
                    "actual": {
                        "claim_count": len(claims),
                        "independent_claim_ids": independent,
                        "metadata": parsed["metadata"],
                        "used_ocr": parsed["used_ocr"],
                        "page_count": parsed.get("page_count"),
                        "specification_sections": list(
                            (parsed.get("specification") or {}).keys()
                        ),
                        "figures": parsed.get("figures") or [],
                    },
                }
            )
        except Exception as exc:  # diagnostic runner must continue to the next sample
            item["error"] = f"{type(exc).__name__}: {exc}"
        item["elapsed_seconds"] = round(
            (datetime.now(timezone.utc) - started).total_seconds(), 3
        )
        results.append(item)
        print(json.dumps(item, ensure_ascii=False), flush=True)

    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "sample_root": str(sample_root),
        "discovered_patent_pdf_count": len(discovered),
        "sample_count": len(results),
        "oracle_matched": sum(
            item["oracle_status"] == "matched" for item in results
        ),
        "oracle_not_available": sum(
            item["oracle_status"] == "not_available" for item in results
        ),
        "oracle_files_missing_from_sample": sorted(
            set(oracle) - {path.name for path in discovered}
        ),
        "passed": sum(item["status"] == "passed" for item in results),
        "failed": sum(item["status"] != "passed" for item in results),
        "results": results,
    }
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        temporary = args.output.with_suffix(args.output.suffix + ".tmp")
        temporary.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(args.output)
    print(
        json.dumps(
            {
                key: summary[key]
                for key in (
                    "discovered_patent_pdf_count",
                    "sample_count",
                    "passed",
                    "failed",
                )
            },
            ensure_ascii=False,
        )
    )
    return 1 if args.fail_on_error and summary["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
