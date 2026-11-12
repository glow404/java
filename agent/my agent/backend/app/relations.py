"""双表关联登记与基数预览；只有账号确认过的字段关系可用于 SQL 联表。"""

from fastapi import HTTPException
from sqlalchemy import select, insert, update, cast, String, Numeric, BigInteger, Boolean, Date, func, and_, literal
from sqlalchemy.dialects.postgresql import aggregate_order_by
from . import db
from .schemas import FieldMeta
from .data_management import active_table, store_preview, finish


def key_expression(rows, field):
    # 连接字段先由服务器字段注册表核对；值和 JSON 键仍作为 SQL 绑定参数。
    raw = rows.c["values"][field["id"]].astext
    types = {"string": String, "number": Numeric, "integer": BigInteger, "boolean": Boolean, "date": Date}
    return cast(raw, types[field["type"]])


def selected_field(table, identifier):
    for field in table["fields"]:
        if field["id"] == identifier:
            return FieldMeta.model_validate(field).model_dump()
    raise HTTPException(422, "关联字段不存在，请选择已登记字段")


def key_name(field):
    return field["name"].lower().replace(" ", "").replace("_", "")


def validate_keys(left, right, left_field_id, right_field_id):
    if left["id"] == right["id"]:
        raise HTTPException(422, "请选择两张不同的数据表")
    left_field, right_field = selected_field(left, left_field_id), selected_field(right, right_field_id)
    if left_field["type"] != right_field["type"] or left_field.get("unit", "") != right_field.get("unit", ""):
        raise HTTPException(422, "关联字段类型与单位必须一致；请先核对元数据")
    if key_name(left_field) in ("姓名", "名字", "name", "学生姓名", "用户姓名") or key_name(right_field) in ("姓名", "名字", "name", "学生姓名", "用户姓名"):
        raise HTTPException(422, "不能仅凭姓名关联同名者，请改用经核对的学号、订单号等身份字段")
    return left_field, right_field


def key_counts(table, field, owner_id, label):
    key = key_expression(db.data_rows, field)
    valid = key.is_not(None)
    if field["type"] == "string":
        valid = and_(valid, key != "")
    return select(key.label("key"), func.count().label("n")).where(db.data_rows.c.owner_id == owner_id,
        db.data_rows.c.table_id == table["id"], valid).group_by(key).subquery(label)


def key_profile(conn, table, counts, owner_id):
    total = conn.scalar(select(func.count()).select_from(db.data_rows).where(db.data_rows.c.owner_id == owner_id,
        db.data_rows.c.table_id == table["id"])) or 0
    summary = conn.execute(select(func.coalesce(func.sum(counts.c.n), 0), func.count(),
        func.count().filter(counts.c.n > 1), func.coalesce(func.sum(counts.c.n - 1), 0))).one()
    samples = [dict(row) for row in conn.execute(select(counts.c.key, counts.c.n.label("count"))
        .where(counts.c.n > 1).order_by(counts.c.n.desc(), counts.c.key).limit(10)).mappings()]
    from .service import json_safe
    key = key_expression(db.data_rows, selected_field(table, table["_preview_key"]))
    # 基数相同也可能替换了实际身份键；指纹在确认时复核目标范围，且不把整表返回浏览器。
    key_token = func.concat(db.data_rows.c.id, ":", func.coalesce(func.md5(cast(key, String)), "null"))
    digest = conn.scalar(select(func.md5(func.coalesce(func.string_agg(
        key_token, aggregate_order_by(literal("|"), db.data_rows.c.id)), ""))).where(
        db.data_rows.c.owner_id == owner_id, db.data_rows.c.table_id == table["id"]))
    return {"rows": total, "non_null_rows": int(summary[0]), "null_or_empty_rows": total - int(summary[0]),
        "distinct_keys": int(summary[1]), "duplicate_keys": int(summary[2]), "extra_duplicate_rows": int(summary[3]),
        "duplicate_examples": json_safe(samples), "key_digest": digest}


