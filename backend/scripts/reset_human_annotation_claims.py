"""备份人工抽取标注的领取与填写记录，再清空领取状态并保留任务。

用法（在 backend/ 目录下）：

    python -m scripts.reset_human_annotation_claims
    python -m scripts.reset_human_annotation_claims --backup-only
    python -m scripts.reset_human_annotation_claims --apply
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_ROOT.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.core.config import get_settings  # noqa: E402
from app.db import base as _models  # noqa: E402,F401
from app.db.session import SessionLocal  # noqa: E402
from app.models.extraction_annotation import ExtractionAnnotationTask  # noqa: E402
from app.models.user import User  # noqa: E402
from app.services.extraction_ai_annotation_share_service import (  # noqa: E402
    CANONICAL_AI_ANNOTATION_EMAIL,
)
from app.services.extraction_annotation_service import ExtractionAnnotationService  # noqa: E402


def _now_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def _sqlite_path() -> Path:
    url = get_settings().sqlite_database_url
    if url.startswith("sqlite:///"):
        raw = url.replace("sqlite:///", "", 1)
        path = Path(raw)
        if not path.is_absolute():
            path = (BACKEND_ROOT / path).resolve()
        return path
    raise RuntimeError(f"不支持的 SQLite URL: {url}")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _rows(cur: sqlite3.Cursor, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
    cur.execute(sql, params)
    columns = [item[0] for item in cur.description]
    return [dict(zip(columns, row)) for row in cur.fetchall()]


def _backup(sqlite_file: Path) -> Path:
    out_dir = REPO_ROOT / "output" / "server-backups" / f"human-annotation-claims-{_now_stamp()}"
    out_dir.mkdir(parents=True, exist_ok=True)
    sqlite_copy = out_dir / "acg_agent.sqlite3"
    shutil.copy2(sqlite_file, sqlite_copy)

    conn = sqlite3.connect(f"file:{sqlite_file.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    integrity = cur.execute("PRAGMA integrity_check").fetchone()[0]
    payload = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source": str(sqlite_file),
        "sqlite_integrity_check": integrity,
        "users": _rows(cur, "SELECT id, email, role, created_at FROM users ORDER BY id"),
        "tasks": _rows(cur, "SELECT * FROM extraction_annotation_tasks ORDER BY id"),
        "submissions": _rows(
            cur,
            "SELECT * FROM extraction_annotation_submissions ORDER BY id",
        ),
        "adjudication_drafts": _rows(
            cur,
            "SELECT * FROM extraction_adjudication_drafts ORDER BY id",
        ),
        "gold_versions": _rows(cur, "SELECT * FROM extraction_gold_versions ORDER BY id"),
        "ai_annotation_counts": {
            "jobs": cur.execute("SELECT COUNT(*) FROM extraction_ai_annotation_jobs").fetchone()[0],
            "saved_results": cur.execute(
                "SELECT COUNT(*) FROM extraction_ai_annotation_saved_results"
            ).fetchone()[0],
            "reviews": cur.execute(
                "SELECT COUNT(*) FROM extraction_ai_annotation_reviews"
            ).fetchone()[0],
        },
    }
    conn.close()

    json_path = out_dir / "human_annotation_claims.json"
    json_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    manifest = {
        "created_at": payload["created_at"],
        "source": str(sqlite_file),
        "sqlite_integrity_check": integrity,
        "row_counts": {
            "users": len(payload["users"]),
            "extraction_annotation_tasks": len(payload["tasks"]),
            "extraction_annotation_submissions": len(payload["submissions"]),
            "extraction_adjudication_drafts": len(payload["adjudication_drafts"]),
            "extraction_gold_versions": len(payload["gold_versions"]),
            **{f"ai_{key}": value for key, value in payload["ai_annotation_counts"].items()},
        },
        "files": {
            sqlite_copy.name: {
                "bytes": sqlite_copy.stat().st_size,
                "sha256": _sha256(sqlite_copy),
            },
            json_path.name: {
                "bytes": json_path.stat().st_size,
                "sha256": _sha256(json_path),
            },
        },
    }
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps({"backup_dir": str(out_dir), **manifest}, ensure_ascii=False, indent=2))
    return out_dir


def _reset_all(db) -> dict[str, int]:
    admin = (
        db.query(User)
        .filter(User.email == CANONICAL_AI_ANNOTATION_EMAIL)
        .first()
    )
    if admin is None or admin.role != "admin":
        raise RuntimeError("未找到 admin@example.com 管理员账号，拒绝清空领取记录。")

    service = ExtractionAnnotationService(db)
    tasks = (
        db.query(ExtractionAnnotationTask)
        .order_by(ExtractionAnnotationTask.id.asc())
        .all()
    )
    task_ids = [task.id for task in tasks]
    for task_id in task_ids:
        service.reset_task(task_id, admin)
    return {"reset_tasks": len(task_ids), "kept_tasks": len(task_ids)}


def main() -> int:
    parser = argparse.ArgumentParser(description="备份并清空人工抽取标注领取记录")
    parser.add_argument("--backup-only", action="store_true", help="只备份，不修改领取状态")
    parser.add_argument("--apply", action="store_true", help="清空领取与填写记录，保留任务")
    args = parser.parse_args()

    sqlite_file = _sqlite_path()
    _backup(sqlite_file)
    if args.backup_only or not args.apply:
        if not args.apply:
            print("dry-run：未修改领取状态。加上 --apply 才会清空填写记录。")
        return 0

    db = SessionLocal()
    try:
        summary = _reset_all(db)
        print(json.dumps({"applied": True, **summary}, ensure_ascii=False, indent=2))
        return 0
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
