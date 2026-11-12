"""表页面回归：精确单行、单表历史和永久删除的数据副本清理。

仅使用隔离 vibe_test 和测试上传目录，绝不删除真实账号资源。
"""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import timedelta
import os
from pathlib import Path

import pytest
from fastapi import HTTPException
from langgraph.checkpoint.postgres import PostgresSaver
from sqlalchemy import func, insert, select, update

from app import db, service, table_actions, workflow
from app.config import settings
from app.schemas import ChatInput, QueryPlan
from test_api import Account
from test_feedback_data import make_table, register, verified


@pytest.fixture(autouse=True)
def isolated_database():
    if os.getenv("VIBE_TEST_DATABASE") != "true" or db.engine.url.database != "vibe_test":
        pytest.skip("requires isolated vibe_test database")


def expect_http(code, operation):
    with pytest.raises(HTTPException) as error:
        operation()
    assert error.value.status_code == code
    return error.value


def row_preview(account, table, operation, row_id=None, values=None, version=None):
    with db.engine.connect() as conn:
        current = service.owned(conn, db.data_tables, table["id"], account.id)
    return table_actions.preview_row(verified(account), table["id"], table_actions.RowPreview(
        conversation_id=table["conversation_id"], version=version or current["version"], operation=operation,
        row_id=row_id, values=values or {}))


def deletion(account, table):
    with db.engine.connect() as conn:
        current = service.owned(conn, db.data_tables, table["id"], account.id)
    return table_actions.preview_delete(verified(account), table["id"], table_actions.DeletePreview(
        conversation_id=table["conversation_id"], version=current["version"]))


def rows(account, table):
    return service.table_data(table["id"], account.id)["rows"]


def test_row_actions_select_physical_id_and_confirm_only_once():
    account = Account()
    table = make_table(account, "重复业务字段", [("same", "同名", 80), ("same", "同名", 80)])
    first, second = rows(account, table)
    preview = row_preview(account, table, "update", first["id"], {"c3": "91"})
    assert [row["values"]["c3"] for row in rows(account, table)] == [80, 80]
    assert preview["plan"]["affected_count"] == 1
    assert preview["plan"]["plan"]["kind"] == "row_action"
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: table_actions.confirm_action(preview["plan"]["id"], verified(account)), range(2)))
    assert responses[0]["result"]["id"] == responses[1]["result"]["id"]
    assert [row["values"]["c3"] for row in rows(account, table)] == [91, 80]
    removal = row_preview(account, table, "delete", second["id"])
    table_actions.confirm_action(removal["plan"]["id"], verified(account))
    assert [row["id"] for row in rows(account, table)] == [first["id"]]
    addition = row_preview(account, table, "insert", values={"c1": "new", "c2": "新记录", "c3": "92"})
    table_actions.confirm_action(addition["plan"]["id"], verified(account))
    assert [row["values"]["c3"] for row in rows(account, table)] == [91, 92]
    with db.engine.connect() as conn:
        assert service.owned(conn, db.data_tables, table["id"], account.id)["version"] == 4
        assert conn.scalar(select(func.count()).select_from(db.audits).where(db.audits.c.plan_id == preview["plan"]["id"])) == 1


def test_row_scope_type_version_session_and_changed_row_are_checked():
    account, other = Account(), Account()
    table = make_table(account, "精确范围", [("001", "甲", 80)])
    foreign_table = make_table(account, "其它表", [("001", "甲", 80)])
    first = rows(account, table)[0]
    expect_http(404, lambda: row_preview(account, table, "delete", rows(account, foreign_table)[0]["id"]))
    expect_http(422, lambda: row_preview(account, table, "update", first["id"], {"missing": 90}))
    expect_http(422, lambda: row_preview(account, table, "update", first["id"], {"c3": "invalid"}))
    expect_http(409, lambda: row_preview(account, table, "update", first["id"], {"c3": 90}, version=99))
    preview = row_preview(account, table, "update", first["id"], {"c3": 90})
    expect_http(404, lambda: table_actions.confirm_action(preview["plan"]["id"], verified(other)))
    expect_http(409, lambda: table_actions.confirm_action(preview["plan"]["id"], {"id": account.id, "session_id": "another"}))
    with db.engine.begin() as conn:
        conn.execute(update(db.data_rows).where(db.data_rows.c.id == first["id"]).values(values={"c1": "001", "c2": "已变化", "c3": 80}))
    expect_http(409, lambda: table_actions.confirm_action(preview["plan"]["id"], verified(account)))
    assert rows(account, table)[0]["values"]["c3"] == 80
    with db.engine.begin() as conn:
        conn.execute(update(db.plans).where(db.plans.c.id == preview["plan"]["id"]).values(expires_at=db.utcnow() - timedelta(seconds=1)))
    expect_http(409, lambda: table_actions.confirm_action(preview["plan"]["id"], verified(account)))


