"""
数据库迁移：为 claim_compare_results 表添加 evidence_images 列

用法: cd /Users/xiexiaoxiong/Documents/patent/4-claim-chat && python scripts/migrate_add_evidence_images.py
"""

import os
import sys
from pathlib import Path

# 加载环境变量
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

def get_db_url():
    url = os.getenv("PGDATABASE_URL") or os.getenv("DATABASE_URL") or ""
    if url:
        return url
    raise ValueError("PGDATABASE_URL is not set")

def main():
    db_url = get_db_url()
    print(f"Connecting to database...")
    
    engine = create_engine(db_url)
    
    with engine.connect() as conn:
        # 检查列是否已存在
        result = conn.execute(text("""
            SELECT column_name 
            FROM information_schema.columns 
            WHERE table_name = 'claim_compare_results' AND column_name = 'evidence_images'
        """))
        
        if result.fetchone():
            print("✅ evidence_images 列已存在，无需迁移")
            return
        
        # 添加列
        print("添加 evidence_images 列...")
        conn.execute(text("""
            ALTER TABLE claim_compare_results 
            ADD COLUMN evidence_images JSONB
        """))
        conn.commit()
        print("✅ evidence_images 列添加成功")

if __name__ == "__main__":
    main()
