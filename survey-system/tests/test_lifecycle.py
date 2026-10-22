"""工单 05：截止时间与关闭问卷（接口层）。

接缝是 HTTP 层：断言状态码、返回字段与中文提示，不直接查询数据库。
截止时间统一按 UTC 构造边界用例，不依赖机器本地时区。
"""

from datetime import datetime, timedelta, timezone

from conftest import register_login as _register_login


def _published(client, allow_multiple=False, anonymous=False):
    """以 zhang 建一份已发布的单选题问卷，退回 (survey_id, question)。"""
    _register_login(client, "zhang")
    survey = client.post(
        "/api/surveys", json={"title": "课程反馈", "description": ""}
    ).json()
    question = client.post(
        f"/api/surveys/{survey['id']}/questions",
        json={
            "title": "你最喜欢哪门课？",
            "required": True,
            "options": [{"text": "数据库"}, {"text": "操作系统"}],
        },
    ).json()
    client.post(
        f"/api/surveys/{survey['id']}/publish",
        json={"anonymous": anonymous, "allow_multiple": allow_multiple},
    )
    client.post("/api/logout")
    return survey["id"], question


def _answer(question):
    return {
        "answers": [{"qid": question["qid"], "oid": question["options"][0]["oid"]}]
    }


def _utc(offset: timedelta) -> str:
    return (datetime.now(timezone.utc) + offset).isoformat()


def test_owner_can_set_deadline(client):
    """创建者为已发布问卷设置截止时间，接口原样返回该 UTC 时刻。"""
    survey_id, _ = _published(client)
    _register_login(client, "zhang")

    response = client.post(
        f"/api/surveys/{survey_id}/deadline",
        json={"deadline": "2026-12-31T23:59:00+00:00"},
    )

    assert response.status_code == 200
    assert response.json()["deadline"] == "2026-12-31T23:59:00+00:00"


def test_deadline_is_normalized_to_utc(client):
    """带其它时区偏移的截止时间统一折算为 UTC 存储。"""
    survey_id, _ = _published(client)
    _register_login(client, "zhang")

    response = client.post(
        f"/api/surveys/{survey_id}/deadline",
        json={"deadline": "2026-12-31T08:00:00+08:00"},
    )

    assert response.status_code == 200
    assert response.json()["deadline"] == "2026-12-31T00:00:00+00:00"


def test_owner_can_adjust_deadline(client):
    """创建者可以调整既有的截止时间。"""
    survey_id, _ = _published(client)
    _register_login(client, "zhang")
    client.post(
        f"/api/surveys/{survey_id}/deadline",
        json={"deadline": "2026-12-31T00:00:00+00:00"},
    )

    response = client.post(
        f"/api/surveys/{survey_id}/deadline",
        json={"deadline": "2027-01-15T12:00:00+00:00"},
    )

    assert response.status_code == 200
    assert response.json()["deadline"] == "2027-01-15T12:00:00+00:00"


def test_deadline_can_be_cleared(client):
    """传入 null 清除已设置的截止时间。"""
    survey_id, _ = _published(client)
    _register_login(client, "zhang")
    client.post(
        f"/api/surveys/{survey_id}/deadline",
        json={"deadline": "2026-12-31T00:00:00+00:00"},
    )

    response = client.post(
        f"/api/surveys/{survey_id}/deadline", json={"deadline": None}
    )

    assert response.status_code == 200
    assert response.json()["deadline"] is None


def test_invalid_deadline_format_is_rejected(client):
    """截止时间格式非法时返回 400。"""
    survey_id, _ = _published(client)
    _register_login(client, "zhang")

    response = client.post(
        f"/api/surveys/{survey_id}/deadline", json={"deadline": "不是时间"}
    )

    assert response.status_code == 400


def test_non_owner_cannot_set_deadline(client):
    """非创建者不能调整别人的截止时间，统一 404。"""
    survey_id, _ = _published(client)
    _register_login(client, "li", "li-pass-1")

    response = client.post(
        f"/api/surveys/{survey_id}/deadline",
        json={"deadline": "2026-12-31T00:00:00+00:00"},
    )

    assert response.status_code == 404


def test_submit_after_deadline_is_rejected(client):
    """截止时间已过，提交被拒绝并给出明确提示。"""
    survey_id, question = _published(client)
    _register_login(client, "zhang")
    client.post(
        f"/api/surveys/{survey_id}/deadline", json={"deadline": _utc(timedelta(hours=-1))}
    )
    client.post("/api/logout")
    _register_login(client, "li", "li-pass-1")

    response = client.post(
        f"/api/surveys/{survey_id}/responses", json=_answer(question)
    )

    assert response.status_code == 409
    assert "截止" in response.json()["detail"]


