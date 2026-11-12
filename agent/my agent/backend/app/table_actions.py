"""表页面操作：精确单行预览、按表变更摘要与永久删除。

所有写操作绑定账号、表版本和登录会话；删除同时清除历史数据副本，保留对话文字。
"""

from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
import hashlib
import json
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from langgraph.checkpoint.postgres import PostgresSaver
from pydantic import Field, model_validator
from sqlalchemy import delete, func, insert, select, text, update

from . import auth, db, service
from .data_management import active_table, checked_pending, store_preview
from .jobs import controlled_path
from .parsing import ParseError
from .query import validate_plan
from .schemas import QueryPlan, Strict

router = APIRouter(prefix="/api")
_workflow_owners = ContextVar("table_action_workflow_owners", default=frozenset())


def _lock_key(owner_id):
    return f"vibe:table-actions:{owner_id}"


def owner_action_lock(conn, owner_id, exclusive=False):
    # 普通操作共享锁可并发；永久删除独占锁防止检查任务后又有新工作进入。
    if exclusive:
        acquired = conn.scalar(text("SELECT pg_try_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": _lock_key(owner_id)})
        if not acquired:
            raise HTTPException(409, "当前账号仍有数据操作正在处理，请完成或中止后重新预览删除")
    else:
        conn.execute(text("SELECT pg_advisory_xact_lock_shared(hashtextextended(:key, 0))"),
            {"key": _lock_key(owner_id)})


@contextmanager
def owner_workflow_guard(owner_id):
    # session 锁保护完整工作流，但不把模型网络等待放进数据库事务。
    # 内部查询使用同一键的共享事务锁，因此不会因不同连接嵌套调用而互相阻塞。
    owners = _workflow_owners.get()
    if owner_id in owners:
        # run_message、invoke 和检查点恢复可能嵌套；复用外层锁，避免多占连接池。
        yield
        return
    with db.engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        conn.execute(text("SELECT pg_advisory_lock_shared(hashtextextended(:key, 0))"),
            {"key": _lock_key(owner_id)})
        token = _workflow_owners.set(owners | {owner_id})
        try:
            yield
        finally:
            _workflow_owners.reset(token)
            conn.execute(text("SELECT pg_advisory_unlock_shared(hashtextextended(:key, 0))"),
                {"key": _lock_key(owner_id)})


class RowPreview(Strict):
    conversation_id: str
    version: int = Field(ge=1)
    operation: Literal["insert", "update", "delete"]
    row_id: str | None = None
    values: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def valid_action(self):
        if self.operation == "insert" and self.row_id:
            raise ValueError("新增记录不能指定现有记录 ID")
        if self.operation != "insert" and not self.row_id:
            raise ValueError("修改和删除必须指定实际记录 ID")
        if self.operation in ("insert", "update") and not self.values:
            raise ValueError("新增和修改必须提供字段值")
        if self.operation == "delete" and self.values:
            raise ValueError("删除记录不能附带修改值")
        if len(self.values) > 200:
            raise ValueError("一次最多写入 200 个字段")
        return self


class DeletePreview(Strict):
    conversation_id: str
    version: int = Field(ge=1)


def _row(conn, owner_id, table_id, row_id):
    row = conn.execute(select(db.data_rows).where(db.data_rows.c.owner_id == owner_id,
        db.data_rows.c.table_id == table_id, db.data_rows.c.id == row_id).with_for_update()).mappings().first()
    if not row:
        raise HTTPException(404, "记录不存在或不属于当前数据表")
    return {key: row[key] for key in ("id", "values", "source")}


def preview_row(user, table_id, body):
    with db.engine.begin() as conn:
        owner_action_lock(conn, user["id"])
        table = active_table(conn, table_id, user["id"], lock=True)
        if table["version"] != body.version:
            raise HTTPException(409, "表版本已变化，请刷新记录后重新预览")
        values = {}
        if body.operation != "delete":
            # 只借用已有字段和类型校验；实际更新范围由服务器 row_id 决定。
            validation = QueryPlan(operation="insert", table_id=table_id, values=body.values)
            validate_plan(validation, table)
            values = validation.values
        if body.operation == "insert":
            before = []
            after = [{"id": db.new_id(), "values": {field["id"]: values.get(field["id"]) for field in table["fields"]},
                "source": {"provenance": "user_confirmed_plan"}}]
        else:
            before = [_row(conn, user["id"], table_id, body.row_id)]
            after = [{**before[0], "values": {**before[0]["values"], **values}}] if body.operation == "update" else []
        specification = {"kind": "row_action", "operation": body.operation, "table_id": table_id,
            "row_id": body.row_id, "values": values}
        label = {"insert": "新增", "update": "修改", "delete": "删除"}[body.operation]
        return store_preview(conn, user, body.conversation_id, body.operation, table, specification,
            before, after, 1, f"待确认：在“{table['name']}”中{label} 1 条记录；按实际记录 ID 精确定位，请核对前后值。")


