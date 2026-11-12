"""RQ 解析/导出队列与数据库任务状态：重启可恢复，重复投递不重复创建数据。

任务只接收服务端 ID；文件路径、账号归属及表版本均在工作进程重新验证。
"""
from __future__ import annotations

import csv
import hashlib
import logging
import os
import sys
import threading
from datetime import timedelta
from pathlib import Path

from fastapi import HTTPException
from redis import Redis
from rq import Queue, Worker
from rq.job import Job
from rq.exceptions import NoSuchJobError
from sqlalchemy import select, update, insert

from .config import settings
from .db import engine, tasks, files, data_tables, data_rows, users, new_id, utcnow
from .parsing import ParseError, csv_safe, parse_file

log = logging.getLogger(__name__)
QUEUE_NAME = "vibe"
JOB_TIMEOUT = 300
MAX_ATTEMPTS = 5
HEALTH_KEY = "vibe:worker:heartbeat"


def redis_connection() -> Redis:
    return Redis.from_url(settings.redis_url, socket_connect_timeout=3, socket_timeout=5)


def controlled_path(stored_name: str) -> Path:
    root = settings.upload_dir.resolve()
    candidate = (root / stored_name).resolve()
    if not stored_name or Path(stored_name).name != stored_name or candidate.parent != root:
        raise ParseError("文件存储路径无效。")
    return candidate


def _dispatch(task: dict) -> None:
    connection = redis_connection()
    job_id = f"vibe-{task['id']}-{task['attempts']}"
    try:
        existing = Job.fetch(job_id, connection=connection)
        if existing.get_status(refresh=True) in ("queued", "started", "deferred", "scheduled"):
            return
        existing.delete()
    except NoSuchJobError:
        pass
    Queue(QUEUE_NAME, connection=connection).enqueue(run_task, task["id"], task["owner_id"], job_id=job_id, job_timeout=JOB_TIMEOUT, result_ttl=86400, failure_ttl=86400)


def _dispatch_or_pending(task: dict) -> None:
    try:
        _dispatch(task)
    except Exception:
        # DB 已保存任务和文件；队列暂时离线时保留 pending，worker 恢复后重新投递。
        with engine.begin() as connection:
            connection.execute(update(tasks).where(tasks.c.id == task["id"], tasks.c.owner_id == task["owner_id"], tasks.c.status == "pending").values(error="后台队列暂不可用；任务已保存，队列恢复后自动重试。", updated_at=utcnow()))
        log.warning("Queue unavailable; persisted task will be recovered: %s", task["id"])


def enqueue_parse(file_id: str, owner_id: str, task_id: str | None = None) -> str:
    from .table_actions import owner_action_lock
    with engine.begin() as connection:
        owner_action_lock(connection, owner_id)
        record = connection.execute(select(files).where(files.c.id == file_id, files.c.owner_id == owner_id)).mappings().first()
        if record is None:
            raise HTTPException(404, "文件不存在")
        if task_id is not None:
            task = connection.execute(select(tasks).where(tasks.c.id == task_id, tasks.c.owner_id == owner_id, tasks.c.file_id == file_id)).mappings().first()
            if task is None:
                raise HTTPException(404, "任务不存在")
            task = dict(task)
        else:
            task_id = new_id()
            task = {"id": task_id, "owner_id": owner_id, "file_id": file_id, "kind": "parse", "status": "pending", "attempts": 0, "version": 1, "created_at": utcnow(), "updated_at": utcnow()}
            connection.execute(insert(tasks).values(**task))
    if task["status"] == "pending":
        _dispatch_or_pending(task)
    return task_id


def enqueue_export(table_id: str, owner_id: str) -> str:
    from .table_actions import owner_action_lock
    with engine.begin() as connection:
        owner_action_lock(connection, owner_id)
        table = connection.execute(select(data_tables).where(data_tables.c.id == table_id, data_tables.c.owner_id == owner_id)).mappings().first()
        if table is None:
            raise HTTPException(404, "数据表不存在")
        if table.get("archived"):
            raise HTTPException(409, "数据表已归档，请先恢复再导出")
        task = {"id": new_id(), "owner_id": owner_id, "kind": "export", "status": "pending", "attempts": 0, "version": table["version"], "table_ids": [table_id], "created_at": utcnow(), "updated_at": utcnow()}
        connection.execute(insert(tasks).values(**task))
    _dispatch_or_pending(task)
    return task["id"]


