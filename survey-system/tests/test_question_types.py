"""工单 03：多选、文本、数字三种题型的创建、编辑与限制配置。

接缝是 HTTP 接口：只通过接口观察行为，不直接查询数据库。
题目与限制字段用稳定标识（qid）关联，不依赖显示序号。
"""

from conftest import register_login as _login


def _draft(client, title="课程反馈"):
    return client.post(
        "/api/surveys", json={"title": title, "description": ""}
    ).json()


def _add(client, survey_id, payload):
    return client.post(f"/api/surveys/{survey_id}/questions", json=payload)


def _stored(client, survey_id, qid):
    detail = client.get(f"/api/surveys/{survey_id}").json()
    return next(q for q in detail["questions"] if q["qid"] == qid)


def test_creator_configures_multi_choice_limits(client):
    """创建者添加多选题并配置最少与最多选择数，读回保持不变。"""
    _login(client)
    survey = _draft(client)

    response = _add(
        client,
        survey["id"],
        {
            "title": "你学过哪些课程？",
            "type": "multi",
            "required": True,
            "options": [{"text": "数据库"}, {"text": "操作系统"}, {"text": "编译原理"}],
            "min_choices": 1,
            "max_choices": 3,
        },
    )

    assert response.status_code == 201
    question = response.json()
    assert question["type"] == "multi"
    assert question["min_choices"] == 1
    assert question["max_choices"] == 3
    assert [option["text"] for option in question["options"]] == [
        "数据库",
        "操作系统",
        "编译原理",
    ]

    stored = _stored(client, survey["id"], question["qid"])
    assert stored["min_choices"] == 1
    assert stored["max_choices"] == 3


def test_fixed_choice_count_uses_equal_bounds(client):
    """固定选择数用相同的上下限表达，与最少 / 最多是同一约束。"""
    _login(client)
    survey = _draft(client)

    question = _add(
        client,
        survey["id"],
        {
            "title": "请选择两项",
            "type": "multi",
            "options": [{"text": "甲"}, {"text": "乙"}, {"text": "丙"}],
            "min_choices": 2,
            "max_choices": 2,
        },
    ).json()

    assert question["min_choices"] == 2
    assert question["max_choices"] == 2


def test_creator_edits_multi_choice_limits(client):
    """编辑多选题时更新选择数限制，题型与稳定标识不变。"""
    _login(client)
    survey = _draft(client)
    question = _add(
        client,
        survey["id"],
        {
            "title": "多选题",
            "type": "multi",
            "options": [{"text": "甲"}, {"text": "乙"}, {"text": "丙"}],
        },
    ).json()

    response = client.patch(
        f"/api/surveys/{survey['id']}/questions/{question['qid']}",
        json={"min_choices": 2, "max_choices": 2},
    )

    assert response.status_code == 200
    updated = response.json()
    assert updated["type"] == "multi"
    assert updated["qid"] == question["qid"]
    assert updated["min_choices"] == 2
    assert updated["max_choices"] == 2


def test_creator_configures_text_length_limits(client):
    """创建者添加文本题并配置最少与最多字数；文本题不带选项。"""
    _login(client)
    survey = _draft(client)

    question = _add(
        client,
        survey["id"],
        {"title": "请说说你的建议", "type": "text", "min_length": 2, "max_length": 20},
    ).json()

    assert question["type"] == "text"
    assert question["min_length"] == 2
    assert question["max_length"] == 20
    assert question["options"] == []

    stored = _stored(client, survey["id"], question["qid"])
    assert stored["min_length"] == 2
    assert stored["max_length"] == 20


def test_creator_configures_number_range_and_integer(client):
    """创建者添加数字题并配置范围与是否整数；数字题不带选项。"""
    _login(client)
    survey = _draft(client)

    question = _add(
        client,
        survey["id"],
        {
            "title": "你的年龄",
            "type": "number",
            "min_value": 0,
            "max_value": 120,
            "integer": True,
        },
    ).json()

    assert question["type"] == "number"
    assert question["min_value"] == 0
    assert question["max_value"] == 120
    assert question["integer"] is True
    assert question["options"] == []


def test_number_integer_defaults_to_false(client):
    """未指定时数字题不要求整数。"""
    _login(client)
    survey = _draft(client)

    question = _add(
        client, survey["id"], {"title": "体重", "type": "number"}
    ).json()

    assert question["integer"] is False
    assert question["min_value"] is None
    assert question["max_value"] is None


def test_unknown_question_type_is_rejected(client):
    """未知题型不能被创建。"""
    _login(client)
    survey = _draft(client)

    response = _add(
        client,
        survey["id"],
        {
            "title": "怪题型",
            "type": "ranking",
            "options": [{"text": "甲"}, {"text": "乙"}],
        },
    )

    assert response.status_code == 400