def _confirm_row(conn, stored, user):
    if stored["status"] == "executed":
        return {"plan": stored, "result": service.owned(conn, db.results, stored["result_id"], user["id"]), "replayed": True}
    table = active_table(conn, stored["table_id"], user["id"], lock=True)
    if table["version"] != stored["table_version"]:
        raise HTTPException(409, "表版本已变化，请重新预览")
    operation, specification = stored["operation"], stored["plan"]
    if operation != "insert" and [_row(conn, user["id"], table["id"], specification["row_id"])] != stored["before"]:
        raise HTTPException(409, "记录内容已变化，请重新预览")
    if operation == "insert":
        row = stored["after"][0]
        position = (conn.scalar(select(func.max(db.data_rows.c.position)).where(db.data_rows.c.owner_id == user["id"],
            db.data_rows.c.table_id == table["id"])) or 0) + 1
        conn.execute(insert(db.data_rows).values(id=row["id"], owner_id=user["id"], table_id=table["id"],
            position=position, values=row["values"], source=row["source"]))
    elif operation == "update":
        row = stored["after"][0]
        source = {**row["source"], "last_confirmed_plan_id": stored["id"]}
        conn.execute(update(db.data_rows).where(db.data_rows.c.owner_id == user["id"], db.data_rows.c.table_id == table["id"],
            db.data_rows.c.id == row["id"]).values(values=row["values"], source=source))
    elif operation == "delete":
        conn.execute(delete(db.data_rows).where(db.data_rows.c.owner_id == user["id"], db.data_rows.c.table_id == table["id"],
            db.data_rows.c.id == specification["row_id"]))
    else:
        raise HTTPException(422, "不支持的单行操作")
    service.assert_grain_unique(conn, table, user["id"])
    count = table["row_count"] + (1 if operation == "insert" else -1 if operation == "delete" else 0)
    conn.execute(update(db.data_tables).where(db.data_tables.c.owner_id == user["id"], db.data_tables.c.id == table["id"])
        .values(row_count=count, version=table["version"] + 1))
    snapshot_rows = stored["before"] if operation == "delete" else stored["after"]
    explanation = f"已确认{ {'insert':'新增','update':'修改','delete':'删除'}[operation]} 1 条记录，按实际记录 ID 定位。"
    result = service.save_result(conn, user["id"], stored["conversation_id"], table["id"], table["fields"],
        [row["values"] for row in snapshot_rows], None, explanation,
        {"plan_id": stored["id"], "plan": specification, "record_ids": [row["id"] for row in snapshot_rows],
            "table_version_before": table["version"], "table_version_after": table["version"] + 1})
    conn.execute(update(db.plans).where(db.plans.c.owner_id == user["id"], db.plans.c.id == stored["id"])
        .values(status="executed", result_id=result["id"]))
    service.audit(conn, user["id"], operation, table["id"], stored["id"],
        {"before": stored["before"], "after": stored["after"], "result_id": result["id"], "affected_count": 1})
    message = service.save_message(conn, user["id"], stored["conversation_id"], "assistant", explanation,
        result_id=result["id"], plan_id=stored["id"])
    stored.update(status="executed", result_id=result["id"])
    return {"plan": stored, "result": result, "message": message}