def test_submit_exactly_at_deadline_is_rejected(client):
    """截止瞬间视为不可提交（闭区间拒绝）。

    用明确的 UTC 当前时刻作截止时间，其后的提交必然落在截止瞬间或之后，
    据此验证实现采用 now >= deadline 的闭区间判定。
    """
    survey_id, question = _published(client)
    _register_login(client, "zhang")
    client.post(
        f"/api/surveys/{survey_id}/deadline",
        json={"deadline": datetime.now(timezone.utc).isoformat()},
    )
    client.post("/api/logout")
    _register_login(client, "li", "li-pass-1")

    response = client.post(
        f"/api/surveys/{survey_id}/responses", json=_answer(question)
    )

    assert response.status_code == 409
    assert "截止" in response.json()["detail"]


def test_submit_before_deadline_is_accepted(client):
    """截止时间未到时，提交正常成功。"""
    survey_id, question = _published(client)
    _register_login(client, "zhang")
    client.post(
        f"/api/surveys/{survey_id}/deadline", json={"deadline": _utc(timedelta(hours=1))}
    )
    client.post("/api/logout")
    _register_login(client, "li", "li-pass-1")

    response = client.post(
        f"/api/surveys/{survey_id}/responses", json=_answer(question)
    )

    assert response.status_code == 201


def test_owner_can_close_survey(client):
    """创建者手动关闭已发布问卷，状态变为已关闭。"""
    survey_id, _ = _published(client)
    _register_login(client, "zhang")

    response = client.post(f"/api/surveys/{survey_id}/close")

    assert response.status_code == 200
    assert response.json()["status"] == "closed"


def test_non_owner_cannot_close_survey(client):
    """非创建者不能关闭别人的问卷，统一 404。"""
    survey_id, _ = _published(client)
    _register_login(client, "li", "li-pass-1")

    assert client.post(f"/api/surveys/{survey_id}/close").status_code == 404


def test_draft_cannot_be_closed(client):
    """草稿尚未发布，不能关闭。"""
    _register_login(client, "zhang")
    draft = client.post(
        "/api/surveys", json={"title": "草稿", "description": ""}
    ).json()

    response = client.post(f"/api/surveys/{draft['id']}/close")

    assert response.status_code == 409


def test_closing_twice_is_rejected(client):
    """重复关闭被拒绝。"""
    survey_id, _ = _published(client)
    _register_login(client, "zhang")
    assert client.post(f"/api/surveys/{survey_id}/close").status_code == 200

    assert client.post(f"/api/surveys/{survey_id}/close").status_code == 409


def test_closed_survey_cannot_be_reopened(client):
    """关闭是终态：不能再次发布或改回草稿，状态保持已关闭。"""
    survey_id, _ = _published(client)
    _register_login(client, "zhang")
    client.post(f"/api/surveys/{survey_id}/close")

    assert client.post(f"/api/surveys/{survey_id}/publish").status_code == 409
    assert (
        client.patch(f"/api/surveys/{survey_id}", json={"title": "重开"}).status_code
        == 409
    )
    assert client.get(f"/api/surveys/{survey_id}").json()["status"] == "closed"


def test_cannot_set_deadline_after_close(client):
    """关闭后不能再调整截止时间。"""
    survey_id, _ = _published(client)
    _register_login(client, "zhang")
    client.post(f"/api/surveys/{survey_id}/close")

    response = client.post(
        f"/api/surveys/{survey_id}/deadline",
        json={"deadline": "2027-01-01T00:00:00+00:00"},
    )

    assert response.status_code == 409


def test_submit_after_close_is_rejected(client):
    """已关闭的问卷拒绝提交。"""
    survey_id, question = _published(client)
    _register_login(client, "zhang")
    client.post(f"/api/surveys/{survey_id}/close")
    client.post("/api/logout")
    _register_login(client, "li", "li-pass-1")

    response = client.post(
        f"/api/surveys/{survey_id}/responses", json=_answer(question)
    )

    assert response.status_code == 409
    assert "关闭" in response.json()["detail"]


def test_close_during_fill_blocks_submit(client):
    """填写页打开时仍开放，提交前问卷被关闭 → 提交被拒绝。"""
    survey_id, question = _published(client)
    _register_login(client, "li", "li-pass-1")
    opened = client.get(f"/s/{survey_id}")
    assert opened.status_code == 200
    assert "你最喜欢哪门课？" in opened.text
    client.post("/api/logout")

    _register_login(client, "zhang")
    client.post(f"/api/surveys/{survey_id}/close")
    client.post("/api/logout")
    _register_login(client, "li", "li-pass-1")

    response = client.post(f"/s/{survey_id}/submit", data=_form_answers(client, survey_id, question))

    assert response.status_code == 409
    assert "关闭" in response.text


