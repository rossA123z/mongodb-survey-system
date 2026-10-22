"""工单 03：编辑页与填写页按题型渲染，错误提示定位到具体题目。

接缝仍是 HTTP 层：断言页面行为特征（状态码、关键控件与中文提示），
不断言整段 HTML 完全相等。
"""

from conftest import CSRF_COOKIE, register_login as _register_login


def _draft(client, title="课程反馈"):
    return client.post(
        "/api/surveys", json={"title": title, "description": "了解学习体验"}
    ).json()


def _add(client, survey_id, payload):
    return client.post(f"/api/surveys/{survey_id}/questions", json=payload)


SINGLE = {"title": "单选", "type": "single", "options": [{"text": "甲"}, {"text": "乙"}]}
MULTI = {
    "title": "多选",
    "type": "multi",
    "required": True,
    "options": [{"text": "甲"}, {"text": "乙"}, {"text": "丙"}],
    "min_choices": 1,
    "max_choices": 2,
}
TEXT = {"title": "文本", "type": "text", "min_length": 2, "max_length": 20}
NUMBER = {"title": "数字", "type": "number", "min_value": 0, "max_value": 120, "integer": True}


def _published_mixed(client):
    """以 zhang 建一份四题型混合的已发布问卷，返回 (survey_id, {type: question})。"""
    _register_login(client)
    survey = _draft(client)
    by_type = {}
    for payload in (SINGLE, MULTI, TEXT, NUMBER):
        response = _add(client, survey["id"], payload)
        assert response.status_code == 201, response.text
        by_type[payload["type"]] = response.json()
    publish = client.post(f"/api/surveys/{survey['id']}/publish")
    assert publish.status_code == 200, publish.text
    client.post("/api/logout")
    return survey["id"], by_type


def test_editor_add_form_offers_type_and_limit_fields(client):
    """编辑页添加题目的表单提供题型选择与各题型限制字段。"""
    _register_login(client)
    survey = _draft(client)

    body = client.get(f"/surveys/{survey['id']}/edit").text

    assert 'name="type"' in body
    assert 'name="min_choices"' in body and 'name="max_choices"' in body
    assert 'name="min_length"' in body and 'name="max_length"' in body
    assert 'name="min_value"' in body and 'name="max_value"' in body
    assert 'name="integer"' in body


def test_editor_form_adds_multi_question_with_limits(client):
    """通过编辑页表单添加多选题并设置选择数限制。"""
    _register_login(client)
    survey = _draft(client)
    client.get(f"/surveys/{survey['id']}/edit")
    token = client.cookies.get(CSRF_COOKIE)

    response = client.post(
        f"/surveys/{survey['id']}/questions",
        data={
            "csrf_token": token,
            "type": "multi",
            "title": "你学过哪些课程？",
            "option": ["数据库", "操作系统", "编译原理"],
            "min_choices": "1",
            "max_choices": "2",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    question = client.get(f"/api/surveys/{survey['id']}").json()["questions"][0]
    assert question["type"] == "multi"
    assert question["min_choices"] == 1
    assert question["max_choices"] == 2


def test_editor_form_adds_text_question_with_lengths(client):
    """通过编辑页表单添加文本题并设置字数限制。"""
    _register_login(client)
    survey = _draft(client)
    client.get(f"/surveys/{survey['id']}/edit")
    token = client.cookies.get(CSRF_COOKIE)

    response = client.post(
        f"/surveys/{survey['id']}/questions",
        data={
            "csrf_token": token,
            "type": "text",
            "title": "请说说你的建议",
            "min_length": "2",
            "max_length": "20",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    question = client.get(f"/api/surveys/{survey['id']}").json()["questions"][0]
    assert question["type"] == "text"
    assert question["min_length"] == 2
    assert question["max_length"] == 20


def test_editor_shows_existing_question_limits(client):
    """编辑页展示已有题目当前题型的限制字段与数值。"""
    _register_login(client)
    survey = _draft(client)
    _add(client, survey["id"], MULTI)

    body = client.get(f"/surveys/{survey['id']}/edit").text

    assert "多选题" in body
    assert 'name="min_choices"' in body
    assert 'value="1"' in body and 'value="2"' in body


def test_fill_page_renders_each_question_type(client):
    """填写页按题型渲染：单选 radio、多选 checkbox、文本 textarea、数字 number。"""
    survey_id, _ = _published_mixed(client)
    _register_login(client, "li", "li-pass-1")

    body = client.get(f"/s/{survey_id}").text

    assert 'type="radio"' in body
    assert 'type="checkbox"' in body
    assert "<textarea" in body
    assert 'type="number"' in body


def test_fill_page_number_out_of_range_shows_question_error(client):
    """数字题越界时，填写页把具体错误提示定位到该题。"""
    survey_id, questions = _published_mixed(client)
    _register_login(client, "li", "li-pass-1")
    client.get(f"/s/{survey_id}")
    token = client.cookies.get(CSRF_COOKIE)
    number = questions["number"]

    response = client.post(
        f"/s/{survey_id}",
        data={"csrf_token": token, f"answer_{number['qid']}": "999"},
    )

    assert response.status_code == 400
    assert number["title"] in response.text
    assert "120" in response.text


def test_fill_page_multi_below_min_shows_question_error(client):
    """多选题少选时，填写页把具体错误提示定位到该题。"""
    survey_id, questions = _published_mixed(client)
    _register_login(client, "li", "li-pass-1")
    client.get(f"/s/{survey_id}")
    token = client.cookies.get(CSRF_COOKIE)
    multi = questions["multi"]

    # 多选题必选下限为 1：不选任何项即触发错误。
    response = client.post(f"/s/{survey_id}", data={"csrf_token": token})

    assert response.status_code == 400
    assert multi["title"] in response.text


def test_mixed_fill_confirm_then_submit(client):
    """四题型混合问卷：填写后进入确认页，确认后提交成功。"""
    survey_id, questions = _published_mixed(client)
    _register_login(client, "li", "li-pass-1")
    client.get(f"/s/{survey_id}")
    token = client.cookies.get(CSRF_COOKIE)
    single, multi, text, number = (
        questions["single"],
        questions["multi"],
        questions["text"],
        questions["number"],
    )
    answers = {
        "csrf_token": token,
        f"answer_{single['qid']}": single["options"][0]["oid"],
        f"answer_{multi['qid']}": [o["oid"] for o in multi["options"][:2]],
        f"answer_{text['qid']}": "服务很好，建议增加示例。",
        f"answer_{number['qid']}": "20",
    }

    confirm = client.post(f"/s/{survey_id}", data=answers)

    assert confirm.status_code == 200
    assert "确认提交" in confirm.text
    assert "服务很好，建议增加示例。" in confirm.text

    submitted = client.post(f"/s/{survey_id}/submit", data=answers)

    assert submitted.status_code == 200
    assert "提交成功" in submitted.text

    client.post("/api/logout")
    _register_login(client)
    listing = client.get(f"/api/surveys/{survey_id}/responses").json()
    assert len(listing) == 1
