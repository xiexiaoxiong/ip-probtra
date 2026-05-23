from __future__ import annotations

import os
from pathlib import Path


def get_project_root() -> Path:
    workspace_path = (os.getenv("COZE_WORKSPACE_PATH") or "").strip()
    if workspace_path:
        return Path(workspace_path).expanduser().resolve()
    return Path(__file__).resolve().parents[2]


def get_data_root() -> Path:
    local_data_dir = (os.getenv("LOCAL_DATA_DIR") or "").strip()
    if local_data_dir:
        return Path(local_data_dir).expanduser().resolve()
    return get_project_root() / ".data"


def get_figures_root() -> Path:
    return get_data_root() / "patent-figures"


def get_task_figures_dir(task_id: str) -> Path:
    safe_task_id = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in (task_id or "default"))
    return get_figures_root() / safe_task_id


def get_module_public_base_url() -> str:
    public_base_url = (os.getenv("MODULE1_PUBLIC_BASE_URL") or "").strip().rstrip("/")
    if public_base_url:
        return public_base_url

    host = (os.getenv("WORKFLOW_HOST") or "127.0.0.1").strip() or "127.0.0.1"
    port = (os.getenv("WORKFLOW_PORT") or os.getenv("PORT") or "5101").strip() or "5101"
    return f"http://{host}:{port}"


def resolve_project_path(relative_path: str) -> Path:
    return get_project_root() / relative_path
