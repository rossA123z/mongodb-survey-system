"""工单 03：提交时服务端校验多选、文本、数字的类型、大小与全部限制。

接缝是 HTTP 接口：只通过接口观察行为，不直接查询数据库。
覆盖边界值、固定数量、可选题为空、混合题型答卷；错误定位到具体题目。
"""

from conftest import register_login as _login


def _survey_with(client, questions, allow_multiple=True):
    """以 zhang 建一份含给定题目的问卷并发布，返回 (survey_id, 题目列表)。"""
    _login(client)
    survey = client.post(
        "/api/surveys", json={"title": "课程反馈", "description": ""}
    ).json()
    created = []
    for payload in questions:
        response = client.post(f"/api/surveys/{survey['id']}/questions", json=payload)
        assert response.status_code == 201, response.text
        created.append(response.json())
    publish = client.post(
        f"/api/surveys/{survey['id']}/publish", json={"allow_multiple": allow_multiple}
    )
    assert publish.status_code == 200, publish.text
    client.post("/api/logout")
    return survey["id"], created


def _respondent(client, username="li"):
    client.post("/api/register", json={"username": username, "password": "li-pass-1"})
    client.post("/api/login", json={"username": username, "password": "li-pass-1"})


def _submit(client, survey_id, answers):
    return client.post(
        f"/api/surveys/{survey_id}/responses", json={"answers": answers}
    )


def _multi(options=(("甲",), ("乙",), ("丙",)), **limits):
    return {
        "title": "多选题",
        "type": "multi",
        "options": [{"text": text} for (text,) in options],
        **limits,
    }


# ---- 多选 ----

def test_multi_within_limits_submits(client):
    """多选数量在限制内提交成功。"""
    survey_id, [q] = _survey_with(
        client, [_multi(min_choices=1, max_choices=2)]
    )
    _respondent(client)
    oids = [o["oid"] for o in q["options"]][:2]

    response = _submit(client, survey_id, [{"qid": q["qid"], "oids": oids}])

    assert response.status_code == 201


def test_multi_below_min_is_rejected(client):
    """少选到低于最少选择数时拒绝并提示。"""
    survey_id, [q] = _survey_with(client, [_multi(min_choices=2)])
    _respondent(client)
    oids = [o["oid"] for o in q["options"]][:1]

    response = _submit(client, survey_id, [{"qid": q["qid"], "oids": oids}])

    assert response.status_code == 400
    assert "2" in response.json()["detail"]["errors"][q["qid"]]


def test_multi_above_max_is_rejected(client):
    """多选超过最多选择数时拒绝并提示。"""
    survey_id, [q] = _survey_with(client, [_multi(max_choices=1)])
    _respondent(client)
    oids = [o["oid"] for o in q["options"]][:2]

    response = _submit(client, survey_id, [{"qid": q["qid"], "oids": oids}])

    assert response.status_code == 400
    assert q["qid"] in response.json()["detail"]["errors"]


def test_multi_fixed_count_enforced(client):
    """固定数量（上下限相等）既拒绝少选也接受正好数量。"""
    survey_id, [q] = _survey_with(client, [_multi(min_choices=2, max_choices=2)])
    _respondent(client)
    oids = [o["oid"] for o in q["options"]]

    too_few = _submit(client, survey_id, [{"qid": q["qid"], "oids": oids[:1]}])
    assert too_few.status_code == 400

    exact = _submit(client, survey_id, [{"qid": q["qid"], "oids": oids[:2]}])
    assert exact.status_code == 201


def test_multi_invalid_option_is_rejected(client):
    """多选包含无效选项时拒绝。"""
    survey_id, [q] = _survey_with(client, [_multi()])
    _respondent(client)

    response = _submit(client, survey_id, [{"qid": q["qid"], "oids": ["o_not-exist"]}])

    assert response.status_code == 400


def test_multi_duplicate_option_is_rejected(client):
    """多选不允许重复选择同一选项。"""
    survey_id, [q] = _survey_with(client, [_multi()])
    _respondent(client)
    oid = q["options"][0]["oid"]

    response = _submit(client, survey_id, [{"qid": q["qid"], "oids": [oid, oid]}])

    assert response.status_code == 400


def test_multi_optional_empty_is_allowed(client):
    """多选题留空且非必答时不触发限制错误。"""
    survey_id, [q] = _survey_with(client, [_multi(required=False, min_choices=2)])
    _respondent(client)

    response = _submit(client, survey_id, [{"qid": q["qid"], "oids": []}])

    assert response.status_code == 201


