"""工单 01：问卷草稿的私有管理。"""

from fastapi.testclient import TestClient

from app.main import create_app

from conftest import register_login as _register_and_login


def test_logged_in_user_can_create_draft(client):
    """登录后创建问卷草稿，返回草稿状态与内容。"""
    _register_and_login(client, "zhang")

    response = client.post(
        "/api/surveys",
        json={"title": "课程反馈", "description": "了解学习体验"},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["title"] == "课程反馈"
    assert body["description"] == "了解学习体验"
    assert body["status"] == "draft"
    assert body["id"]


def test_create_draft_requires_login(client):
    """未登录不能创建问卷。"""
    response = client.post(
        "/api/surveys", json={"title": "匿名尝试", "description": ""}
    )

    assert response.status_code == 401


def test_created_draft_appears_in_own_list(client):
    """创建后，该草稿出现在创建者自己的问卷列表里。"""
    _register_and_login(client, "zhang")
    assert client.get("/api/surveys").json() == []

    created = client.post(
        "/api/surveys",
        json={"title": "课程反馈", "description": "了解学习体验"},
    ).json()

    listing = client.get("/api/surveys")
    assert listing.status_code == 200
    assert [survey["id"] for survey in listing.json()] == [created["id"]]
    assert listing.json()[0]["title"] == "课程反馈"


def test_owner_can_read_own_draft_detail(client):
    """创建者能读取自己问卷的管理详情。"""
    _register_and_login(client, "zhang")
    created = client.post(
        "/api/surveys",
        json={"title": "课程反馈", "description": "了解学习体验"},
    ).json()

    detail = client.get(f"/api/surveys/{created['id']}")

    assert detail.status_code == 200
    assert detail.json()["title"] == "课程反馈"
    assert detail.json()["description"] == "了解学习体验"


def test_other_account_cannot_read_survey(client):
    """另一账号既看不到，也读不到别人的问卷管理数据。"""
    _register_and_login(client, "zhang")
    created = client.post(
        "/api/surveys", json={"title": "课程反馈", "description": ""}
    ).json()
    client.post("/api/logout")

    _register_and_login(client, "li")

    assert client.get("/api/surveys").json() == []
    assert client.get(f"/api/surveys/{created['id']}").status_code == 404


def test_owner_can_update_draft(client):
    """创建者可修改自己草稿的标题与说明，未提供字段保持不变。"""
    _register_and_login(client, "zhang")
    created = client.post(
        "/api/surveys",
        json={"title": "课程反馈", "description": "了解学习体验"},
    ).json()

    response = client.patch(
        f"/api/surveys/{created['id']}", json={"title": "课程反馈 v2"}
    )

    assert response.status_code == 200
    assert response.json()["title"] == "课程反馈 v2"
    assert response.json()["description"] == "了解学习体验"
    reread = client.get(f"/api/surveys/{created['id']}").json()
    assert reread["title"] == "课程反馈 v2"


def test_other_account_cannot_modify_survey(client):
    """另一账号无法修改别人的问卷，且原问卷保持不变。"""
    _register_and_login(client, "zhang")
    created = client.post(
        "/api/surveys",
        json={"title": "课程反馈", "description": "了解学习体验"},
    ).json()
    client.post("/api/logout")

    _register_and_login(client, "li")
    response = client.patch(
        f"/api/surveys/{created['id']}", json={"title": "被篡改"}
    )
    assert response.status_code == 404

    client.post("/api/logout")
    _register_and_login(client, "zhang")
    assert client.get(f"/api/surveys/{created['id']}").json()["title"] == "课程反馈"


def test_draft_survives_app_restart(client):
    """草稿存于 MongoDB：换一个应用实例（模拟重启）后仍在。"""
    _register_and_login(client, "zhang")
    created = client.post(
        "/api/surveys",
        json={"title": "课程反馈", "description": "了解学习体验"},
    ).json()

    with TestClient(create_app()) as restarted:
        restarted.post(
            "/api/login", json={"username": "zhang", "password": "secret123"}
        )
        listing = restarted.get("/api/surveys")
        assert [survey["title"] for survey in listing.json()] == ["课程反馈"]
        assert restarted.get(f"/api/surveys/{created['id']}").status_code == 200
