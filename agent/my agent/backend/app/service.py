"""持久化用例：账号归属、导入、查询快照与一次性事务变更。"""

from copy import deepcopy
from datetime import datetime, date, timedelta
from decimal import Decimal
from fastapi import HTTPException
from sqlalchemy import select, insert, update, delete, func
from . import db
from .config import settings
from .schemas import ImportConfirm, TablePatch, QueryPlan, FieldMeta
from .query import value_for_type, validate_plan, target_rows, execute_query, set_timeout, field_expr


def json_safe(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, dict):
        return {k: json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(v) for v in value]
    return value


def owned(conn, table, identifier, owner_id, lock=False):
    stmt = select(table).where(table.c.id == identifier, table.c.owner_id == owner_id)
    if lock:
        stmt = stmt.with_for_update(key_share=lock == "no_key")
    row = conn.execute(stmt).mappings().first()
    if not row:
        # 对不存在与他人资源返回同样响应，避免通过 ID 枚举账号资料。
        raise HTTPException(404, "资源不存在或当前账号无权访问")
    return dict(row)


def audit(conn, owner_id, action, table_id=None, plan_id=None, details=None):
    conn.execute(insert(db.audits).values(id=db.new_id(), owner_id=owner_id, action=action,
        table_id=table_id, plan_id=plan_id, details=json_safe(details or {}), created_at=db.utcnow()))


def assert_grain_unique(conn, table, owner_id):
    structure = table.get("structure") or {}
    keys = structure.get("identity_fields", [])
    if structure.get("grain") not in ("学生汇总", "一级指标汇总") or not keys:
        return
    registry = {f["id"]: f for f in table["fields"]}
    expressions = [field_expr(registry[k]) for k in keys]
    # 表行锁串行化同表写入；数据库按真实身份/指标检查唯一粒度，空身份仍单独保留待核对。
    duplicate = conn.execute(select(func.count()).select_from(db.data_rows).where(
        db.data_rows.c.owner_id == owner_id, db.data_rows.c.table_id == table["id"],
        *(expr.is_not(None) for expr in expressions)).group_by(*expressions).having(func.count() > 1).limit(1)).first()
    if duplicate:
        raise HTTPException(422, "汇总粒度出现重复身份/指标，请核对；同一学生或学生指标总分只能保存一次")


def check_fields(existing, supplied):
    original = [f["id"] for f in existing]
    identifiers = [f.id for f in supplied]
    if len(identifiers) != len(set(identifiers)) or set(original) != set(identifiers):
        raise HTTPException(422, "字段 ID 必须唯一，并保持原有字段集合；可修改名称、类型与说明")


def description_source(original_value, original_source, new_value, requested_source):
    # 文件原文来源不能仅凭浏览器声明获得；改写后或历史已标为用户/推断时保留真实来源。
    if requested_source in (None, "file"):
        return original_source if new_value == original_value else "user"
    return requested_source


def convert_values(values, fields):
    try:
        return {f["id"]: value_for_type(values.get(f["id"]), f["type"]) for f in fields}
    except (ValueError, TypeError, OverflowError) as exc:
        raise HTTPException(422, f"存在与字段类型不兼容的值，请在预览修正：{exc}") from exc


def catalog(owner_id):
    with db.engine.connect() as conn:
        all_tables = [dict(t) for t in conn.execute(select(db.data_tables).where(db.data_tables.c.owner_id == owner_id)
            .order_by(db.data_tables.c.created_at.desc())).mappings()]
        task_list = [task_public(dict(t)) for t in conn.execute(select(db.tasks).where(db.tasks.c.owner_id == owner_id)
            .order_by(db.tasks.c.created_at.desc()).limit(50)).mappings()]
        history = history_rows(conn, owner_id, 10)
        hidden = hidden_results(conn, owner_id)
    return {"tables": [table for table in all_tables if not table["archived"]],
        "archived_tables": [table for table in all_tables if table["archived"]], "hidden_result_ids": hidden,
        "tasks": task_list, "recent_operations": history, "model_mode": settings.model_provider}


def table_data(table_id, owner_id, offset=0, limit=100):
    with db.engine.begin() as conn:
        table = owned(conn, db.data_tables, table_id, owner_id)
        set_timeout(conn)
        rows = [dict(r) for r in conn.execute(select(db.data_rows.c.id, db.data_rows.c["values"], db.data_rows.c.source)
            .where(db.data_rows.c.table_id == table_id, db.data_rows.c.owner_id == owner_id)
            .order_by(db.data_rows.c.position, db.data_rows.c.id).offset(offset).limit(min(limit, settings.result_limit))).mappings()]
    return {**table, "rows": rows, "total": table["row_count"]}


