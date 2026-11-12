"""试用反馈的数据回归：源顺序、整表管理、关联基数与双端账号隔离。

仅向独立 vibe_test 新建测试账号/表，不清空业务数据。
"""

import hashlib
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Event
import pytest
from fastapi import HTTPException
from sqlalchemy import insert, select, update, func
from app import db, service, data_management, relations
from app.config import settings
from app.schemas import QueryPlan
from app.query import execute_query, target_rows, validate_plan
from test_api import Account


@pytest.fixture(autouse=True)
def isolated_database():
    if os.getenv("VIBE_TEST_DATABASE") != "true" or db.engine.url.database != "vibe_test":
        pytest.skip("requires isolated vibe_test database")


def verified(account):
    return {"id": account.id, "session_id": hashlib.sha256(account.client.cookies.get(settings.cookie_name).encode()).hexdigest()}


def make_table(account, name, rows, field_names=("学号", "姓名", "成绩"), units=("", "", "分")):
    fields = [{"id": f"c{i+1}", "name": label, "type": "number" if i == 2 else "string", "unit": units[i],
        "description": "人工标注虚构数据", "provenance": "user", "type_provenance": "user"} for i, label in enumerate(field_names)]
    table = {"id": db.new_id(), "owner_id": account.id, "name": name, "description": "虚构测试表",
        "description_provenance": "user", "fields": fields, "structure": {}, "version": 1,
        "row_count": len(rows), "archived": False, "created_at": db.utcnow()}
    conversation_id = db.new_id()
    with db.engine.begin() as conn:
        conn.execute(insert(db.data_tables).values(**table))
        conn.execute(insert(db.conversations).values(id=conversation_id, owner_id=account.id, title="反馈测试", created_at=db.utcnow()))
        # UUID 的逆序故意与输入顺序相反，防止用随机主键排序的回归被样例偶然掩盖。
        identifiers = sorted([db.new_id() for _ in rows], reverse=True)
        for position, (identifier, values) in enumerate(zip(identifiers, rows), 1):
            conn.execute(insert(db.data_rows).values(id=identifier, owner_id=account.id, table_id=table["id"],
                position=position, values=dict(zip(["c1", "c2", "c3"], values)), source={"row": position + 1, "provenance": "user"}))
    table["conversation_id"] = conversation_id
    return table


def expect_http(code, fn):
    with pytest.raises(HTTPException) as failure:
        fn()
    assert failure.value.status_code == code


def fixture_pair():
    account = Account()
    left = make_table(account, "学生汇总", [("001", "同名", 90), ("002", "同名", 80), (None, "待核对", 70), ("003", "李三", 60)])
    right = make_table(account, "加分明细", [("001", "项目A", 1), ("001", "项目B", 2), ("002", "项目C", 3), (None, "缺失身份", 4), ("004", "未匹配", 5)], field_names=("学号", "项目", "加分"))
    return account, left, right


def register(account, left, right, join_type="left"):
    response = relations.preview_relation(verified(account), left["conversation_id"], left["id"], right["id"], "c1", "c1", join_type)
    confirmed = data_management.confirm_management(response["plan"]["id"], verified(account))
    return response, confirmed, response["plan"]["plan"]["relation_id"]


def test_empty_extremum_diagnostic_counts_scoped_join_records_and_unmatched_nulls():
    account = Account()
    left = make_table(account, "阈值主体", [("001", "A", 90), ("002", "B", 80)])
    right = make_table(account, "FAR 明细", [("001", "A", None), ("001", "B", None), ("999", "范围外", 0)],
        field_names=("学号", "项目", "far"), units=("", "", "ratio"))
    _, _, relation_id = register(account, left, right)
    plan = QueryPlan(operation="select", table_id=left["id"], relation_id=relation_id,
        fields=["left__c1", "right__c3"], extremum={"field_id": "right__c3", "direction": "min"})
    with db.engine.begin() as conn:
        _, rows, _, explanation, snapshot = execute_query(conn, plan, left, account.id)
    # 一对多保留两条连接记录；未匹配的左记录也算一条，右表范围外的 FAR=0 不参与。
    assert rows == []
    assert snapshot["extremum_diagnostic"] == {"reason": "metric_missing", "matching_rows": 3,
        "non_null_metric_rows": 0, "metric_field_id": "right__c3"}
    assert "过滤条件匹配 3 条记录" in explanation and "全部为空" in explanation
    assert snapshot["relation_id"] == relation_id


