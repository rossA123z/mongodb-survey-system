"""网页验收缺陷回归：注册后返回原问卷，以及冻结问卷的只读题干。"""

import re
from html import unescape
from urllib.parse import quote, unquote

import pytest

from conftest import CSRF_COOKIE, register, register_login


def _next_value(body):
    match = re.search(r'name="next" value="([^"]*)"', body)
    assert match, "表单必须保留返回目标"
    return unescape(match.group(1))


@pytest.mark.parametrize("target", ["/s/example", "/surveys/new?tag=a&other=b"])
def test_signup_flow_returns_to_original_page(client, target):
    login_page = client.get("/login", params={"next": target})
    link = re.search(r'href="([^"]+)">注册新账号', login_page.text)
    assert link
    signup = client.get(unescape(link.group(1)))
    assert _next_value(signup.text) == target

    registered = client.post(
        "/register",
        data={
            "username": "new-filler",
            "password": "secret123",
            "csrf_token": client.cookies.get(CSRF_COOKIE),
            "next": _next_value(signup.text),
        },
        follow_redirects=False,
    )
    assert registered.status_code == 303
    login_page = client.get(registered.headers["location"])
    assert _next_value(login_page.text) == target
    response = client.post(
        "/login",
        data={
            "username": "new-filler",
            "password": "secret123",
            "csrf_token": client.cookies.get(CSRF_COOKIE),
            "next": _next_value(login_page.text),
        },
        follow_redirects=False,
    )
    assert unquote(response.headers["location"]) == target


@pytest.mark.parametrize("username", ["", "existing"])
def test_registration_errors_keep_return_target(client, username):
    register(client, "existing")
    client.get("/register", params={"next": "/s/example"})
    response = client.post(
        "/register",
        data={
            "username": username,
            "password": "secret123",
            "csrf_token": client.cookies.get(CSRF_COOKIE),
            "next": "/s/example",
        },
    )
    assert response.status_code == 400
    assert _next_value(response.text) == "/s/example"
    assert 'href="/login?next=/s/example"' in response.text


@pytest.mark.parametrize("target", ["https://evil.example", "//evil.example", "/\\evil.example"])
def test_registration_rejects_external_return_targets(client, target):
    page = client.get("/register", params={"next": target})
    assert _next_value(page.text) == "/surveys"
    response = client.post(
        "/register",
        data={
            "username": "new-filler",
            "password": "secret123",
            "csrf_token": client.cookies.get(CSRF_COOKIE),
            "next": target,
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert unquote(response.headers["location"]) == "/login?next=/surveys"


def test_registration_without_return_target_keeps_default_redirect(client):
    client.get("/register")
    response = client.post(
        "/register",
        data={"username": "new-filler", "password": "secret123",
              "csrf_token": client.cookies.get(CSRF_COOKIE)},
        follow_redirects=False,
    )
    assert response.headers["location"] == "/login"


@pytest.mark.parametrize("closed", [False, True])
def test_frozen_management_page_shows_escaped_question_title(client, closed):
    register_login(client)
    survey_id = client.post("/api/surveys", json={"title": "验收问卷"}).json()["id"]
    added = client.post(
        f"/api/surveys/{survey_id}/questions",
        json={"title": "<b>你喜欢哪门课？</b>", "required": True,
              "options": [{"text": "数据库"}, {"text": "操作系统"}]},
    )
    assert added.status_code == 201
    assert client.post(f"/api/surveys/{survey_id}/publish").status_code == 200
    if closed:
        assert client.post(f"/api/surveys/{survey_id}/close").status_code == 200
    response = client.get(f"/surveys/{survey_id}/edit")
    assert response.status_code == 200
    assert "&lt;b&gt;你喜欢哪门课？&lt;/b&gt;" in response.text
    assert "<b>你喜欢哪门课？</b>" not in response.text
    assert 'name="title"' not in response.text
