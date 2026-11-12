"""查询与元数据修改的下一步预览；确认后才产生结果或修改服务器资料。"""
from copy import deepcopy
from datetime import timedelta
from fastapi import HTTPException
from sqlalchemy import insert, select, update
from . import db, service
from .config import settings
from .schemas import QueryPlan, TablePatch, FieldMeta
from .query import validate_plan, execute_query, set_timeout, relation_table, validate_relation_statistics
from .data_management import active_table, checked_pending, replay


def metadata_target(table, plan, source=None):
    if plan.metadata is None:
        raise HTTPException(422, "元数据修改项缺失")
    fields = [FieldMeta.model_validate(field).model_dump() for field in table["fields"]]
    registered = {field["id"]: field for field in fields}
    structured = bool(source and (source.get("request") or {}).get("plan"))
    content = (source or {}).get("content", "")
    def origin(value):
        return "user" if structured or (isinstance(value, str) and value and value in content) else "confirmed_inference"
    for change in plan.metadata.field_changes:
        if change.field_id not in registered:
            raise HTTPException(422, "元数据修改引用未登记字段")
        updates = change.model_dump(exclude_none=True)
        updates.pop("field_id")
        provenance = "user" if structured or all(isinstance(value, str) and origin(value) == "user" for value in updates.values()) else "confirmed_inference"
        registered[change.field_id].update(updates, provenance=provenance)
        if change.type is not None:
            registered[change.field_id]["type_provenance"] = provenance
    return TablePatch(version=table["version"], name=plan.metadata.name or table["name"],
        description=plan.metadata.description if plan.metadata.description is not None else table["description"],
        description_provenance=origin(plan.metadata.description) if plan.metadata.description is not None else table["description_provenance"], fields=fields)


def metadata_snapshot(table):
    return {key: deepcopy(table[key]) for key in ("name", "description", "description_provenance", "fields", "row_count")}


def relation_snapshot(context):
    relation = context["relation"]
    return {**{key: relation[key] for key in ("id", "left_table_id", "right_table_id", "left_field_id", "right_field_id", "join_type", "left_version", "right_version")},
        "left_name": context["left"]["name"], "right_name": context["right"]["name"],
        "left_field": next(field["name"] for field in context["left"]["fields"] if field["id"] == relation["left_field_id"]),
        "right_field": next(field["name"] for field in context["right"]["fields"] if field["id"] == relation["right_field_id"]),
        "statistics": context["stats"]}


def describe_query(plan, table, fields):
    names = {field["id"]: field["name"] for field in fields}
    text = f"下一步：查询“{table['name']}”。基表现有 {table['row_count']} 条记录，以下数量不是过滤后的结果数。"
    if plan.relation_id:
        text += " 使用已经确认的双表关联，连接字段和基数见计划。"
    text += " 字段：" + "、".join(names.get(identifier, identifier) for identifier in (plan.fields or plan.group_by or list(names))) + "。"
    if plan.filters:
        text += " 过滤：" + "；".join(f"{names.get(item.field_id, item.field_id)} {item.op} {item.value}" for item in plan.filters) + "。"
    if plan.group_by:
        text += " 按" + "、".join(names.get(identifier, identifier) for identifier in plan.group_by) + "分组。"
    if plan.aggregates:
        text += " 统计：" + "、".join(f"{names.get(item.field_id, '记录')} {item.function}" for item in plan.aggregates) + "；均值仅计非空值。"
    if plan.ratio:
        text += f" 占比阈值 {plan.ratio.threshold}；分母为{'全部记录' if plan.ratio.denominator == 'all' else '该字段非空记录'}。"
    if plan.extremum:
        direction = "最小" if plan.extremum.direction == "min" else "最大"
        text += f" 在上述过滤范围内取 {names.get(plan.extremum.field_id, plan.extremum.field_id)} 的{direction}值，返回全部并列对应记录；空值不参与极值计算。"
    if plan.assumptions:
        text += "\n建议口径（待确认）：\n" + "\n".join(
            f"- {item.subject}：{item.choice}。依据：{item.reason}" +
            (f"；可改为：{'、'.join(item.alternatives)}" if item.alternatives else "")
            for item in plan.assumptions)
        text += "\n这些是本次计划的待确认解释，不会改写字段资料；如需调整，请继续提问生成新预览。"
    text += f" 最多返回 {min(plan.limit, settings.result_limit)} 条，偏移 {plan.offset}；确认后执行，或继续提问调整/取消。"
    return text