def retry_task(task_id: str, owner_id: str) -> str:
    from .table_actions import owner_action_lock
    with engine.begin() as connection:
        owner_action_lock(connection, owner_id)
        task = connection.execute(select(tasks).where(tasks.c.id == task_id, tasks.c.owner_id == owner_id).with_for_update()).mappings().first()
        if task is None:
            raise HTTPException(404, "任务不存在")
        if task["status"] not in ("failed", "pending"):
            raise HTTPException(409, "只有失败或等待中的任务可重试")
        if task["attempts"] >= MAX_ATTEMPTS:
            raise HTTPException(409, "任务已重试 5 次，请修正文件或联系管理员")
        connection.execute(update(tasks).where(tasks.c.id == task_id, tasks.c.owner_id == owner_id).values(status="pending", error=None, updated_at=utcnow()))
        task = {**task, "status": "pending", "error": None}
    _dispatch_or_pending(task)
    return task_id


def _export(task: dict) -> str:
    table_ids = task.get("table_ids") or []
    if len(table_ids) != 1:
        raise ParseError("导出任务的表范围无效。")
    output_name = f"export-{task['id']}-{new_id()}.csv"
    temporary_name = f"export-{task['id']}-{new_id()}.tmp"
    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    temporary = controlled_path(temporary_name)
    # repeatable read 在后台分页时保持同一快照，避免导出过程中混入半次修改。
    with engine.connect().execution_options(isolation_level="REPEATABLE READ") as connection, connection.begin():
        table = connection.execute(select(data_tables).where(data_tables.c.id == table_ids[0], data_tables.c.owner_id == task["owner_id"])).mappings().first()
        if table is None:
            raise ParseError("数据表不存在或无访问权限。")
        if table["version"] != task["version"]:
            raise ParseError("排队期间数据版本已变化，请重新创建导出任务。")
        with temporary.open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.writer(stream)
            fields = table["fields"]
            writer.writerow([csv_safe(field["name"]) for field in fields])
            last_id, last_position = "", -1
            while True:
                from sqlalchemy import or_, and_
                batch = connection.execute(select(data_rows).where(data_rows.c.owner_id == task["owner_id"], data_rows.c.table_id == table_ids[0],
                    or_(data_rows.c.position > last_position, and_(data_rows.c.position == last_position, data_rows.c.id > last_id)))
                    .order_by(data_rows.c.position, data_rows.c.id).limit(1000)).mappings().all()
                if not batch:
                    break
                for row in batch:
                    writer.writerow([csv_safe(row["values"].get(field["id"])) for field in fields])
                last_id, last_position = batch[-1]["id"], batch[-1]["position"]
    # 完整写入后原子改名；下载接口只读取 completed/output_name，因此看不到半成品。
    temporary.replace(controlled_path(output_name))
    return output_name


def run_task(task_id: str, owner_id: str) -> None:
    # RQ fork 后不能复用父进程的 PostgreSQL socket。
    engine.dispose(close=False)
    with engine.begin() as connection:
        task = connection.execute(select(tasks).where(tasks.c.id == task_id, tasks.c.owner_id == owner_id).with_for_update()).mappings().first()
        if task is None or task["status"] != "pending":
            return
        active = connection.execute(select(users.c.id).where(users.c.id == owner_id, users.c.active.is_(True))).first()
        if active is None:
            connection.execute(update(tasks).where(tasks.c.id == task_id).values(status="failed", error="所属账号已失效。", updated_at=utcnow()))
            return
        attempt = task["attempts"] + 1
        if attempt > MAX_ATTEMPTS:
            connection.execute(update(tasks).where(tasks.c.id == task_id).values(status="failed", error="超过最大重试次数。", updated_at=utcnow()))
            return
        task = {**task, "attempts": attempt}
        connection.execute(update(tasks).where(tasks.c.id == task_id, tasks.c.owner_id == owner_id).values(status="running", attempts=attempt, error=None, updated_at=utcnow()))
    try:
        values = {}
        if task["kind"] == "parse":
            with engine.connect() as connection:
                file = connection.execute(select(files).where(files.c.id == task["file_id"], files.c.owner_id == owner_id)).mappings().first()
            if file is None:
                raise ParseError("文件不存在或无访问权限。")
            path = controlled_path(file["stored_name"])
            if not path.is_file() or path.stat().st_size != file["size"]:
                raise ParseError("源文件缺失或大小变化，请重新上传。")
            with path.open("rb") as stream:
                if hashlib.file_digest(stream, "sha256").hexdigest() != file["sha256"]:
                    raise ParseError("源文件校验不一致，请核对备份或重新上传。")
            preview = parse_file(path, file["id"], task_id)
            if path.suffix.lower() == ".csv":
                for table in preview["tables"]:
                    table["name"] = Path(file["original_name"]).stem + (f" · {table['grain']}" if "grain" in table else "")
            values = {"preview": preview}
        elif task["kind"] == "export":
            values = {"output_name": _export(task)}
        else:
            raise ParseError("未知任务类型。")
        with engine.begin() as connection:
            # 丢弃已被新一轮接管的旧 worker 写入，保存预览不会直接创建正式数据表。
            connection.execute(update(tasks).where(tasks.c.id == task_id, tasks.c.owner_id == owner_id, tasks.c.status == "running", tasks.c.attempts == attempt).values(status="completed", error=None, updated_at=utcnow(), **values))
    except Exception as exc:
        detail = str(exc) if isinstance(exc, ParseError) else "后台解析/导出失败；源文件和任务已保留，可重试或查看服务日志。"
        with engine.begin() as connection:
            connection.execute(update(tasks).where(tasks.c.id == task_id, tasks.c.owner_id == owner_id, tasks.c.status == "running", tasks.c.attempts == attempt).values(status="failed", error=detail[:2000], updated_at=utcnow()))
        # 日志不打印文件内容、模型输出、环境变量或异常消息中的潜在敏感数据。
        log.error("Task %s failed (%s)", task_id, type(exc).__name__)


