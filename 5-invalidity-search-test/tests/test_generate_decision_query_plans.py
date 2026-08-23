from __future__ import annotations

import importlib.util
from pathlib import Path


SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "generate_decision_query_plans.py"
)


def _load_generator():
    spec = importlib.util.spec_from_file_location(
        "generate_decision_query_plans", SCRIPT
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_strip_fixture_markers_preserves_profile_facts_recursively() -> None:
    generator = _load_generator()
    profile = {
        "simulated": False,
        "fixture_id": "must-not-cross-manual-boundary",
        "invention_profile": {
            "technical_problem": "降低流道压力损失",
            "search_concepts": [
                {
                    "text": "环形流道",
                    "fixture_output": True,
                    "source_reference": "权利要求1",
                }
            ],
        },
        "query_limit": 5,
    }

    cleaned = generator._strip_fixture_markers(profile)

    assert cleaned == {
        "invention_profile": {
            "technical_problem": "降低流道压力损失",
            "search_concepts": [
                {
                    "text": "环形流道",
                    "source_reference": "权利要求1",
                }
            ],
        },
        "query_limit": 5,
    }
    assert profile["simulated"] is False
    assert profile["invention_profile"]["search_concepts"][0][
        "fixture_output"
    ] is True
