#!/usr/bin/env python3
"""无效检索 module-lab 单元验证脚本。

用途：对 5-invalidity-search-test 的 /v1/lab/module-runs 逐模块发起运行并轮询到终态，
输出每个单元的终态、provider、尝试次数和关键输出统计。只打测试环境 5209，
不会触碰正式版 5109。

示例：
  # fixture 模式跑全部模块（含独立 I4-O）
  python scripts/lab_unit_check.py --mode fixture --tag round1

  # live I1 目标快照（关联既有调查）
  python scripts/lab_unit_check.py --mode live --module I1_TARGET_SNAPSHOT \
      --investigation-id <uuid>

  # live I3 智慧芽 P002 文本检索
  python scripts/lab_unit_check.py --mode live --module I3_PATENT_SEARCH \
      --investigation-id <uuid> \
      --input-json '{"search_provider":"patsnap","search_modality":"text","query":"TACD:(optical sensor AND sealed chamber)","language":"en","server_before":"2020-06-15","max_results":10}'
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ENV_FILE = Path(__file__).resolve().parent.parent / ".env.test.local"
MODULE_CODES = (
    "I1_TARGET_SNAPSHOT",
    "I1_5_CLAIM_DATES",
    "I2_INVENTIVE_PROFILE",
    "I2_QUERY_PLAN",
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
TERMINAL_RUN_STATUS = {"succeeded", "completed", "failed", "cancelled", "partial"}


def load_env() -> dict[str, str]:
    values: dict[str, str] = {}
    if not ENV_FILE.exists():
        return values
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip()
    return values


def api_request(
    method: str,
    url: str,
    token: str,
    payload: dict | None = None,
    timeout: int = 60,
) -> tuple[int, dict]:
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(url, data=body, method=method)
    request.add_header("Authorization", f"Bearer {token}")
    if body is not None:
        request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            return exc.code, json.loads(raw)
        except json.JSONDecodeError:
            return exc.code, {"detail": raw[:300]}


def poll_run(base: str, token: str, run_id: str, timeout_s: int) -> dict:
    deadline = time.monotonic() + timeout_s
    last: dict = {}
    while time.monotonic() < deadline:
        status, data = api_request(
            "GET", f"{base}/v1/lab/module-runs/{run_id}", token, timeout=30
        )
        if status != 200:
            return {"_poll_error": f"HTTP {status}: {data}"}
        last = data
        run_status = str((data.get("module_run") or {}).get("status") or "")
        if run_status in TERMINAL_RUN_STATUS:
            return last
        time.sleep(2)
    last["_poll_error"] = f"轮询超时（{timeout_s}s 未到终态）"
    return last


def summarize(module_code: str, create_status: int, created: dict, final: dict) -> str:
    run = final.get("module_run") or {}
    job = final.get("job") or {}
    output = run.get("output_snapshot") or {}
    if not isinstance(output, dict):
        output = {}
    status = run.get("status", "?")
    provider = output.get("actual_provider") or "-"
    network = output.get("network_used")
    attempts = job.get("attempt_count", "?")
    error = run.get("error_summary") or final.get("_poll_error") or ""
    inner = output.get("output") if isinstance(output.get("output"), dict) else {}
    stats = []
    for key in (
        "total_results",
        "result_count",
        "lead_count",
        "leads",
        "query_count",
        "claims",
        "limitations",
        "gap_count",
    ):
        value = inner.get(key)
        if isinstance(value, list):
            stats.append(f"{key}={len(value)}")
        elif isinstance(value, (int, float)):
            stats.append(f"{key}={value}")
    stat_text = " ".join(stats) if stats else "-"
    line = (
        f"{module_code:<24} create={create_status:<3} run={str(status):<10} "
        f"provider={str(provider):<16} network={str(network):<5} "
        f"attempts={attempts}  {stat_text}"
    )
    if error:
        line += f"\n    └─ error: {str(error)[:220]}"
    return line


def main() -> int:
    parser = argparse.ArgumentParser(description="module-lab 单元验证")
    parser.add_argument("--mode", choices=("fixture", "manual", "live"), required=True)
    parser.add_argument(
        "--module",
        action="append",
        choices=MODULE_CODES,
        help="只跑指定模块，可重复；缺省跑全部已登记模块",
    )
    parser.add_argument("--investigation-id", default=None)
    parser.add_argument("--claim-investigation-id", default=None)
    parser.add_argument("--iteration-number", type=int, default=None)
    parser.add_argument("--input-json", default=None, help="manual/live 的 input JSON")
    parser.add_argument("--tag", default=None, help="幂等键标签，缺省用时间戳")
    parser.add_argument("--timeout", type=int, default=240, help="单 run 轮询超时秒数")
    args = parser.parse_args()

    env = load_env()
    base = (env.get("INVALIDITY_TEST_API_URL") or "http://127.0.0.1:5209").rstrip("/")
    token = env.get("INVALIDITY_TEST_API_TOKEN") or ""
    if not token:
        print("缺少 INVALIDITY_TEST_API_TOKEN（.env.test.local）", file=sys.stderr)
        return 2

    modules = args.module or list(MODULE_CODES)
    tag = args.tag or time.strftime("%Y%m%d-%H%M%S")
    print(f"目标服务: {base}  模式: {args.mode}  标签: {tag}")
    print(f"模块数: {len(modules)}")
    print("-" * 100)

    failures = 0
    for module_code in modules:
        if args.mode == "fixture":
            input_payload: dict = {"fixture": "default"}
        elif args.input_json:
            input_payload = json.loads(args.input_json)
        else:
            input_payload = {}
        body = {
            "module_code": module_code,
            "input_mode": args.mode,
            "input": input_payload,
            "idempotency_key": f"lab-unit-{tag}-{module_code.lower()}",
        }
        if args.investigation_id:
            body["investigation_id"] = args.investigation_id
        if args.claim_investigation_id:
            body["claim_investigation_id"] = args.claim_investigation_id
        if args.iteration_number is not None:
            body["iteration_number"] = args.iteration_number

        create_status, created = api_request(
            "POST", f"{base}/v1/lab/module-runs", token, payload=body
        )
        if create_status not in (200, 201):
            failures += 1
            print(
                f"{module_code:<24} create={create_status} 拒绝: "
                f"{str(created.get('detail'))[:220]}"
            )
            continue
        run_id = str(created["module_run_id"])
        replay = created.get("idempotent_replay")
        final = poll_run(base, token, run_id, args.timeout)
        print(
            summarize(module_code, create_status, created, final)
            + (f"  (replay={replay})" if replay else "")
        )
        run_status = str((final.get("module_run") or {}).get("status") or "")
        if run_status not in {"succeeded", "completed"}:
            failures += 1

    print("-" * 100)
    print(f"完成：{len(modules) - failures}/{len(modules)} 个单元终态正常")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
