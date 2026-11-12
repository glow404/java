"""整表管理的持久预览与一次性确认：创建、归档和恢复均保留审计。"""

from datetime import timedelta
from fastapi import HTTPException
from sqlalchemy import insert, select, update
from . import db
from .config import settings
from .schemas import FieldMeta


def active_table(conn, identifier, owner_id, lock=False):
    from .service import owned
    table = owned(conn, db.data_tables, identifier, owner_id, lock=lock)
    if table.get("archived"):
        raise HTTPException(409, "数据表已归档，请先恢复再操作")
    return table


def store_preview(conn, user, conversation_id, operation, table, specification, before, after, affected_count, text):
    from . import service
    service.owned(conn, db.conversations, conversation_id, user["id"])
    stored = {"id": db.new_id(), "owner_id": user["id"], "conversation_id": conversation_id,
        "session_id": user["session_id"], "table_id": table["id"] if table else None,
        "table_version": table["version"] if table else 0, "operation": operation, "status": "pending",
        "plan": specification, "intent": text, "before": before, "after": after, "affected_count": affected_count,
        "expires_at": db.utcnow() + timedelta(minutes=settings.plan_minutes), "created_at": db.utcnow()}
    conn.execute(insert(db.plans).values(**stored))
    message = service.save_message(conn, user["id"], conversation_id, "assistant", text, plan_id=stored["id"])
    return {"message": message, "plan": stored}


def checked_pending(conn, plan_id, user):
    from . import service
    stored = service.owned(conn, db.plans, plan_id, user["id"], lock=True)
    if stored["session_id"] != user["session_id"]:
        raise HTTPException(409, "此计划属于另一登录会话，请重新预览")
    service.owned(conn, db.conversations, stored["conversation_id"], user["id"])
    if stored["status"] == "executed":
        return stored
    if stored["status"] != "pending" or stored["expires_at"] <= db.utcnow():
        raise HTTPException(409, "计划已取消或过期，请重新预览")
    return stored


def replay(conn, stored, user):
    from . import service
    return {"plan": stored, "result": service.owned(conn, db.results, stored["result_id"], user["id"]), "replayed": True}


def finish(conn, stored, user, table_id, rows, explanation, snapshot):
    from . import service
    # 结果、计划状态与审计共享同一事务；保存任一步失败，前面的元数据变更一起回滚。
    columns = [{"id": "operation", "name": "操作", "type": "string", "unit": ""},
        {"id": "name", "name": "数据表/关联", "type": "string", "unit": ""},
        {"id": "affected_count", "name": "影响记录数", "type": "integer", "unit": "条"}]
    result = service.save_result(conn, user["id"], stored["conversation_id"], table_id, columns, rows,
        None, explanation, {"plan_id": stored["id"], "operation": stored["operation"], **snapshot})
    conn.execute(update(db.plans).where(db.plans.c.id == stored["id"], db.plans.c.owner_id == user["id"])
        .values(status="executed", result_id=result["id"], table_id=table_id))
    service.audit(conn, user["id"], stored["operation"], table_id, stored["id"],
        {"before": stored["before"], "after": stored["after"], "result_id": result["id"], **snapshot})
    message = service.save_message(conn, user["id"], stored["conversation_id"], "assistant", explanation,
        result_id=result["id"], plan_id=stored["id"])
    stored.update(status="executed", result_id=result["id"], table_id=table_id)
    return {"plan": stored, "result": result, "message": message}


