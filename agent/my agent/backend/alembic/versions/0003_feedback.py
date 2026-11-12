"""增量保存行序、关联、可中止助手和显示偏好；不删除任何已有数据。"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("data_rows", sa.Column("position", sa.BigInteger(), nullable=False, server_default="0"))
    op.add_column("data_tables", sa.Column("archived", sa.Boolean(), nullable=False, server_default=sa.text("false")))
    op.add_column("plans", sa.Column("intent", sa.String(), nullable=False, server_default=""))
    op.alter_column("plans", "table_id", existing_type=sa.String(32), nullable=True)
    # 首选导入预览行序，其次文件源行；完全缺失来源的旧行只能按原有ID稳定排序。
    op.execute("""WITH ranked AS (
        SELECT id, row_number() OVER (PARTITION BY owner_id, table_id ORDER BY
          CASE WHEN source->>'preview_row_index' ~ '^[0-9]+$' THEN (source->>'preview_row_index')::bigint
               WHEN source->>'row' ~ '^[0-9]+$' THEN (source->>'row')::bigint END NULLS LAST, id) AS n
        FROM data_rows) UPDATE data_rows SET position=ranked.n FROM ranked WHERE data_rows.id=ranked.id""")
    # 迁移必须冻结这一版结构，不能导入未来可能变化的应用表定义。
    op.create_table("table_relations",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("owner_id", sa.String(32), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("left_table_id", sa.String(32), sa.ForeignKey("data_tables.id"), nullable=False),
        sa.Column("right_table_id", sa.String(32), sa.ForeignKey("data_tables.id"), nullable=False),
        sa.Column("left_field_id", sa.String(64), nullable=False),
        sa.Column("right_field_id", sa.String(64), nullable=False),
        sa.Column("join_type", sa.String(10), nullable=False), sa.Column("status", sa.String(20), nullable=False),
        sa.Column("left_version", sa.Integer(), nullable=False), sa.Column("right_version", sa.Integer(), nullable=False),
        sa.Column("preview", JSONB(), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    op.create_table("assistant_runs",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("owner_id", sa.String(32), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("conversation_id", sa.String(32), sa.ForeignKey("conversations.id"), nullable=False),
        sa.Column("session_id", sa.String(64), sa.ForeignKey("sessions.id"), nullable=False),
        sa.Column("message_id", sa.String(32), sa.ForeignKey("messages.id"), unique=True, nullable=False),
        sa.Column("status", sa.String(20), nullable=False), sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error", sa.String()), sa.Column("response", JSONB()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False))
    op.create_table("result_visibility",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("owner_id", sa.String(32), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("result_id", sa.String(32), sa.ForeignKey("results.id"), nullable=False),
        sa.Column("hidden", sa.Boolean(), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("owner_id", "result_id", name="uq_result_visibility_owner_result"))
    for name in ("table_relations", "assistant_runs", "result_visibility"):
        op.create_index(f"ix_{name}_owner_created", name, ["owner_id", "created_at"])
    op.create_index("ix_relations_owner_tables", "table_relations", ["owner_id", "left_table_id", "right_table_id"])
    op.create_index("ix_runs_status_updated", "assistant_runs", ["status", "updated_at"])
    op.create_index("uq_runs_active_conversation", "assistant_runs", ["owner_id", "conversation_id"], unique=True,
                    postgresql_where=sa.text("status IN ('queued', 'running')"))
    op.create_index("ix_data_rows_table_position", "data_rows", ["owner_id", "table_id", "position", "id"])


def downgrade():
    # 自动回退会删除新运行/关联/偏好记录；要求先备份并审阅，避免一条命令丢失用户数据。
    raise RuntimeError("0003 回退需先导出新增记录并采用经审阅的迁移方案；不自动删除数据")
