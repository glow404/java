"""保存表说明来源及已确认的粒度关联；保留已有表与记录。"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("data_tables", sa.Column("description_provenance", sa.String(30), nullable=False, server_default="file"))
    op.add_column("data_tables", sa.Column("structure", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")))


def downgrade():
    op.drop_column("data_tables", "structure")
    op.drop_column("data_tables", "description_provenance")