def preview_create_table(user, conversation_id, name, fields, description=""):
    from .table_actions import owner_action_lock
    if not name.strip() or len(name) > 200 or len(description) > 5000 or not 1 <= len(fields) <= 200:
        raise HTTPException(422, "请提供表名与 1 至 200 个字段，并遵守说明长度上限")
    normalized = [FieldMeta.model_validate(f).model_dump() if isinstance(f, dict) else f.model_dump() for f in fields]
    if len({f["id"] for f in normalized}) != len(normalized):
        raise HTTPException(422, "新表字段标识必须唯一")
    for field in normalized:
        # 手工输入的说明不能冒充源文件原文。
        field["provenance"], field["type_provenance"] = "user", "user"
    specification = {"operation": "create_table", "new_table_id": db.new_id(), "name": name.strip(),
        "description": description, "fields": normalized}
    with db.engine.begin() as conn:
        owner_action_lock(conn, user["id"])
        return store_preview(conn, user, conversation_id, "create_table", None, specification, [],
            [{"id": specification["new_table_id"], "values": {"name": name.strip(), "description": description,
                "fields": normalized, "row_count": 0}}], 0, f"待确认：创建空数据表“{name.strip()}”，包含 {len(fields)} 个字段。")


def preview_table_state(user, conversation_id, table_id, archived, version=None):
    from . import service
    from .table_actions import owner_action_lock
    with db.engine.begin() as conn:
        owner_action_lock(conn, user["id"])
        table = service.owned(conn, db.data_tables, table_id, user["id"], lock=True)
        if version is not None and version != table["version"]:
            raise HTTPException(409, "表版本已变化，请刷新后重新预览")
        if bool(table.get("archived")) == archived:
            raise HTTPException(409, "数据表已经处于该状态")
        operation = "archive_table" if archived else "restore_table"
        original = {"name": table["name"], "archived": bool(table.get("archived")), "row_count": table["row_count"]}
        return store_preview(conn, user, conversation_id, operation, table,
            {"operation": operation, "table_id": table_id, "archived": archived},
            [{"id": table_id, "values": original}], [{"id": table_id, "values": {**original, "archived": archived}}],
            table["row_count"], f"待确认：{'归档' if archived else '恢复'}“{table['name']}”，保留 {table['row_count']} 条数据、源文件和历史操作。")


def confirm_management(plan_id, user):
    from . import service, relations
    from .table_actions import owner_action_lock
    with db.engine.begin() as conn:
        owner_action_lock(conn, user["id"])
        service.lock_plan_run(conn, plan_id, user)
        stored = checked_pending(conn, plan_id, user)
        if stored["status"] == "executed":
            return replay(conn, stored, user)
        if stored["operation"] == "relation":
            return relations.confirm_relation(conn, stored, user)
        operation = stored["operation"]
        if operation == "create_table":
            specification = stored["plan"]
            table = {"id": specification["new_table_id"], "owner_id": user["id"], "name": specification["name"],
                "description": specification["description"], "description_provenance": "user", "fields": specification["fields"],
                "structure": {"origin": "manual"}, "version": 1, "row_count": 0, "archived": False, "created_at": db.utcnow()}
            conn.execute(insert(db.data_tables).values(**table))
        elif operation in ("archive_table", "restore_table"):
            table = service.owned(conn, db.data_tables, stored["table_id"], user["id"], lock=True)
            if table["version"] != stored["table_version"] or table["row_count"] != stored["before"][0]["values"]["row_count"]:
                raise HTTPException(409, "表版本或影响范围变化，请重新预览")
            archived = operation == "archive_table"
            if bool(table.get("archived")) == archived:
                raise HTTPException(409, "表状态已变化，请重新预览")
            conn.execute(update(db.data_tables).where(db.data_tables.c.id == table["id"], db.data_tables.c.owner_id == user["id"])
                .values(archived=archived, version=table["version"] + 1))
        else:
            raise HTTPException(422, "这不是数据表管理计划")
        explanation = f"已确认{'创建空表' if operation == 'create_table' else '归档数据表' if operation == 'archive_table' else '恢复数据表'}“{table['name']}”。数据、源文件与操作历史均保留。"
        return finish(conn, stored, user, table["id"], [{"operation": operation, "name": table["name"],
            "affected_count": stored["affected_count"]}], explanation, {"table_id": table["id"], "before": stored["before"], "after": stored["after"]})