def edit_table(table_id, body: TablePatch, owner_id):
    from .table_actions import owner_action_lock
    with db.engine.begin() as conn:
        owner_action_lock(conn, owner_id)
        return apply_table_patch(conn, table_id, body, owner_id)


def apply_table_patch(conn, table_id, body: TablePatch, owner_id, emit_audit=True):
    from .data_management import active_table
    table = active_table(conn, table_id, owner_id, lock=True)
    if body.version != table["version"]:
        raise HTTPException(409, "表版本已变化，请重新载入后修改")
    check_fields(table["fields"], body.fields)
    fields = [f.model_dump() for f in body.fields]
    original = {f["id"]: FieldMeta.model_validate(f).model_dump() for f in table["fields"]}
    for field in fields:
        if field != original[field["id"]] and field["provenance"] == "file":
            field["provenance"] = "user"
        if field["type"] != original[field["id"]]["type"]:
            # 助手生成的类型推断经确认仍是推断；手工编辑才标记为用户输入。
            field["type_provenance"] = "confirmed_inference" if field["type_provenance"] == "confirmed_inference" else "user"
        elif field["type_provenance"] == "file":
            field["type_provenance"] = original[field["id"]]["type_provenance"]
    # 改类型时先校验并转换所有现有值；任意一行失败都会回滚元数据与数据。
    if any(f["type"] != original[f["id"]]["type"] for f in fields):
        set_timeout(conn)
        for row in conn.execute(select(db.data_rows.c.id, db.data_rows.c["values"])
            .where(db.data_rows.c.owner_id == owner_id, db.data_rows.c.table_id == table_id)).mappings():
            values = convert_values(row["values"], fields)
            conn.execute(update(db.data_rows).where(db.data_rows.c.id == row["id"], db.data_rows.c.owner_id == owner_id).values(values=values))
    conn.execute(update(db.data_tables).where(db.data_tables.c.id == table_id, db.data_tables.c.owner_id == owner_id)
        .values(name=body.name, description=body.description,
            description_provenance=description_source(table["description"], table["description_provenance"], body.description, body.description_provenance),
            fields=fields, version=table["version"] + 1))
    assert_grain_unique(conn, {**table, "fields": fields}, owner_id)
    if emit_audit:
        audit(conn, owner_id, "metadata_update", table_id, details={"before": {"name": table["name"], "description": table["description"], "fields": table["fields"]},
            "after": {"name": body.name, "description": body.description, "fields": fields}})
    return owned(conn, db.data_tables, table_id, owner_id)


def task_public(task):
    return {k: v for k, v in task.items() if k not in ("owner_id", "preview", "output_name")}


def import_preview(task_id, owner_id, candidate_id=None, offset=0, limit=200):
    with db.engine.connect() as conn:
        task = owned(conn, db.tasks, task_id, owner_id)
    if task["kind"] != "parse" or task["status"] != "completed" or not task["preview"]:
        raise HTTPException(409, "解析尚未完成；请查看任务状态")
    preview = deepcopy(task["preview"])
    preview.update(task_id=task_id, version=task["version"], table_ids=task["table_ids"])
    if candidate_id:
        preview["tables"] = [c for c in preview["tables"] if c["candidate_id"] == candidate_id]
        if not preview["tables"]:
            raise HTTPException(404, "候选表不存在")
    for candidate in preview["tables"]:
        candidate["row_count"] = len(candidate["rows"])
        candidate["preview_limit"] = limit
        candidate["row_offset"] = offset
        candidate["rows"] = candidate["rows"][offset:offset + limit]
        original = candidate.get("original")
        if isinstance(original, dict) and isinstance(original.get("rows"), list):
            original["rows"] = original["rows"][offset:offset + limit]
            original["preview_limit"] = limit
            original["row_offset"] = offset
    return preview


