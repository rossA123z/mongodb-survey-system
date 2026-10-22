"""工单 08：编辑页可增加选择题的选项行，服务端接受任意数量的选项。

接缝是 HTTP 层：通过表单提交与页面响应观察行为，不直接查询数据库。
编辑已存题目时，新增选项行必须同时提交一个空的 ``oid``，服务端才会按位置
配对并在保留原选项稳定标识的前提下追加新选项。
"""

from conftest import CSRF_COOKIE, register_login as _login


def _draft(client, title="课程反馈"):
    return client.post(
        "/api/surveys", json={"title": title, "description": ""}
    ).json()


def _editor_token(client, survey_id):
    client.get(f"/surveys/{survey_id}/edit")
    return client.cookies.get(CSRF_COOKIE)


def _stored_question(client, survey_id):
    return client.get(f"/api/surveys/{survey_id}").json()["questions"][0]


def _add_question(client, survey_id, title, options):
    return client.post(
        f"/api/surveys/{survey_id}/questions",
        json={
            "title": title,
            "type": "single",
            "options": [{"text": text} for text in options],
        },
    ).json()


def test_add_form_accepts_five_options(client):
    """添加题目表单提交五个选项时，题目保存全部五个选项。"""
    _login(client)
    survey = _draft(client)
    token = _editor_token(client, survey["id"])
    texts = ["关系型数据库", "文档数据库", "图数据库", "键值数据库", "时序数据库"]

    response = client.post(
        f"/surveys/{survey['id']}/questions",
        data={
            "csrf_token": token,
            "type": "single",
            "title": "你用过哪些数据库？",
            "option": texts,
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    question = _stored_question(client, survey["id"])
    assert [option["text"] for option in question["options"]] == texts


def test_editor_add_form_offers_add_option_control(client):
    """添加题目表单提供「添加选项」控件，网页上可增加选项行。"""
    _login(client)
    survey = _draft(client)

    body = client.get(f"/surveys/{survey['id']}/edit").text

    assert "添加选项" in body


def test_editor_existing_question_form_offers_add_option_control(client):
    """编辑已有选择题的表单同样提供「添加选项」控件。"""
    _login(client)
    survey = _draft(client)
    _add_question(client, survey["id"], "你用过哪些数据库？", ["关系型数据库", "文档数据库"])

    body = client.get(f"/surveys/{survey['id']}/edit").text

    # 一处在已存题目的编辑表单，一处在下方「添加题目」表单。
    assert body.count("添加选项") >= 2


def test_edit_appends_option_with_empty_oid(client):
    """编辑题目时追加一行空 oid 新选项，新选项生效且原选项标识不变。"""
    _login(client)
    survey = _draft(client)
    added = _add_question(
        client, survey["id"], "你用过哪些数据库？", ["关系型数据库", "文档数据库"]
    )
    original = [(option["oid"], option["text"]) for option in added["options"]]
    token = _editor_token(client, survey["id"])

    response = client.post(
        f"/surveys/{survey['id']}/questions/{added['qid']}",
        data={
            "csrf_token": token,
            "action": "update",
            "title": added["title"],
            "required": "on",
            "oid": [option["oid"] for option in added["options"]] + [""],
            "option": [option["text"] for option in added["options"]] + ["图数据库"],
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    question = _stored_question(client, survey["id"])
    assert [option["text"] for option in question["options"]] == [
        "关系型数据库",
        "文档数据库",
        "图数据库",
    ]
    assert [
        (option["oid"], option["text"]) for option in question["options"][:2]
    ] == original
    assert question["options"][2]["oid"].startswith("o_")


def test_blank_option_rows_are_dropped(client):
    """空白选项行提交后被丢弃，不会产生空选项。"""
    _login(client)
    survey = _draft(client)
    token = _editor_token(client, survey["id"])

    response = client.post(
        f"/surveys/{survey['id']}/questions",
        data={
            "csrf_token": token,
            "type": "single",
            "title": "你用过哪些数据库？",
            "option": ["关系型数据库", "   ", "", "文档数据库"],
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    question = _stored_question(client, survey["id"])
    assert [option["text"] for option in question["options"]] == [
        "关系型数据库",
        "文档数据库",
    ]
