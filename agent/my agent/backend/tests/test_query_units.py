"""比例单位与对应行极值回归：规范化可重放，真实 SQL 保留并列且隔离账号。

只在独立 vibe_test 数据库创建脱敏测试账号，不修改应用表或文件。
"""

from copy import deepcopy
from hashlib import sha256
import os
import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import insert, select, update
from app import db
from app.config import settings
from app.operation_preview import preview_operation, confirm_operation
from app.query import execute_query, proportional_unit, validate_plan
from app.schemas import QueryPlan
from test_api import Account


@pytest.fixture(autouse=True)
def isolated_database():
    if os.getenv("VIBE_TEST_DATABASE") != "true" or db.engine.url.database != "vibe_test":
        pytest.skip("requires isolated vibe_test database")


def registry(unit="", type_name="number"):
    return {"id": "unit_test", "owner_id": "owner_test", "fields": [
        {"id": "c1", "name": "frr", "type": type_name, "unit": unit}], "archived": False}


def filtered(*, value=3, unit="%", storage_unit=None):
    return QueryPlan(operation="select", table_id="unit_test", filters=[
        {"field_id": "c1", "op": "lt", "value": value, "unit": unit, "storage_unit": storage_unit}])


@pytest.mark.parametrize("source,expected", [("％", "percent"), (" Percentage ", "percent"),
    ("百分数", "percent"), ("小数比例", "ratio"), ("无量纲", "ratio"), ("1", "ratio"), ("厘米", None)])
def test_only_explicit_proportional_unit_spellings(source, expected):
    assert proportional_unit(source) == expected


@pytest.mark.parametrize("registered,value,source,target,expected", [
    ("小数比例", 3, "%", None, 0.03), ("ratio", 3, "百分数", "ratio", 0.03),
    ("%", 0.03, "ratio", None, 3), ("百分数", 0.03, "小数比例", "percent", 3),
    ("无量纲", 0.03, "ratio", None, 0.03), ("%", 3, None, None, 3)])
def test_proportional_conversion_and_serialized_revalidation_is_idempotent(registered, value, source, target, expected):
    plan, table = filtered(value=value, unit=source, storage_unit=target), registry(registered)
    for _ in range(3):
        validate_plan(plan, table)
        assert plan.filters[0].value == pytest.approx(expected)
        assert plan.filters[0].unit == registered
        assert not plan.assumptions
        # 预览入库、用户确认、工作流重放均会重新构造计划；不能重复除以 100。
        plan = QueryPlan.model_validate(plan.model_dump())


@pytest.mark.parametrize("unit,value,storage,expected", [("ratio", 0.03, None, 0.03),
    ("小数比例", 0.03, None, 0.03), ("%", 3, "ratio", 0.03), ("ratio", 0.03, "percent", 3)])
def test_blank_registry_uses_only_explicit_candidate_and_does_not_modify_metadata(unit, value, storage, expected):
    table = registry()
    original = deepcopy(table)
    plan = filtered(value=value, unit=unit, storage_unit=storage)
    for _ in range(3):
        validate_plan(plan, table)
        assert plan.filters[0].value == pytest.approx(expected)
        assert plan.filters[0].unit == ""
        assert len(plan.assumptions) == 1
        assert "登记单位为空" in plan.assumptions[0].reason
        assert "不修改字段资料" in plan.assumptions[0].reason
        assert plan.assumptions[0].alternatives
        plan = QueryPlan.model_validate(plan.model_dump())
    assert table == original


@pytest.mark.parametrize("registered,source,storage,type_name", [("", "%", None, "number"),
    ("分", "%", None, "number"), ("ratio", "厘米", None, "number"),
    ("ratio", "%", "percent", "number"), ("%", "ratio", "ratio", "number"),
    ("", "厘米", "ratio", "number"), ("", "ratio", "ratio", "string")])
def test_conflicting_unknown_or_unproven_units_are_rejected(registered, source, storage, type_name):
    plan = filtered(unit=source, storage_unit=storage)
    with pytest.raises(HTTPException) as error:
        validate_plan(plan, registry(registered, type_name))
    assert error.value.status_code == 422


def test_in_values_and_ratio_threshold_use_same_unit_contract():
    table = registry("ratio")
    plan = QueryPlan(operation="select", table_id="unit_test", filters=[
        {"field_id": "c1", "op": "in", "value": [1, 3, None], "unit": "%"}])
    validate_plan(plan, table)
    assert plan.filters[0].value == [0.01, 0.03, None]
    ratio = QueryPlan(operation="select", table_id="unit_test", ratio={
        "field_id": "c1", "threshold": 3, "denominator": "non_null", "unit": "%"})
    validate_plan(ratio, table)
    assert ratio.ratio.threshold == pytest.approx(0.03)
    validate_plan(ratio, table)
    assert ratio.ratio.threshold == pytest.approx(0.03)
    for invalid in (True, float("inf"), "not_a_number"):
        with pytest.raises(HTTPException):
            validate_plan(filtered(value=invalid), table)


