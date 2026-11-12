"""Argon2 密码与可撤销服务端会话；浏览器身份参数不参与授权。"""

import hashlib
import hmac
import secrets
from datetime import timedelta
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, InvalidHashError, VerificationError
from fastapi import Depends, HTTPException, Request, Response
from sqlalchemy import select, insert, delete, update, func, or_, text
from . import db
from .config import settings
from .schemas import Login

hasher = PasswordHasher()
DUMMY_HASH = hasher.hash(secrets.token_urlsafe(24))


def public_user(user):
    """白名单输出个人资料；密码摘要与服务端会话标识不进入 API。"""
    avatar_id = user.get("avatar_file_id")
    return {"id": user["id"], "username": user["username"],
            "display_name": user.get("display_name") or "", "bio": user.get("bio") or "",
            "email": user.get("email"),
            "avatar_url": f"/api/profile/avatar?v={avatar_id}" if avatar_id else None}


def password_matches(password_hash: str, password: str) -> bool:
    try:
        return hasher.verify(password_hash, password)
    except (VerifyMismatchError, InvalidHashError, VerificationError):
        return False


def lock_login_identifier(conn, identifier: str):
    # 邮箱与用户名共用登录入口；同一标识的绑定和账号创建串行化，避免并发抢占。
    conn.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:identifier, 0))"),
                 {"identifier": identifier.strip().lower()})


def create_user(username: str, password: str):
    if len(password) < 12:
        raise ValueError("密码至少 12 个字符")
    if not username.strip() or len(username) > 100:
        raise ValueError("用户名长度应为 1 至 100")
    with db.engine.begin() as conn:
        lock_login_identifier(conn, username)
        if conn.scalar(select(db.users.c.id).where(db.users.c.email == username.strip().lower()).limit(1)):
            raise ValueError("用户名与已绑定邮箱冲突")
        conn.execute(insert(db.users).values(id=db.new_id(), username=username.strip(),
            password_hash=hasher.hash(password), active=True, created_at=db.utcnow()))


def login(body: Login, request: Request, response: Response):
    identifier = body.username.strip()
    key = hashlib.sha256((identifier.lower() + ":" + (request.client.host if request.client else "unknown")).encode()).hexdigest()
    now = db.utcnow()
    # 失败计数保存在数据库；服务重启不会重置限流。哈希校验统一执行，减少账号枚举。
    with db.engine.begin() as conn:
        conn.execute(delete(db.login_attempts).where(db.login_attempts.c.created_at < now - timedelta(minutes=15)))
        attempts = conn.scalar(select(func.count()).select_from(db.login_attempts).where(
            db.login_attempts.c.key == key, db.login_attempts.c.created_at > now - timedelta(minutes=15)))
        if attempts >= 8:
            raise HTTPException(429, "登录失败次数过多，请 15 分钟后重试")
        # 行锁使登录发放会话与改密串行化，避免旧密码在撤销会话后又发放新会话。
        matches = conn.execute(select(db.users).where(or_(db.users.c.username == identifier,
            db.users.c.email == identifier.lower())).limit(2).with_for_update()).mappings().all()
        user = matches[0] if len(matches) == 1 else None
        valid = password_matches(user["password_hash"] if user else DUMMY_HASH, body.password)
        if not user or not user["active"] or not valid:
            conn.execute(insert(db.login_attempts).values(id=db.new_id(), key=key, created_at=now))
            failed = True
        else:
            failed = False
            conn.execute(delete(db.login_attempts).where(db.login_attempts.c.key == key))
            token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
            # Cookie 原始值只发给浏览器；数据库存不可逆摘要，降低备份泄漏的影响。
            session_id = hashlib.sha256(token.encode()).hexdigest()
            conn.execute(insert(db.sessions).values(id=session_id, owner_id=user["id"], csrf_token=csrf,
                created_at=now, expires_at=now + timedelta(hours=settings.session_hours)))
    if failed:
        raise HTTPException(401, "用户名或密码错误")
    response.set_cookie(settings.cookie_name, token, httponly=True, secure=settings.cookie_secure,
        samesite="strict", max_age=settings.session_hours * 3600, path="/api")
    return {**public_user(user), "csrf_token": csrf}


def current_user(request: Request):
    token = request.cookies.get(settings.cookie_name)
    if not token:
        raise HTTPException(401, "请先登录")
    session_id = hashlib.sha256(token.encode()).hexdigest()
    with db.engine.connect() as conn:
        row = conn.execute(select(db.users.c.id, db.users.c.username, db.users.c.display_name, db.users.c.bio,
            db.users.c.email, db.users.c.avatar_file_id, db.sessions.c.id.label("session_id"),
            db.sessions.c.csrf_token).select_from(db.sessions.join(db.users)).where(
            db.sessions.c.id == session_id, db.sessions.c.expires_at > db.utcnow(), db.users.c.active.is_(True))).mappings().first()
    if not row:
        raise HTTPException(401, "会话已过期，请重新登录")
    user = dict(row)
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        csrf = request.headers.get("X-CSRF-Token", "")
        if not hmac.compare_digest(csrf, user["csrf_token"]):
            raise HTTPException(403, "请求校验失败，请刷新登录状态后重试")
    return user


def logout(response: Response, user=Depends(current_user)):
    with db.engine.begin() as conn:
        # 历史变更计划仍引用会话；撤销通过过期时间完成，不删除审计关联。
        conn.execute(update(db.sessions).where(db.sessions.c.id == user["session_id"]).values(expires_at=db.utcnow()))
    response.delete_cookie(settings.cookie_name, path="/api", secure=settings.cookie_secure, httponly=True, samesite="strict")
    return {"ok": True}
