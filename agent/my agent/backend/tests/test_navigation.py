"""导航目录回归：最近提问和活动顺序由当前账号消息派生。

只使用独立 vibe_test 的测试账号，不修改业务对话。
"""
import os
from datetime import timedelta

import pytest
from sqlalchemy import update
from app import db, service
from test_api import Account


@pytest.fixture(autouse=True)
def isolated_database():
    if os.getenv("VIBE_TEST_DATABASE") != "true" or db.engine.url.database != "vibe_test":
        pytest.skip("requires isolated vibe_test database")


def test_recent_conversations_use_last_user_question_and_last_activity_with_owner_scope():
    owner, other = Account(), Account()
    older = owner.post("/api/conversations", json={"title": "旧表名标题"}).json()
    newer = owner.post("/api/conversations", json={"title": "新建但未提问"}).json()
    foreign = other.post("/api/conversations", json={"title": "其他账号"}).json()
    now = db.utcnow()
    with db.engine.begin() as conn:
        conn.execute(update(db.conversations).where(db.conversations.c.id == older["id"])
            .values(created_at=now - timedelta(days=7)))
        first = service.save_message(conn, owner.id, older["id"], "user", "查看前10条")
        conn.execute(update(db.messages).where(db.messages.c.id == first["id"])
            .values(created_at=now - timedelta(days=6)))
        latest = service.save_message(conn, owner.id, older["id"], "user", "FRR小于3%时FAR最小值是多少？")
        conn.execute(update(db.messages).where(db.messages.c.id == latest["id"])
            .values(created_at=now + timedelta(seconds=1)))
        reply = service.save_message(conn, owner.id, older["id"], "assistant", "助手回答不能作为提问选项")
        conn.execute(update(db.messages).where(db.messages.c.id == reply["id"])
            .values(created_at=now + timedelta(seconds=2)))
        service.save_message(conn, other.id, foreign["id"], "user", "不得显示的其他账号问题")
    response = owner.client.get("/api/conversations")
    assert response.status_code == 200, response.text
    conversations = response.json()["conversations"]
    assert [item["id"] for item in conversations] == [older["id"], newer["id"]]
    assert conversations[0]["last_question"] == "FRR小于3%时FAR最小值是多少？"
    assert conversations[0]["updated_at"] > conversations[1]["updated_at"]
    assert conversations[1]["last_question"] is None
    assert foreign["id"] not in response.text