def test_default_query_and_targets_preserve_input_order_and_pagination():
    account = Account()
    table = make_table(account, "输入顺序", [("003", "第三位先输入", 80), ("001", "第一位后输入", 90), ("002", "第二位最后输入", 70)])
    plan = QueryPlan(operation="select", table_id=table["id"], fields=["c1"], limit=2, offset=1)
    with db.engine.begin() as conn:
        _, rows, _, _, snapshot = execute_query(conn, plan, table, account.id)
        assert [row["c1"] for row in rows] == ["001", "002"]
        assert "position" in snapshot["parameterized_sql"]
        targets = target_rows(conn, QueryPlan(operation="update", table_id=table["id"],
            filters=[{"field_id": "c3", "op": "gte", "value": 0}], values={"c3": 99}), table, account.id)
        assert [row["values"]["c1"] for row in targets] == ["003", "001", "002"]


def test_create_preview_then_concurrent_confirmation_once():
    account = Account()
    original = make_table(account, "会话载体", [])
    preview = data_management.preview_create_table(verified(account), original["conversation_id"], "空表", original["fields"], "手工输入")
    plan = preview["plan"]
    assert plan["table_id"] is None and plan["operation"] == "create_table"
    with db.engine.connect() as conn:
        assert not conn.scalar(select(db.data_tables.c.id).where(db.data_tables.c.id == plan["plan"]["new_table_id"]))
    with ThreadPoolExecutor(max_workers=2) as pool:
        confirmations = list(pool.map(lambda _: data_management.confirm_management(plan["id"], verified(account)), range(2)))
    assert confirmations[0]["result"]["id"] == confirmations[1]["result"]["id"]
    with db.engine.connect() as conn:
        created = service.owned(conn, db.data_tables, confirmations[0]["result"]["table_id"], account.id)
        assert created["row_count"] == 0 and all(field["provenance"] == "user" for field in created["fields"])
        assert conn.scalar(select(func.count()).select_from(db.audits).where(db.audits.c.plan_id == plan["id"])) == 1


def test_create_confirmation_failure_rolls_back_table_result_and_audit(monkeypatch):
    account = Account()
    table = make_table(account, "会话载体", [])
    preview = data_management.preview_create_table(verified(account), table["conversation_id"], "回滚空表", table["fields"])
    def failed_audit(*args, **kwargs):
        raise RuntimeError("intentional isolated audit failure")
    monkeypatch.setattr(service, "audit", failed_audit)
    with pytest.raises(RuntimeError, match="isolated audit"):
        data_management.confirm_management(preview["plan"]["id"], verified(account))
    with db.engine.connect() as conn:
        assert not conn.scalar(select(db.data_tables.c.id).where(db.data_tables.c.id == preview["plan"]["plan"]["new_table_id"]))
        assert service.owned(conn, db.plans, preview["plan"]["id"], account.id)["status"] == "pending"
        assert conn.scalar(select(func.count()).select_from(db.results).where(db.results.c.conversation_id == table["conversation_id"])) == 0


