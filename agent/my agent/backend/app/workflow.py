"""LangGraph 持久工作流：认证、目录、计划、校验、查询/预览与确认检查点。"""

from typing import TypedDict, Any
from fastapi import HTTPException
from sqlalchemy import select, update
from langgraph.graph import StateGraph, START, END
from langgraph.types import interrupt, Command
from langgraph.checkpoint.postgres import PostgresSaver
from . import db, service
from .config import settings
from .schemas import QueryPlan
from .query import validate_plan
from .model_adapter import ModelAdapter


class State(TypedDict, total=False):
    owner_id: str
    session_id: str
    conversation_id: str
    message_id: str
    thread_id: str
    content: str
    table_ids: list[str]
    supplied_plan: dict | None
    tables: list[dict]
    history: list[dict]
    plan: dict
    response: dict
    preview_only: bool
    run_id: str


def checkpoint_url():
    return settings.database_url.replace("postgresql+psycopg://", "postgresql://", 1)


def setup_checkpoints():
    with PostgresSaver.from_conn_string(checkpoint_url()) as saver:
        saver.setup()


def verify_identity(state):
    with db.engine.connect() as conn:
        session = conn.execute(select(db.sessions).join(db.users).where(db.sessions.c.id == state["session_id"],
            db.sessions.c.owner_id == state["owner_id"], db.sessions.c.expires_at > db.utcnow(), db.users.c.active.is_(True))).mappings().first()
        if not session:
            raise HTTPException(401, "会话已过期，请重新登录")
        service.owned(conn, db.conversations, state["conversation_id"], state["owner_id"])
    return {}


def metadata_node(state):
    check_run(state)
    tables = []
    with db.engine.connect() as conn:
        for identifier in state["table_ids"]:
            table = service.owned(conn, db.data_tables, identifier, state["owner_id"])
            if table.get("archived"):
                raise HTTPException(409, "所选表已归档，请先恢复")
            limited = {"id": table["id"], "name": table["name"], "description": table["description"][:500],
                "fields": [{k: (v[:500] if isinstance(v, str) else v) for k, v in f.items()} for f in table["fields"]]}
            from .interpretation import add_rate_hints
            add_rate_hints(conn, table, limited["fields"])
            sample_ids = [f["id"] for f in table["fields"] if f["name"] in state["content"]][:5]
            if settings.model_allowed_data and settings.model_send_samples and sample_ids:
                samples = conn.execute(select(db.data_rows.c["values"]).where(db.data_rows.c.owner_id == state["owner_id"],
                    db.data_rows.c.table_id == identifier).order_by(db.data_rows.c.position, db.data_rows.c.id).limit(3)).scalars().all()
                limited["samples"] = [{k: (str(row.get(k))[:100] if row.get(k) is not None else None) for k in sample_ids} for row in samples]
            tables.append(limited)
        # 仅把已确认且两端都属于本次选择的关系加入模型登记表；不传重复键样本或整表。
        from .relations import confirmed_context
        relations = conn.execute(select(db.table_relations).where(db.table_relations.c.owner_id == state["owner_id"],
            db.table_relations.c.status == "active", db.table_relations.c.left_table_id.in_(state["table_ids"]),
            db.table_relations.c.right_table_id.in_(state["table_ids"]))).mappings()
        for relation in relations:
            try:
                context = confirmed_context(conn, relation["id"], state["owner_id"])
            except HTTPException as exc:
                if exc.status_code == 409:
                    continue
                raise
            anchor = next(table for table in tables if table["id"] == relation["left_table_id"])
            anchor.setdefault("confirmed_relations", []).append({"id": relation["id"], "left_table_id": relation["left_table_id"],
                "right_table_id": relation["right_table_id"], "join_type": relation["join_type"], "fields": context["fields"]})
        recent = conn.execute(select(db.messages).where(db.messages.c.owner_id == state["owner_id"],
            db.messages.c.conversation_id == state["conversation_id"], db.messages.c.id != state["message_id"])
            .order_by(db.messages.c.created_at.desc()).limit(6)).mappings().all()
        # 只传最近文本澄清上下文，不传旧结果整表；旧问题的数据范围须与当前选择相同。
        history = [{"role": m["role"], "content": m["content"][:1000]} for m in reversed(recent)
            if m["role"] == "assistant" or set((m["request"] or {}).get("table_ids", [])) == set(state["table_ids"])]
    from .field_resolution import enrich_tables
    return {"tables": enrich_tables(tables), "history": history}