def preview_stats(conn, left, right, left_field, right_field, owner_id, join_type):
    from .query import set_timeout
    set_timeout(conn)
    left_keys = key_counts(left, left_field, owner_id, "left_keys")
    right_keys = key_counts(right, right_field, owner_id, "right_keys")
    left_profile = key_profile(conn, {**left, "_preview_key": left_field["id"]}, left_keys, owner_id)
    right_profile = key_profile(conn, {**right, "_preview_key": right_field["id"]}, right_keys, owner_id)
    matched = left_keys.join(right_keys, left_keys.c.key == right_keys.c.key)
    summary = conn.execute(select(func.coalesce(func.sum(left_keys.c.n * right_keys.c.n), 0),
        func.coalesce(func.sum(left_keys.c.n), 0), func.coalesce(func.sum(right_keys.c.n), 0),
        func.coalesce(func.sum(left_keys.c.n * (right_keys.c.n - 1)), 0),
        func.coalesce(func.sum(right_keys.c.n * (left_keys.c.n - 1)), 0)).select_from(matched)).one()
    inner_rows, matched_left, matched_right, left_multiplication, right_multiplication = map(int, summary)
    cardinality = ("many_to_many" if left_multiplication and right_multiplication else "one_to_many" if left_multiplication
        else "many_to_one" if right_multiplication else "one_to_one")
    warnings = ["空值/空文本不参与连接；前导零按登记字符串原值保留；没有自动填充身份。"]
    if left_multiplication or right_multiplication:
        warnings.append("重复连接键会扩大连接结果；被复制一侧的求和、均值及占比被禁止，避免重复统计汇总值。")
    if left_profile["null_or_empty_rows"] or right_profile["null_or_empty_rows"]:
        warnings.append("存在缺失身份记录，inner 会排除未匹配项，left 会保留左表未匹配项并以空值展示右表。")
    return {"left": left_profile, "right": right_profile, "matched_left_rows": matched_left, "matched_right_rows": matched_right,
        "unmatched_left_rows": left_profile["rows"] - matched_left, "unmatched_right_rows": right_profile["rows"] - matched_right,
        "inner_rows": inner_rows, "joined_rows": inner_rows + (left_profile["rows"] - matched_left if join_type == "left" else 0),
        "left_multiplication": left_multiplication, "right_multiplication": right_multiplication, "cardinality": cardinality,
        "join_type": join_type, "warnings": warnings}


def lock_pair(conn, left_id, right_id, owner_id):
    # 按固定 ID 顺序锁两张表，避免两次反向关联预览/确认相互等待形成死锁。
    tables = {identifier: active_table(conn, identifier, owner_id, lock=True) for identifier in sorted({left_id, right_id})}
    return tables[left_id], tables[right_id]


def preview_relation(user, conversation_id, left_table_id, right_table_id, left_field_id, right_field_id, join_type="inner"):
    from .table_actions import owner_action_lock
    if join_type not in ("inner", "left"):
        raise HTTPException(422, "第一版关联只支持 inner 和 left")
    with db.engine.begin() as conn:
        owner_action_lock(conn, user["id"])
        left, right = lock_pair(conn, left_table_id, right_table_id, user["id"])
        left_field, right_field = validate_keys(left, right, left_field_id, right_field_id)
        stats = preview_stats(conn, left, right, left_field, right_field, user["id"], join_type)
        specification = {"operation": "relation", "action": "register", "relation_id": db.new_id(),
            "left_table_id": left_table_id, "right_table_id": right_table_id, "left_field_id": left_field_id,
            "right_field_id": right_field_id, "join_type": join_type, "left_version": left["version"],
            "right_version": right["version"], "preview": stats}
        return store_preview(conn, user, conversation_id, "relation", left, specification, [],
            [{"id": specification["relation_id"], "values": {**specification, "left_name": left["name"], "right_name": right["name"]}}],
            stats["joined_rows"], f"待确认关联：{left['name']}（{left_field['name']}）与 {right['name']}（{right_field['name']}），{join_type}，预计 {stats['joined_rows']} 条连接记录，基数 {stats['cardinality']}。原表不会合并或复制数据。")


