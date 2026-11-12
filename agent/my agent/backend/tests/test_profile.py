"""个人主页真实 API 回归：资料隔离、邮箱登录、改密撤销会话和受控头像。

仅在独立 vibe_test 中创建测试账号及文件，不读写真实业务账号。
"""

from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
import os
from uuid import uuid4

from fastapi.testclient import TestClient
from PIL import Image
from PIL.PngImagePlugin import PngInfo
import pytest
from sqlalchemy import select, update, func

from app import auth, db
from app.main import app
from app.profile import AVATAR_MAX_BYTES
from test_api import Account


@pytest.fixture(autouse=True)
def isolated_database():
    if os.getenv("VIBE_TEST_DATABASE") != "true" or db.engine.url.database != "vibe_test":
        pytest.skip("requires isolated vibe_test database and VIBE_TEST_DATABASE=true")


@pytest.fixture
def owner():
    return Account()


def unique_email():
    return "profile_" + uuid4().hex + "@example.com"


def image_bytes(size=(600, 400)):
    metadata = PngInfo()
    metadata.add_text("private_source", "should not survive reencoding")
    output = BytesIO()
    Image.new("RGBA", size, (20, 90, 200, 150)).save(output, "PNG", pnginfo=metadata)
    return output.getvalue()


def test_profile_nickname_preserves_login_and_data_ownership(owner):
    table = owner.table()
    initial = owner.client.get("/api/profile")
    assert initial.status_code == 200
    assert initial.json() == {"id": owner.id, "username": owner.username, "display_name": "", "bio": "", "email": None, "avatar_url": None}
    saved = owner.patch("/api/profile", json={"display_name": "学习昵称", "bio": "数据分析学习中"})
    assert saved.status_code == 200
    assert saved.json()["username"] == owner.username
    assert saved.json()["display_name"] == "学习昵称"
    assert saved.json()["bio"] == "数据分析学习中"
    me = owner.client.get("/api/auth/me").json()
    assert me["display_name"] == "学习昵称" and me["bio"] == "数据分析学习中"
    assert "password_hash" not in me and "session_id" not in me
    with db.engine.connect() as conn:
        assert conn.scalar(select(db.data_tables.c.owner_id).where(db.data_tables.c.id == table["id"])) == owner.id
    reopened = TestClient(app)
    assert reopened.post("/api/auth/login", json={"username": owner.username, "password": owner.password}).status_code == 200
    assert reopened.get("/api/profile").json()["display_name"] == "学习昵称"


def test_profile_rejects_anonymous_csrf_and_login_account_edits(owner):
    anonymous = TestClient(app)
    assert anonymous.get("/api/profile").status_code == 401
    assert anonymous.patch("/api/profile", json={"bio": "forged"}).status_code == 401
    assert owner.client.patch("/api/profile", json={"bio": "forged"}).status_code == 403
    assert owner.patch("/api/profile", json={"username": "renamed"}).status_code == 422
    other = Account()
    other.patch("/api/profile", json={"display_name": "另一个昵称"})
    assert owner.client.get("/api/profile").json()["display_name"] == ""


def test_email_change_requires_password_and_supports_normalized_login(owner):
    email = unique_email()
    for payload in ({"email": email}, {"email": email, "current_password": "wrong-password-123", "bio": "must roll back"}):
        denied = owner.patch("/api/profile", json=payload)
        assert denied.status_code == 400
        unchanged = owner.client.get("/api/profile").json()
        assert unchanged["email"] is None and unchanged["bio"] == ""
    bound = owner.patch("/api/profile", json={"email": " " + email.upper() + " ", "current_password": owner.password})
    assert bound.status_code == 200
    assert bound.json()["email"] == email
    for identifier in (owner.username, email.upper(), " " + email + " "):
        client = TestClient(app)
        logged = client.post("/api/auth/login", json={"username": identifier, "password": owner.password})
        assert logged.status_code == 200
        assert logged.json()["id"] == owner.id and logged.json()["username"] == owner.username
    assert owner.patch("/api/profile", json={"email": email}).status_code == 200
    assert owner.patch("/api/profile", json={"email": None}).status_code == 400
    cleared = owner.patch("/api/profile", json={"email": None, "current_password": owner.password})
    assert cleared.status_code == 200 and cleared.json()["email"] is None
    assert TestClient(app).post("/api/auth/login", json={"username": email, "password": owner.password}).status_code == 401


@pytest.mark.parametrize("email", ["not-an-email", "a@@example.com", "a..b@example.com", ".a@example.com", "a@-bad.com", "a@example", "<a@example.com>"])
def test_invalid_email_is_not_saved(owner, email):
    response = owner.patch("/api/profile", json={"email": email, "current_password": owner.password})
    assert response.status_code == 422
    assert owner.client.get("/api/profile").json()["email"] is None


def test_email_unique_namespace_includes_existing_and_new_usernames(owner):
    email = unique_email()
    other = Account()
    assert owner.patch("/api/profile", json={"email": email, "current_password": owner.password}).status_code == 200
    assert other.patch("/api/profile", json={"email": email.upper(), "current_password": other.password}).status_code == 409
    with pytest.raises(ValueError, match="邮箱冲突"):
        auth.create_user(email.upper(), uuid4().hex + "!Aa")
    other_login_email = unique_email()
    auth.create_user(other_login_email.upper(), uuid4().hex + "!Aa")
    assert other.patch("/api/profile", json={"email": other_login_email, "current_password": other.password}).status_code == 409