def plan_node(state):
    check_run(state)
    plan = QueryPlan.model_validate(state["supplied_plan"]) if state.get("supplied_plan") else ModelAdapter().plan(state["content"], state["tables"], state.get("history"))
    from .field_resolution import resolve_plan
    try:
        plan = resolve_plan(plan, state["tables"])
        if not state.get("supplied_plan"):
            from .interpretation import apply_storage_context
            apply_storage_context(plan, state["tables"], state["content"], state.get("history"))
    except HTTPException as exc:
        if state.get("supplied_plan"):
            raise
        plan = QueryPlan(operation="clarify", clarifications=[str(exc.detail)])
    check_run(state)
    return {"plan": plan.model_dump(), "tables": [], "history": []}


def validation_node(state):
    plan = QueryPlan.model_validate(state["plan"])
    if plan.operation != "clarify":
        if plan.table_id not in state["table_ids"]:
            raise HTTPException(422, "计划超出了本次选择的数据范围")
        with db.engine.connect() as conn:
            table = service.owned(conn, db.data_tables, plan.table_id, state["owner_id"])
            if plan.relation_id:
                relation = service.owned(conn, db.table_relations, plan.relation_id, state["owner_id"])
                if relation["right_table_id"] not in state["table_ids"]:
                    raise HTTPException(422, "关联的另一端超出了本次选择范围")
            try:
                validate_plan(plan, table)
            except HTTPException as exc:
                if state.get("supplied_plan") or exc.status_code != 422:
                    raise
                # 不兼容口径转为可继续回答的澄清；权限/版本错误仍拒绝，不能借建议绕过校验。
                plan = QueryPlan(operation="clarify", clarifications=[f"{exc.detail}。请修正本次过滤口径，或先核对登记字段的类型与单位；没有执行查询。"])
            if plan.operation == "metadata":
                from .operation_preview import metadata_target
                metadata_target(table, plan)
    return {"plan": plan.model_dump()}


def execute_node(state):
    check_run(state)
    verify_identity(state)
    plan = QueryPlan.model_validate(state["plan"])
    owner_id, conversation_id = state["owner_id"], state["conversation_id"]
    if plan.operation == "clarify":
        from .table_actions import owner_action_lock
        with db.engine.begin() as conn:
            owner_action_lock(conn, owner_id)
            service.lock_source_message(conn, owner_id, conversation_id, state["message_id"])
            existing = conn.execute(select(db.messages).where(db.messages.c.owner_id == owner_id,
                db.messages.c.conversation_id == conversation_id, db.messages.c.role == "assistant",
                db.messages.c.request["source_message_id"].astext == state["message_id"])).mappings().first()
            message = dict(existing) if existing else service.save_message(conn, owner_id, conversation_id, "assistant",
                "\n".join(plan.clarifications), clarifications=plan.clarifications, request={"source_message_id": state["message_id"]})
        return {"response": {"message": service.json_safe(message), "clarifications": plan.clarifications}}
    if plan.operation == "select" and not state.get("preview_only") and not plan.assumptions:
        response = service.execute_select(owner_id, conversation_id, plan, state["message_id"])
    else:
        from .operation_preview import preview_operation
        response = preview_operation({"id": owner_id, "session_id": state["session_id"]}, conversation_id, plan,
            state["message_id"], state["thread_id"])
    response["plan_source"] = "user_structured_plan" if state.get("supplied_plan") else settings.model_provider
    return {"response": service.json_safe(response)}


def wait_confirmation(state):
    # interrupt 把执行位置保存在 PostgreSQL；重启后能按同一 thread_id 恢复。
    response = state["response"]
    confirmation = interrupt({"plan_id": response["plan"]["id"], "message": "等待用户明确确认或取消"})
    verify_identity(state)
    if confirmation.get("plan_id") != response["plan"]["id"]:
        raise HTTPException(409, "恢复的计划标识不匹配")
    # HTTP 确认入口已经完成同一事务；恢复只检查状态，不重复执行写入。
    with db.engine.connect() as conn:
        stored = service.owned(conn, db.plans, confirmation["plan_id"], state["owner_id"])
        if stored["status"] not in ("executed", "cancelled"):
            raise HTTPException(409, "计划尚未明确确认")
    return {}


def build_graph(saver):
    builder = StateGraph(State)
    builder.add_node("authenticate", verify_identity)
    builder.add_node("metadata", metadata_node)
    builder.add_node("plan", plan_node)
    builder.add_node("validate", validation_node)
    builder.add_node("execute", execute_node)
    builder.add_node("confirm", wait_confirmation)
    builder.add_edge(START, "authenticate")
    builder.add_edge("authenticate", "metadata")
    builder.add_edge("metadata", "plan")
    builder.add_edge("plan", "validate")
    builder.add_edge("validate", "execute")
    builder.add_conditional_edges("execute", lambda state: "confirm" if "plan" in state["response"] else END)
    builder.add_edge("confirm", END)
    return builder.compile(checkpointer=saver)


