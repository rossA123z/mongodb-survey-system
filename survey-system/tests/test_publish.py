"""工单 02：发布校验、可分发链接与发布后冻结。"""

from conftest import register_login as _login


def _draft(client, title="课程反馈"):
    return client.post(
        "/api/surveys", json={"title": title, "description": ""}
    ).json()


def _add_question(client, survey_id, title, options, required=False):
    return client.post(
        f"/api/surveys/{survey_id}/questions",
        json={
            "title": title,
            "required": required,
            "options": [{"text": text} for text in options],
        },
    )


def test_publish_requires_at_least_one_question(client):
    """没有题目的问卷不能发布。"""
    _login(client)
    survey = _draft(client)

    response = client.post(f"/api/surveys/{survey['id']}/publish")

    assert response.status_code == 400


def test_publish_rejects_question_with_too_few_options(client):
    """只有单个选项的单选题不能让问卷通过发布校验。"""
    _login(client)
    survey = _draft(client)
    _add_question(client, survey["id"], "单选题", ["唯一选项"])

    response = client.post(f"/api/surveys/{survey['id']}/publish")

    assert response.status_code == 400


def test_publish_returns_share_link(client):
    """发布成功后问卷状态为已发布，并给出可分发链接。"""
    _login(client)
    survey = _draft(client)
    _add_question(client, survey["id"], "你最喜欢哪门课？", ["数据库", "操作系统"], True)

    response = client.post(f"/api/surveys/{survey['id']}/publish")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "published"
    assert body["share_url"] == f"/s/{survey['id']}"


def test_publish_twice_is_rejected(client):
    """已发布的问卷再次发布被拒绝。"""
    _login(client)
    survey = _draft(client)
    _add_question(client, survey["id"], "单选题", ["甲", "乙"])
    client.post(f"/api/surveys/{survey['id']}/publish")

    response = client.post(f"/api/surveys/{survey['id']}/publish")

    assert response.status_code == 409


def test_questions_frozen_after_publish(client):
    """发布后不能添加、修改、删除或重排题目。"""
    _login(client)
    survey = _draft(client)
    question = _add_question(client, survey["id"], "单选题", ["甲", "乙"]).json()
    client.post(f"/api/surveys/{survey['id']}/publish")

    base = f"/api/surveys/{survey['id']}/questions"
    assert _add_question(client, survey["id"], "新题", ["丙", "丁"]).status_code == 409
    assert (
        client.patch(f"{base}/{question['qid']}", json={"title": "改"}).status_code
        == 409
    )
    assert client.delete(f"{base}/{question['qid']}").status_code == 409
    assert (
        client.post(
            f"{base}/{question['qid']}/move", json={"direction": "up"}
        ).status_code
        == 409
    )


def test_metadata_frozen_after_publish(client):
    """发布后标题、说明与匿名/重复配置都冻结。"""
    _login(client)
    survey = _draft(client)
    _add_question(client, survey["id"], "单选题", ["甲", "乙"])
    client.post(f"/api/surveys/{survey['id']}/publish")

    response = client.patch(
        f"/api/surveys/{survey['id']}",
        json={"title": "改名", "config": {"anonymous": True}},
    )

    assert response.status_code == 409
    detail = client.get(f"/api/surveys/{survey['id']}").json()
    assert detail["title"] == "课程反馈"
    assert detail["config"]["anonymous"] is False


def test_config_settable_before_publish(client):
    """发布前可设置匿名与允许重复填写。"""
    _login(client)
    survey = _draft(client)

    response = client.patch(
        f"/api/surveys/{survey['id']}",
        json={"config": {"anonymous": True, "allow_multiple": True}},
    )

    assert response.status_code == 200
    assert response.json()["config"] == {"anonymous": True, "allow_multiple": True}


def test_publish_can_set_config_in_one_call(client):
    """发布确认页可同时提交匿名与重复策略。"""
    _login(client)
    survey = _draft(client)
    _add_question(client, survey["id"], "单选题", ["甲", "乙"])

    response = client.post(
        f"/api/surveys/{survey['id']}/publish",
        json={"anonymous": True, "allow_multiple": False},
    )

    assert response.status_code == 200
    assert response.json()["config"] == {"anonymous": True, "allow_multiple": False}


def test_other_account_cannot_publish(client):
    """另一账号不能发布别人的问卷，统一 404。"""
    _login(client)
    survey = _draft(client)
    _add_question(client, survey["id"], "单选题", ["甲", "乙"])
    client.post("/api/logout")

    _login(client, "li")

    assert client.post(f"/api/surveys/{survey['id']}/publish").status_code == 404