def confirm_import(task_id, body: ImportConfirm, owner_id):
    from .table_actions import owner_action_lock
    with db.engine.begin() as conn:
        # 先持账号共享锁，再锁任务/表；永久删除不能与导入的新资源写入交错。
        owner_action_lock(conn, owner_id)
        task = owned(conn, db.tasks, task_id, owner_id, lock=True)
        if task["table_ids"] is not None:
            return {"table_ids": task["table_ids"], "replayed": True}
        if task["kind"] != "parse" or task["status"] != "completed" or not task["preview"]:
            raise HTTPException(409, "解析任务尚未完成")
        if task["version"] != body.version:
            raise HTTPException(409, "预览版本已变化，请重新核对")
        owned(conn, db.files, task["file_id"], owner_id)
        candidates = {c["candidate_id"]: c for c in task["preview"]["tables"]}
        ids = []
        linked = []
        seen = set()
        groups = set()
        for chosen in body.tables:
            if not chosen.reviewed:
                raise HTTPException(422, "每张导入表必须由用户明确核对")
            if chosen.candidate_id not in candidates or chosen.candidate_id in seen:
                raise HTTPException(422, "未知或重复的候选表")
            seen.add(chosen.candidate_id)
            candidate = deepcopy(candidates[chosen.candidate_id])
            group = candidate.get("mutually_exclusive_group")
            if group and group in groups:
                raise HTTPException(422, "同一原表的结构候选互斥，请只选择一种，避免重复导入")
            if group:
                groups.add(group)
            check_fields(candidate["fields"], chosen.fields)
            fields = [f.model_dump() for f in chosen.fields]
            original = {f["id"]: FieldMeta.model_validate(f).model_dump() for f in candidate["fields"]}
            for field in fields:
                if field != original[field["id"]] and field["provenance"] == "file":
                    field["provenance"] = "user"
                if field["type"] != original[field["id"]]["type"]:
                    field["type_provenance"] = "user"
                elif field["type_provenance"] == "file":
                    field["type_provenance"] = original[field["id"]]["type_provenance"]
                if field["type_provenance"] == "inference":
                    field["type_provenance"] = "confirmed_inference"
            field_ids = {f["id"] for f in fields}
            for correction in chosen.corrections:
                if correction.row_index >= len(candidate["rows"]) or correction.field_id not in field_ids:
                    raise HTTPException(422, "修正引用了不存在的行或字段")
                row = candidate["rows"][correction.row_index]
                row["values"][correction.field_id] = correction.value
                row.setdefault("source", {}).setdefault("user_corrections", []).append(correction.model_dump())
            table_id = db.new_id()
            structure = {"grain": candidate.get("grain"), "identity_fields": candidate.get("identity_fields", []),
                "source_group": candidate.get("source_group"), "task_id": task_id, "candidate_id": chosen.candidate_id}
            conn.execute(insert(db.data_tables).values(id=table_id, owner_id=owner_id, file_id=task["file_id"],
                name=chosen.name, description=chosen.description, description_provenance=description_source(candidate.get("description", ""), "file", chosen.description, chosen.description_provenance),
                structure=structure, fields=fields, version=1,
                row_count=len(candidate["rows"]), created_at=db.utcnow()))
            # 批量写入仍处于同一事务；重试先锁任务行，避免重复创建数据。
            batch = []
            for index, row in enumerate(candidate["rows"]):
                source = dict(row.get("source", {}))
                source.update(file_id=task["file_id"], task_id=task_id, candidate_id=chosen.candidate_id,
                    preview_row_index=index, reviewed=True)
                batch.append({"id": db.new_id(), "table_id": table_id, "owner_id": owner_id,
                    "values": convert_values(row["values"], fields), "source": json_safe(source), "position": index + 1})
                if len(batch) >= 1000:
                    conn.execute(insert(db.data_rows), batch)
                    batch = []
            if batch:
                conn.execute(insert(db.data_rows), batch)
            assert_grain_unique(conn, {"id": table_id, "fields": fields, "structure": structure}, owner_id)
            linked.append({"id": table_id, "fields": fields, "structure": structure})
            audit(conn, owner_id, "import", table_id, details={"file_id": task["file_id"], "task_id": task_id,
                "candidate_id": chosen.candidate_id, "row_count": len(candidate["rows"]), "warnings": candidate.get("warnings", [])})
            ids.append(table_id)
        for table in linked:
            structure = table["structure"]
            if structure.get("source_group"):
                related = [other for other in linked if other["id"] != table["id"] and other["structure"].get("source_group") == structure["source_group"]]
                structure["relations"] = [{"table_id": other["id"], "shared_field_ids": sorted(set(f["id"] for f in table["fields"]) & set(f["id"] for f in other["fields"]))} for other in related]
                conn.execute(update(db.data_tables).where(db.data_tables.c.id == table["id"], db.data_tables.c.owner_id == owner_id).values(structure=structure))
        conn.execute(update(db.tasks).where(db.tasks.c.id == task_id, db.tasks.c.owner_id == owner_id)
            .values(table_ids=ids, updated_at=db.utcnow()))
        return {"table_ids": ids}


