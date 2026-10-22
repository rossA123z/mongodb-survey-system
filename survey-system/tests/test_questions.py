"""工单 02：草稿里单选题的添加、编辑、删除、排序与必答设置。

接缝是 HTTP 接口：只通过接口观察行为，不直接查询数据库。
题目与选项用稳定标识（qid / oid），编辑文本时标识保持不变。
"""

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


def test_creator_can_add_single_choice_question(client):
    """创建者添加单选题，得到单选取值、稳定标识与有序选项。"""
    _login(client)
    survey = _draft(client)

    response = _add_question(
        client, survey["id"], "你最喜欢哪门课？", ["数据库", "操作系统"], required=True
    )

    assert response.status_code == 201
    question = response.json()
    assert question["type"] == "single"
    assert question["title"] == "你最喜欢哪门课？"
    assert question["required"] is True
    assert question["qid"]
    assert [option["text"] for option in question["options"]] == ["数据库", "操作系统"]
    assert all(option["oid"] for option in question["options"])

    detail = client.get(f"/api/surveys/{survey['id']}").json()
    assert [item["qid"] for item in detail["questions"]] == [question["qid"]]


def test_creator_can_edit_question_and_keep_option_ids(client):
    """编辑题干与选项文本时，保留未改动选项的稳定标识。"""
    _login(client)
    survey = _draft(client)
    added = _add_question(
        client, survey["id"], "你最喜欢哪门课？", ["数据库", "操作系统"]
    ).json()
    first_oid = added["options"][0]["oid"]

    response = client.patch(
        f"/api/surveys/{survey['id']}/questions/{added['qid']}",
        json={
            "title": "请选择你最喜欢的课程",
            "options": [
                {"oid": first_oid, "text": "数据库系统"},
                {"text": "编译原理"},
            ],
        },
    )

    assert response.status_code == 200
    question = response.json()
    assert question["title"] == "请选择你最喜欢的课程"
    assert question["qid"] == added["qid"]
    assert question["options"][0]["oid"] == first_oid
    assert [option["text"] for option in question["options"]] == [
        "数据库系统",
        "编译原理",
    ]


def test_required_flag_defaults_to_false_and_can_toggle(client):
    """必答默认为否，可改为必答。"""
    _login(client)
    survey = _draft(client)
    added = _add_question(client, survey["id"], "选一个", ["甲", "乙"]).json()
    assert added["required"] is False

    response = client.patch(
        f"/api/surveys/{survey['id']}/questions/{added['qid']}",
        json={"required": True},
    )

    assert response.status_code == 200
    assert response.json()["required"] is True
    assert response.json()["options"][0]["text"] == "甲"


def test_creator_can_delete_question(client):
    """删除题目后，详情里不再有该题，其余题保持顺序。"""
    _login(client)
    survey = _draft(client)
    first = _add_question(client, survey["id"], "第一题", ["甲", "乙"]).json()
    second = _add_question(client, survey["id"], "第二题", ["丙", "丁"]).json()

    response = client.delete(
        f"/api/surveys/{survey['id']}/questions/{first['qid']}"
    )

    assert response.status_code == 200
    remaining = client.get(f"/api/surveys/{survey['id']}").json()["questions"]
    assert [item["qid"] for item in remaining] == [second["qid"]]


def test_creator_can_reorder_questions(client):
    """上移 / 下移调整题目顺序，标识不变。"""
    _login(client)
    survey = _draft(client)
    first = _add_question(client, survey["id"], "第一题", ["甲", "乙"]).json()
    second = _add_question(client, survey["id"], "第二题", ["丙", "丁"]).json()

    response = client.post(
        f"/api/surveys/{survey['id']}/questions/{second['qid']}/move",
        json={"direction": "up"},
    )

    assert response.status_code == 200
    order = [item["qid"] for item in response.json()["questions"]]
    assert order == [second["qid"], first["qid"]]


def test_question_without_option_is_rejected(client):
    """题目没有任何选项时被拒绝。"""
    _login(client)
    survey = _draft(client)

    response = _add_question(client, survey["id"], "空空如也", [])

    assert response.status_code == 400


def test_question_operations_require_login(client):
    """未登录不能编辑题目。"""
    _login(client)
    survey = _draft(client)
    client.post("/api/logout")

    assert _add_question(client, survey["id"], "越权", ["甲", "乙"]).status_code == 401


def test_other_account_cannot_edit_questions(client):
    """另一账号编辑别人问卷的题目统一 404。"""
    _login(client)
    survey = _draft(client)
    client.post("/api/logout")

    _login(client, "li")

    assert _add_question(client, survey["id"], "越权", ["甲", "乙"]).status_code == 404
