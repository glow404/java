"""Initial account-scoped data schema; frozen snapshot for reproducible migrations.

Created from the first version's table declarations. Never imports live application
metadata, so future source changes cannot rewrite the history of this revision.
"""
from alembic import op
from sqlalchemy import (MetaData, Table, Column, String, Integer, Boolean, DateTime,
                        ForeignKey, Index, UniqueConstraint)
from sqlalchemy.dialects.postgresql import JSONB
revision = "0001"
down_revision = None
branch_labels = None
depends_on = None
metadata = MetaData()

users = Table("users", metadata,
    Column("id", String(32), primary_key=True), Column("username", String(100), unique=True, nullable=False),
    Column("password_hash", String, nullable=False), Column("active", Boolean, nullable=False, default=True),
    Column("created_at", DateTime(timezone=True), nullable=False))

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
    Column("fields", JSONB, nullable=False), Column("version", Integer, default=1, nullable=False),
    Column("row_count", Integer, default=0, nullable=False), Column("created_at", DateTime(timezone=True), nullable=False))

data_rows = Table("data_rows", metadata,
    Column("id", String(32), primary_key=True), Column("table_id", ForeignKey("data_tables.id"), nullable=False),
    Column("owner_id", ForeignKey("users.id"), nullable=False), Column("values", JSONB, nullable=False), Column("source", JSONB, nullable=False))

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
    Column("table_id", ForeignKey("data_tables.id"), nullable=False), Column("table_version", Integer, nullable=False),
    Column("operation", String(20), nullable=False), Column("status", String(20), nullable=False),
    Column("workflow_thread", String(100)),
    Column("plan", JSONB, nullable=False), Column("before", JSONB, nullable=False), Column("after", JSONB, nullable=False),
    Column("affected_count", Integer, nullable=False), Column("result_id", ForeignKey("results.id")),
    Column("expires_at", DateTime(timezone=True), nullable=False), Column("created_at", DateTime(timezone=True), nullable=False))

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

for table in (sessions, files, tasks, data_tables, conversations, results, plans, messages, audits):
    Index(f"ix_{table.name}_owner_created", table.c.owner_id, table.c.created_at)

Index("ix_sessions_expires", sessions.c.expires_at)

Index("ix_login_attempt_key_time", login_attempts.c.key, login_attempts.c.created_at)

Index("ix_data_rows_owner_table_id", data_rows.c.owner_id, data_rows.c.table_id, data_rows.c.id)

Index("ix_messages_conversation_created", messages.c.conversation_id, messages.c.created_at)

Index("ix_tasks_status_updated", tasks.c.status, tasks.c.updated_at)

Index("ix_results_conversation", results.c.conversation_id)

Index("ix_plans_status_expires", plans.c.status, plans.c.expires_at)

def upgrade():
    metadata.create_all(op.get_bind())


def downgrade():
    raise RuntimeError("Destructive downgrade disabled. Restore a verified backup to a separate database instead.")