def preview_relation_state(user, conversation_id, relation_id, status="inactive"):
    from . import service
    from .table_actions import owner_action_lock
    if status != "inactive":
        raise HTTPException(422, "重新启用关系需重新核对两表连接字段并生成新的关联预览")
    with db.engine.begin() as conn:
        owner_action_lock(conn, user["id"])
        relation = service.owned(conn, db.table_relations, relation_id, user["id"], lock=True)
        left = service.owned(conn, db.data_tables, relation["left_table_id"], user["id"], lock=True)
        service.owned(conn, db.data_tables, relation["right_table_id"], user["id"])
        if relation["status"] != "active":
            raise HTTPException(409, "关联已经停用")
        old = {"relation_id": relation_id, "status": relation["status"], "left_table_id": relation["left_table_id"], "right_table_id": relation["right_table_id"]}
        return store_preview(conn, user, conversation_id, "relation", left,
            {"operation": "relation", "action": "deactivate", "relation_id": relation_id, "left_version": left["version"]},
            [{"id": relation_id, "values": old}], [{"id": relation_id, "values": {**old, "status": status}}], 0,
            "待确认：停用此关联。原表、已有查询快照和操作历史仍保留。")


def confirm_relation(conn, stored, user):
    from . import service
    specification = stored["plan"]
    if specification["action"] == "register":
        left, right = lock_pair(conn, specification["left_table_id"], specification["right_table_id"], user["id"])
        if left["version"] != specification["left_version"] or right["version"] != specification["right_version"]:
            raise HTTPException(409, "关联两端表版本变化，请重新核对预览")
        left_field, right_field = validate_keys(left, right, specification["left_field_id"], specification["right_field_id"])
        current_stats = preview_stats(conn, left, right, left_field, right_field, user["id"], specification["join_type"])
        if current_stats != specification["preview"]:
            raise HTTPException(409, "连接键、空值或连接基数变化，请重新核对预览")
        row = {k: specification[k] for k in ("left_table_id", "right_table_id", "left_field_id", "right_field_id", "join_type", "left_version", "right_version", "preview")}
        row.update(id=specification["relation_id"], owner_id=user["id"], status="active", created_at=db.utcnow())
        conn.execute(insert(db.table_relations).values(**row))
        explanation = f"已确认关联“{left['name']}”与“{right['name']}”；字段按 left__/right__ 区分，未修改原表。"
    elif specification["action"] == "deactivate":
        relation = service.owned(conn, db.table_relations, specification["relation_id"], user["id"], lock=True)
        left = service.owned(conn, db.data_tables, relation["left_table_id"], user["id"], lock=True)
        service.owned(conn, db.data_tables, relation["right_table_id"], user["id"])
        if left["version"] != specification["left_version"] or relation["status"] != "active":
            raise HTTPException(409, "关联或表版本已变化，请重新预览")
        conn.execute(update(db.table_relations).where(db.table_relations.c.id == relation["id"], db.table_relations.c.owner_id == user["id"]).values(status="inactive"))
        explanation = "已确认停用关联；原表与已有查询快照仍保留。"
    else:
        raise HTTPException(422, "未知关联操作")
    return finish(conn, stored, user, left["id"], [{"operation": "relation", "name": explanation,
        "affected_count": stored["affected_count"]}], explanation, {"relation_id": specification["relation_id"], "before": stored["before"], "after": stored["after"]})