def test_archive_restore_preserves_rows_and_requires_preview_version():
    account = Account()
    table = make_table(account, "归档保留", [("001", "张一", 90)])
    expect_http(409, lambda: data_management.preview_table_state(verified(account), table["conversation_id"], table["id"], True, version=99))
    preview = data_management.preview_table_state(verified(account), table["conversation_id"], table["id"], True, version=1)
    result = data_management.confirm_management(preview["plan"]["id"], verified(account))
    with db.engine.connect() as conn:
        archived = service.owned(conn, db.data_tables, table["id"], account.id)
        assert archived["archived"] and archived["version"] == 2
        assert conn.scalar(select(func.count()).select_from(db.data_rows).where(db.data_rows.c.table_id == table["id"])) == 1
        assert service.owned(conn, db.results, result["result"]["id"], account.id)
    expect_http(409, lambda: validate_plan(QueryPlan(operation="select", table_id=table["id"]), archived))
    restore = data_management.preview_table_state(verified(account), table["conversation_id"], table["id"], False, version=2)
    data_management.confirm_management(restore["plan"]["id"], verified(account))
    with db.engine.connect() as conn:
        restored = service.owned(conn, db.data_tables, table["id"], account.id)
        assert not restored["archived"] and restored["version"] == 3


def test_management_plan_owner_session_expiry_and_version_binding():
    account = Account()
    other = Account()
    table = make_table(account, "绑定", [("001", "张一", 90)])
    preview = data_management.preview_table_state(verified(account), table["conversation_id"], table["id"], True)
    identifier = preview["plan"]["id"]
    expect_http(404, lambda: data_management.confirm_management(identifier, verified(other)))
    expect_http(409, lambda: data_management.confirm_management(identifier, {"id": account.id, "session_id": "another"}))
    with db.engine.begin() as conn:
        conn.execute(update(db.plans).where(db.plans.c.id == identifier).values(expires_at=db.utcnow() - timedelta(seconds=1)))
    expect_http(409, lambda: data_management.confirm_management(identifier, verified(account)))
    newer = data_management.preview_table_state(verified(account), table["conversation_id"], table["id"], True)
    with db.engine.begin() as conn:
        conn.execute(update(db.data_tables).where(db.data_tables.c.id == table["id"]).values(version=2))
    expect_http(409, lambda: data_management.confirm_management(newer["plan"]["id"], verified(account)))


def test_relation_preview_nulls_duplicate_keys_and_safe_left_join_order():
    account, left, right = fixture_pair()
    preview, confirmed, relation_id = register(account, left, right)
    stats = preview["plan"]["plan"]["preview"]
    assert stats["left"]["rows"] == 4 and stats["right"]["rows"] == 5
    assert stats["right"]["duplicate_keys"] == 1 and stats["left"]["null_or_empty_rows"] == 1
    assert stats["cardinality"] == "one_to_many" and stats["inner_rows"] == 3 and stats["joined_rows"] == 5
    query = QueryPlan(operation="select", table_id=left["id"], relation_id=relation_id, fields=["left__c1", "right__c2", "right__c3"])
    with db.engine.begin() as conn:
        _, rows, _, explanation, snapshot = execute_query(conn, query, left, account.id)
        assert rows == [{"left__c1": "001", "right__c2": "项目A", "right__c3": 1},
            {"left__c1": "001", "right__c2": "项目B", "right__c3": 2},
            {"left__c1": "002", "right__c2": "项目C", "right__c3": 3},
            {"left__c1": None, "right__c2": None, "right__c3": None},
            {"left__c1": "003", "right__c2": None, "right__c3": None}]
        assert "连接记录" in explanation and snapshot["relation_id"] == relation_id
        assert account.id in snapshot["bindings"].values() and right["id"] in snapshot["bindings"].values()
    replayed = data_management.confirm_management(preview["plan"]["id"], verified(account))
    assert replayed["result"]["id"] == confirmed["result"]["id"]


