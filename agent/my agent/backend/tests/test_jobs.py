"""真实数据库/Redis 任务回归：归属校验、失败重试、幂等与一致导出。"""

from datetime import timedelta
from pathlib import Path
import os

import pytest
from sqlalchemy import select, update, func

from app import db, jobs
from app.config import settings
from app.parsing import ParseError
from test_api import Account


@pytest.fixture(autouse=True)
def isolated_database():
    if os.getenv("VIBE_TEST_DATABASE") != "true" or db.engine.url.database != "vibe_test":
        pytest.skip("requires isolated database")


def task_record(task_id):
    with db.engine.connect() as conn:
        return dict(conn.execute(select(db.tasks).where(db.tasks.c.id == task_id)).mappings().one())


def upload(owner, data=b"id,score\n0001,90\n0002,\n"):
    response = owner.post("/api/files", files={"file": ("scores.csv", data, "text/csv")})
    assert response.status_code == 200, response.text
    return response.json()


def test_real_upload_parse_retry_and_no_duplicate_import(monkeypatch):
    owner = Account()
    saved = upload(owner)
    task_id = saved["task_id"]
    original = jobs.parse_file
    def broken(*args, **kwargs):
        raise ParseError("人工模拟解析失败")
    monkeypatch.setattr(jobs, "parse_file", broken)
    jobs.run_task(task_id, owner.id)
    failed = owner.client.get(f"/api/tasks/{task_id}").json()
    assert failed["status"] == "failed" and "人工模拟" in failed["error"]
    assert owner.client.get(f"/api/files/{saved['file_id']}/download").status_code == 200
    monkeypatch.setattr(jobs, "parse_file", original)
    assert owner.post(f"/api/tasks/{task_id}/retry").status_code == 200
    jobs.run_task(task_id, owner.id)
    assert task_record(task_id)["status"] == "completed" and task_record(task_id)["attempts"] == 2
    jobs.run_task(task_id, owner.id)
    assert task_record(task_id)["attempts"] == 2
    preview = owner.client.get(f"/api/imports/{task_id}").json()
    candidate = preview["tables"][0]
    body = {"version": preview["version"], "tables": [{"candidate_id": candidate["candidate_id"], "name": "人工核对导入",
        "description": "已核对", "fields": candidate["fields"], "reviewed": True,
        "corrections": [{"row_index": 1, "field_id": "c2", "value": 77}]}]}
    unreviewed = {**body, "tables": [{**body["tables"][0], "reviewed": False}]}
    assert owner.post(f"/api/imports/{task_id}/confirm", json=unreviewed).status_code == 422
    imported = owner.post(f"/api/imports/{task_id}/confirm", json=body)
    assert imported.status_code == 200, imported.text
    table_id = imported.json()["table_ids"][0]
    repeat = owner.post(f"/api/imports/{task_id}/confirm", json=body)
    assert repeat.json()["table_ids"] == [table_id]
    values = owner.client.get(f"/api/tables/{table_id}").json()["rows"]
    assert {row["values"]["c1"]: row["values"]["c2"] for row in values} == {"0001": 90, "0002": 77}
    assert any(row["source"].get("user_corrections") for row in values)
    assert owner.post(f"/api/tasks/{task_id}/retry").status_code == 409
    with db.engine.connect() as conn:
        assert conn.scalar(select(func.count()).select_from(db.data_tables).where(db.data_tables.c.owner_id == owner.id)) == 1


def test_background_owner_recheck_and_controlled_path():
    owner, other = Account(), Account()
    saved = upload(owner)
    jobs.run_task(saved["task_id"], other.id)
    assert task_record(saved["task_id"])["attempts"] == 0
    jobs.run_task(saved["task_id"], owner.id)
    assert task_record(saved["task_id"])["status"] == "completed"
    with pytest.raises(ParseError):
        jobs.controlled_path("../other-user/source.csv")
    with pytest.raises(ParseError):
        jobs.controlled_path("/etc/passwd")
    with db.engine.begin() as conn:
        conn.execute(update(db.files).where(db.files.c.id == saved["file_id"]).values(stored_name="../invalid.csv"))
        conn.execute(update(db.tasks).where(db.tasks.c.id == saved["task_id"]).values(status="pending"))
    jobs.run_task(saved["task_id"], owner.id)
    assert task_record(saved["task_id"])["status"] == "failed"
    assert "路径" in task_record(saved["task_id"])["error"]


def test_queue_network_failure_and_abandoned_task_recovery(monkeypatch):
    owner = Account()
    original = jobs._dispatch
    def unavailable(task):
        raise ConnectionError("artificial queue outage")
    monkeypatch.setattr(jobs, "_dispatch", unavailable)
    saved = upload(owner)
    assert task_record(saved["task_id"])["status"] == "pending"
    assert "队列" in task_record(saved["task_id"])["error"]
    with db.engine.begin() as conn:
        conn.execute(update(db.tasks).where(db.tasks.c.id == saved["task_id"]).values(status="running", attempts=1,
            updated_at=db.utcnow() - timedelta(seconds=jobs.JOB_TIMEOUT + 120)))
    monkeypatch.setattr(jobs, "_dispatch", original)
    jobs.recover_unfinished()
    assert task_record(saved["task_id"])["status"] == "pending"
    jobs.run_task(saved["task_id"], owner.id)
    assert task_record(saved["task_id"])["status"] == "completed"


def test_real_background_export_is_scoped_and_versioned():
    owner, other = Account(), Account()
    table = owner.table()
    exported = owner.post(f"/api/tables/{table['id']}/exports")
    assert exported.status_code == 200, exported.text
    task_id = exported.json().get("task_id") or exported.json().get("id")
    jobs.run_task(task_id, owner.id)
    assert task_record(task_id)["status"] == "completed"
    download = owner.client.get(f"/api/tasks/{task_id}/download")
    assert download.status_code == 200 and "001" in download.content.decode("utf-8-sig")
    assert other.client.get(f"/api/tasks/{task_id}/download").status_code == 404
    newer = owner.post(f"/api/tables/{table['id']}/exports").json()
    next_id = newer.get("task_id") or newer.get("id")
    with db.engine.begin() as conn:
        conn.execute(update(db.data_tables).where(db.data_tables.c.id == table["id"]).values(version=2))
    jobs.run_task(next_id, owner.id)
    assert task_record(next_id)["status"] == "failed" and "版本" in task_record(next_id)["error"]
    assert owner.client.get(f"/api/tasks/{next_id}/download").status_code == 409
