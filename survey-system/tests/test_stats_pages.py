"""工单 06：统计页面（问卷级汇总页与题目级视图）。

接缝仍是 HTTP 层：断言页面状态码、跳转与关键中文内容，不断言整段 HTML。
"""

from conftest import register_login as _register_login


def _draft(client, title="课程反馈"):
    return client.post(
        "/api/surveys", json={"title": title, "description": ""}
    ).json()


def _add(client, survey_id, payload):
    return client.post(f"/api/surveys/{survey_id}/questions", json=payload).json()


def _publish(client, survey_id, **config):
    return client.post(f"/api/surveys/{survey_id}/publish", json=config)


def _respond(client, survey_id, username, answers):
    client.post("/api/logout")
    _register_login(client, username, f"{username}-pass")
    client.post(f"/api/surveys/{survey_id}/responses", json={"answers": answers})


def _as_creator(client):
    client.post("/api/logout")
    _register_login(client)


SINGLE = {
    "title": "你最喜欢哪门课？",
    "type": "single",
    "options": [{"text": "数据库"}, {"text": "操作系统"}],
}


def test_stats_page_requires_login(client):
    """未登录访问统计页 → 跳转登录并记住地址。"""
    response = client.get("/surveys/abc/stats", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login?next=/surveys/abc/stats"


def test_stats_page_is_private(client):
    """别人的问卷统计页返回 404。"""
    _register_login(client)
    survey = _draft(client)
    _add(client, survey["id"], SINGLE)
    _publish(client, survey["id"])
    client.post("/api/logout")
    _register_login(client, "li", "li-pass-1")

    assert client.get(f"/surveys/{survey['id']}/stats").status_code == 404


def test_survey_stats_page_shows_counts(client):
    """问卷级统计页展示各选项人数与有效回答总数。"""
    _register_login(client)
    survey = _draft(client)
    question = _add(client, survey["id"], SINGLE)
    _publish(client, survey["id"])
    jia = question["options"][0]["oid"]
    _respond(client, survey["id"], "li", [{"qid": question["qid"], "oid": jia}])
    _as_creator(client)

    body = client.get(f"/surveys/{survey['id']}/stats").text

    assert "课程反馈" in body
    assert "数据库" in body and "操作系统" in body
    assert "有效回答总数" in body
    assert f"/surveys/{survey['id']}/stats/{question['qid']}" in body


def test_stats_page_for_survey_without_responses(client):
    """无答卷时统计页正常显示全零，而不是报错。"""
    _register_login(client)
    survey = _draft(client)
    _add(client, survey["id"], SINGLE)
    _publish(client, survey["id"])

    response = client.get(f"/surveys/{survey['id']}/stats")

    assert response.status_code == 200
    assert "0" in response.text


def test_question_stats_page_shows_selected_question(client):
    """题目级统计页只展示所选题目。"""
    _register_login(client)
    survey = _draft(client)
    q1 = _add(client, survey["id"], SINGLE)
    q2 = _add(
        client,
        survey["id"],
        {"title": "每周学习多久？", "type": "number"},
    )
    _publish(client, survey["id"], allow_multiple=True)
    _respond(client, survey["id"], "li", [{"qid": q2["qid"], "number": 12}])
    _as_creator(client)

    body = client.get(f"/surveys/{survey['id']}/stats/{q2['qid']}").text

    assert "每周学习多久？" in body
    assert "12" in body
    assert "平均值" in body


def test_edit_page_links_to_stats(client):
    """已发布问卷的管理页提供进入统计的入口。"""
    _register_login(client)
    survey = _draft(client)
    _add(client, survey["id"], SINGLE)
    _publish(client, survey["id"])

    body = client.get(f"/surveys/{survey['id']}/edit").text

    assert f"/surveys/{survey['id']}/stats" in body


def test_question_stats_page_for_unknown_question_is_404(client):
    """题目级统计页对不存在的题目返回 404。"""
    _register_login(client)
    survey = _draft(client)
    _add(client, survey["id"], SINGLE)
    _publish(client, survey["id"])

    assert client.get(f"/surveys/{survey['id']}/stats/q_not_exist").status_code == 404
