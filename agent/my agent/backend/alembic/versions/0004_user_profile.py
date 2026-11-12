"""增加账号昵称、简介、邮箱和头像引用；保留旧登录账号与已有数据。"""

from alembic import op
import sqlalchemy as sa

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("users", sa.Column("display_name", sa.String(100), nullable=False, server_default=""))
    op.add_column("users", sa.Column("bio", sa.String(2000), nullable=False, server_default=""))
    op.add_column("users", sa.Column("email", sa.String(254), nullable=True))
    op.add_column("users", sa.Column("avatar_file_id", sa.String(32), nullable=True))
    op.create_unique_constraint("uq_users_email", "users", ["email"])
    op.create_foreign_key("fk_users_avatar_file", "users", "files", ["avatar_file_id"], ["id"])


def downgrade():
    op.drop_constraint("fk_users_avatar_file", "users", type_="foreignkey")
    op.drop_constraint("uq_users_email", "users", type_="unique")
    for column in ("avatar_file_id", "email", "bio", "display_name"):
        op.drop_column("users", column)