def test_irrelevant_limit_fields_are_dropped(client):
    """不属于该题型的限制字段被丢弃，不混进题目数据。"""
    _login(client)
    survey = _draft(client)

    question = _add(
        client,
        survey["id"],
        {
            "title": "文本题",
            "type": "text",
            "min_length": 1,
            "min_choices": 5,
            "integer": True,
        },
    ).json()

    assert "min_choices" not in question
    assert "integer" not in question


def test_single_choice_default_type_unchanged(client):
    """不传题型时默认单选，既有接口行为不变。"""
    _login(client)
    survey = _draft(client)

    question = _add(
        client,
        survey["id"],
        {"title": "单选题", "options": [{"text": "甲"}, {"text": "乙"}]},
    ).json()

    assert question["type"] == "single"


def test_non_choice_question_accepts_no_options(client):
    """文本题不需要选项，也能创建成功。"""
    _login(client)
    survey = _draft(client)

    response = _add(client, survey["id"], {"title": "填空题", "type": "text"})

    assert response.status_code == 201
    assert response.json()["options"] == []


def _publish_single_question(client, payload):
    """建一份只含单题的问卷并发布，返回发布响应。

    创建必须成功（断言 201），否则后续“发布拒绝”的断言会因创建失败而假通过。
    """
    _login(client)
    survey = _draft(client)
    created = _add(client, survey["id"], payload)
    assert created.status_code == 201, created.text
    return client.post(f"/api/surveys/{survey['id']}/publish")


def test_publish_rejects_multi_min_greater_than_max(client):
    """多选题最少选择数大于最多选择数时，发布校验拒绝。"""
    publish = _publish_single_question(
        client,
        {
            "title": "多选题",
            "type": "multi",
            "options": [{"text": "甲"}, {"text": "乙"}, {"text": "丙"}],
            "min_choices": 3,
            "max_choices": 1,
        },
    )

    assert publish.status_code == 400


def test_publish_rejects_multi_min_exceeding_option_count(client):
    """最少选择数超过选项总数时配置不可满足，发布校验拒绝。"""
    publish = _publish_single_question(
        client,
        {
            "title": "多选题",
            "type": "multi",
            "options": [{"text": "甲"}, {"text": "乙"}],
            "min_choices": 3,
        },
    )

    assert publish.status_code == 400


def test_publish_rejects_negative_choice_count(client):
    """负数的选择数限制属于非法配置，发布校验拒绝。"""
    publish = _publish_single_question(
        client,
        {
            "title": "多选题",
            "type": "multi",
            "options": [{"text": "甲"}, {"text": "乙"}],
            "max_choices": -1,
        },
    )

    assert publish.status_code == 400


def test_publish_rejects_text_min_greater_than_max(client):
    """文本题最少字数大于最多字数时，发布校验拒绝。"""
    publish = _publish_single_question(
        client, {"title": "文本题", "type": "text", "min_length": 10, "max_length": 2}
    )

    assert publish.status_code == 400


def test_publish_rejects_negative_text_length(client):
    """负数字数限制属于非法配置，发布校验拒绝。"""
    publish = _publish_single_question(
        client, {"title": "文本题", "type": "text", "max_length": -1}
    )

    assert publish.status_code == 400


def test_publish_rejects_number_min_greater_than_max(client):
    """数字题最小值大于最大值时，发布校验拒绝。"""
    publish = _publish_single_question(
        client,
        {"title": "数字题", "type": "number", "min_value": 10, "max_value": 1},
    )

    assert publish.status_code == 400


def test_publish_accepts_valid_limits(client):
    """合法限制的四种题型问卷可以发布。"""
    _login(client)
    survey = _draft(client)
    _add(
        client,
        survey["id"],
        {"title": "单选", "type": "single", "options": [{"text": "甲"}, {"text": "乙"}]},
    )
    _add(
        client,
        survey["id"],
        {
            "title": "多选",
            "type": "multi",
            "options": [{"text": "甲"}, {"text": "乙"}, {"text": "丙"}],
            "min_choices": 1,
            "max_choices": 2,
        },
    )
    _add(
        client,
        survey["id"],
        {"title": "文本", "type": "text", "min_length": 1, "max_length": 10},
    )
    _add(
        client,
        survey["id"],
        {"title": "数字", "type": "number", "min_value": 0, "max_value": 9, "integer": True},
    )

    response = client.post(f"/api/surveys/{survey['id']}/publish")

    assert response.status_code == 200
