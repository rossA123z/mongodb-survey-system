"""运行配置：全部通过环境变量提供，便于应用与测试使用不同的数据库。"""

import os

DEFAULT_MONGO_URI = "mongodb://127.0.0.1:27017"
DEFAULT_DB_NAME = "survey_db"


def mongo_uri() -> str:
    return os.environ.get("MONGO_URI", DEFAULT_MONGO_URI)


def db_name() -> str:
    return os.environ.get("SURVEY_DB_NAME", DEFAULT_DB_NAME)
