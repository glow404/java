"""真实 PostgreSQL API 回归：验证账号隔离、查询快照和变更事务。

只在独立 vibe_test 数据库执行；不截断、清空或修改应用数据库。
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from hashlib import sha256
import os
from pathlib import Path
from uuid import uuid4

import pytest
from argon2 import PasswordHasher
from fastapi.testclient import TestClient
from sqlalchemy import insert, select, update, func

from app import db, auth
from app.config import settings
from app.main import app


@pytest.fixture(autouse=True)
def isolated_database():
    # 测试绝不以生产连接执行；保留已有验收数据，每个用例创建自己的账号。
    if os.getenv("VIBE_TEST_DATABASE") != "true" or db.engine.url.database != "vibe_test":
        pytest.skip("requires isolated vibe_test database and VIBE_TEST_DATABASE=true")


class Account:
    def __init__(self):
        self.username = "test_" + uuid4().hex[:12]
        self.password = uuid4().hex + "!Aa"
        auth.create_user(self.username, self.password)
        with db.engine.connect() as conn:
            self.id = conn.scalar(select(db.users.c.id).where(db.users.c.username == self.username))
        self.client = TestClient(app)
        response = self.client.post("/api/auth/login", json={"username": self.username, "password": self.password})
        assert response.status_code == 200, response.text
        me = self.client.get("/api/auth/me")
        assert me.status_code == 200
        self.csrf = me.json()["csrf_token"]

    def post(self, path, **kwargs):
        headers = kwargs.pop("headers", {})
        return self.client.post(path, headers={"X-CSRF-Token": self.csrf, **headers}, **kwargs)

    def patch(self, path, **kwargs):
        return self.client.patch(path, headers={"X-CSRF-Token": self.csrf}, **kwargs)

    def table(self):
        file_id, task_id = db.new_id(), db.new_id()
        stored = file_id + ".csv"
        settings.upload_dir.mkdir(parents=True, exist_ok=True)
        file_bytes = "学号,班级,成绩\n001,一班,90\n002,一班,50\n003,二班,80\n004,二班,\n".encode()
        (settings.upload_dir / stored).write_bytes(file_bytes)
        fields = [{"id": "c1", "name": "学号", "type": "string", "unit": "", "description": "原文件学号", "provenance": "file"},
                  {"id": "c2", "name": "班级", "type": "string", "unit": "", "description": "原文件班级", "provenance": "file"},
                  {"id": "c3", "name": "成绩", "type": "number", "unit": "分", "description": "原文件成绩", "provenance": "file"}]
        annotated = [("001", "一班", 90), ("002", "一班", 50), ("003", "二班", 80), ("004", "二班", None)]
        rows = [{"values": dict(zip(["c1", "c2", "c3"], row)),
                 "source": {"file_id": file_id, "row": index + 2, "worksheet": "CSV", "task_id": task_id}}
                for index, row in enumerate(annotated)]
        candidate = {"candidate_id": "sheet1", "name": "成绩", "description": "脱敏人工标注验收数据", "fields": fields,
                     "rows": rows, "row_count": len(rows), "original": {"rows": annotated}, "warnings": [], "requires_review": True}
        with db.engine.begin() as conn:
            conn.execute(insert(db.files).values(id=file_id, owner_id=self.id, original_name="成绩.csv", stored_name=stored,
                mime="text/csv", size=len(file_bytes), sha256=sha256(file_bytes).hexdigest(), created_at=db.utcnow()))
            conn.execute(insert(db.tasks).values(id=task_id, owner_id=self.id, file_id=file_id, kind="parse", status="completed",
                attempts=1, version=1, preview={"tables": [candidate], "warnings": []}, created_at=db.utcnow(), updated_at=db.utcnow()))
        payload = {"version": 1, "tables": [{"candidate_id": "sheet1", "name": "成绩", "description": candidate["description"],
                    "fields": fields, "reviewed": True, "corrections": []}]}
        imported = self.post(f"/api/imports/{task_id}/confirm", json=payload)
        assert imported.status_code == 200, imported.text
        table_id = imported.json()["table_ids"][0]
        conversation = self.post("/api/conversations", json={"title": "验收对话"})
        assert conversation.status_code == 200, conversation.text
        return {"id": table_id, "file_id": file_id, "task_id": task_id, "fields": fields,
                "conversation_id": conversation.json()["id"], "import_payload": payload}

    def ask(self, table, plan, content="结构化计划验收"):
        plan = {"table_id": table["id"], **plan}
        return self.post(f"/api/conversations/{table['conversation_id']}/messages",
                         json={"content": content, "table_ids": [table["id"]], "plan": plan})


@pytest.fixture
def owner():
    return Account()


def test_auth_cookie_csrf_logout_expiry(owner):
    assert owner.client.cookies.get(settings.cookie_name)
    anonymous = TestClient(app)
    assert anonymous.get("/api/catalog").status_code == 401
    assert owner.client.post("/api/conversations", json={}).status_code == 403
    assert owner.post("/api/conversations", json={}).status_code == 200
    # 过期来自服务端，不依赖浏览器自行声明身份。
    with db.engine.begin() as conn:
        conn.execute(update(db.sessions).where(db.sessions.c.owner_id == owner.id).values(expires_at=db.utcnow() - timedelta(seconds=1)))
    assert owner.client.get("/api/catalog").status_code == 401
    login = anonymous.post("/api/auth/login", json={"username": owner.username, "password": owner.password})
    assert "httponly" in login.headers["set-cookie"].lower()
    token = anonymous.get("/api/auth/me").json()["csrf_token"]
    assert anonymous.post("/api/auth/logout", headers={"X-CSRF-Token": token}).status_code == 200
    assert anonymous.get("/api/auth/me").status_code == 401


def test_every_resource_is_scoped_to_verified_session(owner):
    table = owner.table()
    other = Account()
    query = owner.ask(table, {"operation": "select", "fields": ["c1", "c3"]})
    assert query.status_code == 200, query.text
    result_id = query.json()["result"]["id"]
    mutation = owner.ask(table, {"operation": "update", "filters": [{"field_id": "c1", "op": "eq", "value": "001"}], "values": {"c3": 95}})
    assert mutation.status_code == 200, mutation.text
    plan_id = mutation.json()["plan"]["id"]
    paths = [f"/api/tables/{table['id']}", f"/api/files/{table['file_id']}/download", f"/api/tasks/{table['task_id']}",
             f"/api/imports/{table['task_id']}", f"/api/conversations/{table['conversation_id']}",
             f"/api/results/{result_id}", f"/api/plans/{plan_id}", f"/api/tasks/{table['task_id']}/download"]
    for path in paths:
        assert other.client.get(path, headers={"X-Owner-Id": owner.id}).status_code == 404, path
        assert TestClient(app).get(path).status_code == 401, path
    for path in [f"/api/plans/{plan_id}/confirm", f"/api/plans/{plan_id}/cancel", f"/api/tasks/{table['task_id']}/retry",
                 f"/api/tables/{table['id']}/exports"]:
        assert other.post(path).status_code == 404, path
    assert other.post(f"/api/imports/{table['task_id']}/confirm", json=table["import_payload"]).status_code == 404
    assert other.post(f"/api/conversations/{table['conversation_id']}/messages", json={"content": "查看", "table_ids": [table["id"]]}).status_code == 404
    catalog = other.client.get("/api/catalog").json()
    assert catalog["tables"] == [] and catalog["tasks"] == []
    assert other.client.get("/api/history").json()["operations"] == []
    assert owner.client.get(f"/api/files/{table['file_id']}/download").content.startswith("学号".encode())
    assert owner.post("/api/conversations", json={"owner_id": other.id}).status_code == 422


def test_import_review_correction_and_idempotence(owner):
    table = owner.table()
    repeat = owner.post(f"/api/imports/{table['task_id']}/confirm", json=table["import_payload"])
    assert repeat.status_code == 200
    assert repeat.json()["table_ids"] == [table["id"]]
    rows = owner.client.get(f"/api/tables/{table['id']}?offset=0&limit=2").json()
    assert rows["total"] == 4 and len(rows["rows"]) == 2
    assert all(row["values"]["c1"].startswith("00") for row in rows["rows"])
    with db.engine.connect() as conn:
        count = conn.scalar(select(func.count()).select_from(db.data_tables).where(db.data_tables.c.owner_id == owner.id))
    assert count == 1


def test_real_filter_sort_group_and_result_snapshot(owner):
    table = owner.table()
    reply = owner.ask(table, {"operation": "select", "fields": ["c1", "c3"],
        "filters": [{"field_id": "c3", "op": "gte", "value": 80}], "sort": [{"field_id": "c3", "direction": "desc"}]})
    assert reply.status_code == 200, reply.text
    result = reply.json()["result"]
    assert [row["c3"] for row in result["rows"]] == [90, 80]
    fetched = owner.client.get(f"/api/results/{result['id']}").json()
    assert fetched["query_snapshot"] == result["query_snapshot"] and fetched["rows"] == result["rows"]
    grouped = owner.ask(table, {"operation": "select", "group_by": ["c2"],
        "aggregates": [{"field_id": "c3", "function": "avg", "alias": "mean"}], "chart": {"type": "bar", "x": "c2", "y": "mean"}})
    assert grouped.status_code == 200, grouped.text
    assert {row["c2"]: float(row["mean"]) for row in grouped.json()["result"]["rows"]} == {"一班": 70, "二班": 80}
    conversation = owner.client.get(f"/api/conversations/{table['conversation_id']}").json()
    assert len(conversation["results"]) == 2
    assert result["id"] in {item["id"] for item in conversation["results"]}
    assert len([m for m in conversation["messages"] if m.get("result_id")]) == 2
    invalid = owner.ask(table, {"operation": "select", "fields": ["physical_sql_column"]})
    assert invalid.status_code in (400, 422)
    malicious = owner.ask(table, {"operation": "select", "fields": ["c1"], "sql": "DROP TABLE users"})
    assert malicious.status_code == 422


def test_ratio_ambiguity_and_independent_group_chart(owner):
    table = owner.table()
    unclear = owner.post(f"/api/conversations/{table['conversation_id']}/messages", json={"content": "高分学生占比", "table_ids": [table["id"]]})
    assert unclear.status_code == 200, unclear.text
    assert unclear.json().get("clarifications")
    ratio = owner.ask(table, {"operation": "select", "group_by": ["c2"],
        "ratio": {"field_id": "c3", "threshold": 60, "denominator": "all"},
        "chart": {"type": "pie", "title": "各班达标率"}})
    assert ratio.status_code == 200, ratio.text
    result = ratio.json()["result"]
    assert result["chart"]["type"] == "bar"
    assert "分母" in result["explanation"] and "分子" in result["explanation"]
    for row in result["rows"]:
        assert row["numerator"] == 1 and row["denominator"] == 2


def test_update_delete_insert_preview_and_double_confirm(owner):
    table = owner.table()
    update_reply = owner.ask(table, {"operation": "update", "filters": [{"field_id": "c1", "op": "eq", "value": "001"}], "values": {"c3": 96}})
    assert update_reply.status_code == 200, update_reply.text
    plan = update_reply.json()["plan"]
    assert plan["affected_count"] == 1 and plan["before"][0]["values"]["c3"] == 90
    confirmed = owner.post(f"/api/plans/{plan['id']}/confirm")
    assert confirmed.status_code == 200, confirmed.text
    assert len(confirmed.json()["result"]["rows"]) == 1
    second = owner.post(f"/api/plans/{plan['id']}/confirm")
    assert second.status_code == 200 and second.json()["result"]["id"] == confirmed.json()["result"]["id"]
    with db.engine.connect() as conn:
        assert conn.scalar(select(func.count()).select_from(db.audits).where(db.audits.c.plan_id == plan["id"])) == 1
    deletion = owner.ask(table, {"operation": "delete", "filters": [{"field_id": "c1", "op": "eq", "value": "002"}]}).json()["plan"]
    deleted = owner.post(f"/api/plans/{deletion['id']}/confirm")
    assert deleted.status_code == 200
    assert deleted.json()["result"]["rows"][0]["c1"] == "002"
    addition = owner.ask(table, {"operation": "insert", "values": {"c1": "005", "c2": "二班", "c3": 88}}).json()["plan"]
    assert owner.post(f"/api/plans/{addition['id']}/confirm").status_code == 200
    assert owner.client.get(f"/api/tables/{table['id']}").json()["total"] == 4


def test_plan_expiry_cancel_version_scope_and_session_binding(owner):
    table = owner.table()
    def preview():
        r = owner.ask(table, {"operation": "update", "filters": [{"field_id": "c3", "op": "gte", "value": 80}], "values": {"c3": 99}})
        assert r.status_code == 200, r.text
        return r.json()["plan"]
    expired = preview()
    with db.engine.begin() as conn:
        conn.execute(update(db.plans).where(db.plans.c.id == expired["id"]).values(expires_at=db.utcnow() - timedelta(seconds=1)))
    assert owner.post(f"/api/plans/{expired['id']}/confirm").status_code == 409
    cancelled = preview()
    assert owner.post(f"/api/plans/{cancelled['id']}/cancel").status_code == 200
    assert owner.post(f"/api/plans/{cancelled['id']}/confirm").status_code == 409
    stale = preview()
    with db.engine.begin() as conn:
        conn.execute(update(db.data_tables).where(db.data_tables.c.id == table["id"]).values(version=2))
    assert owner.post(f"/api/plans/{stale['id']}/confirm").status_code == 409
    changed = preview()
    with db.engine.begin() as conn:
        row = conn.execute(select(db.data_rows).where(db.data_rows.c.table_id == table["id"], db.data_rows.c["values"]["c1"].astext == "001")).mappings().one()
        conn.execute(update(db.data_rows).where(db.data_rows.c.id == row["id"]).values(values={**row["values"], "c3": 40}))
    assert owner.post(f"/api/plans/{changed['id']}/confirm").status_code == 409
    bound = preview()
    fresh = TestClient(app)
    assert fresh.post("/api/auth/login", json={"username": owner.username, "password": owner.password}).status_code == 200
    token = fresh.get("/api/auth/me").json()["csrf_token"]
    assert fresh.post(f"/api/plans/{bound['id']}/confirm", headers={"X-CSRF-Token": token}).status_code == 409


def test_concurrent_confirm_and_transaction_rollback(owner, monkeypatch):
    table = owner.table()
    reply = owner.ask(table, {"operation": "update", "filters": [{"field_id": "c1", "op": "eq", "value": "001"}], "values": {"c3": 97}})
    plan_id = reply.json()["plan"]["id"]
    # 独立请求连接竞争同一行锁；必须只提交一次，且共用保存的结果标识。
    with ThreadPoolExecutor(max_workers=2) as pool:
        answers = list(pool.map(lambda _: owner.post(f"/api/plans/{plan_id}/confirm"), range(2)))
    assert [r.status_code for r in answers] == [200, 200]
    assert answers[0].json()["result"]["id"] == answers[1].json()["result"]["id"]
    with db.engine.connect() as conn:
        assert conn.scalar(select(func.count()).select_from(db.audits).where(db.audits.c.plan_id == plan_id)) == 1
    rollback_reply = owner.ask(table, {"operation": "update", "filters": [{"field_id": "c1", "op": "eq", "value": "001"}], "values": {"c3": 12}})
    rollback_id = rollback_reply.json()["plan"]["id"]
    from app import service
    original_audit = service.audit
    def broken_audit(*args, **kwargs):
        raise RuntimeError("artificial audit write failure")
    monkeypatch.setattr(service, "audit", broken_audit)
    with pytest.raises(RuntimeError, match="artificial audit"):
        owner.post(f"/api/plans/{rollback_id}/confirm")
    monkeypatch.setattr(service, "audit", original_audit)
    with db.engine.connect() as conn:
        assert conn.scalar(select(db.data_rows.c["values"]).where(db.data_rows.c.table_id == table["id"],
            db.data_rows.c["values"]["c1"].astext == "001"))["c3"] == 97
        assert conn.scalar(select(db.plans.c.status).where(db.plans.c.id == rollback_id)) == "pending"
        assert conn.scalar(select(db.data_tables.c.version).where(db.data_tables.c.id == table["id"])) == 2
        assert conn.scalar(select(func.count()).select_from(db.audits).where(db.audits.c.plan_id == rollback_id)) == 0
    assert owner.post(f"/api/plans/{rollback_id}/confirm").status_code == 200


def test_metadata_type_validation_and_persistence_new_app(owner):
    table = owner.table()
    bad = {"version": 1, "name": "成绩新名称", "description": "用户确认说明", "fields": [dict(f) for f in table["fields"]]}
    bad["fields"][1]["type"] = "number"
    assert owner.patch(f"/api/tables/{table['id']}", json=bad).status_code in (400, 422)
    assert owner.client.get(f"/api/tables/{table['id']}").json()["version"] == 1
    good = {**bad, "fields": table["fields"]}
    assert owner.patch(f"/api/tables/{table['id']}", json=good).status_code == 200
    assert owner.patch(f"/api/tables/{table['id']}", json=good).status_code == 409
    db.engine.dispose()
    from app.main import create_app
    reconnected = TestClient(create_app())
    reconnected.cookies.update(owner.client.cookies)
    assert reconnected.get(f"/api/tables/{table['id']}").json()["name"] == "成绩新名称"
    assert reconnected.get(f"/api/conversations/{table['conversation_id']}").status_code == 200
