"""可恢复的异步助手任务：服务端中止、同事务副作用检查及待确认计划替换。"""
from datetime import timedelta
import logging
from fastapi import HTTPException
from sqlalchemy import select, insert, update
from rq import Queue
from rq.job import Job
from rq.exceptions import NoSuchJobError
from rq.command import send_stop_job_command
from . import db, service, workflow
from .schemas import ChatInput
from .jobs import redis_connection, QUEUE_NAME

log = logging.getLogger(__name__)
RUN_TIMEOUT = 900


def public(run):
    return service.json_safe({key: value for key, value in run.items() if key not in ("owner_id", "session_id")})


def dispatch(run):
    connection = redis_connection()
    job_id = f"assistant-{run['id']}-{run['attempts']}"
    try:
        job = Job.fetch(job_id, connection=connection)
        if job.get_status(refresh=True) in ("queued", "started", "deferred", "scheduled"):
            return
        job.delete()
    except NoSuchJobError:
        pass
    Queue(QUEUE_NAME, connection=connection).enqueue(process_run, run["id"], run["owner_id"], job_id=job_id,
        job_timeout=RUN_TIMEOUT, result_ttl=86400, failure_ttl=86400)


def cancel_pending(conn, owner_id, conversation_id, source_message_id=None):
    clauses = [db.plans.c.owner_id == owner_id, db.plans.c.conversation_id == conversation_id, db.plans.c.status == "pending"]
    if source_message_id:
        clauses.append(db.plans.c.source_message_id == source_message_id)
    else:
        clauses.append(db.plans.c.source_message_id.is_not(None))
    for plan in conn.execute(select(db.plans).where(*clauses).with_for_update()).mappings():
        conn.execute(update(db.plans).where(db.plans.c.id == plan["id"]).values(status="cancelled"))
        service.audit(conn, owner_id, "plan_superseded", plan["table_id"], details={"plan_id": plan["id"], "data_changed": False})


def start(user, conversation_id, body):
    from .table_actions import owner_action_lock
    with db.engine.begin() as conn:
        owner_action_lock(conn, user["id"])
        # 对话锁串行化重复发送；partial unique index 再兜底防止同一对话两个活跃任务。
        # NO KEY UPDATE 仍串行化发送，但兼容结果/消息插入外键所需的 KEY SHARE 锁。
        service.owned(conn, db.conversations, conversation_id, user["id"], lock="no_key")
        active = conn.execute(select(db.assistant_runs.c.id).where(db.assistant_runs.c.owner_id == user["id"],
            db.assistant_runs.c.conversation_id == conversation_id, db.assistant_runs.c.status.in_(["queued", "running"]))).first()
        if active:
            raise HTTPException(409, "该对话仍在处理问题；可中止后重新发送，输入内容不会丢失")
        for table_id in body.table_ids:
            table = service.owned(conn, db.data_tables, table_id, user["id"])
            if table["archived"]:
                raise HTTPException(409, "所选表已归档，请先恢复")
        cancel_pending(conn, user["id"], conversation_id)
        message = service.save_message(conn, user["id"], conversation_id, "user", body.content, request=body.model_dump())
        run = {"id": db.new_id(), "owner_id": user["id"], "conversation_id": conversation_id,
            "session_id": user["session_id"], "message_id": message["id"], "status": "queued", "attempts": 0,
            "created_at": db.utcnow(), "updated_at": db.utcnow(), "error": None, "response": None}
        conn.execute(insert(db.assistant_runs).values(**run))
    try:
        dispatch(run)
    except Exception:
        # 队列离线仍保留服务器请求；worker监控恢复后自动投递，不伪装已完成。
        with db.engine.begin() as conn:
            conn.execute(update(db.assistant_runs).where(db.assistant_runs.c.id == run["id"], db.assistant_runs.c.status == "queued")
                .values(error="队列暂不可用；问题已保存，后台恢复后继续", updated_at=db.utcnow()))
        log.warning("Assistant run dispatch unavailable: %s", run["id"])
    return public(run)


def get(run_id, owner_id):
    with db.engine.connect() as conn:
        return public(service.owned(conn, db.assistant_runs, run_id, owner_id))


def list_runs(conversation_id, owner_id):
    with db.engine.connect() as conn:
        service.owned(conn, db.conversations, conversation_id, owner_id)
        return {"runs": [public(row) for row in conn.execute(select(db.assistant_runs).where(db.assistant_runs.c.owner_id == owner_id,
            db.assistant_runs.c.conversation_id == conversation_id).order_by(db.assistant_runs.c.created_at.desc()).limit(30)).mappings()]}


