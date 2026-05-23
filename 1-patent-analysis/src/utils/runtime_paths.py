from __future__ import annotations

import os
from pathlib import Path


def get_project_root() -> Path:
    workspace_path = (os.getenv("COZE_WORKSPACE_PATH") or "").strip()
    if workspace_path:
        return Path(workspace_path).expanduser().resolve()
    return Path(__file__).resolve().parents[2]


def resolve_project_path(relative_path: str) -> Path:
    return get_project_root() / relative_path