def test_changes_filter_table_and_mutations_before_limit():
    account, other = Account(), Account()
    table = make_table(account, "目标表历史", [])
    different = make_table(account, "日志很多的其它表", [])
    with db.engine.begin() as conn:
        service.audit(conn, account.id, "insert", table["id"], details={"after": [{"values": {"c1": "secret"}}]})
        service.audit(conn, account.id, "metadata_update", table["id"], details={"before": {"description": "secret"}})
        for _ in range(15):
            service.audit(conn, account.id, "update", different["id"], details={"before": [{"values": {"c1": "secret"}}]})
            service.audit(conn, account.id, "query", table["id"], details={"snapshot": {"rows": ["secret"]}})
    response = table_actions.table_changes(table["id"], account.id, 2)
    assert [item["action"] for item in response["operations"]] == ["metadata_update", "insert"]
    assert response["operations"][1]["summary"] == "新增记录 · 1 条"
    assert "secret" not in str(response)
    expect_http(404, lambda: table_actions.table_changes(table["id"], other.id))


def test_permanent_delete_clears_join_results_checkpoints_files_and_preserves_text():
    account = Account()
    table = account.table()
    left = make_table(account, "联表左侧仍保留", [("001", "左侧", 1)])
    _, _, relation_id = register(account, left, table)
    join_result = service.execute_select(account.id, left["conversation_id"], QueryPlan(operation="select", table_id=left["id"],
        relation_id=relation_id, fields=["left__c1", "right__c3"]))["result"]
    queried = account.ask(table, {"operation": "select", "fields": ["c1", "c3"]}, content="保留这段提问文字")
    assert queried.status_code == 200, queried.text
    result = queried.json()["result"]
    pending = row_preview(account, table, "update", rows(account, table)[0]["id"], {"c3": 95})
    export_id, run_id = db.new_id(), db.new_id()
    export_name = f"export-{export_id}.csv"
    export_path = settings.upload_dir / export_name
    export_path.write_text("secret-export", encoding="utf-8")
    with db.engine.begin() as conn:
        source = service.owned(conn, db.messages, result["source_message_id"], account.id)
        conn.execute(insert(db.assistant_runs).values(id=run_id, owner_id=account.id, conversation_id=table["conversation_id"],
            session_id=verified(account)["session_id"], message_id=source["id"], status="completed", attempts=1,
            response=queried.json(), created_at=db.utcnow(), updated_at=db.utcnow()))
        conn.execute(insert(db.result_visibility).values(id=db.new_id(), owner_id=account.id, result_id=result["id"], hidden=True, created_at=db.utcnow()))
        conn.execute(insert(db.tasks).values(id=export_id, owner_id=account.id, kind="export", status="completed", attempts=1,
            version=1, table_ids=[table["id"]], output_name=export_name, created_at=db.utcnow(), updated_at=db.utcnow()))
        conn.execute(update(db.data_tables).where(db.data_tables.c.id == left["id"]).values(
            structure={"relations": [{"table_id": table["id"], "shared_field_ids": ["c1"]}]}))
        original_messages = {row["id"]: row["content"] for row in conn.execute(select(db.messages).where(db.messages.c.owner_id == account.id)).mappings()}
        file = service.owned(conn, db.files, table["file_id"], account.id)
    source_path = settings.upload_dir / file["stored_name"]
    preview = deletion(account, table)
    assert preview["plan"]["plan"]["summary"]["source_file_deleted"]
    assert preview["plan"]["plan"]["summary"]["results"] >= 2
    confirmed = table_actions.confirm_action(preview["plan"]["id"], verified(account))
    assert confirmed["deleted_table_id"] == table["id"]
    assert confirmed["plan"]["table_id"] is None and confirmed["plan"]["result_id"] is None
    assert confirmed["plan"]["before"] == confirmed["plan"]["after"] == []
    assert confirmed["plan"]["plan"]["cleanup"]["completed"]
    assert not source_path.exists() and not export_path.exists()
    with db.engine.connect() as conn:
        assert not conn.scalar(select(db.data_tables.c.id).where(db.data_tables.c.id == table["id"]))
        assert not conn.scalar(select(db.data_rows.c.id).where(db.data_rows.c.table_id == table["id"]))
        assert not conn.scalar(select(db.table_relations.c.id).where(db.table_relations.c.id == relation_id))
        assert not conn.scalar(select(db.results.c.id).where(db.results.c.id.in_([result["id"], join_result["id"]])))
        assert not conn.scalar(select(db.plans.c.id).where(db.plans.c.id == pending["plan"]["id"]))
        assert not conn.scalar(select(db.tasks.c.id).where(db.tasks.c.id.in_([table["task_id"], export_id])))
        assert not conn.scalar(select(db.files.c.id).where(db.files.c.id == table["file_id"]))
        assert not conn.scalar(select(db.result_visibility.c.id).where(db.result_visibility.c.result_id == result["id"]))
        assert service.owned(conn, db.assistant_runs, run_id, account.id)["response"] is None
        message = service.owned(conn, db.messages, source["id"], account.id)
        assert message["content"] == "保留这段提问文字"
        assert message["request"]["table_ids"] == [] and message["request"]["plan"] is None
        assert service.owned(conn, db.data_tables, left["id"], account.id)["structure"]["relations"] == []
        messages = {row["id"]: row["content"] for row in conn.execute(select(db.messages).where(db.messages.c.owner_id == account.id)).mappings()}
        assert all(messages[identifier] == content for identifier, content in original_messages.items())
        old_audits = conn.execute(select(db.audits).where(db.audits.c.owner_id == account.id,
            db.audits.c.details["deleted_table_id"].astext == table["id"], db.audits.c.action != "delete_table")).mappings().all()
        assert old_audits and all(audit["details"]["data_removed"] for audit in old_audits)
        assert all("before" not in audit["details"] and "snapshot" not in audit["details"] for audit in old_audits)
    with PostgresSaver.from_conn_string(workflow.checkpoint_url()) as saver:
        assert saver.get_tuple({"configurable": {"thread_id": f"{account.id}:{source['id']}"}}) is None
    assert rows(account, left)[0]["values"]["c3"] == 1
    replay = table_actions.confirm_action(preview["plan"]["id"], verified(account))
    assert replay["replayed"]
    with db.engine.connect() as conn:
        assert conn.scalar(select(func.count()).select_from(db.audits).where(db.audits.c.plan_id == preview["plan"]["id"])) == 1


