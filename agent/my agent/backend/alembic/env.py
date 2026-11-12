"""Alembic 从服务配置建立迁移连接，不把凭据写进 ini 文件。"""
from logging.config import fileConfig
from alembic import context
from app.db import metadata, engine

config = context.config
if config.config_file_name:
    fileConfig(config.config_file_name)

if context.is_offline_mode():
    from app.config import settings
    context.configure(url=settings.database_url, target_metadata=metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()
else:
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=metadata)
        with context.begin_transaction():
            context.run_migrations()
