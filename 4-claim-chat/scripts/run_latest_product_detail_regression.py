from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from sqlalchemy import create_engine, text

from storage.database.db import get_db_url


BAD_IMAGE_TOKENS = (
    "logo",
    "qrcode",
    "qr-code",
    "search",
    "cart",
    "blank",
    "trend",
    "snms",
    "new_people",
    "footer",
    "etc/designs",
    "themes/default/assets",
    "/theme/default/assets/",
    "about-sony-close",
    "{{",
    "}}",
)

BAD_IMAGE_FILE_EXACT = (
    "er.png",
    "er.jpg",
    "er.jpeg",
    "er.webp",
    "ewm.png",
    "ewm.jpg",
    "ewm.jpeg",
    "ewm.webp",
)

BAD_IMAGE_FILE_SUBSTRINGS = (
    "erweima",
    "ewm",
    "shopping-cart",
    "kefu",
    "nationalemblem",
    "gend-finish",
    "return-process",
    "tmreturn-process",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run module4 sequential regression against latest module3 product-detail runs."
    )
    parser.add_argument("--service-url", default="http://127.0.0.1:5106")
    parser.add_argument("--record-id", action="append", type=int, default=[])
    parser.add_argument("--poll-interval-seconds", type=float, default=20.0)
    parser.add_argument("--per-record-timeout-seconds", type=float, default=3600.0)
    parser.add_argument("--jsonl-output", default="")
    parser.add_argument("--fail-on-fallback", action="store_true")
    parser.add_argument("--fail-on-bad-images", action="store_true")
    return parser.parse_args()


def post_json(url: str, payload: dict[str, Any], timeout: float = 30.0) -> dict[str, Any]:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def get_json(url: str, timeout: float = 30.0) -> dict[str, Any]:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def latest_module3_runs(engine, record_ids: list[int]) -> list[dict[str, Any]]:
    params: dict[str, Any] = {}
    where = ""
    if record_ids:
        where = "WHERE patent_record_id = ANY(:record_ids)"
        params["record_ids"] = record_ids
    sql = f"""
      SELECT DISTINCT ON (patent_record_id)
             id, patent_record_id, analysis_session_id, accepted_products_count, status, created_at
      FROM product_detail_search_runs
      {where}
      ORDER BY patent_record_id, created_at DESC, id DESC
    """
    with engine.connect() as conn:
        rows = [dict(row) for row in conn.execute(text(sql), params).mappings().all()]
    return rows


def audit_claim_compare_results(engine, claim_compare_run_id: int) -> dict[str, Any]:
    sql = """
      SELECT product_name, feature_id, reason, evidence_images
      FROM claim_compare_results
      WHERE claim_compare_run_id = :claim_compare_run_id
      ORDER BY id ASC
    """
    with engine.connect() as conn:
        rows = [
            dict(row)
            for row in conn.execute(
                text(sql),
                {"claim_compare_run_id": claim_compare_run_id},
            ).mappings().all()
        ]

    fallback_rows = [
        row for row in rows
        if "规则兜底" in str(row.get("reason") or "")
    ]
    bad_images: list[dict[str, Any]] = []
    for row in rows:
        images = row.get("evidence_images") or []
        if not isinstance(images, list):
            continue
        hits = [
            url for url in images
            if is_bad_evidence_image_url(url)
        ]
        if hits:
            bad_images.append(
                {
                    "product_name": row.get("product_name"),
                    "feature_id": row.get("feature_id"),
                    "images": hits,
                }
            )

    return {
        "result_count": len(rows),
        "fallback_reason_count": len(fallback_rows),
        "bad_evidence_image_count": len(bad_images),
        "bad_evidence_images": bad_images[:20],
    }


def is_bad_evidence_image_url(url: Any) -> bool:
    lower = str(url or "").strip().lower()
    if not lower:
        return False
    if any(token in lower for token in BAD_IMAGE_TOKENS):
        return True
    lower_file = urlparse(lower).path.rsplit("/", 1)[-1]
    if lower_file in BAD_IMAGE_FILE_EXACT:
        return True
    return any(token in lower_file for token in BAD_IMAGE_FILE_SUBSTRINGS)


def wait_for_run(service_url: str, run_id: str, poll_interval: float, timeout: float) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while True:
        status = get_json(f"{service_url.rstrip('/')}/runs/{run_id}", timeout=30.0)
        if status.get("is_finished") or status.get("status") in {"completed", "failed", "cancelled", "timeout"}:
            return status
        if time.monotonic() >= deadline:
            raise TimeoutError(f"module4 run timed out: {run_id}")
        time.sleep(max(1.0, poll_interval))


def append_jsonl(path: str, row: dict[str, Any]) -> None:
    if not path:
        return
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")


def main() -> int:
    args = parse_args()
    engine = create_engine(get_db_url(), pool_pre_ping=True)
    runs = latest_module3_runs(engine, args.record_id)
    if not runs:
        print("No module3 product-detail runs found")
        return 1

    failed = False
    for run in runs:
        patent_record_id = int(run["patent_record_id"])
        analysis_session_id = str(run.get("analysis_session_id") or "")
        if int(run.get("accepted_products_count") or 0) <= 0:
            outcome = {
                "patent_record_id": patent_record_id,
                "module3_run_id": run["id"],
                "status": "skipped",
                "reason": "latest module3 run has no accepted products",
            }
            print(json.dumps(outcome, ensure_ascii=False, default=str))
            append_jsonl(args.jsonl_output, outcome)
            failed = True
            continue

        requested_run_id = f"module4_latest_pd_{patent_record_id}_{int(time.time())}_{uuid.uuid4().hex[:8]}"
        payload = {
            "run_id": requested_run_id,
            "patent_record_id": patent_record_id,
            "analysis_session_id": analysis_session_id,
        }
        print(
            "START | "
            f"record={patent_record_id} | module3_run={run['id']} | session={analysis_session_id}"
            ,
            flush=True,
        )
        try:
            accepted = post_json(f"{args.service_url.rstrip('/')}/async_run", payload)
            run_id = str(accepted["run_id"])
            status = wait_for_run(
                args.service_url,
                run_id,
                args.poll_interval_seconds,
                args.per_record_timeout_seconds,
            )
            audit = audit_claim_compare_results(engine, int(status.get("claim_compare_run_id") or 0))
            outcome = {
                "patent_record_id": patent_record_id,
                "module3_run_id": run["id"],
                "analysis_session_id": analysis_session_id,
                "module4_run_id": run_id,
                "claim_compare_run_id": status.get("claim_compare_run_id"),
                "status": status.get("status"),
                "product_count": status.get("product_count"),
                "result_summary": status.get("result_summary"),
                "error_message": status.get("error_message"),
                **audit,
            }
        except (TimeoutError, urllib.error.URLError, KeyError, ValueError) as error:
            outcome = {
                "patent_record_id": patent_record_id,
                "module3_run_id": run["id"],
                "analysis_session_id": analysis_session_id,
                "status": "error",
                "error_message": str(error),
            }

        print(json.dumps(outcome, ensure_ascii=False, default=str), flush=True)
        append_jsonl(args.jsonl_output, outcome)
        if outcome.get("status") != "completed":
            failed = True
        if args.fail_on_fallback and int(outcome.get("fallback_reason_count") or 0) > 0:
            failed = True
        if args.fail_on_bad_images and int(outcome.get("bad_evidence_image_count") or 0) > 0:
            failed = True

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
