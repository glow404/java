"""真实 PostgreSQL 口径建议回归：先预览、用户补充优先、极值对应行与历史兼容。

模型输出与队列投递是明确的自动化替身，不代表真实中转站验收。
"""

import os
import pytest
from sqlalchemy import insert, update, select, func
from app import db, workflow, assistant_runs
from app.model_adapter import ModelAdapter
from app.schemas import QueryPlan
from test_api import Account
from test_feedback_workflow import start, process


@pytest.fixture(autouse=True)
def isolated_database(monkeypatch):
    if os.getenv("VIBE_TEST_DATABASE") != "true" or db.engine.url.database != "vibe_test":
        pytest.skip("requires isolated vibe_test database")
    monkeypatch.setattr(assistant_runs, "dispatch", lambda run: None)


def rates(owner):
    table = owner.table()
    names = ["threshold", "unlock_threshold", "match_score_threshold", "frr", "far"]
    fields = [{"id": f"c{index + 1}", "name": name, "type": "number", "unit": "", "description": "", "provenance": "user"} for index, name in enumerate(names)]
    annotated = [(0.1, 0.8, 0.6, 0.01, 0.002), (0.2, 0.85, 0.7, 0.02, 0.002),
                 (0.3, 0.9, 0.8, 0.04, 0.0001), (0.4, 0.95, 0.9, 0.03, 0.00005), (0.5, 0.99, 1, 0.01, None)]
    with db.engine.begin() as conn:
        old = list(conn.execute(select(db.data_rows).where(db.data_rows.c.table_id == table["id"]).order_by(db.data_rows.c.position)).mappings())
        for index, values in enumerate(annotated):
            stored = dict(zip([field["id"] for field in fields], values))
            if index < len(old):
                conn.execute(update(db.data_rows).where(db.data_rows.c.id == old[index]["id"]).values(values=stored))
            else:
                conn.execute(insert(db.data_rows).values(id=db.new_id(), owner_id=owner.id, table_id=table["id"],
                    values=stored, source={"fixture": "manual FRR/FAR annotation"}, position=index))
        conn.execute(update(db.data_tables).where(db.data_tables.c.id == table["id"]).values(fields=fields, row_count=len(annotated)))
    return {**table, "fields": fields, "row_count": len(annotated)}


def candidate(table):
    return QueryPlan(operation="select", table_id=table["id"], fields=["c2", "c4", "c5"],
        filters=[{"field_id": "c4", "op": "lt", "value": 3, "unit": "%"}],
        extremum={"field_id": "c5", "direction": "min"}, assumptions=[
            {"subject": "FAR最优值", "choice": "取最小值", "reason": "提出待确认的优化方向", "alternatives": ["改取最大值"]}])


def test_range_hint_is_aggregate_candidate_without_raw_samples():
    owner = Account()
    table = rates(owner)
    result = workflow.metadata_node({"owner_id": owner.id, "conversation_id": table["conversation_id"],
        "message_id": "not-saved", "content": "frr<3%时far最优值", "table_ids": [table["id"]]})
    fields = {field["name"]: field for field in result["tables"][0]["fields"]}
    assert "samples" not in result["tables"][0]
    assert "rate_storage_hint" not in fields["unlock_threshold"]
    hint = fields["frr"]["rate_storage_hint"]
    assert hint["candidate"] == "ratio" and hint["certainty"] == "candidate_only"
    assert "候选" in hint["basis"] and "也可能" in hint["basis"]
    assert not {"min", "max", "rows", "values"}.intersection(hint)


def test_suggested_scope_is_saved_without_execution_and_confirm_returns_ties(monkeypatch):
    owner = Account()
    table = rates(owner)
    monkeypatch.setattr(ModelAdapter, "plan", lambda self, *args: candidate(table))
    finished = process(owner, start(owner, table, content="frr<3%时far最优值及阈值是多少？"))
    assert finished["status"] == "completed", finished
    plan = finished["response"]["plan"]
    assert plan["plan"]["filters"][0]["value"] == pytest.approx(0.03)
    assert "建议口径（待确认）" in plan["intent"]
    assert any("范围" in item["reason"] and "候选" in item["reason"] for item in plan["plan"]["assumptions"])
    assert owner.client.get(f"/api/conversations/{table['conversation_id']}").json()["results"] == []
    saved = owner.client.get(f"/api/plans/{plan['id']}").json()
    assert saved["plan"]["assumptions"] == plan["plan"]["assumptions"]
    first = owner.post(f"/api/plans/{plan['id']}/confirm")
    assert first.status_code == 200, first.text
    result = first.json()["result"]
    assert [row["c2"] for row in result["rows"]] == [0.8, 0.85]
    assert all(row["c5"] == 0.002 for row in result["rows"])
    second = owner.post(f"/api/plans/{plan['id']}/confirm").json()
    assert second["result"]["id"] == result["id"]
    assert second["result"]["query_snapshot"] == result["query_snapshot"]
    assert all(field["unit"] == "" for field in owner.client.get(f"/api/tables/{table['id']}").json()["fields"])