def test_join_rejects_duplicated_summary_statistics_but_detail_sum_is_actual():
    account, left, right = fixture_pair()
    _, _, relation_id = register(account, left, right)
    def aggregate(field):
        return QueryPlan(operation="select", table_id=left["id"], relation_id=relation_id,
            aggregates=[{"field_id": field, "function": "sum", "alias": "total"}])
    with db.engine.begin() as conn:
        expect_http(422, lambda: execute_query(conn, aggregate("left__c3"), left, account.id))
        _, rows, _, _, _ = execute_query(conn, aggregate("right__c3"), left, account.id)
        assert rows[0]["total"] == 6
        ratio = QueryPlan(operation="select", table_id=left["id"], relation_id=relation_id,
            ratio={"field_id": "left__c3", "threshold": 80, "denominator": "all"})
        expect_http(422, lambda: execute_query(conn, ratio, left, account.id))


def test_join_filters_sort_group_parameterized_and_scope_validated():
    account, left, right = fixture_pair()
    _, _, relation_id = register(account, left, right, "inner")
    plan = QueryPlan(operation="select", table_id=left["id"], relation_id=relation_id, group_by=["left__c1"],
        filters=[{"field_id": "right__c3", "op": "gte", "value": 2, "unit": "分"}],
        aggregates=[{"field_id": "right__c3", "function": "sum", "alias": "bonus"}], sort=[{"field_id": "bonus", "direction": "desc"}])
    with db.engine.begin() as conn:
        _, rows, _, _, _ = execute_query(conn, plan, left, account.id)
        assert rows == [{"left__c1": "002", "bonus": 3}, {"left__c1": "001", "bonus": 2}]
        expect_http(422, lambda: execute_query(conn, QueryPlan(operation="select", table_id=left["id"], relation_id=relation_id, fields=["right__owner_id"]), left, account.id))
        expect_http(422, lambda: execute_query(conn, QueryPlan(operation="select", table_id=right["id"], relation_id=relation_id), right, account.id))


def test_relation_owner_both_ends_type_unit_name_and_candidates():
    account, left, right = fixture_pair()
    other = Account()
    foreign = make_table(other, "他人表", [("001", "张一", 99)])
    expect_http(404, lambda: relations.preview_relation(verified(account), left["conversation_id"], left["id"], foreign["id"], "c1", "c1"))
    expect_http(422, lambda: relations.preview_relation(verified(account), left["conversation_id"], left["id"], right["id"], "c2", "c2"))
    wrong_units = make_table(account, "单位不符", [("001", "项目", 90)], units=("个", "", "分"))
    expect_http(422, lambda: relations.preview_relation(verified(account), left["conversation_id"], left["id"], wrong_units["id"], "c1", "c1"))
    suggestions = relations.candidates(account.id, left["id"])["suggestions"]
    assert suggestions and all(item["left_field_id"] == "c1" and item["right_field_id"] == "c1" for item in suggestions)
    assert all(foreign["id"] not in (item["left_table_id"], item["right_table_id"]) for item in suggestions)
    _, _, relation_id = register(account, left, right)
    with db.engine.connect() as conn:
        expect_http(404, lambda: relations.confirmed_context(conn, relation_id, other.id))
    with db.engine.begin() as conn:
        # 验证不仅看关系owner，也重新核对右表归属；模拟登记损坏也不能泄露他人资料。
        conn.execute(update(db.table_relations).where(db.table_relations.c.id == relation_id).values(right_table_id=foreign["id"]))
    with db.engine.connect() as conn:
        expect_http(404, lambda: relations.confirmed_context(conn, relation_id, account.id))


def test_relation_confirm_rechecks_both_versions_and_key_range_even_same_counts():
    account = Account()
    left = make_table(account, "左", [("001", "甲", 90), ("002", "乙", 80)])
    right = make_table(account, "右", [("001", "丙", 1), ("002", "丁", 2)])
    preview = relations.preview_relation(verified(account), left["conversation_id"], left["id"], right["id"], "c1", "c1")
    with db.engine.begin() as conn:
        row = conn.execute(select(db.data_rows).where(db.data_rows.c.table_id == right["id"], db.data_rows.c.position == 1)).mappings().one()
        other_row = conn.execute(select(db.data_rows).where(db.data_rows.c.table_id == right["id"], db.data_rows.c.position == 2)).mappings().one()
        conn.execute(update(db.data_rows).where(db.data_rows.c.id == row["id"]).values(values={**row["values"], "c1": "002"}))
        conn.execute(update(db.data_rows).where(db.data_rows.c.id == other_row["id"]).values(values={**other_row["values"], "c1": "001"}))
    expect_http(409, lambda: data_management.confirm_management(preview["plan"]["id"], verified(account)))
    newer = relations.preview_relation(verified(account), left["conversation_id"], left["id"], right["id"], "c1", "c1")
    with db.engine.begin() as conn:
        conn.execute(update(db.data_tables).where(db.data_tables.c.id == right["id"]).values(version=2))
    expect_http(409, lambda: data_management.confirm_management(newer["plan"]["id"], verified(account)))