def cancel(run_id, user):
    with db.engine.begin() as conn:
        run = service.owned(conn, db.assistant_runs, run_id, user["id"], lock=True)
        if run["status"] in ("failed", "cancelled"):
            return public(run)
        executed = conn.execute(select(db.plans.c.id).where(db.plans.c.owner_id == user["id"],
            db.plans.c.source_message_id == run["message_id"], db.plans.c.status == "executed")).first()
        if executed:
            raise HTTPException(409, "操作已经确认执行；不能通过中止撤销已提交事务")
        cancel_pending(conn, user["id"], run["conversation_id"], run["message_id"])
        conn.execute(update(db.assistant_runs).where(db.assistant_runs.c.id == run_id).values(status="cancelled", response=None, error=None, updated_at=db.utcnow()))
        service.save_message(conn, user["id"], run["conversation_id"], "assistant", "已中止本次规划；不会执行其中的查询或变更计划。")
        service.audit(conn, user["id"], "assistant_cancelled", details={"run_id": run_id, "data_changed": False})
        run.update(status="cancelled", response=None, error=None)
    try:
        connection = redis_connection()
        # 运行时 attempts 已递增；排队job编号对应前一个attempt。
        candidates = {f"assistant-{run_id}-{run['attempts']}", f"assistant-{run_id}-{max(run['attempts'] - 1, 0)}"}
        for identifier in candidates:
            try:
                job = Job.fetch(identifier, connection=connection)
                status = job.get_status(refresh=True)
                if status == "started":
                    send_stop_job_command(connection, identifier)
                elif status in ("queued", "deferred", "scheduled"):
                    job.cancel()
            except NoSuchJobError:
                pass
    except Exception:
        # 即使RQ中止命令暂不可用，数据库锁检查仍保证旧输出不会产生可执行效果。
        log.warning("RQ stop unavailable for persisted cancelled run: %s", run_id)
    return public(run)


def process_run(run_id, owner_id):
    db.engine.dispose(close=False)
    with db.engine.begin() as conn:
        run = service.owned(conn, db.assistant_runs, run_id, owner_id, lock=True)
        if run["status"] != "queued":
            return
        message = service.owned(conn, db.messages, run["message_id"], owner_id)
        attempt = run["attempts"] + 1
        conn.execute(update(db.assistant_runs).where(db.assistant_runs.c.id == run_id).values(status="running", attempts=attempt, error=None, updated_at=db.utcnow()))
    try:
        body = ChatInput.model_validate(message["request"])
        response = workflow.invoke_saved_message({"id": owner_id, "session_id": run["session_id"]}, run["conversation_id"],
            run["message_id"], body, preview_only=True, run_id=run_id)
        with db.engine.begin() as conn:
            conn.execute(update(db.assistant_runs).where(db.assistant_runs.c.id == run_id, db.assistant_runs.c.status == "running",
                db.assistant_runs.c.attempts == attempt).values(status="completed", response=service.json_safe(response), updated_at=db.utcnow()))
    except Exception as exc:
        detail = str(exc.detail) if isinstance(exc, HTTPException) else "助手规划失败；问题已保留，可调整后重新发送"
        with db.engine.begin() as conn:
            conn.execute(update(db.assistant_runs).where(db.assistant_runs.c.id == run_id, db.assistant_runs.c.status == "running",
                db.assistant_runs.c.attempts == attempt).values(status="failed", error=detail[:1000], updated_at=db.utcnow()))
        log.error("Assistant run %s failed (%s)", run_id, type(exc).__name__)


def recover():
    with db.engine.begin() as conn:
        stale = conn.execute(select(db.assistant_runs).where(db.assistant_runs.c.status == "running",
            db.assistant_runs.c.updated_at < db.utcnow() - timedelta(seconds=RUN_TIMEOUT + 60)).with_for_update(skip_locked=True)).mappings().all()
        for run in stale:
            conn.execute(update(db.assistant_runs).where(db.assistant_runs.c.id == run["id"]).values(
                status="queued" if run["attempts"] < 3 else "failed", error="工作进程中断；等待恢复" if run["attempts"] < 3 else "工作进程连续中断，请重新发送", updated_at=db.utcnow()))
        queued = [dict(run) for run in conn.execute(select(db.assistant_runs).where(db.assistant_runs.c.status == "queued")).mappings()]
    for run in queued:
        dispatch(run)
