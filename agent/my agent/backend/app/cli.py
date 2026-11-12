"""运维入口：迁移及检查点初始化、交互创建无默认密码的账号。"""

import argparse
import getpass
import os
from pathlib import Path
from alembic import command
from alembic.config import Config
from sqlalchemy.exc import IntegrityError
from .auth import create_user
from .workflow import setup_checkpoints


def main():
    parser = argparse.ArgumentParser(description="vibe 服务管理")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("migrate")
    user = commands.add_parser("create-user")
    user.add_argument("--username", required=True)
    user.add_argument("--password-env", help="读取指定环境变量；避免密码出现在命令行或日志")
    args = parser.parse_args()
    if args.command == "migrate":
        root = Path(__file__).resolve().parent.parent
        config = Config(str(root / "alembic.ini"))
        config.set_main_option("script_location", str(root / "alembic"))
        command.upgrade(config, "head")
        setup_checkpoints()
        print("数据库与 LangGraph 检查点迁移完成")
    else:
        if args.password_env:
            password = os.environ.get(args.password_env)
            if not password:
                parser.error("指定的密码环境变量未配置")
        else:
            password = getpass.getpass("新密码（至少 12 字符）：")
            if password != getpass.getpass("再次输入："):
                parser.error("两次密码不一致")
        try:
            create_user(args.username, password)
        except ValueError as exc:
            parser.error(str(exc))
        except IntegrityError:
            parser.error("用户名已存在；没有覆盖账号或修改已有密码")
        print(f"账号 {args.username} 已创建")


if __name__ == "__main__":
    main()
