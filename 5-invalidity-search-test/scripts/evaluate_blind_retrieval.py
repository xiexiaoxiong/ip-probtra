from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any, Mapping


def normalize_publication_number(value: Any) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(value or "").upper())


def evaluate(
    benchmark: Mapping[str, Any],
    frozen_results: Mapping[str, Any],
) -> dict[str, Any]:
    """Evaluate frozen search output after generation and retrieval are over.

    This script is deliberately outside ``src/invalidity``.  Runtime query
    generation and provider calls cannot import it or see the expected public
    numbers.
    """

    runs = frozen_results.get("search_runs")
    if not isinstance(runs, list):
        runs = []
    ranks_by_role: dict[str, dict[str, int]] = {}
    ranks_by_query: dict[str, dict[str, int]] = {}
    for run_index, run in enumerate(runs, start=1):
        if not isinstance(run, Mapping):
            continue
        role = str(run.get("query_role") or "").strip()
        documents = run.get("documents")
        if not role or not isinstance(documents, list):
            continue
        query_id = str(run.get("query_id") or f"query-{run_index}")
        query_ranks: dict[str, int] = {}
        for rank, document in enumerate(documents[:10], start=1):
            if not isinstance(document, Mapping):
                continue
            number = normalize_publication_number(
                document.get("publication_number")
                or document.get("external_id")
            )
            if number and number not in query_ranks:
                query_ranks[number] = rank
            role_ranks = ranks_by_role.setdefault(role, {})
            if number:
                role_ranks[number] = min(rank, role_ranks.get(number, rank))
        ranks_by_query[query_id] = query_ranks

    evaluations: list[dict[str, Any]] = []
    for expected in benchmark.get("expected_documents") or []:
        if not isinstance(expected, Mapping):
            continue
        number = normalize_publication_number(expected.get("publication_number"))
        accepted_numbers = [
            item
            for item in [
                number,
                *(
                    normalize_publication_number(value)
                    for value in expected.get("equivalent_publication_numbers") or []
                ),
            ]
            if item
        ]
        accepted_roles = [
            str(item) for item in expected.get("accepted_roles") or []
        ]
        if accepted_roles:
            ranks = {
                role: min(
                    (
                        ranks_by_role.get(role, {}).get(candidate)
                        for candidate in accepted_numbers
                        if ranks_by_role.get(role, {}).get(candidate) is not None
                    ),
                    default=None,
                )
                for role in accepted_roles
            }
        else:
            ranks = {
                role: min(
                    (
                        role_ranks.get(candidate)
                        for candidate in accepted_numbers
                        if role_ranks.get(candidate) is not None
                    ),
                    default=None,
                )
                for role, role_ranks in ranks_by_role.items()
            }
        query_ranks = {
            query_id: min(
                (
                    values.get(candidate)
                    for candidate in accepted_numbers
                    if values.get(candidate) is not None
                ),
                default=None,
            )
            for query_id, values in ranks_by_query.items()
            if any(values.get(candidate) is not None for candidate in accepted_numbers)
        }
        matched_publication_number = next(
            (
                candidate
                for candidate in accepted_numbers
                if any(candidate in values for values in ranks_by_query.values())
            ),
            None,
        )
        observed = [rank for rank in ranks.values() if isinstance(rank, int)]
        best_rank = min(observed) if observed else None
        maximum = int(expected.get("maximum_cumulative_rank") or 10)
        evaluations.append(
            {
                "label": expected.get("label"),
                "publication_number": number,
                "accepted_publication_numbers": accepted_numbers,
                "matched_publication_number": matched_publication_number,
                "ranks_by_role": ranks,
                "ranks_by_query": query_ranks,
                "best_rank": best_rank,
                "maximum_cumulative_rank": maximum,
                "required": bool(expected.get("required", True)),
                "passed": best_rank is not None and best_rank <= maximum,
            }
        )
    required = [item for item in evaluations if item["required"]]
    optional = [item for item in evaluations if not item["required"]]
    return {
        "benchmark_version": benchmark.get("benchmark_version"),
        "case_id": benchmark.get("case_id"),
        "passed": bool(required) and all(item["passed"] for item in required),
        "required_hit_count": sum(bool(item["passed"]) for item in required),
        "required_document_count": len(required),
        "optional_hit_count": sum(bool(item["passed"]) for item in optional),
        "optional_document_count": len(optional),
        "documents": evaluations,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Evaluate frozen module-3 results without exposing answers to runtime."
    )
    parser.add_argument("benchmark", type=Path)
    parser.add_argument("results", type=Path)
    arguments = parser.parse_args()
    benchmark = json.loads(arguments.benchmark.read_text(encoding="utf-8"))
    results = json.loads(arguments.results.read_text(encoding="utf-8"))
    print(json.dumps(evaluate(benchmark, results), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