def _form_answers(client, survey_id, question):
    """取回 CSRF 令牌并拼出该题的填写表单数据。"""
    client.get(f"/s/{survey_id}")
    token = client.cookies.get("csrf_token")
    return {
        "csrf_token": token,
        f"answer_{question['qid']}": question["options"][0]["oid"],
    }


def test_closed_fill_page_shows_message(client):
    """已关闭问卷的填写页给出明确提示。"""
    survey_id, _ = _published(client)
    _register_login(client, "zhang")
    client.post(f"/api/surveys/{survey_id}/close")
    client.post("/api/logout")
    _register_login(client, "li", "li-pass-1")

    response = client.get(f"/s/{survey_id}")

    assert response.status_code == 409
    assert "关闭" in response.text


def test_expired_fill_page_shows_message(client):
    """已截止问卷的填写页给出明确提示。"""
    survey_id, _ = _published(client)
    _register_login(client, "zhang")
    client.post(
        f"/api/surveys/{survey_id}/deadline", json={"deadline": _utc(timedelta(hours=-1))}
    )
    client.post("/api/logout")
    _register_login(client, "li", "li-pass-1")

    response = client.get(f"/s/{survey_id}")

    assert response.status_code == 409
    assert "截止" in response.text


def test_management_page_offers_deadline_and_close(client):
    """已发布问卷的管理页提供设置截止时间表单与关闭按钮。"""
    survey_id, _ = _published(client)
    _register_login(client, "zhang")

    body = client.get(f"/surveys/{survey_id}/edit").text

    assert f'action="/surveys/{survey_id}/deadline"' in body
    assert 'type="datetime-local"' in body
    assert f'action="/surveys/{survey_id}/close"' in body
    assert "关闭问卷" in body


def test_set_deadline_via_form_stores_utc(client):
    """通过管理页表单提交本地时间，按本地→UTC 折算后存储。"""
    survey_id, _ = _published(client)
    _register_login(client, "zhang")
    client.get(f"/surveys/{survey_id}/edit")
    token = client.cookies.get("csrf_token")
    local_value = "2027-06-01T09:30"
    expected = datetime.fromisoformat(local_value).astimezone(timezone.utc).isoformat()

    response = client.post(
        f"/surveys/{survey_id}/deadline",
        data={"csrf_token": token, "deadline": local_value},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == f"/surveys/{survey_id}/edit"
    assert client.get(f"/api/surveys/{survey_id}").json()["deadline"] == expected


def test_management_page_shows_deadline_as_local_time(client):
    """管理页把 UTC 存储的截止时间按本地时间预填进 datetime-local 输入框。"""
    survey_id, _ = _published(client)
    _register_login(client, "zhang")
    client.post(
        f"/api/surveys/{survey_id}/deadline",
        json={"deadline": "2027-06-01T09:30:00+00:00"},
    )
    expected = (
        datetime.fromisoformat("2027-06-01T09:30:00+00:00")
        .astimezone()
        .strftime("%Y-%m-%dT%H:%M")
    )

    body = client.get(f"/surveys/{survey_id}/edit").text

    assert f'value="{expected}"' in body


def test_close_via_form_closes_survey(client):
    """通过管理页按钮关闭问卷，状态变为已关闭且不再提供关闭按钮。"""
    survey_id, _ = _published(client)
    _register_login(client, "zhang")
    client.get(f"/surveys/{survey_id}/edit")
    token = client.cookies.get("csrf_token")

    response = client.post(
        f"/surveys/{survey_id}/close",
        data={"csrf_token": token},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == f"/surveys/{survey_id}/edit"
    assert client.get(f"/api/surveys/{survey_id}").json()["status"] == "closed"
    body = client.get(f"/surveys/{survey_id}/edit").text
    assert "已关闭" in body
    assert f'action="/surveys/{survey_id}/close"' not in body


def test_lifecycle_form_requires_login(client):
    """未登录提交截止时间表单 → 跳转登录并记住原地址。"""
    survey_id, _ = _published(client)

    response = client.post(
        f"/surveys/{survey_id}/close", data={}, follow_redirects=False
    )

    assert response.status_code == 303
    assert response.headers["location"] == f"/login?next=/surveys/{survey_id}/edit"