def run_message(user, conversation_id, body):
    from .table_actions import owner_workflow_guard
    # 从保存输入到工作流结束都保护同一范围，避免删除插入到两步之间并留下旧检查点。
    with owner_workflow_guard(user["id"]):
        return _run_message(user, conversation_id, body)


def _run_message(user, conversation_id, body):
    from .table_actions import owner_action_lock
    with db.engine.begin() as conn:
        owner_action_lock(conn, user["id"])
        service.owned(conn, db.conversations, conversation_id, user["id"])
        for table_id in body.table_ids:
            service.owned(conn, db.data_tables, table_id, user["id"])
        message = service.save_message(conn, user["id"], conversation_id, "user", body.content, request=body.model_dump())
    return invoke_saved_message(user, conversation_id, message["id"], body)


def invoke_saved_message(user, conversation_id, message_id, body, preview_only=False, run_id=None):
    from .table_actions import owner_workflow_guard
    # 会话级共享锁覆盖模型等待与检查点保存，不把网络等待放进长数据库事务。
    with owner_workflow_guard(user["id"]):
        return _invoke_saved_message(user, conversation_id, message_id, body, preview_only, run_id)


def _invoke_saved_message(user, conversation_id, message_id, body, preview_only=False, run_id=None):
    thread = f"{user['id']}:{message_id}"
    state = {"owner_id": user["id"], "session_id": user["session_id"], "conversation_id": conversation_id,
        "message_id": message_id, "thread_id": thread, "content": body.content, "table_ids": body.table_ids,
        "supplied_plan": body.plan.model_dump() if body.plan else None, "preview_only": preview_only, "run_id": run_id}
    with PostgresSaver.from_conn_string(checkpoint_url()) as saver:
        graph = build_graph(saver)
        config = {"configurable": {"thread_id": thread}}
        saved = graph.get_state(config)
        if saved.values.get("response"):
            return saved.values["response"]
        result = graph.invoke(None if saved.values else state, config)
    return result["response"]


def check_run(state):
    if state.get("run_id"):
        with db.engine.connect() as conn:
            run = service.owned(conn, db.assistant_runs, state["run_id"], state["owner_id"])
        if run["status"] != "running":
            raise HTTPException(409, "助手运行已中止或失效")


def resume_saved_message(user, conversation_id, message_id):
    from .table_actions import owner_workflow_guard
    with owner_workflow_guard(user["id"]):
        return _resume_saved_message(user, conversation_id, message_id)


def _resume_saved_message(user, conversation_id, message_id):
    with db.engine.connect() as conn:
        service.owned(conn, db.conversations, conversation_id, user["id"])
        message = service.owned(conn, db.messages, message_id, user["id"])
        if message["conversation_id"] != conversation_id or message["role"] != "user":
            raise HTTPException(404, "可重试消息不存在")
    thread = f"{user['id']}:{message_id}"
    with PostgresSaver.from_conn_string(checkpoint_url()) as saver:
        graph = build_graph(saver)
        snapshot = graph.get_state({"configurable": {"thread_id": thread}})
        if not snapshot.values:
            raise HTTPException(409, "无工作流检查点，请重新提交消息")
        if snapshot.values["session_id"] != user["session_id"]:
            raise HTTPException(409, "消息属于另一登录会话，请重新提交")
        if snapshot.values.get("response"):
            return snapshot.values["response"]
        result = graph.invoke(None, {"configurable": {"thread_id": thread}})
    return result["response"]


def resume_plan_checkpoint(plan_id, user):
    from .table_actions import owner_workflow_guard
    with owner_workflow_guard(user["id"]):
        return _resume_plan_checkpoint(plan_id, user)


def _resume_plan_checkpoint(plan_id, user):
    with db.engine.connect() as conn:
        plan = service.owned(conn, db.plans, plan_id, user["id"])
    if not plan["workflow_thread"]:
        return
    with PostgresSaver.from_conn_string(checkpoint_url()) as saver:
        graph = build_graph(saver)
        config = {"configurable": {"thread_id": plan["workflow_thread"]}}
        snapshot = graph.get_state(config)
        if snapshot.next:
            graph.invoke(Command(resume={"plan_id": plan_id}), config)