def test_shared_source_keeps_archived_table_file_and_only_other_candidate_preview():
    account = Account()
    table = account.table()
    other = make_table(account, "共享文件的归档表", [])
    with db.engine.begin() as conn:
        conn.execute(update(db.data_tables).where(db.data_tables.c.id == other["id"]).values(file_id=table["file_id"], archived=True))
        task = service.owned(conn, db.tasks, table["task_id"], account.id)
        preview = deepcopy(task["preview"])
        preview["tables"].append({"candidate_id": "other", "name": "其它候选", "rows": [{"values": {"c1": "keep"}}]})
        conn.execute(update(db.tasks).where(db.tasks.c.id == table["task_id"]).values(table_ids=[table["id"], other["id"]], preview=preview))
        file = service.owned(conn, db.files, table["file_id"], account.id)
    preview = deletion(account, table)
    assert preview["plan"]["plan"]["summary"]["source_file_shared"]
    assert not preview["plan"]["plan"]["summary"]["source_file_deleted"]
    table_actions.confirm_action(preview["plan"]["id"], verified(account))
    with db.engine.connect() as conn:
        assert service.owned(conn, db.data_tables, other["id"], account.id)["archived"]
        assert service.owned(conn, db.files, file["id"], account.id)
        task = service.owned(conn, db.tasks, task["id"], account.id)
        assert task["table_ids"] == [other["id"]]
        assert [candidate["candidate_id"] for candidate in task["preview"]["tables"]] == ["other"]
    assert (settings.upload_dir / file["stored_name"]).exists()