def save_message(conn, owner_id, conversation_id, role, content, result_id=None, plan_id=None, clarifications=None, request=None):
    identifier = db.new_id()
    row = {"id": identifier, "owner_id": owner_id, "conversation_id": conversation_id,
        "role": role, "content": content, "result_id": result_id, "plan_id": plan_id,
        "clarifications": clarifications, "request": request, "created_at": db.utcnow()}
    conn.execute(insert(db.messages).values(**row))
    return {k: v for k, v in row.items() if k not in ("owner_id", "request")}


def save_result(conn, owner_id, conversation_id, table_id, columns, rows, chart, explanation, snapshot, source_message_id=None):
    result = {"id": db.new_id(), "owner_id": owner_id, "conversation_id": conversation_id, "table_id": table_id,
        "columns": columns, "rows": json_safe(rows), "chart": chart, "explanation": explanation,
        "query_snapshot": json_safe(snapshot), "created_at": db.utcnow(), "source_message_id": source_message_id}
    conn.execute(insert(db.results).values(**result))
    return result


def lock_source_message(conn, owner_id, conversation_id, source_message_id):
    if not source_message_id:
        return
    # 取消与保存效果都先锁同一运行行；仅取消浏览器 fetch 无法提供这个保证。
    run = conn.execute(select(db.assistant_runs).where(db.assistant_runs.c.owner_id == owner_id,
        db.assistant_runs.c.message_id == source_message_id).with_for_update()).mappings().first()
    if run and run["status"] not in ("running", "completed"):
        raise HTTPException(409, "助手运行已中止或失效，不再保存执行计划")
    message = owned(conn, db.messages, source_message_id, owner_id, lock=True)
    if message["role"] != "user" or message["conversation_id"] != conversation_id:
        raise HTTPException(409, "工作流的源消息不属于当前对话")


def cached_message(conn, owner_id, conversation_id, result_id=None, plan_id=None):
    criterion = db.messages.c.result_id == result_id if result_id else db.messages.c.plan_id == plan_id
    return dict(conn.execute(select(db.messages).where(db.messages.c.owner_id == owner_id,
        db.messages.c.conversation_id == conversation_id, db.messages.c.role == "assistant", criterion)
        .order_by(db.messages.c.created_at).limit(1)).mappings().one())


def execute_select(owner_id, conversation_id, plan: QueryPlan, source_message_id=None):
    from .table_actions import owner_action_lock
    with db.engine.begin() as conn:
        owner_action_lock(conn, owner_id)
        owned(conn, db.conversations, conversation_id, owner_id)
        lock_source_message(conn, owner_id, conversation_id, source_message_id)
        if source_message_id:
            existing = conn.execute(select(db.results).where(db.results.c.owner_id == owner_id,
                db.results.c.source_message_id == source_message_id)).mappings().first()
            if existing:
                return {"message": cached_message(conn, owner_id, conversation_id, result_id=existing["id"]), "result": dict(existing)}
        table = owned(conn, db.data_tables, plan.table_id, owner_id, lock=not bool(plan.relation_id))
        columns, rows, chart, explanation, snapshot = execute_query(conn, plan, table, owner_id)
        result = save_result(conn, owner_id, conversation_id, table["id"], columns, rows, chart, explanation, snapshot, source_message_id)
        audit(conn, owner_id, "query", table["id"], details={"result_id": result["id"], "snapshot": snapshot})
        message = save_message(conn, owner_id, conversation_id, "assistant", explanation, result_id=result["id"])
    return {"message": message, "result": result}