def rate_table(account):
    table = account.table()
    fields = [{"id": "c1", "name": "unlock_threshold", "type": "number", "unit": ""},
        {"id": "c2", "name": "far", "type": "number", "unit": "ratio"},
        {"id": "c3", "name": "frr", "type": "number", "unit": "ratio"}]
    annotated = [(10, 0.02, 0.02), (20, 0.01, 0.025), (30, 0.01, 0.01), (40, 0.0001, 0.03)]
    with db.engine.begin() as conn:
        conn.execute(update(db.data_tables).where(db.data_tables.c.id == table["id"], db.data_tables.c.owner_id == account.id).values(fields=fields))
        rows = conn.execute(select(db.data_rows).where(db.data_rows.c.table_id == table["id"], db.data_rows.c.owner_id == account.id)
            .order_by(db.data_rows.c.position, db.data_rows.c.id)).mappings().all()
        for row, values in zip(rows, annotated):
            conn.execute(update(db.data_rows).where(db.data_rows.c.id == row["id"]).values(values=dict(zip(["c1", "c2", "c3"], values))))
        for index, values in enumerate([(50, 0.00001, None), (60, None, 0.01)], len(rows)):
            conn.execute(insert(db.data_rows).values(id=db.new_id(), table_id=table["id"], owner_id=account.id,
                values=dict(zip(["c1", "c2", "c3"], values)), source={"fixture": "人工标注极值并列/空值"}, position=index))
        conn.execute(update(db.data_tables).where(db.data_tables.c.id == table["id"]).values(row_count=6))
        return {**dict(conn.execute(select(db.data_tables).where(db.data_tables.c.id == table["id"])).mappings().one()), "conversation_id": table["conversation_id"]}


def rate_plan(table, **kwargs):
    return QueryPlan(operation="select", table_id=table["id"], fields=["c1", "c2", "c3"],
        filters=[{"field_id": "c3", "op": "lt", "value": 3, "unit": "%"}],
        extremum={"field_id": "c2", "direction": "min"}, **kwargs)


def test_real_extremum_filters_scope_ties_nulls_and_parameterized_snapshot():
    account = Account()
    table = rate_table(account)
    other_table = rate_table(account)
    foreign = Account()
    foreign_table = rate_table(foreign)
    with db.engine.begin() as conn:
        # 同账号另一张表和另一账号均有更小值；两个范围都不能影响本表的极值。
        for identifier in (other_table["id"], foreign_table["id"]):
            conn.execute(update(db.data_rows).where(db.data_rows.c.table_id == identifier).values(values={"c1": 0, "c2": 0.000001, "c3": 0.01}))
        columns, rows, chart, explanation, snapshot = execute_query(conn, rate_plan(table), table, account.id)
    assert [float(row["c1"]) for row in rows] == [20, 30]
    assert [float(row["c2"]) for row in rows] == [0.01, 0.01]
    assert [column["id"] for column in columns] == ["c1", "c2", "c3"]
    assert "排除该字段空值" in explanation and "所有并列最优记录" in explanation
    assert "min(" in snapshot["parameterized_sql"].lower()
    assert account.id in snapshot["bindings"].values() and table["id"] in snapshot["bindings"].values()
    assert foreign.id not in snapshot["bindings"].values() and other_table["id"] not in snapshot["bindings"].values()
    assert snapshot["plan"]["filters"][0]["value"] == pytest.approx(0.03)
    assert foreign.ask({"id": table["id"], "conversation_id": foreign_table["conversation_id"]}, rate_plan(table).model_dump()).status_code == 404