def recover_unfinished() -> int:
    count = 0
    with engine.begin() as connection:
        # 超过 RQ 硬超时且额外留 60s 的任务视作失去 worker，可重新解析/导出。
        stale = connection.execute(select(tasks).where(tasks.c.status == "running", tasks.c.updated_at < utcnow() - timedelta(seconds=JOB_TIMEOUT + 60)).with_for_update(skip_locked=True)).mappings().all()
        for task in stale:
            exhausted = task["attempts"] >= MAX_ATTEMPTS
            connection.execute(update(tasks).where(tasks.c.id == task["id"]).values(status="failed" if exhausted else "pending", error="工作进程中断；超过最大重试次数。" if exhausted else "工作进程中断，任务等待恢复。", updated_at=utcnow()))
        pending = connection.execute(select(tasks).where(tasks.c.status == "pending")).mappings().all()
    for task in pending:
        try:
            _dispatch(dict(task))
            count += 1
        except Exception:
            log.warning("Unable to recover queued task: %s", task["id"])
    return count


def worker() -> None:
    # RQ 的 BLPOP 会合法阻塞几十秒；短请求超时只用于 API/健康检查，不能用于队列消费。
    connection = Redis.from_url(settings.redis_url, socket_connect_timeout=3, socket_timeout=None, health_check_interval=30)
    queue = Queue(QUEUE_NAME, connection=connection)
    stop = threading.Event()

    def maintain() -> None:
        while not stop.is_set():
            try:
                connection.setex(HEALTH_KEY, 90, utcnow().isoformat())
                recover_unfinished()
                from .assistant_runs import recover
                recover()
            except Exception:
                log.warning("Worker recovery/heartbeat unavailable")
            stop.wait(30)

    monitor = threading.Thread(target=maintain, daemon=True)
    monitor.start()
    try:
        if os.name == "nt":
            from rq.worker import SpawnWorker
            worker_class = SpawnWorker
        else:
            worker_class = Worker
        worker_class([queue], connection=connection, worker_ttl=90, maintenance_interval=30).work()
    finally:
        stop.set()


def health() -> bool:
    connection = redis_connection()
    if not connection.get(HEALTH_KEY):
        return False
    # 同时验证 RQ 主进程的心跳，避免只有监控线程存活时误报健康。
    return any(worker.last_heartbeat is not None and worker.last_heartbeat > utcnow() - timedelta(seconds=120) and worker.get_state() != "stopped" for worker in Worker.all(connection=connection))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    if len(sys.argv) != 2 or sys.argv[1] not in ("worker", "health"):
        raise SystemExit("usage: python -m app.jobs worker|health")
    if sys.argv[1] == "health":
        try:
            raise SystemExit(0 if health() else 1)
        except (RuntimeError, ConnectionError):
            raise SystemExit(1)
    else:
        worker()
