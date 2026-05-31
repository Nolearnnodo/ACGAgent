from __future__ import annotations

import sys
import types
from io import BytesIO
from pathlib import Path

import pytest
from fastapi import BackgroundTasks, HTTPException
from sqlalchemy import create_engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import sessionmaker
from starlette.datastructures import UploadFile

if "app.core.deps" not in sys.modules:
    deps_stub = types.ModuleType("app.core.deps")

    def get_current_user():
        raise RuntimeError("dependency override is not used in direct route tests")

    deps_stub.get_current_user = get_current_user
    sys.modules["app.core.deps"] = deps_stub

from app.api.v1.routers.passages import upload_passages
from app.db import base  # noqa: F401
from app.db.base_class import Base
from app.models.passage import Passage
from app.models.user import User
from app.services.passage_service import PassageService


def _upload_file(filename: str, text: str) -> UploadFile:
    return UploadFile(file=BytesIO(text.encode("utf-8")), filename=filename)


@pytest.fixture
def passage_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    with Session() as db:
        user = User(
            id=1,
            email="admin@example.com",
            password_hash="hash",
            role="admin",
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        yield db, user


def _install_plain_text_extractor(monkeypatch, *, fail_filename: str | None = None):
    def fake_extract(
        self: PassageService,
        filename: str,
        file_bytes: bytes,
    ) -> tuple[str, str]:
        if filename == fail_filename:
            raise ValueError("parse failed")
        return Path(filename).stem, file_bytes.decode("utf-8")

    monkeypatch.setattr(
        PassageService,
        "extract_text_via_markitdown",
        fake_extract,
    )


@pytest.mark.asyncio
async def test_upload_batch_parse_failure_does_not_commit_previous_files(
    monkeypatch,
    passage_db,
):
    db, user = passage_db
    _install_plain_text_extractor(monkeypatch, fail_filename="bad.txt")
    background_tasks = BackgroundTasks()

    with pytest.raises(HTTPException) as exc_info:
        await upload_passages(
            background_tasks=background_tasks,
            files=[
                _upload_file("ok.txt", "第一篇"),
                _upload_file("bad.txt", "第二篇"),
            ],
            current_user=user,
            db=db,
        )

    assert exc_info.value.status_code == 400
    assert db.query(Passage).count() == 0
    assert len(background_tasks.tasks) == 0


@pytest.mark.asyncio
async def test_upload_batch_write_failure_rolls_back_all_new_passages(
    monkeypatch,
    passage_db,
):
    db, user = passage_db
    _install_plain_text_extractor(monkeypatch)
    original_create_passage = PassageService.create_passage

    def fail_second_create(self: PassageService, *args, **kwargs):
        if kwargs.get("title") == "bad":
            raise SQLAlchemyError("forced write failure")
        return original_create_passage(self, *args, **kwargs)

    monkeypatch.setattr(PassageService, "create_passage", fail_second_create)
    background_tasks = BackgroundTasks()

    with pytest.raises(HTTPException) as exc_info:
        await upload_passages(
            background_tasks=background_tasks,
            files=[
                _upload_file("ok.txt", "第一篇"),
                _upload_file("bad.txt", "第二篇"),
            ],
            current_user=user,
            db=db,
        )

    assert exc_info.value.status_code == 500
    assert db.query(Passage).count() == 0
    assert len(background_tasks.tasks) == 0