def test_relation_deactivate_preview_and_version_change_invalidate_new_queries():
    account, left, right = fixture_pair()
    _, _, relation_id = register(account, left, right)
    preview = relations.preview_relation_state(verified(account), left["conversation_id"], relation_id)
    with db.engine.connect() as conn:
        assert relations.confirmed_context(conn, relation_id, account.id)
    data_management.confirm_management(preview["plan"]["id"], verified(account))
    with db.engine.connect() as conn:
        expect_http(409, lambda: relations.confirmed_context(conn, relation_id, account.id))
    _, _, fresh_id = register(account, left, right)
    with db.engine.begin() as conn:
        conn.execute(update(db.data_tables).where(db.data_tables.c.id == right["id"]).values(version=2))
    with db.engine.connect() as conn:
        expect_http(409, lambda: relations.confirmed_context(conn, fresh_id, account.id))
    listed = relations.list_relations(account.id)["relations"]
    assert next(item for item in listed if item["id"] == fresh_id)["stale"]


@pytest.mark.parametrize("left_keys,right_keys,cardinality,joined", [
    (["001", "001"], ["001"], "many_to_one", 2),
    (["001", "001"], ["001", "001"], "many_to_many", 4),
    (["001", "002"], ["001", "002"], "one_to_one", 2),
])
def test_join_cardinality_and_count_explains_join_records(left_keys, right_keys, cardinality, joined):
    account = Account()
    left = make_table(account, "左粒度", [(key, "甲", 80) for key in left_keys])
    right = make_table(account, "右粒度", [(key, "乙", 1) for key in right_keys])
    preview, _, relation_id = register(account, left, right, "inner")
    assert preview["plan"]["plan"]["preview"]["cardinality"] == cardinality
    with db.engine.begin() as conn:
        count = QueryPlan(operation="select", table_id=left["id"], relation_id=relation_id,
            aggregates=[{"function": "count", "alias": "records"}])
        _, rows, _, explanation, _ = execute_query(conn, count, left, account.id)
        assert rows == [{"records": joined}] and "不等同于独立学生/订单数量" in explanation


def test_concurrent_relation_deactivation_cannot_execute_cached_active_context():
    account, left, right = fixture_pair()
    _, _, relation_id = register(account, left, right)
    started = Event()
    def query_during_deactivation():
        started.set()
        try:
            with db.engine.begin() as query_conn:
                execute_query(query_conn, QueryPlan(operation="select", table_id=left["id"], relation_id=relation_id), left, account.id)
            return 200
        except HTTPException as exc:
            return exc.status_code
    with ThreadPoolExecutor(max_workers=1) as pool:
        with db.engine.begin() as conn:
            # 复现停用确认已持有关联/表锁但尚未提交，另一查询必须等到看到真实停用状态。
            service.owned(conn, db.table_relations, relation_id, account.id, lock=True)
            service.owned(conn, db.data_tables, left["id"], account.id, lock=True)
            conn.execute(update(db.table_relations).where(db.table_relations.c.id == relation_id).values(status="inactive"))
            future = pool.submit(query_during_deactivation)
            assert started.wait(5)
            with pytest.raises(TimeoutError):
                future.result(timeout=0.2)
        assert future.result(timeout=5) == 409
