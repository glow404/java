"""持久 LangGraph 回归：确认中断恢复、节点重放幂等及有限上下文。"""
import hashlib
import os

import pytest
from sqlalchemy import select, func
from langgraph.checkpoint.postgres import PostgresSaver

from app import db, service, workflow
from app.config import settings
from app.schemas import QueryPlan
from test_api import Account


@pytest.fixture(autouse=True)
def isolated_database():
    if os.getenv("VIBE_TEST_DATABASE") != "true" or db.engine.url.database != "vibe_test":
        pytest.skip("requires isolated database")


def verified_user(owner):
    return {"id": owner.id, "session_id": hashlib.sha256(owner.client.cookies.get(settings.cookie_name).encode()).hexdigest()}


def test_commit_before_checkpoint_replay_reuses_result_and_plan():
    owner = Account()
    table = owner.table()
    user = verified_user(owner)
    with db.engine.begin() as conn:
        message = service.save_message(conn, owner.id, table["conversation_id"], "user", "测试节点重放")
    plan = QueryPlan(operation="select", table_id=table["id"], fields=["c1", "c3"])
    first = service.execute_select(owner.id, table["conversation_id"], plan, message["id"])
    db.engine.dispose()
    replayed = service.execute_select(owner.id, table["conversation_id"], plan, message["id"])
    assert replayed["result"]["id"] == first["result"]["id"]
    assert replayed["message"]["id"] == first["message"]["id"]
    with db.engine.begin() as conn:
        source = service.save_message(conn, owner.id, table["conversation_id"], "user", "测试预览重放")
    change = QueryPlan(operation="update", table_id=table["id"], filters=[{"field_id": "c1", "op": "eq", "value": "001"}], values={"c3": 93})
    initial = service.preview_mutation(user, table["conversation_id"], change, source["id"], "saved-thread")
    again = service.preview_mutation(user, table["conversation_id"], change, source["id"], "saved-thread")
    assert again["plan"]["id"] == initial["plan"]["id"] and initial["plan"]["workflow_thread"] == "saved-thread"
    with db.engine.connect() as conn:
        assert conn.scalar(select(func.count()).select_from(db.results).where(db.results.c.source_message_id == message["id"])) == 1
        assert conn.scalar(select(func.count()).select_from(db.plans).where(db.plans.c.source_message_id == source["id"])) == 1


def test_real_checkpoint_interrupt_resumes_after_new_graph_and_engine():
    owner = Account()
    table = owner.table()
    reply = owner.ask(table, {"operation": "update", "filters": [{"field_id": "c1", "op": "eq", "value": "001"}], "values": {"c3": 94}})
    assert reply.status_code == 200, reply.text
    plan = reply.json()["plan"]
    thread = plan["workflow_thread"]
    with PostgresSaver.from_conn_string(workflow.checkpoint_url()) as saver:
        before = workflow.build_graph(saver).get_state({"configurable": {"thread_id": thread}})
        assert before.next == ("confirm",)
    db.engine.dispose()
    confirmed = owner.post(f"/api/plans/{plan['id']}/confirm")
    assert confirmed.status_code == 200 and "checkpoint_warning" not in confirmed.json()
    with PostgresSaver.from_conn_string(workflow.checkpoint_url()) as saver:
        after = workflow.build_graph(saver).get_state({"configurable": {"thread_id": thread}})
        assert after.next == ()
    assert owner.post("/api/auth/logout").status_code == 200
    assert owner.client.get("/api/catalog").status_code == 401


def test_metadata_samples_are_explicit_limited_and_account_scoped(monkeypatch):
    owner = Account()
    table = owner.table()
    state = {**verified_user(owner), "owner_id": owner.id, "conversation_id": table["conversation_id"],
             "message_id": "current", "content": "查看成绩", "table_ids": [table["id"]]}
    monkeypatch.setattr(settings, "model_allowed_data", True)
    monkeypatch.setattr(settings, "model_send_samples", False)
    assert "samples" not in workflow.metadata_node(state)["tables"][0]
    monkeypatch.setattr(settings, "model_send_samples", True)
    metadata = workflow.metadata_node(state)["tables"][0]
    assert len(metadata["samples"]) <= 3
    assert all(set(sample) == {"c3"} for sample in metadata["samples"])
    other = Account()
    with pytest.raises(Exception) as failure:
        workflow.metadata_node({**state, "owner_id": other.id})
    assert failure.value.status_code == 404


def test_unit_mismatch_scope_and_followup_clarification():
    owner = Account()
    table = owner.table()
    mismatch = owner.ask(table, {"operation": "select", "filters": [{"field_id": "c3", "op": "gte", "value": 80, "unit": "厘米"}]})
    assert mismatch.status_code == 422
    unclear = owner.post(f"/api/conversations/{table['conversation_id']}/messages", json={"content": "成绩高分学生占比", "table_ids": [table["id"]]})
    assert unclear.json().get("clarifications")
    clarified = owner.post(f"/api/conversations/{table['conversation_id']}/messages", json={"content": "阈值80，分母全部记录", "table_ids": [table["id"]]})
    assert clarified.status_code == 200, clarified.text
    assert clarified.json()["result"]["rows"][0]["denominator"] == 4
    other_table = owner.table()
    out_of_scope = owner.ask(table, {"operation": "select", "table_id": other_table["id"], "fields": ["c1"]})
    assert out_of_scope.status_code == 422
