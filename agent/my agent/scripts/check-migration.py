"""在独立空库验证0001至最新迁移和应用注册表一致；保留验收库，不修改业务库。"""
import os
from sqlalchemy.engine import make_url

database = os.environ.get("VIBE_MIGRATION_DATABASE", "")
if not database.startswith("vibe_migration_") or not database.removeprefix("vibe_migration_").isdigit():
    raise ValueError("Only isolated migration databases may be verified")
os.environ["DATABASE_URL"] = make_url(os.environ["DATABASE_URL"]).set(database=database).render_as_string(hide_password=False)

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text
from app import db
from app.workflow import setup_checkpoints

config = Config("/app/alembic.ini")
command.upgrade(config, "head")
setup_checkpoints()
# 对照运行时字段、非空约束和索引，防止只验证“迁移命令未报错”。
inspector = inspect(db.engine)
for table in db.metadata.sorted_tables:
    actual = {column["name"]: column for column in inspector.get_columns(table.name)}
    assert set(actual) == set(table.c.keys()), f"Columns differ: {table.name}"
    for column in table.c:
        assert actual[column.name]["nullable"] == column.nullable, f"Nullability differs: {table.name}.{column.name}"
    indices = {index["name"] for index in inspector.get_indexes(table.name)}
    assert {index.name for index in table.indexes}.issubset(indices), f"Indexes differ: {table.name}"
with db.engine.connect() as connection:
    revision = connection.scalar(text("SELECT version_num FROM alembic_version"))
print({"database": database, "revision": revision, "tables_verified": len(db.metadata.tables), "checkpoints": "initialized"})
