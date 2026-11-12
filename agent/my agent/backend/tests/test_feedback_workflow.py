"""反馈工作流的真实 PostgreSQL 回归：规划、中止竞态、来源与账号级显示。

模型和 RQ 投递仅作自动化替身，LangGraph 检查点与数据事务真实执行。
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Event
import os
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update, func
from app import db, service, assistant_runs
from app.main import app, create_app
from app.schemas import QueryPlan
from app.model_adapter import ModelAdapter
from test_api import Account


@pytest.fixture(autouse=True)
def isolated_database(monkeypatch):
    if os.getenv("VIBE_TEST_DATABASE") != "true" or db.engine.url.database != "vibe_test":
        pytest.skip("requires isolated vibe_test database")
    # 保留任务状态在服务器；手工调用同一worker函数，避免真实队列与本测试竞争。
    monkeypatch.setattr(assistant_runs, "dispatch", lambda run: None)


def start(account, table, plan=None, content="请先解释下一步并等待我确认"):
    payload = {"content": content, "table_ids": table.get("table_ids", [table["id"]])}
    if plan is not None:
        payload["plan"] = {"table_id": table["id"], **plan}
    response = account.post(f"/api/conversations/{table['conversation_id']}/runs", json=payload)
    assert response.status_code == 200, response.text
    return response.json()


def process(account, run):
    assistant_runs.process_run(run["id"], account.id)
    response = account.client.get(f"/api/runs/{run['id']}")
    assert response.status_code == 200, response.text
    return response.json()


def planned(account, table, plan):
    run = start(account, table, plan)
    run = process(account, run)
    assert run["status"] == "completed", run
    assert "plan" in run["response"] and "result" not in run["response"]
    return run, run["response"]["plan"]


def mutation():
    return {"operation": "update", "filters": [{"field_id": "c1", "op": "eq", "value": "001"}], "values": {"c3": 99}}


def query():
    return {"operation": "select", "fields": ["c1", "c3"], "filters": [{"field_id": "c3", "op": "gte", "value": 80}]}


def count_for(table, column, identifier):
    with db.engine.connect() as conn:
        return conn.scalar(select(func.count()).select_from(table).where(column == identifier))


def score(table_id):
    with db.engine.connect() as conn:
        return conn.scalar(select(db.data_rows.c["values"]).where(db.data_rows.c.table_id == table_id,
            db.data_rows.c["values"]["c1"].astext == "001"))["c3"]


def test_async_query_previews_then_confirms_same_result_once():
    owner = Account()
    table = owner.table()
    run = start(owner, table, query())
    assert run["status"] == "queued" and "owner_id" not in run and "session_id" not in run
    assert count_for(db.results, db.results.c.conversation_id, table["conversation_id"]) == 0
    duplicate = owner.post(f"/api/conversations/{table['conversation_id']}/runs", json={"content": "又一个问题", "table_ids": [table["id"]]})
    assert duplicate.status_code == 409
    finished = process(owner, run)
    assert finished["status"] == "completed" and finished["response"]["plan"]["operation"] == "select"
    identifier = finished["response"]["plan"]["id"]
    assert count_for(db.results, db.results.c.conversation_id, table["conversation_id"]) == 0
    assert "确认后执行" in finished["response"]["plan"]["intent"]
    assert owner.post(f"/api/plans/{identifier}/confirm", json={"sql": "arbitrary"}).status_code == 422
    confirmed = owner.post(f"/api/plans/{identifier}/confirm")
    assert confirmed.status_code == 200, confirmed.text
    assert {row["c1"] for row in confirmed.json()["result"]["rows"]} == {"001", "003"}
    replayed = owner.post(f"/api/plans/{identifier}/confirm")
    assert replayed.status_code == 200 and replayed.json()["result"]["id"] == confirmed.json()["result"]["id"]
    assert count_for(db.results, db.results.c.source_message_id, run["message_id"]) == 1
    assert count_for(db.audits, db.audits.c.plan_id, identifier) == 1
    assistant_runs.process_run(run["id"], owner.id)
    assert count_for(db.plans, db.plans.c.source_message_id, run["message_id"]) == 1


@pytest.mark.parametrize("winner", ["confirm", "cancel"])
def test_cancel_confirm_race_linearizes_both_possible_outcomes(monkeypatch, winner):
    owner = Account()
    table = owner.table()
    run, plan = planned(owner, table, mutation())
    entered, release = Event(), Event()
    original = service.audit
    def paused_audit(conn, owner_id, action, table_id=None, plan_id=None, details=None):
        should_pause = (winner == "confirm" and action == "update" and plan_id == plan["id"]) or (
            winner == "cancel" and action == "assistant_cancelled" and (details or {}).get("run_id") == run["id"])
        if should_pause:
            # audit 在数据/计划事务内部；此处暂停能真实复现另一请求竞争同一run锁。
            entered.set()
            assert release.wait(10), "test failed to release transaction"
        return original(conn, owner_id, action, table_id, plan_id, details)
    monkeypatch.setattr(service, "audit", paused_audit)
    def confirm():
        return owner.post(f"/api/plans/{plan['id']}/confirm")
    def cancel():
        return owner.post(f"/api/runs/{run['id']}/cancel")
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(confirm if winner == "confirm" else cancel)
        try:
            assert entered.wait(5), "first transaction did not reach its audit"
            second = pool.submit(cancel if winner == "confirm" else confirm)
            with pytest.raises(TimeoutError):
                second.result(timeout=0.2)
        finally:
            release.set()
        first_answer, second_answer = first.result(timeout=10), second.result(timeout=10)
    assert first_answer.status_code == 200, first_answer.text
    assert second_answer.status_code == 409, second_answer.text
    assert score(table["id"]) == (99 if winner == "confirm" else 90)
    assert count_for(db.audits, db.audits.c.plan_id, plan["id"]) == (1 if winner == "confirm" else 0)
    stored = owner.client.get(f"/api/plans/{plan['id']}").json()
    assert stored["status"] == ("executed" if winner == "confirm" else "cancelled")


def test_cancel_running_model_after_release_never_saves_plan_or_result(monkeypatch):
    owner = Account()
    table = owner.table()
    entered, release = Event(), Event()
    def blocked_model(self, content, tables, history=None):
        entered.set()
        assert release.wait(10), "test failed to release model substitute"
        return QueryPlan(operation="select", table_id=table["id"], fields=["c1", "c3"])
    monkeypatch.setattr(ModelAdapter, "plan", blocked_model)
    run = start(owner, table)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(assistant_runs.process_run, run["id"], owner.id)
        try:
            assert entered.wait(5)
            cancelled = owner.post(f"/api/runs/{run['id']}/cancel")
            assert cancelled.status_code == 200 and cancelled.json()["status"] == "cancelled"
        finally:
            release.set()
        pending.result(timeout=10)
    assert owner.client.get(f"/api/runs/{run['id']}").json()["status"] == "cancelled"
    assert count_for(db.plans, db.plans.c.source_message_id, run["message_id"]) == 0
    assert count_for(db.results, db.results.c.source_message_id, run["message_id"]) == 0


def test_queued_cancel_is_idempotent_and_worker_cannot_begin(monkeypatch):
    owner = Account()
    table = owner.table()
    def unexpected_model(*args, **kwargs):
        pytest.fail("a cancelled queued run must not call the model")
    monkeypatch.setattr(ModelAdapter, "plan", unexpected_model)
    run = start(owner, table)
    assert owner.post(f"/api/runs/{run['id']}/cancel").status_code == 200
    assert owner.post(f"/api/runs/{run['id']}/cancel").status_code == 200
    assistant_runs.process_run(run["id"], owner.id)
    assert count_for(db.plans, db.plans.c.source_message_id, run["message_id"]) == 0
    messages = owner.client.get(f"/api/conversations/{table['conversation_id']}").json()["messages"]
    assert len([message for message in messages if "已中止" in message["content"]]) == 1


def test_followup_replaces_pending_plan_and_can_change_or_cancel_new_intent():
    owner = Account()
    table = owner.table()
    old_run, old_plan = planned(owner, table, mutation())
    next_run = start(owner, table, query(), "改成只查看80分以上，取消刚才的修改")
    assert owner.client.get(f"/api/plans/{old_plan['id']}").json()["status"] == "cancelled"
    assert owner.post(f"/api/plans/{old_plan['id']}/confirm").status_code == 409
    new_run = process(owner, next_run)
    new_plan = new_run["response"]["plan"]
    assert new_plan["id"] != old_plan["id"] and new_plan["operation"] == "select"
    assert score(table["id"]) == 90
    assert owner.post(f"/api/plans/{new_plan['id']}/cancel").status_code == 200
    assert owner.post(f"/api/plans/{new_plan['id']}/confirm").status_code == 409


def test_registered_synonyms_compile_into_real_field_ids(monkeypatch):
    owner = Account()
    table = owner.table()
    monkeypatch.setattr(ModelAdapter, "plan", lambda self, *args: QueryPlan(operation="select", table_id=table["id"],
        fields=["学生编号", "得分"], filters=[{"field_id": "分数", "op": "gte", "value": 80}], sort=[{"field_id": "得分", "direction": "desc"}]))
    run = process(owner, start(owner, table, content="查看得分至少80的学生编号"))
    assert run["status"] == "completed", run
    plan = run["response"]["plan"]
    assert plan["plan"]["fields"] == ["c1", "c3"] and plan["plan"]["filters"][0]["field_id"] == "c3"
    confirmed = owner.post(f"/api/plans/{plan['id']}/confirm")
    assert confirmed.status_code == 200 and [row["c3"] for row in confirmed.json()["result"]["rows"]] == [90, 80]


@pytest.mark.parametrize("ambiguous", [False, True])
def test_unknown_or_ambiguous_model_field_becomes_clarification(monkeypatch, ambiguous):
    owner = Account()
    table = owner.table()
    reference = "分数" if ambiguous else "未登记指标"
    if ambiguous:
        with db.engine.begin() as conn:
            fields = [dict(field) for field in table["fields"]]
            fields[1].update(name="加分", aliases=["分数"])
            conn.execute(update(db.data_tables).where(db.data_tables.c.id == table["id"]).values(fields=fields))
    monkeypatch.setattr(ModelAdapter, "plan", lambda self, *args: QueryPlan(operation="select", table_id=table["id"], fields=[reference]))
    run = process(owner, start(owner, table, content=f"查看{reference}"))
    assert run["status"] == "completed" and run["response"].get("clarifications"), run
    assert ("多个字段" if ambiguous else "未找到") in run["response"]["clarifications"][0]
    assert count_for(db.plans, db.plans.c.source_message_id, run["message_id"]) == 0


@pytest.mark.parametrize("source", ["structured", "literal", "inference"])
def test_metadata_preview_confirmation_keeps_literal_and_inference_origin(monkeypatch, source):
    owner = Account()
    table = owner.table()
    description = "期末百分制考试分数" if source != "inference" else "模型根据字段用途补充的推断说明"
    metadata = {"description": description, "field_changes": [{"field_id": "c3", "description": description}]}
    if source == "inference":
        metadata["field_changes"][0]["type"] = "integer"
    model_plan = QueryPlan(operation="metadata", table_id=table["id"], metadata=metadata)
    monkeypatch.setattr(ModelAdapter, "plan", lambda self, *args: model_plan)
    run = start(owner, table, model_plan.model_dump() if source == "structured" else None,
        content=f"把表与成绩说明改成：{description}" if source == "literal" else "请补充合适的说明和字段类型")
    completed = process(owner, run)
    assert completed["status"] == "completed", completed
    plan = completed["response"]["plan"]
    expected = "confirmed_inference" if source == "inference" else "user"
    preview_fields = plan["after"][0]["values"]["fields"]
    assert next(field for field in preview_fields if field["id"] == "c3")["provenance"] == expected
    before = owner.client.get(f"/api/tables/{table['id']}").json()
    assert before["description"] != description
    confirmation = owner.post(f"/api/plans/{plan['id']}/confirm")
    assert confirmation.status_code == 200, confirmation.text
    after = owner.client.get(f"/api/tables/{table['id']}").json()
    assert after["description_provenance"] == expected and after["description"] == description
    field = next(field for field in after["fields"] if field["id"] == "c3")
    assert field["provenance"] == expected and field["description"] == description
    if source == "inference":
        assert field["type_provenance"] == "confirmed_inference" and field["type"] == "integer"


def test_visibility_persists_account_wide_without_erasing_result_or_audit():
    owner, other = Account(), Account()
    table = owner.table()
    _, plan = planned(owner, table, query())
    confirmed = owner.post(f"/api/plans/{plan['id']}/confirm").json()
    identifier = confirmed["result"]["id"]
    second = owner.post("/api/conversations", json={"title": "另一个对话"}).json()
    endpoint = f"/api/results/{identifier}/visibility"
    assert other.client.delete(endpoint, headers={"X-CSRF-Token": other.csrf}).status_code == 404
    assert TestClient(app).delete(endpoint).status_code == 401
    assert owner.client.delete(endpoint, headers={"X-CSRF-Token": owner.csrf}).status_code == 200
    assert owner.client.delete(endpoint, headers={"X-CSRF-Token": owner.csrf}).status_code == 200
    assert identifier in owner.client.get("/api/catalog").json()["hidden_result_ids"]
    assert identifier in owner.client.get(f"/api/conversations/{second['id']}").json()["hidden_result_ids"]
    db.engine.dispose()
    reopened = TestClient(create_app())
    reopened.cookies.update(owner.client.cookies)
    assert identifier in reopened.get("/api/catalog").json()["hidden_result_ids"]
    assert reopened.get(f"/api/results/{identifier}").json()["rows"] == confirmed["result"]["rows"]
    assert count_for(db.audits, db.audits.c.plan_id, plan["id"]) == 1
    assert owner.post(endpoint).status_code == 200
    assert identifier not in owner.client.get("/api/catalog").json()["hidden_result_ids"]
    assert identifier not in owner.client.get(f"/api/conversations/{table['conversation_id']}").json()["hidden_result_ids"]
    assert count_for(db.results, db.results.c.id, identifier) == 1
    actions = [item["action"] for item in owner.client.get("/api/history").json()["operations"]]
    assert actions.count("result_hidden") == 1 and actions.count("result_restored") == 1 and "query" in actions


def test_rename_conversation_is_persisted_and_scoped():
    owner, other = Account(), Account()
    table = owner.table()
    path = f"/api/conversations/{table['conversation_id']}"
    assert owner.patch(path, json={"title": "   "}).status_code == 422
    assert other.patch(path, json={"title": "越权名称"}).status_code == 404
    assert TestClient(app).patch(path, json={"title": "匿名名称"}).status_code == 401
    renamed = owner.patch(path, json={"title": "  本周成绩复核  "})
    assert renamed.status_code == 200 and renamed.json()["title"] == "本周成绩复核"
    db.engine.dispose()
    assert owner.client.get(path).json()["title"] == "本周成绩复核"
    assert "conversation_renamed" in [operation["action"] for operation in owner.client.get("/api/history").json()["operations"]]


def test_async_resources_recheck_owner_conversation_table_and_session_expiry():
    owner, other = Account(), Account()
    table, foreign = owner.table(), other.table()
    run = start(owner, table, query())
    paths = [f"/api/runs/{run['id']}", f"/api/conversations/{table['conversation_id']}/runs"]
    for path in paths:
        assert other.client.get(path, headers={"X-Owner-Id": owner.id}).status_code == 404
        assert TestClient(app).get(path).status_code == 401
    assert other.post(f"/api/runs/{run['id']}/cancel").status_code == 404
    assert TestClient(app).post(f"/api/runs/{run['id']}/cancel").status_code == 401
    assert other.post(f"/api/conversations/{table['conversation_id']}/runs", json={"content": "读取", "table_ids": [table["id"]]}).status_code == 404
    assert other.post(f"/api/conversations/{foreign['conversation_id']}/runs", json={"content": "读取", "table_ids": [table["id"]]}).status_code == 404
    assert other.post(f"/api/conversations/{foreign['conversation_id']}/runs", json={"content": "读取", "table_ids": [foreign["id"]], "owner_id": owner.id}).status_code == 422
    with db.engine.begin() as conn:
        conn.execute(update(db.sessions).where(db.sessions.c.owner_id == owner.id).values(expires_at=db.utcnow() - timedelta(seconds=1)))
    assistant_runs.process_run(run["id"], owner.id)
    with db.engine.connect() as conn:
        stored = service.owned(conn, db.assistant_runs, run["id"], owner.id)
        assert stored["status"] == "failed" and "会话" in stored["error"]
    assert count_for(db.plans, db.plans.c.source_message_id, run["message_id"]) == 0


def test_queue_outage_retains_request_and_recovery_keeps_single_source(monkeypatch):
    owner = Account()
    table = owner.table()
    def unavailable(*args):
        raise ConnectionError("synthetic queue unavailable")
    monkeypatch.setattr(assistant_runs, "dispatch", unavailable)
    run = start(owner, table, query())
    saved = owner.client.get(f"/api/runs/{run['id']}").json()
    assert saved["status"] == "queued" and "队列" in saved["error"]
    assert count_for(db.messages, db.messages.c.id, run["message_id"]) == 1
    monkeypatch.setattr(assistant_runs, "dispatch", lambda run: None)
    assistant_runs.recover()
    completed = process(owner, run)
    assert completed["status"] == "completed"
    assistant_runs.process_run(run["id"], owner.id)
    assert count_for(db.plans, db.plans.c.source_message_id, run["message_id"]) == 1


@pytest.mark.parametrize("change", [None, "right_version", "same_count_key_swap"])
def test_join_query_preview_contains_verified_keys_cardinality_and_rechecks_scope(change):
    owner = Account()
    left, right = owner.table(), owner.table()
    preview = owner.post("/api/relations/preview", json={"conversation_id": left["conversation_id"],
        "left_table_id": left["id"], "right_table_id": right["id"], "left_field_id": "c1", "right_field_id": "c1", "join_type": "left"})
    assert preview.status_code == 200, preview.text
    relation_plan = preview.json()["plan"]
    assert owner.post(f"/api/plans/{relation_plan['id']}/confirm").status_code == 200
    relation_id = relation_plan["plan"]["relation_id"]
    selected = {**left, "table_ids": [left["id"], right["id"]]}
    _, query_plan = planned(owner, selected, {"operation": "select", "relation_id": relation_id,
        "fields": ["left__c1", "right__c3"]})
    snapshot = query_plan["after"][0]["values"]
    assert snapshot["left_table_id"] == left["id"] and snapshot["right_table_id"] == right["id"]
    assert snapshot["left_version"] == snapshot["right_version"] == 1
    assert snapshot["left_field"] == snapshot["right_field"] == "学号"
    assert snapshot["statistics"]["cardinality"] == "one_to_one" and snapshot["statistics"]["joined_rows"] == 4
    if change:
        with db.engine.begin() as conn:
            if change == "right_version":
                conn.execute(update(db.data_tables).where(db.data_tables.c.id == right["id"]).values(version=2))
            else:
                rows = [dict(row) for row in conn.execute(select(db.data_rows).where(db.data_rows.c.table_id == right["id"],
                    db.data_rows.c["values"]["c1"].astext.in_(["001", "002"]))).mappings()]
                for row in rows:
                    conn.execute(update(db.data_rows).where(db.data_rows.c.id == row["id"]).values(
                        values={**row["values"], "c1": "002" if row["values"]["c1"] == "001" else "001"}))
        confirmed = owner.post(f"/api/plans/{query_plan['id']}/confirm")
        assert confirmed.status_code == 409, confirmed.text
        assert count_for(db.results, db.results.c.source_message_id, query_plan["source_message_id"]) == 0
        assert owner.client.get(f"/api/plans/{query_plan['id']}").json()["status"] == "pending"
    else:
        confirmed = owner.post(f"/api/plans/{query_plan['id']}/confirm")
        assert confirmed.status_code == 200, confirmed.text
        result = confirmed.json()["result"]
        assert [row["left__c1"] for row in result["rows"]] == ["001", "002", "003", "004"]
        assert result["query_snapshot"]["relation_id"] == relation_id
        assert owner.post(f"/api/plans/{query_plan['id']}/confirm").json()["result"]["id"] == result["id"]
