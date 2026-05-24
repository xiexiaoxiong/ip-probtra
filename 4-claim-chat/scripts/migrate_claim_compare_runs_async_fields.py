"""
数据库迁移：为 claim_compare_runs 表添加模块4异步任务字段

用法: cd /Users/adamrainbow/server/ip-probtra/4-claim-chat && python scripts/migrate_claim_compare_runs_async_fields.py
"""

import os
from pathlib import Path

env_candidates = [
    Path(__file__).resolve().parents[2] / "IP-protral" / ".env.local",
    Path(__file__).resolve().parents[1] / ".env.local",
    Path.cwd() / ".env.local",
]
for env_path in env_candidates:
    if env_path.exists():
        print(f"Loading env from: {env_path}")
        from dotenv import load_dotenv
        load_dotenv(env_path, override=False)
        break

from sqlalchemy import create_engine, text


def get_db_url() -> str:
    url = os.getenv("PGDATABASE_URL") or os.getenv("DATABASE_URL") or ""
    if url:
        return url
    raise ValueError("PGDATABASE_URL is not set")


def has_column(conn, column_name: str) -> bool:
    result = conn.execute(text("""
        SELECT column_name
        FROM information_schema.columns
        WHERE table_name = 'claim_compare_runs' AND column_name = :column_name
    """), {"column_name": column_name})
    return result.fetchone() is not None


def main():
    engine = create_engine(get_db_url())
    with engine.connect() as conn:
        statements = []

        if not has_column(conn, "run_id"):
            statements.append("ALTER TABLE claim_compare_runs ADD COLUMN run_id TEXT")
        if not has_column(conn, "status"):
            statements.append("ALTER TABLE claim_compare_runs ADD COLUMN status TEXT NOT NULL DEFAULT 'queued'")
        if not has_column(conn, "error_message"):
            statements.append("ALTER TABLE claim_compare_runs ADD COLUMN error_message TEXT")
        if not has_column(conn, "started_at"):
            statements.append("ALTER TABLE claim_compare_runs ADD COLUMN started_at TIMESTAMPTZ")
        if not has_column(conn, "finished_at"):
            statements.append("ALTER TABLE claim_compare_runs ADD COLUMN finished_at TIMESTAMPTZ")

        for statement in statements:
            print(f"Executing: {statement}")
            conn.execute(text(statement))

        conn.execute(text("""
            CREATE UNIQUE INDEX IF NOT EXISTS claim_compare_runs_run_id_unique_idx
            ON claim_compare_runs (run_id)
            WHERE run_id IS NOT NULL
        """))
        conn.commit()
        if statements:
            print("✅ claim_compare_runs 异步任务字段迁移完成")
        else:
            print("✅ claim_compare_runs 异步任务字段已存在，无需迁移")


if __name__ == "__main__":
    main()
