"""工单 02：四个网页 —— 草稿编辑页、发布确认页、填写页、提交确认页。

接缝仍是 HTTP 层：断言页面行为特征（状态码、跳转、关键控件与中文提示），
不断言整段 HTML 完全相等。
"""

from conftest import CSRF_COOKIE, register_login as _register_login


def _draft(client, title="课程反馈"):
    return client.post(
        "/api/surveys", json={"title": title, "description": "了解学习体验"}
    ).json()


def _question(client, survey_id):
    return client.post(
        f"/api/surveys/{survey_id}/questions",
        json={
            "title": "你最喜欢哪门课？",
            "required": True,
            "options": [{"text": "数据库"}, {"text": "操作系统"}],
        },
    ).json()


def _publish(client, survey_id):
    client.post(f"/api/surveys/{survey_id}/publish")


def test_editor_page_requires_login(client):
    """未登录访问草稿编辑页 → 跳转登录并记住地址。"""
    response = client.get("/surveys/abc/edit", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login?next=/surveys/abc/edit"


def test_editor_page_is_private(client):
    """别人的问卷编辑页返回 404。"""
    _register_login(client)
    survey = _draft(client)
    client.post("/api/logout")
    _register_login(client, "li", "li-pass-1")

    assert client.get(f"/surveys/{survey['id']}/edit").status_code == 404


def test_editor_page_lists_questions_and_add_form(client):
    """编辑页展示已有题目，并提供添加题的带标签表单。"""
    _register_login(client)
    survey = _draft(client)
    _question(client, survey["id"])

    body = client.get(f"/surveys/{survey['id']}/edit").text

    assert "你最喜欢哪门课？" in body
    assert "数据库" in body and "操作系统" in body
    assert 'name="csrf_token"' in body
    assert "添加题目" in body
    assert "<label" in body


def test_editor_add_question_form_creates_question(client):
    """通过编辑页表单添加单选题后，题目出现在页面上。"""
    _register_login(client)
    survey = _draft(client)
    client.get(f"/surveys/{survey['id']}/edit")
    token = client.cookies.get(CSRF_COOKIE)

    response = client.post(
        f"/surveys/{survey['id']}/questions",
        data={
            "csrf_token": token,
            "title": "你最喜欢的课程",
            "required": "on",
            "option": ["数据库", "操作系统"],
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == f"/surveys/{survey['id']}/edit"
    assert "你最喜欢的课程" in client.get(f"/surveys/{survey['id']}/edit").text


def test_editor_move_and_delete_buttons(client):
    """编辑页的排序与删除按钮调整题目。"""
    _register_login(client)
    survey = _draft(client)
    first = _question(client, survey["id"])
    second = client.post(
        f"/api/surveys/{survey['id']}/questions",
        json={
            "title": "第二题",
            "required": False,
            "options": [{"text": "甲"}, {"text": "乙"}],
        },
    ).json()
    client.get(f"/surveys/{survey['id']}/edit")
    token = client.cookies.get(CSRF_COOKIE)

    client.post(
        f"/surveys/{survey['id']}/questions/{second['qid']}",
        data={"csrf_token": token, "action": "up"},
    )
    order = [q["qid"] for q in client.get(f"/api/surveys/{survey['id']}").json()["questions"]]
    assert order == [second["qid"], first["qid"]]

    client.post(
        f"/surveys/{survey['id']}/questions/{second['qid']}",
        data={"csrf_token": token, "action": "delete"},
    )
    remaining = client.get(f"/api/surveys/{survey['id']}").json()["questions"]
    assert [q["qid"] for q in remaining] == [first["qid"]]


def test_publish_page_publishes_and_shows_link(client):
    """发布确认页可设置匿名/重复并发布，发布后展示可分发链接。"""
    _register_login(client)
    survey = _draft(client)
    _question(client, survey["id"])
    page = client.get(f"/surveys/{survey['id']}/publish")

    assert page.status_code == 200
    assert "匿名" in page.text and "重复" in page.text
    token = client.cookies.get(CSRF_COOKIE)

    response = client.post(
        f"/surveys/{survey['id']}/publish",
        data={"csrf_token": token, "anonymous": "on"},
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert f"/s/{survey['id']}" in response.text
    assert client.get(f"/api/surveys/{survey['id']}").json()["status"] == "published"


def test_published_survey_editor_is_read_only(client):
    """发布后编辑页不再提供添加/修改题目控件，只展示链接。"""
    _register_login(client)
    survey = _draft(client)
    _question(client, survey["id"])
    _publish(client, survey["id"])

    body = client.get(f"/surveys/{survey['id']}/edit").text

    assert f"/s/{survey['id']}" in body
    assert "添加题目" not in body


def test_fill_link_requires_login_and_returns(client):
    """未登录打开填写链接 → 登录页记住该问卷；登录后回到问卷。"""
    _register_login(client)
    survey = _draft(client)
    _question(client, survey["id"])
    _publish(client, survey["id"])
    client.post("/api/logout")

    response = client.get(f"/s/{survey['id']}", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == f"/login?next=/s/{survey['id']}"


def test_unpublished_survey_fill_page_says_not_published(client):
    """未发布的问卷打开链接提示未发布。"""
    _register_login(client)
    survey = _draft(client)
    client.post("/api/logout")
    _register_login(client, "li", "li-pass-1")

    response = client.get(f"/s/{survey['id']}")

    assert response.status_code == 404
    assert "未发布" in response.text


def test_login_form_returns_to_survey(client):
    """通过登录页登录后，回到原本要填写的问卷。"""
    _register_login(client)
    survey = _draft(client)
    _question(client, survey["id"])
    _publish(client, survey["id"])
    client.post("/api/logout")
    client.post("/api/register", json={"username": "li", "password": "li-pass-1"})

    client.get(f"/s/{survey['id']}")  # 触发跳转登录并签发 CSRF Cookie
    token = client.cookies.get(CSRF_COOKIE)

    response = client.post(
        "/login",
        data={
            "username": "li",
            "password": "li-pass-1",
            "csrf_token": token,
            "next": f"/s/{survey['id']}",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == f"/s/{survey['id']}"
    assert "你最喜欢哪门课？" in client.get(f"/s/{survey['id']}").text


def test_fill_page_shows_questions(client):
    """已登录打开已发布问卷，逐题显示单选控件。"""
    _register_login(client)
    survey = _draft(client)
    _question(client, survey["id"])
    _publish(client, survey["id"])
    client.post("/api/logout")
    _register_login(client, "li", "li-pass-1")

    body = client.get(f"/s/{survey['id']}").text

    assert "你最喜欢哪门课？" in body
    assert "数据库" in body
    assert 'type="radio"' in body
    assert 'name="csrf_token"' in body


def test_fill_page_invalid_answer_shows_question_hint(client):
    """填写页提交无效答案时，页面把提示定位到题目。"""
    _register_login(client)
    survey = _draft(client)
    _question(client, survey["id"])
    _publish(client, survey["id"])
    client.post("/api/logout")
    _register_login(client, "li", "li-pass-1")
    client.get(f"/s/{survey['id']}")
    token = client.cookies.get(CSRF_COOKIE)

    response = client.post(
        f"/s/{survey['id']}", data={"csrf_token": token}
    )

    assert response.status_code == 400
    assert "必答" in response.text
    assert "你最喜欢哪门课？" in response.text


def test_fill_confirm_then_submit(client):
    """填写后进入提交确认页，确认后提交成功。"""
    _register_login(client)
    survey = _draft(client)
    question = _question(client, survey["id"])
    _publish(client, survey["id"])
    client.post("/api/logout")
    _register_login(client, "li", "li-pass-1")
    client.get(f"/s/{survey['id']}")
    token = client.cookies.get(CSRF_COOKIE)
    answers = {
        "csrf_token": token,
        f"answer_{question['qid']}": question["options"][0]["oid"],
    }

    confirm = client.post(f"/s/{survey['id']}", data=answers)

    assert confirm.status_code == 200
    assert "确认提交" in confirm.text
    assert "数据库" in confirm.text

    submitted = client.post(f"/s/{survey['id']}/submit", data=answers)

    assert submitted.status_code == 200
    assert "提交成功" in submitted.text

    client.post("/api/logout")
    _register_login(client)
    assert len(client.get(f"/api/surveys/{survey['id']}/responses").json()) == 1


def test_fill_duplicate_submit_shows_message(client):
    """同一账号重复提交时，确认页提交后给出明确提示。"""
    _register_login(client)
    survey = _draft(client)
    question = _question(client, survey["id"])
    _publish(client, survey["id"])
    client.post("/api/logout")
    _register_login(client, "li", "li-pass-1")
    client.get(f"/s/{survey['id']}")
    token = client.cookies.get(CSRF_COOKIE)
    answers = {
        "csrf_token": token,
        f"answer_{question['qid']}": question["options"][0]["oid"],
    }
    client.post(f"/s/{survey['id']}/submit", data=answers)

    duplicate = client.post(f"/s/{survey['id']}/submit", data=answers)

    assert duplicate.status_code == 409
    assert "已经提交" in duplicate.text