def preview_operation(user, conversation_id, plan, source_message_id=None, workflow_thread=None):
    if plan.operation not in ("select", "metadata"):
        return service.preview_mutation(user, conversation_id, plan, source_message_id, workflow_thread)
    with db.engine.begin() as conn:
        from .table_actions import owner_action_lock
        owner_action_lock(conn, user["id"])
        service.owned(conn, db.conversations, conversation_id, user["id"])
        service.lock_source_message(conn, user["id"], conversation_id, source_message_id)
        if source_message_id:
            existing = conn.execute(select(db.plans).where(db.plans.c.owner_id == user["id"], db.plans.c.source_message_id == source_message_id)).mappings().first()
            if existing:
                return {"plan": dict(existing), "message": service.cached_message(conn, user["id"], conversation_id, plan_id=existing["id"])}
        context = None
        if plan.relation_id:
            from .relations import confirmed_context
            context = confirmed_context(conn, plan.relation_id, user["id"], plan.table_id, lock=True)
            table = context["left"]
            fields = validate_plan(plan, relation_table(context))
            validate_relation_statistics(plan, context)
        else:
            table = active_table(conn, plan.table_id, user["id"], lock=True)
            fields = validate_plan(plan, table)
        before, after = [], []
        if plan.operation == "metadata":
            source = service.owned(conn, db.messages, source_message_id, user["id"]) if source_message_id else None
            target = metadata_target(table, plan, source)
            # 类型改变先验证全表并提供有限行样本，不能静默吞掉失败值或丢失前导零。
            changes_type = any(field.type != next(old["type"] for old in table["fields"] if old["id"] == field.id) for field in target.fields)
            samples = []
            if changes_type:
                set_timeout(conn)
                for row in conn.execute(select(db.data_rows).where(db.data_rows.c.owner_id == user["id"], db.data_rows.c.table_id == table["id"])
                    .order_by(db.data_rows.c.position, db.data_rows.c.id)).mappings():
                    converted = service.convert_values(row["values"], [field.model_dump() for field in target.fields])
                    if len(samples) < 20:
                        samples.append({"before": row["values"], "after": converted})
            before = [{"id": table["id"], "values": metadata_snapshot(table)}]
            after = [{"id": table["id"], "values": {**target.model_dump(exclude={"version"}), "row_count": table["row_count"], "conversion_samples": samples}}]
            count = len(plan.metadata.field_changes) + int(plan.metadata.name is not None) + int(plan.metadata.description is not None)
            intent = f"下一步：修改“{table['name']}”的 {count} 项表/字段资料。用户明确提供的内容标为用户输入，模型补充的说明经确认后标为确认推断。"
            if changes_type:
                intent += f" 字段类型转换将检查并转换 {table['row_count']} 条数据；以下最多20条转换样本，前导零可能随类型改变而丢失。"
            intent += " 核对前后说明后确认，或追问修改/取消。"
        else:
            count = table["row_count"]
            intent = describe_query(plan, table, list(fields.values()))
            if context:
                # 把双表版本、连接键和真实基数固定在预览里，确认时重新计算并逐项比较。
                after = [{"id": plan.relation_id, "values": relation_snapshot(context)}]
        stored = {"id": db.new_id(), "owner_id": user["id"], "conversation_id": conversation_id, "session_id": user["session_id"],
            "table_id": table["id"], "table_version": table["version"], "operation": plan.operation, "status": "pending",
            "plan": plan.model_dump(), "before": before, "after": after, "affected_count": count, "intent": intent,
            "source_message_id": source_message_id, "workflow_thread": workflow_thread,
            "expires_at": db.utcnow() + timedelta(minutes=settings.plan_minutes), "created_at": db.utcnow()}
        conn.execute(insert(db.plans).values(**stored))
        message = service.save_message(conn, user["id"], conversation_id, "assistant", intent, plan_id=stored["id"])
        return {"plan": stored, "message": message}