def preview_mutation(user, conversation_id, plan: QueryPlan, source_message_id=None, workflow_thread=None):
    from .table_actions import owner_action_lock
    owner_id = user["id"]
    with db.engine.begin() as conn:
        owner_action_lock(conn, owner_id)
        owned(conn, db.conversations, conversation_id, owner_id)
        lock_source_message(conn, owner_id, conversation_id, source_message_id)
        if source_message_id:
            existing = conn.execute(select(db.plans).where(db.plans.c.owner_id == owner_id,
                db.plans.c.source_message_id == source_message_id)).mappings().first()
            if existing:
                return {"message": cached_message(conn, owner_id, conversation_id, plan_id=existing["id"]), "plan": dict(existing)}
        table = owned(conn, db.data_tables, plan.table_id, owner_id, lock=True)
        validate_plan(plan, table)
        if plan.operation == "insert":
            values = {f["id"]: plan.values.get(f["id"]) for f in table["fields"]}
            before, after = [], [{"id": db.new_id(), "values": values, "source": {"provenance": "user_confirmed_plan"}}]
        else:
            before = target_rows(conn, plan, table, owner_id)
            after = [{**r, "values": {**r["values"], **plan.values}} for r in before] if plan.operation == "update" else []
        identifier = db.new_id()
        stored = {"id": identifier, "owner_id": owner_id, "conversation_id": conversation_id, "session_id": user["session_id"],
            "table_id": table["id"], "table_version": table["version"], "operation": plan.operation,
            "status": "pending", "plan": plan.model_dump(), "before": before, "after": after,
            "source_message_id": source_message_id, "workflow_thread": workflow_thread,
            "affected_count": len(after) if plan.operation == "insert" else len(before), "expires_at": db.utcnow() + timedelta(minutes=settings.plan_minutes),
            "created_at": db.utcnow(), "intent": f"准备{ {'insert':'新增','update':'修改','delete':'删除'}[plan.operation]}“{table['name']}”中的记录；核对范围和前后值后确认。"}
        conn.execute(insert(db.plans).values(**stored))
        message = save_message(conn, owner_id, conversation_id, "assistant",
            f"待确认：{plan.operation} 将影响 {stored['affected_count']} 条记录。请核对修改前后值，确认后才会执行。", plan_id=identifier)
    return {"message": message, "plan": stored}


def confirm_mutation(plan_id, user):
    from .table_actions import owner_action_lock
    owner_id = user["id"]
    with db.engine.begin() as conn:
        owner_action_lock(conn, owner_id)
        lock_plan_run(conn, plan_id, user)
        # 计划与表均使用行锁；两个并发确认只有一方可以提交，另一方读取已保存结果。
        stored = owned(conn, db.plans, plan_id, owner_id, lock=True)
        if stored["session_id"] != user["session_id"]:
            raise HTTPException(409, "此计划属于另一登录会话，请重新生成预览")
        owned(conn, db.conversations, stored["conversation_id"], owner_id)
        if stored["status"] == "executed":
            result = owned(conn, db.results, stored["result_id"], owner_id)
            return {"plan": stored, "result": result, "replayed": True}
        if stored["status"] != "pending":
            raise HTTPException(409, "此计划已取消或失效，请重新预览")
        if stored["expires_at"] <= db.utcnow():
            raise HTTPException(409, "计划已过期，请重新预览")
        table = owned(conn, db.data_tables, stored["table_id"], owner_id, lock=True)
        if table["version"] != stored["table_version"]:
            raise HTTPException(409, "表版本已变化，请重新预览")
        plan = QueryPlan.model_validate(stored["plan"])
        validate_plan(plan, table)
        if plan.operation != "insert":
            current = target_rows(conn, plan, table, owner_id)
            if current != stored["before"]:
                raise HTTPException(409, "记录范围或内容已变化，请重新预览")
        if plan.operation == "insert":
            row = stored["after"][0]
            position = (conn.scalar(select(func.max(db.data_rows.c.position)).where(db.data_rows.c.table_id == table["id"], db.data_rows.c.owner_id == owner_id)) or 0) + 1
            conn.execute(insert(db.data_rows).values(id=row["id"], table_id=table["id"], owner_id=owner_id,
                values=row["values"], source=row["source"], position=position))
        elif plan.operation == "update":
            for row in stored["after"]:
                source = dict(row["source"])
                source["last_confirmed_plan_id"] = plan_id
                conn.execute(update(db.data_rows).where(db.data_rows.c.id == row["id"], db.data_rows.c.owner_id == owner_id,
                    db.data_rows.c.table_id == table["id"]).values(values=row["values"], source=source))
        else:
            ids = [r["id"] for r in stored["before"]]
            if ids:
                conn.execute(delete(db.data_rows).where(db.data_rows.c.id.in_(ids), db.data_rows.c.owner_id == owner_id,
                    db.data_rows.c.table_id == table["id"]))
        row_count = table["row_count"] + (1 if plan.operation == "insert" else -len(stored["before"]) if plan.operation == "delete" else 0)
        assert_grain_unique(conn, table, owner_id)
        conn.execute(update(db.data_tables).where(db.data_tables.c.id == table["id"], db.data_tables.c.owner_id == owner_id)
            .values(version=table["version"] + 1, row_count=row_count))
        snapshot_rows = stored["before"] if plan.operation == "delete" else stored["after"]
        explanation = f"已确认执行 {plan.operation}，影响 {stored['affected_count']} 条记录。" + ("显示删除前快照。" if plan.operation == "delete" else "仅显示本次影响记录。")
        result = save_result(conn, owner_id, stored["conversation_id"], table["id"], table["fields"],
            [r["values"] for r in snapshot_rows], None, explanation,
            {"plan_id": plan_id, "plan": stored["plan"], "table_version_before": table["version"], "table_version_after": table["version"] + 1,
             "record_ids": [r["id"] for r in snapshot_rows], "display": "before_delete" if plan.operation == "delete" else "after_mutation"})
        conn.execute(update(db.plans).where(db.plans.c.id == plan_id, db.plans.c.owner_id == owner_id)
            .values(status="executed", result_id=result["id"]))
        audit(conn, owner_id, plan.operation, table["id"], plan_id, {"before": stored["before"], "after": stored["after"], "result_id": result["id"]})
        message = save_message(conn, owner_id, stored["conversation_id"], "assistant", explanation, result_id=result["id"], plan_id=plan_id)
        stored.update(status="executed", result_id=result["id"])
    return {"plan": stored, "result": result, "message": message}


