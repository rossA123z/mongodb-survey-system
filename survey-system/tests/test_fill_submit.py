"""工单 02：填写与提交单选问卷（接口层）。

覆盖：未发布不可填、必答与无效选项拒绝并定位到题、默认每账号一次、
允许重复时可多次提交、创建者能看到答卷。
"""

from conftest import register_login as _register_login


def _published_survey(client, required=True, allow_multiple=False, anonymous=False):
    """以 zhang 建一份已发布的单选题问卷，返回题目信息。"""
    _register_login(client, "zhang")
    survey = client.post(
        "/api/surveys", json={"title": "课程反馈", "description": ""}
    ).json()
    question = client.post(
        f"/api/surveys/{survey['id']}/questions",
        json={
            "title": "你最喜欢哪门课？",
            "required": required,
            "options": [{"text": "数据库"}, {"text": "操作系统"}],
        },
    ).json()
    client.post(
        f"/api/surveys/{survey['id']}/publish",
        json={"anonymous": anonymous, "allow_multiple": allow_multiple},
    )
    client.post("/api/logout")
    return survey["id"], question


def test_submit_requires_login(client):
    """未登录不能提交答卷。"""
    survey_id, question = _published_survey(client)

    response = client.post(
        f"/api/surveys/{survey_id}/responses",
        json={"answers": [{"qid": question["qid"], "oid": question["options"][0]["oid"]}]},
    )

    assert response.status_code == 401


def test_unpublished_survey_cannot_be_filled(client):
    """草稿状态的问卷不能被填写或提交。"""
    _register_login(client, "zhang")
    survey = client.post(
        "/api/surveys", json={"title": "还没发布", "description": ""}
    ).json()
    client.post("/api/logout")
    _register_login(client, "li", "li-pass-1")

    response = client.post(
        f"/api/surveys/{survey['id']}/responses", json={"answers": []}
    )

    assert response.status_code == 404


def test_valid_answer_saved_and_visible_to_creator(client):
    """有效答案提交成功，创建者能看到答卷与该账号。"""
    survey_id, question = _published_survey(client)
    _register_login(client, "li", "li-pass-1")

    response = client.post(
        f"/api/surveys/{survey_id}/responses",
        json={"answers": [{"qid": question["qid"], "oid": question["options"][1]["oid"]}]},
    )

    assert response.status_code == 201

    client.post("/api/logout")
    _register_login(client, "zhang")
    listing = client.get(f"/api/surveys/{survey_id}/responses")

    assert listing.status_code == 200
    assert len(listing.json()) == 1
    saved = listing.json()[0]
    assert saved["respondent"] == "li"
    assert saved["answers"] == [
        {"qid": question["qid"], "oid": question["options"][1]["oid"]}
    ]


def test_invalid_option_is_rejected_with_question_hint(client):
    """答案不是有效选项时拒绝，并把错误定位到具体题目。"""
    survey_id, question = _published_survey(client)
    _register_login(client, "li", "li-pass-1")

    response = client.post(
        f"/api/surveys/{survey_id}/responses",
        json={"answers": [{"qid": question["qid"], "oid": "o_not-exist"}]},
    )

    assert response.status_code == 400
    errors = response.json()["detail"]["errors"]
    assert question["qid"] in errors
    assert "你最喜欢哪门课？" in errors[question["qid"]]


def test_missing_required_answer_is_rejected_with_question_hint(client):
    """必答题为空时拒绝，并把错误定位到具体题目。"""
    survey_id, question = _published_survey(client, required=True)
    _register_login(client, "li", "li-pass-1")

    response = client.post(f"/api/surveys/{survey_id}/responses", json={"answers": []})

    assert response.status_code == 400
    errors = response.json()["detail"]["errors"]
    assert question["qid"] in errors
    assert "必答" in errors[question["qid"]]


def test_optional_question_may_be_skipped(client):
    """可选题留空也能提交。"""
    survey_id, question = _published_survey(client, required=False)
    _register_login(client, "li", "li-pass-1")

    response = client.post(f"/api/surveys/{survey_id}/responses", json={"answers": []})

    assert response.status_code == 201


def test_second_submit_is_rejected_by_default(client):
    """默认每账号每问卷只允许一次成功提交。"""
    survey_id, question = _published_survey(client)
    answer = {"answers": [{"qid": question["qid"], "oid": question["options"][0]["oid"]}]}
    _register_login(client, "li", "li-pass-1")

    first = client.post(f"/api/surveys/{survey_id}/responses", json=answer)
    second = client.post(f"/api/surveys/{survey_id}/responses", json=answer)

    assert first.status_code == 201
    assert second.status_code == 409


def test_allow_multiple_permits_repeat_submission(client):
    """允许重复填写时，同一账号可提交多次，答卷不互相覆盖。"""
    survey_id, question = _published_survey(client, allow_multiple=True)
    answer = {"answers": [{"qid": question["qid"], "oid": question["options"][0]["oid"]}]}
    _register_login(client, "li", "li-pass-1")

    assert client.post(f"/api/surveys/{survey_id}/responses", json=answer).status_code == 201
    assert client.post(f"/api/surveys/{survey_id}/responses", json=answer).status_code == 201

    client.post("/api/logout")
    _register_login(client, "zhang")
    assert len(client.get(f"/api/surveys/{survey_id}/responses").json()) == 2


def test_other_account_cannot_read_responses(client):
    """另一账号读不到别人问卷的答卷，统一 404。"""
    survey_id, question = _published_survey(client)
    _register_login(client, "li", "li-pass-1")

    assert client.get(f"/api/surveys/{survey_id}/responses").status_code == 404