@pytest.mark.parametrize("kind,status", [("assistant", "queued"), ("assistant", "running"), ("export", "pending"),
    ("export", "running"), ("parse", "pending"), ("parse", "running")])
def test_delete_refuses_related_active_work_at_preview_and_confirmation(kind, status):
    account = Account()
    table = account.table()
    preview = deletion(account, table)
    with db.engine.begin() as conn:
        if kind == "assistant":
            message = service.save_message(conn, account.id, table["conversation_id"], "user", "尚在处理",
                request={"table_ids": [table["id"]]})
            conn.execute(insert(db.assistant_runs).values(id=db.new_id(), owner_id=account.id, conversation_id=table["conversation_id"],
                session_id=verified(account)["session_id"], message_id=message["id"], status=status, attempts=0,
                created_at=db.utcnow(), updated_at=db.utcnow()))
        elif kind == "parse":
            conn.execute(update(db.tasks).where(db.tasks.c.id == table["task_id"]).values(status=status))
        else:
            conn.execute(insert(db.tasks).values(id=db.new_id(), owner_id=account.id, kind="export", status=status,
                attempts=0, version=1, table_ids=[table["id"]], created_at=db.utcnow(), updated_at=db.utcnow()))
    expect_http(409, lambda: deletion(account, table))
    expect_http(409, lambda: table_actions.confirm_action(preview["plan"]["id"], verified(account)))
    assert len(rows(account, table)) == 4


def test_delete_refuses_new_history_scope_and_legacy_workflow_lock():
    account = Account()
    table = make_table(account, "范围变化", [("001", "甲", 80)])
    preview = deletion(account, table)
    service.execute_select(account.id, table["conversation_id"], QueryPlan(operation="select", table_id=table["id"], fields=["c3"]))
    expect_http(409, lambda: table_actions.confirm_action(preview["plan"]["id"], verified(account)))
    latest = deletion(account, table)
    with table_actions.owner_workflow_guard(account.id):
        expect_http(409, lambda: table_actions.confirm_action(latest["plan"]["id"], verified(account)))
    table_actions.confirm_action(latest["plan"]["id"], verified(account))


def test_checkpoint_cleanup_failure_rolls_back_all_database_deletion(monkeypatch):
    account = Account()
    table = account.table()
    queried = account.ask(table, {"operation": "select", "fields": ["c1", "c3"]})
    assert queried.status_code == 200, queried.text
    source_message = queried.json()["result"]["source_message_id"]
    thread = f"{account.id}:{source_message}"
    with db.engine.connect() as conn:
        file = service.owned(conn, db.files, table["file_id"], account.id)
    preview = deletion(account, table)
    original = PostgresSaver.delete_thread
    def failure(saver, thread_id):
        original(saver, thread_id)
        raise RuntimeError("isolated checkpoint cleanup failure")
    monkeypatch.setattr(PostgresSaver, "delete_thread", failure)
    with pytest.raises(RuntimeError, match="isolated checkpoint"):
        table_actions.confirm_action(preview["plan"]["id"], verified(account))
    assert len(rows(account, table)) == 4
    assert (settings.upload_dir / file["stored_name"]).exists()
    with db.engine.connect() as conn:
        assert service.owned(conn, db.plans, preview["plan"]["id"], account.id)["status"] == "pending"
        assert service.owned(conn, db.tasks, table["task_id"], account.id)
    with PostgresSaver.from_conn_string(workflow.checkpoint_url()) as saver:
        assert saver.get_tuple({"configurable": {"thread_id": thread}}) is not None