def test_extremum_pagination_max_empty_and_all_null():
    account = Account()
    table = rate_table(account)
    with db.engine.begin() as conn:
        rows = execute_query(conn, rate_plan(table, limit=1, offset=1), table, account.id)[1]
        assert [float(row["c1"]) for row in rows] == [30]
        maximum = rate_plan(table)
        maximum.extremum.direction = "max"
        assert float(execute_query(conn, maximum, table, account.id)[1][0]["c1"]) == 10
        empty = rate_plan(table)
        empty.filters[0].op = "gt"
        empty.filters[0].value, empty.filters[0].unit = 1, "ratio"
        _, rows, _, explanation, snapshot = execute_query(conn, empty, table, account.id)
        assert rows == []
        assert snapshot["extremum_diagnostic"] == {"reason": "no_matching_rows", "matching_rows": 0,
            "non_null_metric_rows": 0, "metric_field_id": "c2"}
        assert "没有符合过滤条件的记录" in explanation
        nulls = QueryPlan(operation="select", table_id=table["id"], filters=[{"field_id": "c2", "op": "is_null"}], extremum={"field_id": "c2", "direction": "min"})
        _, rows, _, explanation, snapshot = execute_query(conn, nulls, table, account.id)
        assert rows == []
        assert snapshot["extremum_diagnostic"] == {"reason": "metric_missing", "matching_rows": 1,
            "non_null_metric_rows": 0, "metric_field_id": "c2"}
        assert "far 全部为空" in explanation and "空值不按 0 计" in explanation
        _, rows, _, explanation, snapshot = execute_query(conn, rate_plan(table, limit=1, offset=2), table, account.id)
        assert rows == []
        assert snapshot["extremum_diagnostic"] == {"reason": "empty_page", "matching_rows": 4,
            "non_null_metric_rows": 3, "metric_field_id": "c2"}
        assert "分页偏移为 2" in explanation and "偏移设为 0" in explanation


@pytest.mark.parametrize("direction,label", [("min", "最小值"), ("max", "最大值")])
def test_empty_extremum_diagnostic_uses_filtered_scope_and_never_fills_missing_metric(direction, label):
    account = Account()
    table = rate_table(account)
    other_table = rate_table(account)
    foreign = Account()
    foreign_table = rate_table(foreign)
    with db.engine.begin() as conn:
        # 原表过滤范围外仍有 FAR；其他表/账号还有真实零值，都不能替本次空值生成极值。
        for identifier in (other_table["id"], foreign_table["id"]):
            conn.execute(update(db.data_rows).where(db.data_rows.c.table_id == identifier)
                .values(values={"c1": 0, "c2": 0, "c3": 0.01}))
        for row in conn.execute(select(db.data_rows).where(db.data_rows.c.table_id == table["id"])).mappings().all():
            if row["values"]["c3"] is not None and row["values"]["c3"] < 0.03:
                conn.execute(update(db.data_rows).where(db.data_rows.c.id == row["id"])
                    .values(values={**row["values"], "c2": None}))
        plan = rate_plan(table)
        plan.extremum.direction = direction
        _, rows, chart, explanation, snapshot = execute_query(conn, plan, table, account.id)
    assert rows == [] and chart is None
    assert snapshot["extremum_diagnostic"] == {"reason": "metric_missing", "matching_rows": 4,
        "non_null_metric_rows": 0, "metric_field_id": "c2"}
    assert "过滤条件匹配 4 条记录，但 far 全部为空" in explanation
    assert f"无法计算{label}和对应阈值" in explanation
    assert snapshot["plan"]["filters"][0]["value"] == pytest.approx(0.03)


def test_real_zero_far_is_an_extremum_value_and_empty_diagnostic_replay_is_saved():
    account = Account()
    table = rate_table(account)
    user = {"id": account.id, "session_id": sha256(account.client.cookies.get(settings.cookie_name).encode()).hexdigest()}
    # 只选择已有 FAR 空记录，确认所得诊断必须与结果快照一同持久保存。
    plan_data = rate_plan(table).model_dump()
    plan_data["filters"].append({"field_id": "c2", "op": "is_null"})
    plan = QueryPlan.model_validate(plan_data)
    stored = preview_operation(user, table["conversation_id"], plan)["plan"]
    confirmed = confirm_operation(stored["id"], user)
    assert confirmed["result"]["query_snapshot"]["extremum_diagnostic"]["reason"] == "metric_missing"
    with db.engine.begin() as conn:
        null_row = conn.execute(select(db.data_rows).where(db.data_rows.c.table_id == table["id"])
            .order_by(db.data_rows.c.position.desc())).mappings().first()
        conn.execute(update(db.data_rows).where(db.data_rows.c.id == null_row["id"])
            .values(values={**null_row["values"], "c2": 0}))
        conn.execute(update(db.data_tables).where(db.data_tables.c.id == table["id"]).values(version=table["version"] + 1))
        _, rows, _, explanation, snapshot = execute_query(conn, rate_plan(table), table, account.id)
    assert [float(row["c1"]) for row in rows] == [60]
    assert float(rows[0]["c2"]) == 0
    assert "extremum_diagnostic" not in snapshot and "全部为空" not in explanation
    replayed = confirm_operation(stored["id"], user)
    assert replayed["result"]["id"] == confirmed["result"]["id"]
    assert replayed["result"]["query_snapshot"] == confirmed["result"]["query_snapshot"]
    assert replayed["result"]["explanation"] == confirmed["result"]["explanation"]


