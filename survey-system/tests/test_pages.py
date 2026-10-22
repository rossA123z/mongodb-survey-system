"""工单 01 补全：中文网页、CSRF 防护与登录后回到原页面。

接缝仍是 HTTP 层：断言页面的行为特征（状态码、跳转目标、关键表单元素），
不断言整段 HTML 完全相等。
"""

from conftest import (
    CSRF_COOKIE,
    csrf_token as _csrf,
    login as _login_api,
    register as _register,
)


def test_login_page_shows_chinese_form(client):
    """登录页返回中文表单，含标签、密码字段与 CSRF 隐藏字段。"""
    response = client.get("/login")

    assert response.status_code == 200
    body = response.text
    assert "<form" in body
    assert "<label" in body
    assert "用户名" in body and "密码" in body
    assert 'name="csrf_token"' in body


def test_register_page_shows_chinese_form(client):
    """注册页返回中文表单并携带 CSRF 令牌。"""
    response = client.get("/register")

    assert response.status_code == 200
    assert "注册" in response.text
    assert 'name="csrf_token"' in response.text


def test_protected_page_redirects_anonymous_user_to_login(client):
    """未登录访问我的问卷列表 → 跳转登录页，并记住原地址。"""
    response = client.get("/surveys", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login?next=/surveys"


def test_login_form_returns_to_requested_page(client):
    """登录成功后回到登录前想去的地址。"""
    _register(client)
    token = _csrf(client, "/login?next=/surveys/new")

    response = client.post(
        "/login",
        data={
            "username": "zhang",
            "password": "secret123",
            "csrf_token": token,
            "next": "/surveys/new",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/surveys/new"


def test_login_form_rejects_external_next_target(client):
    """next 指向站外地址时忽略，回到默认页，避免开放重定向。"""
    _register(client)
    token = _csrf(client, "/login")

    response = client.post(
        "/login",
        data={
            "username": "zhang",
            "password": "secret123",
            "csrf_token": token,
            "next": "https://evil.example/",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/surveys"


def test_form_login_grants_access_to_surveys_page(client):
    """通过网页表单登录后即可访问我的问卷列表。"""
    _register(client)
    token = _csrf(client, "/login")

    client.post(
        "/login",
        data={"username": "zhang", "password": "secret123", "csrf_token": token},
    )

    response = client.get("/surveys")
    assert response.status_code == 200
    assert "我的问卷" in response.text


def test_surveys_page_lists_only_own_surveys(client):
    """列表页展示当前账号自己的问卷，看不到别人的。"""
    _register(client)
    _login_api(client)
    client.post("/api/surveys", json={"title": "课程反馈", "description": "了解学习体验"})

    body = client.get("/surveys").text
    assert "课程反馈" in body
    assert "了解学习体验" in body

    client.post("/api/logout")
    _register(client, "li")
    _login_api(client, "li")

    assert "课程反馈" not in client.get("/surveys").text


def test_survey_content_is_rendered_as_text(client):
    """用户提交的标题按文本显示，不执行 HTML（防 XSS）。"""
    _register(client)
    _login_api(client)
    client.post(
        "/api/surveys",
        json={"title": "<script>alert(1)</script>", "description": "<b>加粗</b>"},
    )

    body = client.get("/surveys").text
    assert "<script>alert(1)</script>" not in body
    assert "&lt;script&gt;" in body


def test_surveys_page_links_to_edit_page(client):
    """列表页为每份问卷提供进入管理 / 编辑页的链接，使完整流程可点击复现。"""
    _register(client)
    _login_api(client)
    survey = client.post(
        "/api/surveys", json={"title": "可点击问卷", "description": ""}
    ).json()

    body = client.get("/surveys").text
    assert f'/surveys/{survey["id"]}/edit' in body


def test_surveys_page_shows_chinese_status(client):
    """列表页问卷状态用中文展示，不显示英文状态值。"""
    _register(client)
    _login_api(client)
    client.post("/api/surveys", json={"title": "状态问卷", "description": ""})

    body = client.get("/surveys").text
    assert "状态：草稿" in body
    assert "状态：draft" not in body


def test_surveys_page_has_logout_entry(client):
    """列表页提供退出入口（表单 POST 到 /logout）。"""
    _register(client)
    _login_api(client)

    body = client.get("/surveys").text
    assert 'action="/logout"' in body
    assert "退出" in body


def test_new_survey_form_creates_draft(client):
    """新建页填写标题与说明并提交后，草稿出现在列表里。"""
    _register(client)
    _login_api(client)
    page = client.get("/surveys/new")
    assert page.status_code == 200
    assert "标题" in page.text and "说明" in page.text
    token = client.cookies.get(CSRF_COOKIE)

    response = client.post(
        "/surveys",
        data={"title": "校园问卷", "description": "简短说明", "csrf_token": token},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/surveys"
    assert "校园问卷" in client.get("/surveys").text


def test_new_survey_form_requires_title(client):
    """标题为空时提示定位到标题字段。"""
    _register(client)
    _login_api(client)
    client.get("/surveys/new")
    token = client.cookies.get(CSRF_COOKIE)

    response = client.post(
        "/surveys", data={"title": "   ", "description": "", "csrf_token": token}
    )

    assert response.status_code == 400
    assert "请填写问卷标题" in response.text


def test_write_without_csrf_token_is_rejected(client):
    """缺少 CSRF 令牌的表单写操作被拒绝。"""
    _register(client)
    _login_api(client)
    client.get("/surveys/new")

    response = client.post("/surveys", data={"title": "无令牌", "description": ""})

    assert response.status_code == 403


def test_write_with_wrong_csrf_token_is_rejected(client):
    """CSRF 令牌与 Cookie 不一致时被拒绝。"""
    _register(client)
    _login_api(client)
    client.get("/surveys/new")

    response = client.post(
        "/surveys",
        data={"title": "错误令牌", "description": "", "csrf_token": "forged-token"},
    )

    assert response.status_code == 403


def test_register_form_rejects_duplicate_username(client):
    """注册页重复用户名给出中文提示，不泄露内部细节。"""
    _register(client, "zhang")
    token = _csrf(client, "/register")

    response = client.post(
        "/register",
        data={"username": "zhang", "password": "other456", "csrf_token": token},
    )

    assert response.status_code == 400
    assert "用户名已被占用" in response.text


def test_logout_entry_ends_session(client):
    """退出入口使会话失效，之后访问受保护页面需重新登录。"""
    _register(client)
    token = _csrf(client, "/login")
    client.post(
        "/login",
        data={"username": "zhang", "password": "secret123", "csrf_token": token},
    )
    assert client.get("/surveys").status_code == 200

    token = client.cookies.get(CSRF_COOKIE)
    response = client.post("/logout", data={"csrf_token": token}, follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"
    assert client.get("/surveys", follow_redirects=False).status_code == 303


def test_api_write_rejects_cross_site_form_encodings(client):
    """跨站 HTML 表单构造不出 application/json，/api/* 写接口对表单编码一律拒绝。

    只接受 JSON 请求体即服务端层面的 CSRF 防护（表单只能发
    urlencoded / multipart / text/plain），因此无需再为 JSON 接口加令牌。
    依据见 docs/adr/0003-api-csrf-via-content-type.md。
    """
    _register(client)
    _login_api(client)

    # 表单编码（跨站表单唯一能发出的编码）被拒。
    assert client.post("/api/surveys", data={"title": "校园问卷"}).status_code == 422
    # text/plain 即使正文写成 JSON 也被拒。
    plain = client.post(
        "/api/surveys",
        content='{"title": "校园问卷"}'.encode("utf-8"),
        headers={"content-type": "text/plain"},
    )
    assert plain.status_code == 422
    # 注册接口同样只接受 JSON。
    assert client.post("/api/register", data={"username": "x", "password": "y"}).status_code == 422
    # 确认以上尝试都没有产生副作用。
    assert client.get("/api/surveys").json() == []

    # 对照：同源页面的 JSON 请求（浏览器会先发 CORS 预检）可以正常创建。
    assert client.post("/api/surveys", json={"title": "校园问卷"}).status_code == 201