def cancel_mutation(plan_id, user):
    with db.engine.begin() as conn:
        plan = owned(conn, db.plans, plan_id, user["id"], lock=True)
        if plan["session_id"] != user["session_id"]:
            raise HTTPException(409, "此计划属于另一会话")
        if plan["status"] == "executed":
            raise HTTPException(409, "已执行的计划不能取消")
        conn.execute(update(db.plans).where(db.plans.c.id == plan_id, db.plans.c.owner_id == user["id"]).values(status="cancelled"))
        plan["status"] = "cancelled"
        return plan


def history_rows(conn, owner_id, limit):
    return [dict(row) for row in conn.execute(select(db.audits).where(db.audits.c.owner_id == owner_id)
        .order_by(db.audits.c.created_at.desc()).limit(limit)).mappings()]


def hidden_results(conn, owner_id):
    return list(conn.scalars(select(db.result_visibility.c.result_id).where(
        db.result_visibility.c.owner_id == owner_id, db.result_visibility.c.hidden.is_(True))))


def lock_plan_run(conn, plan_id, user):
    # 与中止统一按 run→plan 顺序锁定；不能在已经锁plan后反向锁run。
    plan = owned(conn, db.plans, plan_id, user["id"])
    if plan["source_message_id"]:
        run = conn.execute(select(db.assistant_runs).where(db.assistant_runs.c.owner_id == user["id"],
            db.assistant_runs.c.message_id == plan["source_message_id"]).with_for_update()).mappings().first()
        if run and run["status"] != "completed":
            raise HTTPException(409, "规划尚未完成、已中止或已失效；不能执行此计划")


def set_result_visibility(result_id, owner_id, hidden):
    from sqlalchemy.dialects.postgresql import insert as pg_insert
    with db.engine.begin() as conn:
        result = owned(conn, db.results, result_id, owner_id, lock=True)
        existing = conn.execute(select(db.result_visibility).where(db.result_visibility.c.owner_id == owner_id,
            db.result_visibility.c.result_id == result_id)).mappings().first()
        if not existing or existing["hidden"] != hidden:
            conn.execute(pg_insert(db.result_visibility).values(id=db.new_id(), owner_id=owner_id, result_id=result_id,
                hidden=hidden, created_at=db.utcnow()).on_conflict_do_update(constraint="uq_result_visibility_owner_result", set_={"hidden": hidden}))
            audit(conn, owner_id, "result_hidden" if hidden else "result_restored", result["table_id"], details={"result_id": result_id, "hidden": hidden, "data_deleted": False})
        return {"result_id": result_id, "hidden": hidden}