def confirmed_context(conn, relation_id, owner_id, anchor_table_id=None, lock=False):
    from . import service
    # 执行查询先锁关系再锁表，与停用确认保持同一锁顺序；避免等待表锁期间关系已被停用。
    relation = service.owned(conn, db.table_relations, relation_id, owner_id, lock=lock)
    if relation["status"] != "active":
        raise HTTPException(409, "关联尚未确认或已经停用")
    if lock:
        left, right = lock_pair(conn, relation["left_table_id"], relation["right_table_id"], owner_id)
    else:
        left, right = active_table(conn, relation["left_table_id"], owner_id), active_table(conn, relation["right_table_id"], owner_id)
    if anchor_table_id is not None and left["id"] != anchor_table_id:
        raise HTTPException(422, "联表计划的主表必须是关联左表")
    if left["version"] != relation["left_version"] or right["version"] != relation["right_version"]:
        raise HTTPException(409, "关联两端版本变化，请重新核对并确认关联")
    left_field, right_field = validate_keys(left, right, relation["left_field_id"], relation["right_field_id"])
    stats = preview_stats(conn, left, right, left_field, right_field, owner_id, relation["join_type"])
    fields = []
    for side, table in (("left", left), ("right", right)):
        for field in table["fields"]:
            original = FieldMeta.model_validate(field).model_dump()
            fields.append({**original, "id": f"{side}__{original['id']}", "name": f"{table['name']} · {original['name']}"})
    return {"left": left, "right": right, "relation": relation, "fields": fields, "stats": stats}


def list_relations(owner_id):
    with db.engine.connect() as conn:
        rows = []
        for stored in conn.execute(select(db.table_relations).where(db.table_relations.c.owner_id == owner_id).order_by(db.table_relations.c.created_at.desc())).mappings():
            relation = dict(stored)
            from .service import owned
            left = owned(conn, db.data_tables, relation["left_table_id"], owner_id)
            right = owned(conn, db.data_tables, relation["right_table_id"], owner_id)
            rows.append({**relation, "left_name": left["name"], "right_name": right["name"],
                "fields": [{**field, "id": f"{side}__{field['id']}", "name": f"{table['name']} · {field['name']}"}
                    for side, table in (("left", left), ("right", right)) for field in table["fields"]],
                "stale": left["version"] != relation["left_version"] or right["version"] != relation["right_version"],
                "archived": bool(left.get("archived") or right.get("archived"))})
    return {"relations": rows}


def candidates(owner_id, table_id=None):
    reliable = {"学号", "学生id", "学生编号", "订单号", "订单编号", "用户id", "客户id", "客户编号", "员工号", "工号", "studentid", "ordernumber", "orderid", "userid", "customerid", "employeeid"}
    with db.engine.connect() as conn:
        if table_id:
            active_table(conn, table_id, owner_id)
        tables = [dict(row) for row in conn.execute(select(db.data_tables).where(db.data_tables.c.owner_id == owner_id,
            db.data_tables.c.archived.is_(False)).order_by(db.data_tables.c.created_at.desc())).mappings()]
    suggestions = []
    for index, left in enumerate(tables):
        for right in tables[index + 1:]:
            if table_id and table_id not in (left["id"], right["id"]):
                continue
            # 先索引可靠身份字段，避免宽表候选比较退化成所有字段两两匹配。
            right_keys = {}
            for rf in right["fields"]:
                if key_name(rf) in reliable:
                    right_keys.setdefault(key_name(rf), []).append(rf)
            for lf in left["fields"]:
                for rf in right_keys.get(key_name(lf), []):
                    if lf["type"] != rf["type"] or lf.get("unit", "") != rf.get("unit", ""):
                        continue
                    suggestions.append({"left_table_id": left["id"], "right_table_id": right["id"],
                        "left_name": left["name"], "right_name": right["name"], "left_field_id": lf["id"], "right_field_id": rf["id"],
                        "left_field_name": lf["name"], "right_field_name": rf["name"], "join_type": "inner", "requires_confirmation": True,
                        "reason": "两表登记了同名、同类型及同单位的可靠身份字段；仍须预览重复键、空值和连接基数，不能自动建立关联。"})
                    if len(suggestions) >= 50:
                        return {"suggestions": suggestions, "truncated": True}
    return {"suggestions": suggestions, "truncated": False}