def test_explicit_followup_overrides_old_candidate_and_cancels_old_plan(monkeypatch):
    owner = Account()
    table = rates(owner)
    monkeypatch.setattr(ModelAdapter, "plan", lambda self, *args: candidate(table))
    first = process(owner, start(owner, table, content="frr<3%时far最优值是多少？"))["response"]["plan"]
    def wrong_old_candidate(self, *args):
        plan = candidate(table)
        plan.filters[0].storage_unit = "percent"
        return plan
    monkeypatch.setattr(ModelAdapter, "plan", wrong_old_candidate)
    followup = "对应的阈值指的是unlock_threshold（c2），far的最优值是最小值，frr字段的存储单位：小数比例。"
    finished = process(owner, start(owner, table, content=followup))
    assert finished["status"] == "completed", finished
    plan = finished["response"]["plan"]
    assert plan["plan"]["filters"][0]["storage_unit"] == "ratio"
    assert plan["plan"]["filters"][0]["value"] == pytest.approx(0.03)
    assert any("用户已明确" in item["reason"] for item in plan["plan"]["assumptions"])
    assert owner.post(f"/api/plans/{first['id']}/confirm").status_code == 409
    confirmed = owner.post(f"/api/plans/{plan['id']}/confirm")
    assert confirmed.status_code == 200 and len(confirmed.json()["result"]["rows"]) == 2


@pytest.mark.parametrize("kind,value,unit,storage,expected", [
    ("filter", 57, "%", "ratio", 0.57),
    ("filter", 57, "%", "percent", 57),
    ("ratio", 57, "%", "ratio", 0.57),
    ("ratio", 0.57, "ratio", "percent", 57),
])
def test_storage_suggestion_preserves_actual_preview_boundary(monkeypatch, kind, value, unit, storage, expected):
    owner = Account()
    table = rates(owner)
    if kind == "filter":
        proposed = candidate(table)
        proposed.filters[0].value, proposed.filters[0].unit = value, unit
        content = "frr<57%时far最优值及阈值是多少？"
    else:
        proposed = QueryPlan(operation="select", table_id=table["id"], ratio={
            "field_id": "c4", "threshold": value, "unit": unit, "denominator": "non_null"})
        content = f"统计frr达标率，阈值{value}，分母为非空记录。"
    if storage == "percent":
        content += "frr存储单位：百分数。"
    monkeypatch.setattr(ModelAdapter, "plan", lambda self, *args: proposed)
    finished = process(owner, start(owner, table, content=content))
    assert finished["status"] == "completed", finished
    preview = finished["response"]["plan"]
    normalized = preview["plan"]["filters"][0] if kind == "filter" else preview["plan"]["ratio"]
    assert normalized["value" if kind == "filter" else "threshold"] == pytest.approx(expected)
    assert normalized["storage_unit"] == storage
    suggestion = next(item for item in preview["plan"]["assumptions"] if item["subject"] == "服务器校验：frr 存储单位")
    assert suggestion["choice"] == ("小数比例" if storage == "ratio" else "百分数")
    # 多次校验后的真实边界与建议一起持久保存，不能在用户核对的预览中混入别的问题数值。
    assert "3%" not in preview["intent"]
    expected_step = f"过滤：frr lt {expected}" if kind == "filter" else f"占比阈值 {expected}"
    assert expected_step in preview["intent"]
    saved = owner.client.get(f"/api/plans/{preview['id']}").json()
    assert saved["plan"] == preview["plan"] and saved["intent"] == preview["intent"]
    assert owner.client.get(f"/api/conversations/{table['conversation_id']}").json()["results"] == []


def test_incompatible_model_unit_becomes_actionable_clarification(monkeypatch):
    owner = Account()
    table = rates(owner)
    plan = candidate(table)
    plan.filters[0].unit = "厘米"
    monkeypatch.setattr(ModelAdapter, "plan", lambda self, *args: plan)
    finished = process(owner, start(owner, table, content="frr小于3厘米"))
    assert finished["status"] == "completed" and finished["response"]["clarifications"], finished
    assert "单位" in finished["response"]["clarifications"][0]
    assert not finished["response"].get("plan")
    with db.engine.connect() as conn:
        assert conn.scalar(select(func.count()).select_from(db.results).where(db.results.c.conversation_id == table["conversation_id"])) == 0


def test_legacy_messages_cannot_execute_proposed_assumption_without_confirmation():
    owner = Account()
    table = rates(owner)
    proposed = candidate(table)
    proposed.filters[0].storage_unit = "ratio"
    reply = owner.ask(table, proposed.model_dump())
    assert reply.status_code == 200, reply.text
    assert reply.json().get("plan") and not reply.json().get("result")
    assert owner.client.get(f"/api/conversations/{table['conversation_id']}").json()["results"] == []


def test_latest_explicit_context_wins_and_assistant_options_are_not_user_choices():
    from app.interpretation import explicit_scale
    field = {"id": "c4", "name": "frr"}
    history = [{"role": "user", "content": "frr存储单位为百分数"},
        {"role": "assistant", "content": "请确认frr字段单位小数比例还是百分数"}]
    assert explicit_scale(field, "frr字段的存储单位：小数比例", history) == "ratio"
    assert explicit_scale(field, "继续", history) == "percent"
    assert explicit_scale(field, "继续", history[1:]) is None


@pytest.mark.parametrize("content", ["frr存储单位不是百分数，是小数比例", "frr存储单位可能为小数比例", "frr单位是否为小数比例？",
    "frr存储单位为小数比例吗？", "frr存储单位为小数比例，但我不确定", "frr存储单位为小数比例还是百分数？"])
def test_uncertain_or_negative_statement_is_not_misrepresented_as_user_confirmation(content):
    from app.interpretation import explicit_scale
    assert explicit_scale({"id": "c4", "name": "frr"}, content,
        [{"role": "user", "content": "frr存储单位为百分数"}]) is None


def test_last_clear_correction_in_same_message_has_priority():
    from app.interpretation import explicit_scale
    field = {"id": "c4", "name": "frr"}
    assert explicit_scale(field, "frr存储单位为百分数，刚才说错了，frr存储单位为小数比例。", []) == "ratio"
    assert explicit_scale(field, "frr单位为小数比例，c4单位为百分数。", []) == "percent"
    assert explicit_scale(field, "frr单位为小数比例，frr单位是否为百分数？", []) is None
