"""MongoDB 连接：业务数据全部经由这里读写。"""

from functools import lru_cache

from pymongo import MongoClient
from pymongo.database import Database

from .config import db_name, mongo_uri


@lru_cache(maxsize=1)
def _client() -> MongoClient:
    # tz_aware：取回的时间统一为 UTC 感知型，便于与 datetime.now(timezone.utc) 比较。
    return MongoClient(mongo_uri(), tz_aware=True)


def get_db() -> Database:
    return _client()[db_name()]


def ensure_indexes(db: Database) -> None:
    """建立唯一约束，用数据库而非先查后写保证唯一性。"""
    db["users"].create_index("username", unique=True)
    db["sessions"].create_index("token", unique=True)
    # 禁止重复的答卷带 once=true，落入该唯一部分索引；允许重复的答卷不带此标记，
    # 不进索引，因此可多次提交。唯一性由数据库约束保证，先查后写挡不住并发。
    db["responses"].create_index(
        [("survey_id", 1), ("respondent", 1)],
        unique=True,
        partialFilterExpression={"once": True},
        name="unique_once_per_respondent",
    )
