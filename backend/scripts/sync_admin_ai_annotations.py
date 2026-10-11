"""备份各账号私有 AI 标注，并把 admin@example.com 的结果同步到其他账号。

用法（在 backend/ 目录下）：

    python -m scripts.sync_admin_ai_annotations
    python -m scripts.sync_admin_ai_annotations --backup-only
    python -m scripts.sync_admin_ai_annotations --apply
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

BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_ROOT.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.core.config import get_settings  # noqa: E402
from app.db import base as _models  # noqa: E402,F401
from app.db.session import SessionLocal  # noqa: E402
from app.services.extraction_ai_annotation_share_service import (  # noqa: E402
    ExtractionAIAnnotationShareService,
)


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


def _backup(db, sqlite_file: Path) -> Path:
    out_dir = REPO_ROOT / "output" / "server-backups" / f"ai-annotations-private-{_now_stamp()}"
    out_dir.mkdir(parents=True, exist_ok=True)
    sqlite_copy = out_dir / "acg_agent.sqlite3"
    shutil.copy2(sqlite_file, sqlite_copy)

    integrity = "unknown"
    try:
        conn = sqlite3.connect(f"file:{sqlite_file.as_posix()}?mode=ro", uri=True)
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        conn.close()
    except sqlite3.Error:
        integrity = "error"

    payload = ExtractionAIAnnotationShareService(db).backup_private_annotations()
    json_path = out_dir / "private_ai_annotations.json"
    json_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    manifest = {
        "created_at": payload["created_at"],
        "source": str(sqlite_file),
        "sqlite_integrity_check": integrity,
        "user_counts": [
            {
                "email": item["email"],
                "role": item["role"],
                "jobs": len(item["jobs"]),
                "saved_results": len(item["saved_results"]),
                "reviews": len(item["reviews"]),
            }
            for item in payload["users"]
        ],
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


def main() -> int:
    parser = argparse.ArgumentParser(description="备份并同步 admin AI 标注到其他账号")
    parser.add_argument("--backup-only", action="store_true", help="只备份，不写入同步结果")
    parser.add_argument("--apply", action="store_true", help="把 admin 结果复制到其他账号")
    args = parser.parse_args()

    sqlite_file = _sqlite_path()
    db = SessionLocal()
    try:
        _backup(db, sqlite_file)
        if args.backup_only or not args.apply:
            if not args.apply:
                print("dry-run：未写入同步结果。加上 --apply 才会复制到其他账号。")
            return 0
        summary = ExtractionAIAnnotationShareService(db).sync_all_users()
        db.commit()
        print(json.dumps({"applied": True, **summary}, ensure_ascii=False, indent=2))
        return 0
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