def test_multi_required_empty_is_rejected(client):
    """必答多选题为空时拒绝。"""
    survey_id, [q] = _survey_with(client, [_multi(required=True)])
    _respondent(client)

    response = _submit(client, survey_id, [{"qid": q["qid"], "oids": []}])

    assert response.status_code == 400
    assert "必答" in response.json()["detail"]["errors"][q["qid"]]


# ---- 文本 ----

def _text(**limits):
    return {"title": "文本题", "type": "text", **limits}


def test_text_within_limits_submits(client):
    """字数在限制内的文本提交成功。"""
    survey_id, [q] = _survey_with(client, [_text(min_length=1, max_length=10)])
    _respondent(client)

    response = _submit(client, survey_id, [{"qid": q["qid"], "text": "很好"}])

    assert response.status_code == 201


def test_text_below_min_is_rejected(client):
    """文本短于最少字数时拒绝并提示。"""
    survey_id, [q] = _survey_with(client, [_text(min_length=3)])
    _respondent(client)

    response = _submit(client, survey_id, [{"qid": q["qid"], "text": "你好"}])

    assert response.status_code == 400
    assert "3" in response.json()["detail"]["errors"][q["qid"]]


def test_text_above_max_is_rejected(client):
    """文本长于最多字数时拒绝。"""
    survey_id, [q] = _survey_with(client, [_text(max_length=1)])
    _respondent(client)

    response = _submit(client, survey_id, [{"qid": q["qid"], "text": "你好"}])

    assert response.status_code == 400


def test_text_counts_characters_not_bytes(client):
    """文本按字符计数：四个汉字长度为 4，不按字节（UTF-8 为 12 字节）。"""
    survey_id, [q] = _survey_with(client, [_text(min_length=4, max_length=4)])
    _respondent(client)

    response = _submit(client, survey_id, [{"qid": q["qid"], "text": "你好世界"}])

    assert response.status_code == 201


def test_text_boundary_lengths_are_inclusive(client):
    """恰好等于上下限的字数通过。"""
    survey_id, [q] = _survey_with(client, [_text(min_length=2, max_length=2)])
    _respondent(client)

    response = _submit(client, survey_id, [{"qid": q["qid"], "text": "刚好"}])

    assert response.status_code == 201


def test_text_optional_empty_is_allowed(client):
    """可选题文本留空（空字符串）不触发限制错误。"""
    survey_id, [q] = _survey_with(client, [_text(required=False, min_length=5)])
    _respondent(client)

    response = _submit(client, survey_id, [{"qid": q["qid"], "text": ""}])

    assert response.status_code == 201


def test_text_required_empty_is_rejected(client):
    """必答文本题留空时拒绝。"""
    survey_id, [q] = _survey_with(client, [_text(required=True)])
    _respondent(client)

    response = _submit(client, survey_id, [{"qid": q["qid"], "text": ""}])

    assert response.status_code == 400
    assert "必答" in response.json()["detail"]["errors"][q["qid"]]


# ---- 数字 ----

def _number(**limits):
    return {"title": "数字题", "type": "number", **limits}


def test_number_within_range_submits(client):
    """数值在范围内的数字提交成功。"""
    survey_id, [q] = _survey_with(client, [_number(min_value=0, max_value=10)])
    _respondent(client)

    response = _submit(client, survey_id, [{"qid": q["qid"], "number": 5}])

    assert response.status_code == 201


def test_number_boundary_values_are_inclusive(client):
    """区间含两端：等于最小值和最大值都通过。"""
    survey_id, [q] = _survey_with(client, [_number(min_value=0, max_value=10)])
    _respondent(client)

    assert _submit(client, survey_id, [{"qid": q["qid"], "number": 0}]).status_code == 201
    assert _submit(client, survey_id, [{"qid": q["qid"], "number": 10}]).status_code == 201


def test_number_below_min_is_rejected(client):
    """小于最小值时拒绝。"""
    survey_id, [q] = _survey_with(client, [_number(min_value=0)])
    _respondent(client)

    response = _submit(client, survey_id, [{"qid": q["qid"], "number": -1}])

    assert response.status_code == 400


def test_number_above_max_is_rejected(client):
    """大于最大值时拒绝。"""
    survey_id, [q] = _survey_with(client, [_number(max_value=10)])
    _respondent(client)

    response = _submit(client, survey_id, [{"qid": q["qid"], "number": 11}])

    assert response.status_code == 400