def test_concurrent_email_binding_has_one_owner():
    accounts = [Account(), Account()]
    email = unique_email()
    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(executor.map(lambda account: account.patch("/api/profile", json={"email": email, "current_password": account.password}), accounts))
    assert sorted(response.status_code for response in responses) == [200, 409]
    with db.engine.connect() as conn:
        assert conn.scalar(select(func.count()).select_from(db.users).where(db.users.c.email == email)) == 1


def test_password_change_revokes_every_session_and_preserves_history(owner):
    table = owner.table()
    plan_response = owner.ask(table, {"operation": "update", "filters": [{"field_id": "c1", "op": "eq", "value": "001"}], "values": {"c3": 91}})
    assert plan_response.status_code == 200
    plan_id = plan_response.json()["plan"]["id"]
    second = TestClient(app)
    assert second.post("/api/auth/login", json={"username": owner.username, "password": owner.password}).status_code == 200
    with db.engine.connect() as conn:
        previous_sessions = conn.scalar(select(func.count()).select_from(db.sessions).where(db.sessions.c.owner_id == owner.id))
    rejected = owner.post("/api/profile/password", json={"current_password": "incorrect-password-123", "new_password": "a-valid-new-password-123"})
    assert rejected.status_code == 400
    assert owner.client.get("/api/profile").status_code == 200
    assert owner.post("/api/profile/password", json={"current_password": owner.password, "new_password": "too-short"}).status_code == 422
    new_password = uuid4().hex + "new!Aa"
    changed = owner.post("/api/profile/password", json={"current_password": owner.password, "new_password": new_password})
    assert changed.status_code == 200 and changed.json()["reauthenticate"] is True
    assert owner.client.get("/api/profile").status_code == 401
    assert second.get("/api/profile").status_code == 401
    with db.engine.connect() as conn:
        assert conn.scalar(select(func.count()).select_from(db.sessions).where(db.sessions.c.owner_id == owner.id)) == previous_sessions
        assert conn.scalar(select(db.plans.c.id).where(db.plans.c.id == plan_id)) == plan_id
        active = conn.scalar(select(func.count()).select_from(db.sessions).where(db.sessions.c.owner_id == owner.id, db.sessions.c.expires_at > db.utcnow()))
        assert active == 0
        details = conn.execute(select(db.audits.c.details).where(db.audits.c.owner_id == owner.id, db.audits.c.action == "password_change")).scalar_one()
        assert details == {"all_sessions_revoked": True}
    assert second.post("/api/auth/login", json={"username": owner.username, "password": owner.password}).status_code == 401
    assert second.post("/api/auth/login", json={"username": owner.username, "password": new_password}).status_code == 200


def test_avatar_reencodes_image_without_parse_task_and_is_account_scoped(owner):
    with db.engine.connect() as conn:
        tasks_before = conn.scalar(select(func.count()).select_from(db.tasks).where(db.tasks.c.owner_id == owner.id))
    uploaded = owner.post("/api/profile/avatar", files={"file": ("../../source.png", image_bytes(), "image/png")})
    assert uploaded.status_code == 200
    avatar_url = uploaded.json()["avatar_url"]
    assert avatar_url.startswith("/api/profile/avatar?v=")
    image_response = owner.client.get(avatar_url)
    assert image_response.status_code == 200 and image_response.headers["content-type"] == "image/png"
    with Image.open(BytesIO(image_response.content)) as avatar:
        assert avatar.format == "PNG" and max(avatar.size) == 256
        assert "private_source" not in avatar.info
    with db.engine.connect() as conn:
        tasks_after = conn.scalar(select(func.count()).select_from(db.tasks).where(db.tasks.c.owner_id == owner.id))
        file_id = conn.scalar(select(db.users.c.avatar_file_id).where(db.users.c.id == owner.id))
        file_row = conn.execute(select(db.files).where(db.files.c.id == file_id)).mappings().one()
        assert tasks_before == tasks_after and file_row["owner_id"] == owner.id
        assert file_row["stored_name"].endswith(".png") and "/" not in file_row["stored_name"]
    other = Account()
    assert other.client.get(avatar_url).status_code == 404
    assert other.client.get(f"/api/files/{file_id}/download").status_code == 404
    assert TestClient(app).get(avatar_url).status_code == 401
    assert owner.client.post("/api/profile/avatar", files={"file": ("image.png", image_bytes(), "image/png")}).status_code == 403
    # 即便存储引用被错误指向别人的文件，读取时仍必须再次核对文件归属。
    with db.engine.begin() as conn:
        conn.execute(update(db.users).where(db.users.c.id == other.id).values(avatar_file_id=file_id))
    assert other.client.get("/api/profile/avatar").status_code == 404


@pytest.mark.parametrize("filename,data,status", [
    ("fake.png", b"not an image", 422),
    ("image.svg", b"<svg></svg>", 415),
    ("empty.png", b"", 422),
    ("large.png", b"x" * (AVATAR_MAX_BYTES + 1), 413),
    ("wide.png", image_bytes((9000, 1)), 413),
])
def test_invalid_avatar_does_not_replace_saved_image(owner, filename, data, status):
    original = owner.post("/api/profile/avatar", files={"file": ("valid.png", image_bytes(), "image/png")})
    assert original.status_code == 200
    rejected = owner.post("/api/profile/avatar", files={"file": (filename, data, "image/png")})
    assert rejected.status_code == status
    assert owner.client.get("/api/profile").json()["avatar_url"] == original.json()["avatar_url"]