def test_blank_unit_candidate_survives_preview_confirmation_and_duplicate_confirm():
    account = Account()
    table = rate_table(account)
    fields = deepcopy(table["fields"])
    fields[2]["unit"] = ""
    with db.engine.begin() as conn:
        conn.execute(update(db.data_tables).where(db.data_tables.c.id == table["id"]).values(fields=fields))
    user = {"id": account.id, "session_id": sha256(account.client.cookies.get(settings.cookie_name).encode()).hexdigest()}
    plan = rate_plan(table)
    plan.filters[0].storage_unit = "ratio"
    preview = preview_operation(user, table["conversation_id"], plan)
    stored = preview["plan"]
    assert stored["plan"]["filters"][0]["value"] == pytest.approx(0.03)
    assert stored["plan"]["assumptions"]
    assert "建议口径" in stored["intent"]
    confirmed = confirm_operation(stored["id"], user)
    assert [float(row["c1"]) for row in confirmed["result"]["rows"]] == [20, 30]
    assert confirmed["result"]["query_snapshot"]["plan"]["filters"][0]["value"] == pytest.approx(0.03)
    assert confirm_operation(stored["id"], user)["result"]["id"] == confirmed["result"]["id"]
    with db.engine.connect() as conn:
        saved = conn.execute(select(db.data_tables.c.fields, db.data_tables.c.version).where(db.data_tables.c.id == table["id"])).one()
    assert saved.fields == fields and saved.version == table["version"]


def test_extremum_rejects_unregistered_nonnumeric_and_incompatible_shapes():
    for identifier in ("unknown", "c1"):
        table = registry(type_name="string")
        plan = QueryPlan(operation="select", table_id=table["id"], extremum={"field_id": identifier, "direction": "min"})
        with pytest.raises(HTTPException) as error:
            validate_plan(plan, table)
        assert error.value.status_code == 422
    for extra in ({"group_by": ["c1"]}, {"aggregates": [{"field_id": "c1", "function": "min", "alias": "minimum"}]},
        {"ratio": {"field_id": "c1", "threshold": 3, "denominator": "all"}}, {"operation": "insert", "values": {"c1": 1}}):
        with pytest.raises(ValidationError):
            QueryPlan.model_validate({"operation": "select", "table_id": "unit_test", "extremum": {"field_id": "c1", "direction": "min"}, **extra})


@pytest.mark.parametrize("op,expected", [("lt", [10]), ("lte", [10, 20]), ("eq", [20]), ("gt", [30])])
def test_decimal_percent_conversion_preserves_exact_database_boundary(op, expected):
    owner = Account()
    table = rate_table(owner)
    with db.engine.begin() as conn:
        source = list(conn.execute(select(db.data_rows).where(db.data_rows.c.table_id == table["id"]).order_by(db.data_rows.c.position)).mappings())
        annotation = {10: 0.56, 20: 0.57, 30: 0.57000001}
        for row in source:
            value = annotation.get(float(row["values"]["c1"]))
            conn.execute(update(db.data_rows).where(db.data_rows.c.id == row["id"]).values(values={**row["values"], "c3": value}))
        plan = QueryPlan(operation="select", table_id=table["id"], fields=["c1", "c3"],
            filters=[{"field_id": "c3", "op": op, "value": 57, "unit": "%"}])
        rows = execute_query(conn, plan, table, owner.id)[1]
    assert plan.filters[0].value == 0.57
    assert [row["c1"] for row in rows] == expected


def test_decimal_ratio_to_percent_preserves_equality():
    owner = Account()
    table = rate_table(owner)
    table["fields"][2]["unit"] = "%"
    with db.engine.begin() as conn:
        conn.execute(update(db.data_tables).where(db.data_tables.c.id == table["id"]).values(fields=table["fields"]))
        source = list(conn.execute(select(db.data_rows).where(db.data_rows.c.table_id == table["id"]).order_by(db.data_rows.c.position)).mappings())
        annotation = {10: 28, 20: 29, 30: 30}
        for row in source:
            value = annotation.get(float(row["values"]["c1"]))
            conn.execute(update(db.data_rows).where(db.data_rows.c.id == row["id"]).values(values={**row["values"], "c3": value}))
        plan = QueryPlan(operation="select", table_id=table["id"], fields=["c1"],
            filters=[{"field_id": "c3", "op": "eq", "value": 0.29, "unit": "ratio"}])
        rows = execute_query(conn, plan, table, owner.id)[1]
    assert plan.filters[0].value == 29
    assert [row["c1"] for row in rows] == [20]