def table_changes(table_id, owner_id, limit=10):
    actions = ("import", "insert", "update", "delete", "metadata_update", "archive_table", "restore_table", "relation", "create_table")
    with db.engine.connect() as conn:
        service.owned(conn, db.data_tables, table_id, owner_id)
        # 先按账号、表和真正变更过滤，再取最近 N 条，避免被其它表的查询日志挤掉。
        operations = conn.execute(select(db.audits).where(db.audits.c.owner_id == owner_id,
            db.audits.c.table_id == table_id, db.audits.c.action.in_(actions))
            .order_by(db.audits.c.created_at.desc(), db.audits.c.id.desc()).limit(limit)).mappings().all()
    result = []
    labels = {"import": "导入数据", "insert": "新增记录", "update": "修改记录", "delete": "删除记录",
        "metadata_update": "更新表与字段说明", "archive_table": "归档数据表", "restore_table": "恢复数据表",
        "relation": "更新表关联", "create_table": "创建数据表"}
    for operation in operations:
        detail = operation["details"] or {}
        affected = detail.get("affected_count", detail.get("row_count"))
        if affected is None and operation["action"] in ("insert", "update", "delete"):
            affected = len(detail.get("after" if operation["action"] == "insert" else "before", []))
        summary = labels[operation["action"]] + (f" · {affected} 条" if affected is not None else "")
        result.append({"id": operation["id"], "table_id": table_id, "action": operation["action"],
            "created_at": operation["created_at"], "summary": summary, "affected_count": affected})
    return {"operations": result}


def _references(value, identifiers):
    if isinstance(value, dict):
        return any(key in identifiers or _references(item, identifiers) for key, item in value.items())
    if isinstance(value, (list, tuple)):
        return any(_references(item, identifiers) for item in value)
    return isinstance(value, str) and value in identifiers


def _owner_rows(conn, table, owner_id):
    return [dict(row) for row in conn.execute(select(table).where(table.c.owner_id == owner_id)).mappings()]


