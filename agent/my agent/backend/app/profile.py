"""个人资料 API：保存昵称和简介、绑定邮箱、上传头像与安全修改密码。

所有读写只针对当前会话账号；头像使用已有持久文件目录，不创建数据解析任务。
"""

import hashlib
from io import BytesIO
from pathlib import Path
import re
import warnings

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile
from fastapi.responses import FileResponse
from PIL import Image, ImageOps, UnidentifiedImageError
from sqlalchemy import insert, select, update, func
from sqlalchemy.exc import IntegrityError

from . import auth, db, service
from .config import settings
from .jobs import controlled_path
from .schemas import PasswordChange, ProfilePatch

router = APIRouter(prefix="/api/profile", tags=["profile"])
AVATAR_MAX_BYTES = 2 * 1024 * 1024
AVATAR_MAX_PIXELS = 16_000_000
AVATAR_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}


def normalize_email(value: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    normalized = value.strip().lower()
    local, separator, domain = normalized.rpartition("@")
    # 绑定邮箱是登录标识，统一大小写和空格后再校验，避免同一地址被重复绑定。
    local_valid = re.fullmatch(r"[a-z0-9.!#$%&'*+/=?^_`{|}~-]+", local)
    labels = domain.split(".")
    domain_valid = len(labels) >= 2 and all(re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", part) for part in labels)
    if not separator or not local_valid or len(local) > 64 or local.startswith(".") or local.endswith(".") or ".." in local or not domain_valid:
        raise HTTPException(422, "请输入有效邮箱地址")
    return normalized


def locked_account(conn, user):
    row = conn.execute(select(db.users).where(db.users.c.id == user["id"],
        db.users.c.active.is_(True)).with_for_update()).mappings().first()
    session = conn.scalar(select(db.sessions.c.id).where(db.sessions.c.id == user["session_id"],
        db.sessions.c.owner_id == user["id"], db.sessions.c.expires_at > db.utcnow()))
    # 再次检查会话，阻止请求排队期间已被另一设备改密撤销的旧会话继续修改资料。
    if not row or not session:
        raise HTTPException(401, "会话已过期，请重新登录")
    return dict(row)


def verify_current_password(account, password: str | None):
    if not password or not auth.password_matches(account["password_hash"], password):
        raise HTTPException(400, "当前密码不正确")


@router.get("")
def get_profile(user=Depends(auth.current_user)):
    return auth.public_user(user)


@router.patch("")
def update_profile(body: ProfilePatch, user=Depends(auth.current_user)):
    try:
        with db.engine.begin() as conn:
            account = locked_account(conn, user)
            changes = {key: getattr(body, key).strip() for key in ("display_name", "bio") if key in body.model_fields_set}
            if "email" in body.model_fields_set:
                email = normalize_email(body.email)
                if email != account["email"]:
                    verify_current_password(account, body.current_password)
                    if email:
                        auth.lock_login_identifier(conn, email)
                        collision = conn.scalar(select(db.users.c.id).where(db.users.c.id != user["id"],
                            (db.users.c.email == email) | (func.lower(func.trim(db.users.c.username)) == email)).limit(1))
                        if collision:
                            raise HTTPException(409, "该邮箱已被其他账号使用，或与其他登录账号冲突")
                    changes["email"] = email
            changes = {key: value for key, value in changes.items() if value != account[key]}
            if changes:
                conn.execute(update(db.users).where(db.users.c.id == user["id"]).values(**changes))
                service.audit(conn, user["id"], "profile_update", details={"changed_fields": sorted(changes)})
                account.update(changes)
        return auth.public_user(account)
    except IntegrityError as exc:
        if getattr(exc.orig, "sqlstate", None) == "23505":
            raise HTTPException(409, "该邮箱已被其他账号使用") from exc
        raise


@router.post("/password")
def change_password(body: PasswordChange, response: Response, user=Depends(auth.current_user)):
    with db.engine.begin() as conn:
        account = locked_account(conn, user)
        verify_current_password(account, body.current_password)
        conn.execute(update(db.users).where(db.users.c.id == user["id"]).values(password_hash=auth.hasher.hash(body.new_password)))
        # 旧会话仍被审计/计划引用；统一过期而不是删除，同时阻止其他设备继续操作。
        conn.execute(update(db.sessions).where(db.sessions.c.owner_id == user["id"]).values(expires_at=db.utcnow()))
        service.audit(conn, user["id"], "password_change", details={"all_sessions_revoked": True})
    response.delete_cookie(settings.cookie_name, path="/api", secure=settings.cookie_secure, httponly=True, samesite="strict")
    return {"ok": True, "reauthenticate": True}


def encode_avatar(data: bytes) -> bytes:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(data)) as image:
                if image.format not in ("JPEG", "PNG", "WEBP"):
                    raise HTTPException(415, "头像仅支持 JPG、PNG、WebP 图片")
                if image.width * image.height > AVATAR_MAX_PIXELS or max(image.size) > 8192:
                    raise HTTPException(413, "头像分辨率过大，请缩小图片后重试")
                image.load()
                prepared = ImageOps.exif_transpose(image).convert("RGBA")
                prepared.thumbnail((256, 256))
                # 重编码并去除元数据，不直接返回用户上传的原始文件内容。
                clean = Image.new("RGBA", prepared.size)
                clean.paste(prepared)
                output = BytesIO()
                clean.save(output, format="PNG")
                return output.getvalue()
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise HTTPException(422, "头像图片无法读取，请上传有效图片") from exc


@router.post("/avatar")
async def upload_avatar(file: UploadFile = File(...), user=Depends(auth.current_user)):
    try:
        if Path(file.filename or "").suffix.lower() not in AVATAR_SUFFIXES:
            raise HTTPException(415, "头像仅支持 JPG、PNG、WebP 图片")
        data = bytearray()
        while chunk := await file.read(65536):
            data.extend(chunk)
            if len(data) > AVATAR_MAX_BYTES:
                raise HTTPException(413, "头像不能超过 2 MB")
        if not data:
            raise HTTPException(422, "头像图片不能为空")
        encoded = encode_avatar(bytes(data))
    finally:
        await file.close()
    identifier, stored_name = db.new_id(), db.new_id() + ".png"
    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    path = controlled_path(stored_name)
    created = False
    try:
        with path.open("xb") as destination:
            created = True
            destination.write(encoded)
        with db.engine.begin() as conn:
            account = locked_account(conn, user)
            conn.execute(insert(db.files).values(id=identifier, owner_id=user["id"], original_name="avatar.png",
                stored_name=stored_name, mime="image/png", size=len(encoded), sha256=hashlib.sha256(encoded).hexdigest(), created_at=db.utcnow()))
            conn.execute(update(db.users).where(db.users.c.id == user["id"]).values(avatar_file_id=identifier))
            service.audit(conn, user["id"], "avatar_update", details={"file_id": identifier})
            account["avatar_file_id"] = identifier
    except Exception:
        # 数据库提交失败时仅清理本请求新文件，不修改之前头像或数据文件。
        if created:
            path.unlink(missing_ok=True)
        raise
    return auth.public_user(account)


@router.get("/avatar")
def read_avatar(user=Depends(auth.current_user)):
    identifier = user.get("avatar_file_id")
    if not identifier:
        raise HTTPException(404, "尚未设置头像")
    with db.engine.connect() as conn:
        row = conn.execute(select(db.files.c.stored_name).where(db.files.c.id == identifier,
            db.files.c.owner_id == user["id"], db.files.c.mime == "image/png")).mappings().first()
    if not row:
        raise HTTPException(404, "头像不可用")
    path = controlled_path(row["stored_name"])
    if not path.is_file():
        raise HTTPException(404, "头像文件暂时不可用")
    return FileResponse(path, media_type="image/png")
