"""SQLAlchemy Core 表注册：动态数据保存在带账号范围的 JSONB 行中。"""

from datetime import datetime, timezone
from uuid import uuid4
from sqlalchemy import (MetaData, Table, Column, String, Integer, BigInteger, Boolean, DateTime,
                        ForeignKey, Index, UniqueConstraint, create_engine, text)
from sqlalchemy.dialects.postgresql import JSONB
from .config import settings

metadata = MetaData()
engine = create_engine(settings.database_url, pool_pre_ping=True, pool_size=10, max_overflow=10)


def new_id():
    return uuid4().hex


def utcnow():
    return datetime.now(timezone.utc)


users = Table("users", metadata,
    Column("id", String(32), primary_key=True), Column("username", String(100), unique=True, nullable=False),
    Column("password_hash", String, nullable=False), Column("active", Boolean, nullable=False, default=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("display_name", String(100), nullable=False, server_default=""),
    Column("bio", String(2000), nullable=False, server_default=""),
    Column("email", String(254)),
    Column("avatar_file_id", String(32), ForeignKey("files.id", name="fk_users_avatar_file", use_alter=True)),
    UniqueConstraint("email", name="uq_users_email"))
sessions = Table("sessions", metadata,
    Column("id", String(64), primary_key=True), Column("owner_id", ForeignKey("users.id"), nullable=False),
    Column("csrf_token", String(64), nullable=False), Column("expires_at", DateTime(timezone=True), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False))
login_attempts = Table("login_attempts", metadata,
    Column("id", String(32), primary_key=True), Column("key", String(64), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False))
files = Table("files", metadata,
    Column("id", String(32), primary_key=True), Column("owner_id", ForeignKey("users.id"), nullable=False),
    Column("original_name", String(255), nullable=False), Column("stored_name", String(100), nullable=False),
    Column("mime", String(100)), Column("size", Integer, nullable=False), Column("sha256", String(64), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False))
tasks = Table("tasks", metadata,
    Column("id", String(32), primary_key=True), Column("owner_id", ForeignKey("users.id"), nullable=False),
    Column("file_id", ForeignKey("files.id")), Column("kind", String(20), nullable=False),
    Column("status", String(20), nullable=False), Column("error", String),
    Column("attempts", Integer, default=0, nullable=False), Column("preview", JSONB),
    Column("version", Integer, default=1, nullable=False), Column("table_ids", JSONB), Column("output_name", String(100)),
    Column("created_at", DateTime(timezone=True), nullable=False), Column("updated_at", DateTime(timezone=True), nullable=False))
data_tables = Table("data_tables", metadata,
    Column("id", String(32), primary_key=True), Column("owner_id", ForeignKey("users.id"), nullable=False),
    Column("file_id", ForeignKey("files.id")), Column("name", String(200), nullable=False), Column("description", String, nullable=False, default=""),
    Column("description_provenance", String(30), nullable=False, server_default="file"),
    Column("structure", JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    Column("fields", JSONB, nullable=False), Column("version", Integer, default=1, nullable=False),
    Column("row_count", Integer, default=0, nullable=False), Column("created_at", DateTime(timezone=True), nullable=False))
data_tables.append_column(Column("archived", Boolean, nullable=False, server_default=text("false")))
data_rows = Table("data_rows", metadata,
    Column("id", String(32), primary_key=True), Column("table_id", ForeignKey("data_tables.id"), nullable=False),
    Column("owner_id", ForeignKey("users.id"), nullable=False), Column("values", JSONB, nullable=False), Column("source", JSONB, nullable=False))
data_rows.append_column(Column("position", BigInteger, nullable=False, server_default=text("0")))
conversations = Table("conversations", metadata,
    Column("id", String(32), primary_key=True), Column("owner_id", ForeignKey("users.id"), nullable=False),
    Column("title", String(200), nullable=False), Column("created_at", DateTime(timezone=True), nullable=False))
results = Table("results", metadata,
    Column("id", String(32), primary_key=True), Column("owner_id", ForeignKey("users.id"), nullable=False),
    Column("conversation_id", ForeignKey("conversations.id"), nullable=False), Column("table_id", ForeignKey("data_tables.id"), nullable=False),
    Column("source_message_id", String(32), unique=True),
    Column("columns", JSONB, nullable=False), Column("rows", JSONB, nullable=False), Column("chart", JSONB),
    Column("explanation", String, nullable=False), Column("query_snapshot", JSONB, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False))
plans = Table("plans", metadata,
    Column("id", String(32), primary_key=True), Column("owner_id", ForeignKey("users.id"), nullable=False),
    Column("conversation_id", ForeignKey("conversations.id"), nullable=False), Column("session_id", ForeignKey("sessions.id"), nullable=False),
    Column("source_message_id", String(32), unique=True),
    Column("table_id", ForeignKey("data_tables.id")), Column("table_version", Integer, nullable=False),
    Column("operation", String(20), nullable=False), Column("status", String(20), nullable=False),
    Column("workflow_thread", String(100)),
    Column("plan", JSONB, nullable=False), Column("before", JSONB, nullable=False), Column("after", JSONB, nullable=False),
    Column("affected_count", Integer, nullable=False), Column("result_id", ForeignKey("results.id")),
    Column("expires_at", DateTime(timezone=True), nullable=False), Column("created_at", DateTime(timezone=True), nullable=False))
plans.append_column(Column("intent", String, nullable=False, server_default=""))
messages = Table("messages", metadata,
    Column("id", String(32), primary_key=True), Column("owner_id", ForeignKey("users.id"), nullable=False),
    Column("conversation_id", ForeignKey("conversations.id"), nullable=False), Column("role", String(20), nullable=False),
    Column("content", String, nullable=False), Column("result_id", ForeignKey("results.id")), Column("plan_id", ForeignKey("plans.id")),
    Column("clarifications", JSONB), Column("request", JSONB), Column("created_at", DateTime(timezone=True), nullable=False))
audits = Table("audits", metadata,
    Column("id", String(32), primary_key=True), Column("owner_id", ForeignKey("users.id"), nullable=False),
    Column("action", String(50), nullable=False), Column("table_id", ForeignKey("data_tables.id")),
    Column("plan_id", ForeignKey("plans.id")), Column("details", JSONB, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False), UniqueConstraint("plan_id", name="uq_audit_plan"))

table_relations = Table("table_relations", metadata,
    Column("id", String(32), primary_key=True), Column("owner_id", ForeignKey("users.id"), nullable=False),
    Column("left_table_id", ForeignKey("data_tables.id"), nullable=False), Column("right_table_id", ForeignKey("data_tables.id"), nullable=False),
    Column("left_field_id", String(64), nullable=False), Column("right_field_id", String(64), nullable=False),
    Column("join_type", String(10), nullable=False), Column("status", String(20), nullable=False),
    Column("left_version", Integer, nullable=False), Column("right_version", Integer, nullable=False),
    Column("preview", JSONB, nullable=False), Column("created_at", DateTime(timezone=True), nullable=False))
assistant_runs = Table("assistant_runs", metadata,
    Column("id", String(32), primary_key=True), Column("owner_id", ForeignKey("users.id"), nullable=False),
    Column("conversation_id", ForeignKey("conversations.id"), nullable=False), Column("session_id", ForeignKey("sessions.id"), nullable=False),
    Column("message_id", ForeignKey("messages.id"), unique=True, nullable=False),
    Column("status", String(20), nullable=False), Column("attempts", Integer, nullable=False, server_default="0"),
    Column("error", String), Column("response", JSONB), Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False))
result_visibility = Table("result_visibility", metadata,
    Column("id", String(32), primary_key=True), Column("owner_id", ForeignKey("users.id"), nullable=False),
    Column("result_id", ForeignKey("results.id"), nullable=False), Column("hidden", Boolean, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False), UniqueConstraint("owner_id", "result_id", name="uq_result_visibility_owner_result"))

# 高频接口均先限制账号；这些复合索引避免目录、历史与大表分页扫描其他账号。
for table in (sessions, files, tasks, data_tables, conversations, results, plans, messages, audits, table_relations, assistant_runs, result_visibility):
    Index(f"ix_{table.name}_owner_created", table.c.owner_id, table.c.created_at)
Index("ix_sessions_expires", sessions.c.expires_at)
Index("ix_login_attempt_key_time", login_attempts.c.key, login_attempts.c.created_at)
Index("ix_data_rows_owner_table_id", data_rows.c.owner_id, data_rows.c.table_id, data_rows.c.id)
Index("ix_messages_conversation_created", messages.c.conversation_id, messages.c.created_at)
Index("ix_tasks_status_updated", tasks.c.status, tasks.c.updated_at)
Index("ix_results_conversation", results.c.conversation_id)
Index("ix_plans_status_expires", plans.c.status, plans.c.expires_at)
Index("ix_data_rows_table_position", data_rows.c.owner_id, data_rows.c.table_id, data_rows.c.position, data_rows.c.id)
Index("ix_relations_owner_tables", table_relations.c.owner_id, table_relations.c.left_table_id, table_relations.c.right_table_id)
Index("ix_runs_status_updated", assistant_runs.c.status, assistant_runs.c.updated_at)
Index("uq_runs_active_conversation", assistant_runs.c.owner_id, assistant_runs.c.conversation_id, unique=True,
      postgresql_where=assistant_runs.c.status.in_(["queued", "running"]))