def _inventory(conn, table, owner_id, exclude_plan=None):
    tables = _owner_rows(conn, db.data_tables, owner_id)
    relations = _owner_rows(conn, db.table_relations, owner_id)
    related_relations = [row for row in relations if table["id"] in (row["left_table_id"], row["right_table_id"])]
    identifiers = {table["id"], *(row["id"] for row in related_relations)}
    messages = _owner_rows(conn, db.messages, owner_id)
    results = _owner_rows(conn, db.results, owner_id)
    plans = [row for row in _owner_rows(conn, db.plans, owner_id) if row["id"] != exclude_plan]
    audits = _owner_rows(conn, db.audits, owner_id)
    request_ids = {row["id"] for row in messages if _references(row["request"], identifiers)}
    # 联表的右表没有 results.table_id 外键；递归检查登记 ID 和源请求，覆盖旧查询副本。
    result_ids, plan_ids = set(), set()
    while True:
        known = identifiers | result_ids | plan_ids
        next_results = {row["id"] for row in results if row["table_id"] == table["id"]
            or row["source_message_id"] in request_ids or _references(row["query_snapshot"], known)}
        next_plans = {row["id"] for row in plans if row["table_id"] == table["id"]
            or row["source_message_id"] in request_ids or row["result_id"] in next_results
            or _references({"plan": row["plan"], "before": row["before"], "after": row["after"]}, known)}
        if next_results == result_ids and next_plans == plan_ids:
            break
        result_ids, plan_ids = next_results, next_plans
    related_results = [row for row in results if row["id"] in result_ids]
    related_plans = [row for row in plans if row["id"] in plan_ids]
    related_messages = [row for row in messages if row["id"] in request_ids or row["result_id"] in result_ids or row["plan_id"] in plan_ids]
    known = identifiers | result_ids | plan_ids
    related_runs = [row for row in _owner_rows(conn, db.assistant_runs, owner_id)
        if row["message_id"] in request_ids or _references(row["response"], known)]
    shared_tables = [row for row in tables if table.get("file_id") and row["id"] != table["id"] and row["file_id"] == table["file_id"]]
    avatar_reference = bool(table.get("file_id")) and bool(conn.scalar(select(db.users.c.id)
        .where(db.users.c.avatar_file_id == table["file_id"]).limit(1)))
    remove_file = bool(table.get("file_id")) and not shared_tables and not avatar_reference
    tasks = _owner_rows(conn, db.tasks, owner_id)
    related_tasks = [row for row in tasks if table["id"] in (row["table_ids"] or [])
        or (table.get("file_id") and row["file_id"] == table["file_id"])]
    related_audits = [row for row in audits if row["table_id"] == table["id"] or row["plan_id"] in plan_ids
        or _references(row["details"], known | ({table["file_id"]} if remove_file else set()))]
    candidates = {row["details"]["candidate_id"] for row in audits if row["table_id"] == table["id"]
        and row["action"] == "import" and (row["details"] or {}).get("candidate_id")}
    row_sources = conn.execute(select(db.data_rows.c.source).where(db.data_rows.c.owner_id == owner_id,
        db.data_rows.c.table_id == table["id"])).scalars()
    candidates.update(source["candidate_id"] for source in row_sources if source.get("candidate_id"))
    threads = {f"{owner_id}:{message_id}" for message_id in request_ids}
    threads.update(row["workflow_thread"] for row in related_plans if row["workflow_thread"])
    files = []
    if remove_file:
        files.append(service.owned(conn, db.files, table["file_id"], owner_id))
    summary = {"rows": table["row_count"], "relations": len(related_relations), "results": len(related_results),
        "plans": len(related_plans), "tasks": len(related_tasks), "checkpoints": len(threads),
        "source_file_deleted": remove_file, "source_file_shared": bool(shared_tables) or avatar_reference}
    signature = {"table_version": table["version"], "summary": summary, "results": sorted(result_ids), "plans": sorted(plan_ids),
        "audits": sorted(row["id"] for row in related_audits), "relations": sorted(row["id"] for row in related_relations),
        "runs": sorted((row["id"], row["status"]) for row in related_runs),
        "tasks": sorted((row["id"], row["status"], row["version"], row["table_ids"]) for row in related_tasks),
        "shared_tables": sorted(row["id"] for row in shared_tables), "threads": sorted(threads)}
    digest = hashlib.sha256(json.dumps(signature, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    return {"results": related_results, "plans": related_plans, "messages": related_messages, "runs": related_runs,
        "audits": related_audits, "relations": related_relations, "tasks": related_tasks, "files": files,
        "candidate_ids": candidates, "threads": threads, "tables": tables, "summary": summary, "digest": digest}


def _check_active(inventory):
    if any(row["status"] in ("queued", "running") for row in inventory["runs"]):
        raise HTTPException(409, "此表仍有助手问题正在处理，请中止或完成后重新预览删除")
    if any(row["status"] in ("pending", "running") for row in inventory["tasks"]):
        raise HTTPException(409, "此表或共享源文件仍有解析/导出任务，请等待完成后重新预览删除")


def preview_delete(user, table_id, body):
    with db.engine.begin() as conn:
        owner_action_lock(conn, user["id"])
        table = service.owned(conn, db.data_tables, table_id, user["id"], lock=True)
        if table["version"] != body.version:
            raise HTTPException(409, "表版本已变化，请刷新后重新预览")
        inventory = _inventory(conn, table, user["id"])
        _check_active(inventory)
        specification = {"kind": "table_delete", "operation": "delete_table", "table_id": table_id,
            "table_name": table["name"], "resources_digest": inventory["digest"], "summary": inventory["summary"]}
        # 删除预览只保存范围摘要，不再复制整表数据，为最后的不可恢复确认提供明确依据。
        return store_preview(conn, user, body.conversation_id, "delete_table", table, specification,
            [{"id": table_id, "values": {"name": table["name"], "row_count": table["row_count"], **inventory["summary"]}}], [],
            table["row_count"], f"待确认：永久删除“{table['name']}”及 {table['row_count']} 条记录，清除关联、结果和历史数据副本；保留对话文字。此操作不可恢复。")


def _purge(conn, stored, table, inventory, user):
    owner_id = user["id"]
    result_ids = {row["id"] for row in inventory["results"]}
    plan_ids = {row["id"] for row in inventory["plans"]}
    removed_ids = {table["id"], *(row["id"] for row in inventory["relations"]), *result_ids, *plan_ids}
    for message in inventory["messages"]:
        request = deepcopy(message["request"])
        if request:
            request["table_ids"] = [identifier for identifier in request.get("table_ids", []) if identifier != table["id"]]
            if _references(request.get("plan"), removed_ids):
                request["plan"] = None
            if request.get("source_message_id") in removed_ids:
                request.pop("source_message_id", None)
        conn.execute(update(db.messages).where(db.messages.c.owner_id == owner_id, db.messages.c.id == message["id"])
            .values(result_id=None if message["result_id"] in result_ids else message["result_id"],
                plan_id=None if message["plan_id"] in plan_ids else message["plan_id"], request=request))
    for run in inventory["runs"]:
        conn.execute(update(db.assistant_runs).where(db.assistant_runs.c.owner_id == owner_id, db.assistant_runs.c.id == run["id"])
            .values(response=None, updated_at=db.utcnow()))
    for audit in inventory["audits"]:
        detail = audit["details"] or {}
        affected = detail.get("affected_count", detail.get("row_count"))
        if affected is None and audit["action"] in ("insert", "update", "delete"):
            affected = len(detail.get("after" if audit["action"] == "insert" else "before", []))
        conn.execute(update(db.audits).where(db.audits.c.owner_id == owner_id, db.audits.c.id == audit["id"])
            .values(table_id=None, plan_id=None, details={"deleted_table_id": table["id"], "table_name": table["name"],
                "data_removed": True, "affected_count": affected}))
    conn.execute(delete(db.result_visibility).where(db.result_visibility.c.owner_id == owner_id, db.result_visibility.c.result_id.in_(result_ids)))
    conn.execute(delete(db.plans).where(db.plans.c.owner_id == owner_id, db.plans.c.id.in_(plan_ids)))
    conn.execute(delete(db.results).where(db.results.c.owner_id == owner_id, db.results.c.id.in_(result_ids)))
    conn.execute(delete(db.table_relations).where(db.table_relations.c.owner_id == owner_id,
        db.table_relations.c.id.in_([row["id"] for row in inventory["relations"]])))
    conn.execute(delete(db.data_rows).where(db.data_rows.c.owner_id == owner_id, db.data_rows.c.table_id == table["id"]))
    paths = [row["stored_name"] for row in inventory["files"]]
    for task in inventory["tasks"]:
        if task["kind"] == "export" or inventory["summary"]["source_file_deleted"]:
            if task["output_name"]:
                paths.append(task["output_name"])
            conn.execute(delete(db.tasks).where(db.tasks.c.owner_id == owner_id, db.tasks.c.id == task["id"]))
        else:
            preview = deepcopy(task["preview"])
            # 共享上传文件继续供其它表使用；被删表的候选缓存不能成为历史快照的旁路。
            if preview and inventory["candidate_ids"]:
                preview["tables"] = [candidate for candidate in preview.get("tables", [])
                    if candidate.get("candidate_id") not in inventory["candidate_ids"]]
            elif preview:
                preview = None
            conn.execute(update(db.tasks).where(db.tasks.c.owner_id == owner_id, db.tasks.c.id == task["id"])
                .values(preview=preview, table_ids=[identifier for identifier in (task["table_ids"] or []) if identifier != table["id"]],
                    version=task["version"] + 1, updated_at=db.utcnow()))
    for other in inventory["tables"]:
        if other["id"] == table["id"]:
            continue
        structure = deepcopy(other["structure"] or {})
        if any(relation.get("table_id") == table["id"] for relation in structure.get("relations", [])):
            structure["relations"] = [relation for relation in structure["relations"] if relation.get("table_id") != table["id"]]
            conn.execute(update(db.data_tables).where(db.data_tables.c.owner_id == owner_id, db.data_tables.c.id == other["id"])
                .values(structure=structure))
    # 使用已安装 Saver 的公共删除 API，底层连接参与同一 SQL 事务，失败则整次删除回滚。
    saver = PostgresSaver(conn.connection.driver_connection)
    for thread in inventory["threads"]:
        saver.delete_thread(thread)
    conn.execute(update(db.plans).where(db.plans.c.owner_id == owner_id, db.plans.c.id == stored["id"])
        .values(table_id=None, result_id=None, before=[], after=[], status="executed",
            plan={"kind": "table_delete", "operation": "delete_table", "deleted_table_id": table["id"],
                "table_name": table["name"], "summary": inventory["summary"], "cleanup": {"files": sorted(set(paths)), "completed": False}}))
    conn.execute(delete(db.data_tables).where(db.data_tables.c.owner_id == owner_id, db.data_tables.c.id == table["id"]))
    for file in inventory["files"]:
        conn.execute(delete(db.files).where(db.files.c.owner_id == owner_id, db.files.c.id == file["id"]))
    explanation = f"已永久删除“{table['name']}”及 {table['row_count']} 条记录，历史数据快照已清除；对话文字和操作摘要保留。"
    service.audit(conn, owner_id, "delete_table", plan_id=stored["id"], details={"deleted_table_id": table["id"],
        "table_name": table["name"], "summary": inventory["summary"], "data_removed": True})
    message = service.save_message(conn, owner_id, stored["conversation_id"], "assistant", explanation, plan_id=stored["id"])
    return {"plan": service.owned(conn, db.plans, stored["id"], owner_id), "message": message, "deleted_table_id": table["id"]}


def _finish_files(response, user):
    stored = response["plan"]
    cleanup = stored["plan"].get("cleanup", {})
    if cleanup.get("completed"):
        return response
    try:
        for stored_name in cleanup.get("files", []):
            # 仅删除应用上传根目录内登记的单个文件，绝不解析用户输入路径或递归删除。
            controlled_path(stored_name).unlink(missing_ok=True)
    except ParseError as exc:
        raise HTTPException(503, "数据表已删除，但登记的文件清理路径无效；请联系管理员处理此计划") from exc
    except OSError as exc:
        raise HTTPException(503, "数据表已删除，但文件清理尚未完成；请重试确认此计划以完成清理") from exc
    with db.engine.begin() as conn:
        owner_action_lock(conn, user["id"])
        latest = service.owned(conn, db.plans, stored["id"], user["id"], lock=True)
        specification = {**latest["plan"], "cleanup": {"files": [], "completed": True}}
        conn.execute(update(db.plans).where(db.plans.c.owner_id == user["id"], db.plans.c.id == stored["id"]).values(plan=specification))
        response["plan"] = {**latest, "plan": specification}
    return response


def confirm_action(plan_id, user):
    with db.engine.connect() as conn:
        initial = service.owned(conn, db.plans, plan_id, user["id"])
    if initial["operation"] != "delete_table" and initial["plan"].get("kind") != "row_action":
        raise HTTPException(422, "这不是表页面操作计划")
    with db.engine.begin() as conn:
        owner_action_lock(conn, user["id"], exclusive=initial["operation"] == "delete_table")
        stored = checked_pending(conn, plan_id, user)
        if stored["operation"] != "delete_table":
            return _confirm_row(conn, stored, user)
        if stored["status"] == "executed":
            response = {"plan": stored, "deleted_table_id": stored["plan"]["deleted_table_id"], "replayed": True}
        else:
            table = service.owned(conn, db.data_tables, stored["table_id"], user["id"], lock=True)
            if table["version"] != stored["table_version"]:
                raise HTTPException(409, "表版本已变化，请重新预览永久删除")
            inventory = _inventory(conn, table, user["id"], exclude_plan=plan_id)
            _check_active(inventory)
            if inventory["digest"] != stored["plan"]["resources_digest"]:
                raise HTTPException(409, "相关结果、计划或文件范围已变化，请重新预览永久删除")
            response = _purge(conn, stored, table, inventory, user)
    # SQL 与文件系统无法形成同一事务；失败保留可重复确认的清理清单，明确报告未完成。
    return _finish_files(response, user)


@router.post("/tables/{table_id}/rows/preview")
def row_preview(table_id: str, body: RowPreview, user=Depends(auth.current_user)):
    return preview_row(user, table_id, body)


@router.get("/tables/{table_id}/changes")
def changes(table_id: str, limit: int = Query(10, ge=1, le=100), user=Depends(auth.current_user)):
    return table_changes(table_id, user["id"], limit)


@router.post("/tables/{table_id}/delete/preview")
def delete_preview(table_id: str, body: DeletePreview, user=Depends(auth.current_user)):
    return preview_delete(user, table_id, body)


@router.post("/table-actions/plans/{plan_id}/confirm")
async def confirm(plan_id: str, request: Request, user=Depends(auth.current_user)):
    if await request.body():
        raise HTTPException(422, "确认只能提交计划标识，不能附带修改内容")
    # 和现有确认入口保持空请求契约；阻塞数据库操作交给 FastAPI 的线程池。
    from starlette.concurrency import run_in_threadpool
    return await run_in_threadpool(confirm_action, plan_id, user)
