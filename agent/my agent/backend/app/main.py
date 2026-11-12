"""FastAPI API：同源会话、受控文件、后台任务、持久查询与确认入口。"""

import hashlib
import logging
from pathlib import Path
from urllib.parse import urlparse
from fastapi import FastAPI, Depends, HTTPException, Response, Request, UploadFile, File, Query
from fastapi.responses import JSONResponse, FileResponse
from fastapi.openapi.docs import get_swagger_ui_html
from sqlalchemy import select, insert, update, func
from sqlalchemy.exc import SQLAlchemyError
from redis.exceptions import RedisError
from . import db, auth, service, workflow
from .config import settings
from .schemas import Login, TablePatch, ImportConfirm, ConversationCreate, ChatInput, TableCreate, TableState, RelationCreate, RelationState

logger = logging.getLogger("vibe")


def create_app():
    app = FastAPI(title="vibe 数据智能体", version="1.0.0", docs_url=None, redoc_url=None, openapi_url=None)
    from . import profile, table_actions
    app.include_router(profile.router)
    app.include_router(table_actions.router)

    @app.middleware("http")
    async def same_origin(request, call_next):
        origin = request.headers.get("origin")
        if request.method not in ("GET", "HEAD", "OPTIONS") and origin:
            expected = f"{request.url.scheme}://{request.headers.get('host', '')}"
            trusted = {o.strip() for o in settings.trusted_origins.split(",") if o.strip()}
            if origin != expected and origin not in trusted:
                return JSONResponse({"detail": "跨站写入请求被拒绝"}, status_code=403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(SQLAlchemyError)
    async def database_error(request, exc):
        logger.error("数据库请求失败：%s", type(exc).__name__)
        return JSONResponse({"detail": "服务器保存或查询失败；未完成的事务已回滚，请保留输入并重试"}, status_code=503)

    @app.exception_handler(RedisError)
    async def queue_error(request, exc):
        logger.error("队列连接失败：%s", type(exc).__name__)
        return JSONResponse({"detail": "后台队列暂时不可用，请重试；已上传文件仍然保留"}, status_code=503)

    @app.get("/api/health")
    def health():
        return {"status": "ok", "version": "1.0.0"}

    @app.get("/api/openapi.json", include_in_schema=False)
    def openapi(user=Depends(auth.current_user)):
        return app.openapi()

    @app.get("/api/docs", include_in_schema=False)
    def api_docs(user=Depends(auth.current_user)):
        return get_swagger_ui_html(openapi_url="/api/openapi.json", title="vibe API")

    @app.get("/api/ready")
    def ready():
        from .jobs import redis_connection
        try:
            with db.engine.connect() as conn:
                conn.execute(select(db.users.c.id).limit(1))
            redis_connection().ping()
        except (SQLAlchemyError, RedisError):
            raise HTTPException(503, "数据库或任务队列未就绪")
        return {"status": "ready", "model_mode": settings.model_provider}

    @app.post("/api/auth/login")
    def login(body: Login, request: Request, response: Response):
        return auth.login(body, request, response)

    @app.get("/api/auth/me")
    def me(user=Depends(auth.current_user)):
        return {**auth.public_user(user), "csrf_token": user["csrf_token"]}

    app.post("/api/auth/logout")(auth.logout)

    @app.get("/api/catalog")
    def catalog(user=Depends(auth.current_user)):
        return service.catalog(user["id"])

    @app.get("/api/tables")
    def list_tables(include_archived: bool = False, user=Depends(auth.current_user)):
        with db.engine.connect() as conn:
            statement = select(db.data_tables).where(db.data_tables.c.owner_id == user["id"])
            if not include_archived:
                statement = statement.where(db.data_tables.c.archived.is_(False))
            return {"tables": [dict(row) for row in conn.execute(statement.order_by(db.data_tables.c.created_at.desc())).mappings()]}

    @app.post("/api/tables/preview")
    def preview_create_table(body: TableCreate, user=Depends(auth.current_user)):
        from .data_management import preview_create_table
        return preview_create_table(user, body.conversation_id, body.name, body.fields, body.description)

    @app.post("/api/tables/{table_id}/state/preview")
    def preview_table_state(table_id: str, body: TableState, user=Depends(auth.current_user)):
        from .data_management import preview_table_state
        return preview_table_state(user, body.conversation_id, table_id, body.archived, body.version)

    @app.get("/api/relations")
    def list_relations(user=Depends(auth.current_user)):
        from .relations import list_relations
        return list_relations(user["id"])

    @app.get("/api/relations/candidates")
    @app.get("/api/relations/suggestions")
    def relation_candidates(table_id: str | None = None, user=Depends(auth.current_user)):
        from .relations import candidates
        return candidates(user["id"], table_id)

    @app.post("/api/relations/preview")
    def preview_relation(body: RelationCreate, user=Depends(auth.current_user)):
        from .relations import preview_relation
        return preview_relation(user, body.conversation_id, body.left_table_id, body.right_table_id, body.left_field_id, body.right_field_id, body.join_type)

    @app.post("/api/relations/{relation_id}/state/preview")
    def preview_relation_state(relation_id: str, body: RelationState, user=Depends(auth.current_user)):
        from .relations import preview_relation_state
        return preview_relation_state(user, body.conversation_id, relation_id, body.status)

    @app.get("/api/tables/{table_id}")
    def get_table(table_id: str, offset: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=5000), user=Depends(auth.current_user)):
        return service.table_data(table_id, user["id"], offset, limit)

    @app.patch("/api/tables/{table_id}")
    def patch_table(table_id: str, body: TablePatch, user=Depends(auth.current_user)):
        return service.edit_table(table_id, body, user["id"])

    @app.post("/api/files")
    async def upload(file: UploadFile = File(...), user=Depends(auth.current_user)):
        from .jobs import enqueue_parse
        suffix = Path(file.filename or "").suffix.lower()
        if suffix == ".xls":
            raise HTTPException(415, "旧版 xls 不支持可靠解析，请在 Excel 中另存为 xlsx 后上传")
        if suffix not in (".xlsx", ".csv", ".docx", ".pdf"):
            raise HTTPException(415, "仅支持 xlsx、csv、docx 与文本型 pdf")
        identifier, stored_name, task_id = db.new_id(), db.new_id() + suffix, db.new_id()
        settings.upload_dir.mkdir(parents=True, exist_ok=True)
        path = settings.upload_dir / stored_name
        total, digest = 0, hashlib.sha256()
        try:
            with path.open("xb") as destination:
                while chunk := await file.read(65536):
                    total += len(chunk)
                    if total > settings.max_upload_mb * 1024 * 1024:
                        raise HTTPException(413, f"文件超过 {settings.max_upload_mb} MB 上限")
                    digest.update(chunk)
                    destination.write(chunk)
            if total == 0:
                raise HTTPException(422, "空文件无法解析")
            with db.engine.begin() as conn:
                from .table_actions import owner_action_lock
                owner_action_lock(conn, user["id"])
                conn.execute(insert(db.files).values(id=identifier, owner_id=user["id"],
                    original_name=Path((file.filename or "upload").replace("\\", "/")).name[:255],
                    stored_name=stored_name, mime=file.content_type or "application/octet-stream", size=total,
                    sha256=digest.hexdigest(), created_at=db.utcnow()))
                service.audit(conn, user["id"], "upload", details={"file_id": identifier, "size": total})
                # 文件登记与待解析任务一起提交；进程中断后 worker 可恢复 pending 任务。
                conn.execute(insert(db.tasks).values(id=task_id, owner_id=user["id"], file_id=identifier,
                    kind="parse", status="pending", attempts=0, version=1, created_at=db.utcnow(), updated_at=db.utcnow()))
        except Exception:
            # 只清理当前请求新建且尚未登记的文件，不触碰任何已有文件/演示数据。
            path.unlink(missing_ok=True)
            raise
        finally:
            await file.close()
        enqueue_parse(identifier, user["id"], task_id)
        return {"file_id": identifier, "task_id": task_id}

    @app.get("/api/files/{file_id}/download")
    def download_file(file_id: str, user=Depends(auth.current_user)):
        from .jobs import controlled_path
        with db.engine.connect() as conn:
            row = service.owned(conn, db.files, file_id, user["id"])
        path = controlled_path(row["stored_name"])
        if not path.is_file():
            raise HTTPException(404, "源文件暂时不可用，请检查文件备份")
        return FileResponse(path, media_type="application/octet-stream", filename=row["original_name"])

    @app.get("/api/tasks/{task_id}")
    def get_task(task_id: str, user=Depends(auth.current_user)):
        with db.engine.connect() as conn:
            return service.task_public(service.owned(conn, db.tasks, task_id, user["id"]))

    @app.post("/api/tasks/{task_id}/retry")
    def retry_task(task_id: str, user=Depends(auth.current_user)):
        from .jobs import retry_task as retry
        return {"task_id": retry(task_id, user["id"])}

    @app.get("/api/imports/{task_id}")
    def get_import(task_id: str, candidate_id: str | None = None, offset: int = Query(0, ge=0),
                   limit: int = Query(200, ge=1, le=200), user=Depends(auth.current_user)):
        return service.import_preview(task_id, user["id"], candidate_id, offset, limit)

    @app.post("/api/imports/{task_id}/confirm")
    def confirm_import(task_id: str, body: ImportConfirm, user=Depends(auth.current_user)):
        return service.confirm_import(task_id, body, user["id"])

    @app.get("/api/conversations")
    def conversations(user=Depends(auth.current_user)):
        with db.engine.connect() as conn:
            # 用户问题与活动时间从消息派生，旧对话不用迁移，查询仍严格限制当前账号。
            scope = [db.messages.c.conversation_id == db.conversations.c.id, db.messages.c.owner_id == user["id"]]
            question = select(db.messages.c.content).where(*scope, db.messages.c.role == "user")\
                .order_by(db.messages.c.created_at.desc(), db.messages.c.id.desc()).limit(1).scalar_subquery()
            activity = select(func.max(db.messages.c.created_at)).where(*scope).scalar_subquery()
            updated = func.coalesce(activity, db.conversations.c.created_at)
            rows = [dict(r) for r in conn.execute(select(db.conversations, question.label("last_question"), updated.label("updated_at"))
                .where(db.conversations.c.owner_id == user["id"]).order_by(updated.desc(), db.conversations.c.id)).mappings()]
        return {"conversations": rows}

    @app.post("/api/conversations")
    def create_conversation(body: ConversationCreate, user=Depends(auth.current_user)):
        if not body.title.strip():
            raise HTTPException(422, "对话名称不能为空白")
        row = {"id": db.new_id(), "owner_id": user["id"], "title": body.title.strip(), "created_at": db.utcnow()}
        with db.engine.begin() as conn:
            conn.execute(insert(db.conversations).values(**row))
        return row

    @app.patch("/api/conversations/{conversation_id}")
    def rename_conversation(conversation_id: str, body: ConversationCreate, user=Depends(auth.current_user)):
        if not body.title.strip():
            raise HTTPException(422, "对话名称不能为空白")
        with db.engine.begin() as conn:
            existing = service.owned(conn, db.conversations, conversation_id, user["id"], lock=True)
            conn.execute(update(db.conversations).where(db.conversations.c.id == conversation_id, db.conversations.c.owner_id == user["id"]).values(title=body.title.strip()))
            service.audit(conn, user["id"], "conversation_renamed", details={"conversation_id": conversation_id, "before": existing["title"], "after": body.title.strip()})
            return {**existing, "title": body.title.strip()}

    @app.get("/api/conversations/{conversation_id}")
    def conversation(conversation_id: str, user=Depends(auth.current_user)):
        with db.engine.connect() as conn:
            row = service.owned(conn, db.conversations, conversation_id, user["id"])
            row["messages"] = [{**{k: v for k, v in dict(m).items() if k not in ("owner_id", "request")}, "table_ids": (m["request"] or {}).get("table_ids", [])} for m in conn.execute(select(db.messages)
                .where(db.messages.c.conversation_id == conversation_id, db.messages.c.owner_id == user["id"]).order_by(db.messages.c.created_at)).mappings()]
            row["results"] = [dict(r) for r in conn.execute(select(db.results).where(db.results.c.conversation_id == conversation_id,
                db.results.c.owner_id == user["id"]).order_by(db.results.c.created_at)).mappings()]
            row["hidden_result_ids"] = service.hidden_results(conn, user["id"])
        return row

    @app.post("/api/conversations/{conversation_id}/runs")
    def start_run(conversation_id: str, body: ChatInput, user=Depends(auth.current_user)):
        from .assistant_runs import start
        return start(user, conversation_id, body)

    @app.get("/api/conversations/{conversation_id}/runs")
    def conversation_runs(conversation_id: str, user=Depends(auth.current_user)):
        from .assistant_runs import list_runs
        return list_runs(conversation_id, user["id"])

    @app.get("/api/runs/{run_id}")
    def assistant_run(run_id: str, user=Depends(auth.current_user)):
        from .assistant_runs import get
        return get(run_id, user["id"])

    @app.post("/api/runs/{run_id}/cancel")
    def stop_assistant_run(run_id: str, user=Depends(auth.current_user)):
        from .assistant_runs import cancel
        return cancel(run_id, user)

    @app.post("/api/conversations/{conversation_id}/messages")
    def message(conversation_id: str, body: ChatInput, user=Depends(auth.current_user)):
        return workflow.run_message(user, conversation_id, body)

    @app.post("/api/conversations/{conversation_id}/messages/{message_id}/retry")
    def retry_message(conversation_id: str, message_id: str, user=Depends(auth.current_user)):
        return workflow.resume_saved_message(user, conversation_id, message_id)

    @app.get("/api/results/{result_id}")
    def result(result_id: str, user=Depends(auth.current_user)):
        with db.engine.connect() as conn:
            return service.owned(conn, db.results, result_id, user["id"])

    @app.delete("/api/results/{result_id}/visibility")
    def hide_result(result_id: str, user=Depends(auth.current_user)):
        return service.set_result_visibility(result_id, user["id"], True)

    @app.post("/api/results/{result_id}/visibility")
    def show_result(result_id: str, user=Depends(auth.current_user)):
        return service.set_result_visibility(result_id, user["id"], False)

    @app.get("/api/plans/{plan_id}")
    def plan(plan_id: str, user=Depends(auth.current_user)):
        with db.engine.connect() as conn:
            return service.owned(conn, db.plans, plan_id, user["id"])

    def finish_checkpoint(plan_id, user, response):
        try:
            workflow.resume_plan_checkpoint(plan_id, user)
        except Exception as exc:
            # 数据事务已经提交，检查点故障不得把成功变更伪装成保存失败。
            logger.error("已保存变更但检查点恢复失败：%s", type(exc).__name__)
            response["checkpoint_warning"] = "操作已保存，检查点尚未恢复；重复确认不会再次执行"
        return response

    async def empty_confirmation(request: Request):
        if await request.body():
            raise HTTPException(422, "确认只能提交计划标识，不能附带修改内容")

    @app.post("/api/plans/{plan_id}/confirm")
    def confirm(plan_id: str, user=Depends(auth.current_user), body_check=Depends(empty_confirmation)):
        # FastAPI 把同步数据库事务放到线程池，避免确认大量记录时阻塞整个事件循环。
        from .operation_preview import confirm_operation
        return finish_checkpoint(plan_id, user, confirm_operation(plan_id, user))

    @app.post("/api/plans/{plan_id}/cancel")
    def cancel(plan_id: str, user=Depends(auth.current_user)):
        row = service.cancel_mutation(plan_id, user)
        return finish_checkpoint(plan_id, user, {"plan": row})

    @app.post("/api/tables/{table_id}/exports")
    def export(table_id: str, user=Depends(auth.current_user)):
        from .jobs import enqueue_export
        return {"task_id": enqueue_export(table_id, user["id"])}

    @app.get("/api/tasks/{task_id}/download")
    def download_export(task_id: str, user=Depends(auth.current_user)):
        from .jobs import controlled_path
        with db.engine.connect() as conn:
            row = service.owned(conn, db.tasks, task_id, user["id"])
        if row["kind"] != "export" or row["status"] != "completed" or not row["output_name"]:
            raise HTTPException(409, "导出尚未完成")
        path = controlled_path(row["output_name"])
        if not path.is_file():
            raise HTTPException(404, "导出文件不可用，请重新导出")
        return FileResponse(path, media_type="text/csv; charset=utf-8", filename="data-export.csv")

    @app.get("/api/history")
    def history(limit: int = Query(50, ge=1, le=200), user=Depends(auth.current_user)):
        with db.engine.connect() as conn:
            return {"operations": service.history_rows(conn, user["id"], limit)}

    return app


app = create_app()
