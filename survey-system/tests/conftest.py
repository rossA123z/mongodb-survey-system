"""pytest 共享装置与测试辅助函数。

每个用例开始前清空**独立的测试数据库**，不触碰业务数据库。
测试只通过 HTTP 接口观察行为，不直接查询数据库断言。

文末的 register / login / csrf_token 是各测试文件共用的辅助函数（原先在约 16 个
文件里各写一遍）；各文件 ``from conftest import ...`` 后按本地别名使用。
"""

import os

import pytest
from fastapi.testclient import TestClient
from pymongo import MongoClient

# 默认 survey_test；并行 worktree 可用 SURVEY_TEST_DB_NAME 指向各自的库，避免互相清空。
TEST_DB_NAME = os.environ.get("SURVEY_TEST_DB_NAME", "survey_test")

# 双提交 Cookie 的令牌字段名，与 app/csrf.py 的 CSRF_COOKIE 一致。
CSRF_COOKIE = "csrf_token"


@pytest.fixture(scope="session", autouse=True)
def _point_app_at_test_database():
    """整个测试会话都指向测试库，避免误连业务库。"""
    os.environ["SURVEY_DB_NAME"] = TEST_DB_NAME
    yield


@pytest.fixture()
def mongo():
    client = MongoClient(os.environ.get("MONGO_URI", "mongodb://127.0.0.1:27017"))
    yield client
    client.close()


@pytest.fixture()
def client(mongo):
    """每个用例拿到干净测试库 + 一个应用客户端。"""
    mongo.drop_database(TEST_DB_NAME)

    from app.main import create_app

    with TestClient(create_app()) as test_client:
        yield test_client


def register(client, username="zhang", password="secret123"):
    """经接口注册一个账号，返回响应。"""
    return client.post(
        "/api/register", json={"username": username, "password": password}
    )


def login(client, username="zhang", password="secret123"):
    """经接口登录，返回响应。"""
    return client.post("/api/login", json={"username": username, "password": password})


def register_login(client, username="zhang", password="secret123"):
    """注册并登录同一账号（多数用例的起点）。"""
    register(client, username, password)
    login(client, username, password)


def csrf_token(client, path="/login"):
    """访问一个渲染表单的页面，取回服务端签发的 CSRF 令牌（双提交 Cookie）。"""
    client.get(path)
    return client.cookies.get(CSRF_COOKIE)
