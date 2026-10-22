"""登录会话：服务端保存随机令牌，客户端只持有 HttpOnly Cookie。

令牌用标准库 secrets 生成，不可预测；会话文档存于 MongoDB，
支持退出（删除）与过期（按 expires_at 判定）。
"""

import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

from pymongo.database import Database

SESSION_COOKIE = "survey_session"
SESSION_TTL = timedelta(days=7)
_TOKEN_BYTES = 32


def create_session(db: Database, username: str) -> str:
    """为用户名创建会话，返回随机令牌。"""
    token = secrets.token_urlsafe(_TOKEN_BYTES)
    now = datetime.now(timezone.utc)
    db["sessions"].insert_one(
        {
            "token": token,
            "username": username,
            "created_at": now,
            "expires_at": now + SESSION_TTL,
        }
    )
    return token


def username_for_token(db: Database, token: Optional[str]) -> Optional[str]:
    """按令牌取用户名；令牌无效或已过期返回 None。"""
    if not token:
        return None
    session = db["sessions"].find_one({"token": token})
    if session is None:
        return None
    if session["expires_at"] <= datetime.now(timezone.utc):
        return None
    return session["username"]


def delete_session(db: Database, token: Optional[str]) -> None:
    if token:
        db["sessions"].delete_one({"token": token})