def test_file_cleanup_failure_is_reported_and_repeat_confirmation_finishes_it(monkeypatch):
    account = Account()
    table = account.table()
    with db.engine.connect() as conn:
        file = service.owned(conn, db.files, table["file_id"], account.id)
    preview = deletion(account, table)
    def denied(*args, **kwargs):
        raise PermissionError("isolated file cleanup denial")
    with monkeypatch.context() as scope:
        scope.setattr(Path, "unlink", denied)
        expect_http(503, lambda: table_actions.confirm_action(preview["plan"]["id"], verified(account)))
    with db.engine.connect() as conn:
        assert not conn.scalar(select(db.data_tables.c.id).where(db.data_tables.c.id == table["id"]))
        stored = service.owned(conn, db.plans, preview["plan"]["id"], account.id)
        assert stored["status"] == "executed" and not stored["plan"]["cleanup"]["completed"]
    assert (settings.upload_dir / file["stored_name"]).exists()
    replay = table_actions.confirm_action(preview["plan"]["id"], verified(account))
    assert replay["replayed"] and replay["plan"]["plan"]["cleanup"]["completed"]
    assert not (settings.upload_dir / file["stored_name"]).exists()


def test_api_preview_changes_confirm_and_empty_body_contract():
    account, other = Account(), Account()
    table = make_table(account, "普通表单接口", [("001", "甲", 80)])
    first = rows(account, table)[0]
    response = account.post(f"/api/tables/{table['id']}/rows/preview", json={"conversation_id": table["conversation_id"],
        "version": 1, "operation": "update", "row_id": first["id"], "values": {"c3": 90}})
    assert response.status_code == 200, response.text
    plan_id = response.json()["plan"]["id"]
    assert other.post(f"/api/table-actions/plans/{plan_id}/confirm").status_code == 404
    assert account.post(f"/api/table-actions/plans/{plan_id}/confirm", json={"values": {"c3": 100}}).status_code == 422
    confirmed = account.post(f"/api/plans/{plan_id}/confirm")
    assert confirmed.status_code == 200, confirmed.text
    changes = account.client.get(f"/api/tables/{table['id']}/changes?limit=1")
    assert changes.status_code == 200 and changes.json()["operations"][0]["summary"] == "修改记录 · 1 条"
    assert other.client.get(f"/api/tables/{table['id']}/changes").status_code == 404
    assert account.post(f"/api/tables/{table['id']}/rows/preview", json={"conversation_id": table["conversation_id"],
        "version": 2, "operation": "delete"}).status_code == 422
    deletion_preview = account.post(f"/api/tables/{table['id']}/delete/preview", json={"conversation_id": table["conversation_id"], "version": 2})
    assert deletion_preview.status_code == 200, deletion_preview.text
    deleted = account.post(f"/api/plans/{deletion_preview.json()['plan']['id']}/confirm")
    assert deleted.status_code == 200 and deleted.json()["deleted_table_id"] == table["id"]
    assert account.client.get(f"/api/tables/{table['id']}").status_code == 404


def test_legacy_message_guard_covers_message_save_and_workflow_entry(monkeypatch):
    account = Account()
    table = make_table(account, "同步消息保护", [("001", "甲", 80)])
    calls = []
    def workflow_entry(*args, **kwargs):
        with db.engine.begin() as conn:
            expect_http(409, lambda: table_actions.owner_action_lock(conn, account.id, exclusive=True))
        calls.append(True)
        return {"protected": True}
    monkeypatch.setattr(workflow, "invoke_saved_message", workflow_entry)
    response = workflow.run_message(verified(account), table["conversation_id"], ChatInput(content="查询", table_ids=[table["id"]]))
    assert calls == [True] and response == {"protected": True}


def test_nested_workflow_guards_share_one_autocommit_connection(monkeypatch):
    account = Account()
    original = db.engine.connect
    calls = []
    def connect(*args, **kwargs):
        calls.append(True)
        return original(*args, **kwargs)
    monkeypatch.setattr(db.engine, "connect", connect)
    with table_actions.owner_workflow_guard(account.id):
        with table_actions.owner_workflow_guard(account.id):
            assert len(calls) == 1


def test_unsafe_registered_cleanup_path_never_unlinks_outside_upload_root(tmp_path):
    account = Account()
    table = account.table()
    sentinel = tmp_path / "outside.txt"
    sentinel.write_text("must remain", encoding="utf-8")
    with db.engine.begin() as conn:
        conn.execute(update(db.files).where(db.files.c.id == table["file_id"]).values(stored_name=str(sentinel)))
    preview = deletion(account, table)
    error = expect_http(503, lambda: table_actions.confirm_action(preview["plan"]["id"], verified(account)))
    assert "路径无效" in error.detail
    assert sentinel.read_text(encoding="utf-8") == "must remain"