def confirm_operation(plan_id, user):
    with db.engine.connect() as conn:
        saved = service.owned(conn, db.plans, plan_id, user["id"])
        operation = saved["operation"]
    if operation == "delete_table" or saved["plan"].get("kind") == "row_action":
        from .table_actions import confirm_action
        return confirm_action(plan_id, user)
    if operation in ("relation", "create_table", "archive_table", "restore_table"):
        from .data_management import confirm_management
        return confirm_management(plan_id, user)
    if operation not in ("select", "metadata"):
        return service.confirm_mutation(plan_id, user)
    with db.engine.begin() as conn:
        from .table_actions import owner_action_lock
        owner_action_lock(conn, user["id"])
        service.lock_plan_run(conn, plan_id, user)
        stored = checked_pending(conn, plan_id, user)
        if stored["status"] == "executed":
            return replay(conn, stored, user)
        plan = QueryPlan.model_validate(stored["plan"])
        table = active_table(conn, stored["table_id"], user["id"], lock=not bool(plan.relation_id))
        if table["version"] != stored["table_version"]:
            raise HTTPException(409, "表版本已变化，请重新预览下一步操作")
        if operation == "select":
            if plan.relation_id:
                from .relations import confirmed_context
                context = confirmed_context(conn, plan.relation_id, user["id"], plan.table_id, lock=True)
                if not stored["after"] or relation_snapshot(context) != stored["after"][0]["values"]:
                    raise HTTPException(409, "双表连接键、版本或基数变化，请重新预览查询")
            columns, rows, chart, explanation, snapshot = execute_query(conn, plan, table, user["id"])
        else:
            if metadata_snapshot(table) != stored["before"][0]["values"]:
                raise HTTPException(409, "元数据范围已变化，请重新预览")
            source = service.owned(conn, db.messages, stored["source_message_id"], user["id"]) if stored["source_message_id"] else None
            target = metadata_target(table, plan, source)
            updated = service.apply_table_patch(conn, table["id"], target, user["id"], emit_audit=False)
            columns = [{"id": "name", "name": "表", "type": "string"}, {"id": "description", "name": "说明", "type": "string"}, {"id": "fields", "name": "字段资料", "type": "string"}]
            rows, chart = [{key: updated[key] for key in ("name", "description", "fields")}], None
            explanation = f"已确认修改“{updated['name']}”的表/字段资料；用户修改不会标为文件原文。"
            snapshot = {"plan": stored["plan"], "before": stored["before"], "after": metadata_snapshot(updated)}
        snapshot["plan_id"] = plan_id
        result = service.save_result(conn, user["id"], stored["conversation_id"], table["id"], columns, rows, chart, explanation, snapshot, stored["source_message_id"])
        service.audit(conn, user["id"], "query" if operation == "select" else "metadata_update", table["id"], plan_id,
            {"result_id": result["id"], "snapshot": snapshot})
        conn.execute(update(db.plans).where(db.plans.c.id == plan_id, db.plans.c.owner_id == user["id"]).values(status="executed", result_id=result["id"]))
        stored.update(status="executed", result_id=result["id"])
        message = service.save_message(conn, user["id"], stored["conversation_id"], "assistant", explanation, result_id=result["id"], plan_id=plan_id)
        return {"plan": stored, "result": result, "message": message}