def test_number_non_integer_rejected_when_integer_required(client):
    """要求整数时小数被拒绝。"""
    survey_id, [q] = _survey_with(client, [_number(integer=True)])
    _respondent(client)

    response = _submit(client, survey_id, [{"qid": q["qid"], "number": 3.5}])

    assert response.status_code == 400


def test_number_integer_accepted_when_integer_required(client):
    """要求整数时整数通过。"""
    survey_id, [q] = _survey_with(client, [_number(integer=True)])
    _respondent(client)

    response = _submit(client, survey_id, [{"qid": q["qid"], "number": 3}])

    assert response.status_code == 201


def test_number_rejects_boolean(client):
    """布尔值不被视作数字。"""
    survey_id, [q] = _survey_with(client, [_number()])
    _respondent(client)

    response = _submit(client, survey_id, [{"qid": q["qid"], "number": True}])

    assert response.status_code == 400


def test_number_rejects_non_numeric_text(client):
    """非数字文本被拒绝。"""
    survey_id, [q] = _survey_with(client, [_number()])
    _respondent(client)

    response = _submit(client, survey_id, [{"qid": q["qid"], "number": "abc"}])

    assert response.status_code == 400


def test_number_rejects_non_finite(client):
    """非有限数值（如 1e999 溢出为无穷）被拒绝。"""
    survey_id, [q] = _survey_with(client, [_number()])
    _respondent(client)

    response = _submit(client, survey_id, [{"qid": q["qid"], "number": "1e999"}])

    assert response.status_code == 400


def test_number_accepts_numeric_string(client):
    """数字以字符串提交时按有限数值解析。"""
    survey_id, [q] = _survey_with(client, [_number(min_value=0, max_value=100)])
    _respondent(client)

    response = _submit(client, survey_id, [{"qid": q["qid"], "number": "42"}])

    assert response.status_code == 201


def test_number_optional_empty_is_allowed(client):
    """可选数字题留空不触发限制错误。"""
    survey_id, [q] = _survey_with(client, [_number(required=False, min_value=0)])
    _respondent(client)

    response = _submit(client, survey_id, [{"qid": q["qid"], "number": ""}])

    assert response.status_code == 201


# ---- 混合题型 ----

def test_mixed_survey_submits_fully(client):
    """单选、多选、文本、数字混合的问卷能完整提交。"""
    survey_id, questions = _survey_with(
        client,
        [
            {"title": "单选", "type": "single", "options": [{"text": "甲"}, {"text": "乙"}]},
            _multi(min_choices=1, max_choices=3),
            _text(min_length=1, max_length=20),
            _number(min_value=0, max_value=120, integer=True),
        ],
    )
    single, multi, text, number = questions
    _respondent(client)

    response = _submit(
        client,
        survey_id,
        [
            {"qid": single["qid"], "oid": single["options"][0]["oid"]},
            {"qid": multi["qid"], "oids": [o["oid"] for o in multi["options"][:2]]},
            {"qid": text["qid"], "text": "服务很好，建议增加示例。"},
            {"qid": number["qid"], "number": 20},
        ],
    )

    assert response.status_code == 201

    _login(client)
    listing = client.get(f"/api/surveys/{survey_id}/responses").json()
    assert len(listing) == 1
    saved = {answer["qid"]: answer for answer in listing[0]["answers"]}
    assert saved[single["qid"]]["oid"] == single["options"][0]["oid"]
    assert saved[multi["qid"]]["oids"] == [o["oid"] for o in multi["options"][:2]]
    assert saved[text["qid"]]["text"] == "服务很好，建议增加示例。"
    assert saved[number["qid"]]["number"] == 20


def test_mixed_survey_reports_only_invalid_question(client):
    """混合问卷中只有越界的那道题报错，错误定位到该题。"""
    survey_id, questions = _survey_with(
        client,
        [
            {"title": "单选", "type": "single", "options": [{"text": "甲"}, {"text": "乙"}]},
            _number(min_value=0, max_value=10),
        ],
    )
    single, number = questions
    _respondent(client)

    response = _submit(
        client,
        survey_id,
        [
            {"qid": single["qid"], "oid": single["options"][0]["oid"]},
            {"qid": number["qid"], "number": 99},
        ],
    )

    assert response.status_code == 400
    errors = response.json()["detail"]["errors"]
    assert number["qid"] in errors
    assert single["qid"] not in errors
