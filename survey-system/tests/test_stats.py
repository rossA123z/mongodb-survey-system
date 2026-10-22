"""工单 06：问卷与题目统计（接口层）。

接缝是 HTTP 接口：只通过接口提交答卷并读取统计，不直接查询数据库断言。
统计口径（逐条照规格）：
- 只统计成功保存的答卷与有效答案；被跳过或未填的可选题不计为回答；
- 空文本不计入文本题回答；数字题无回答时平均值为空（不是零）；
- 路径外答案在提交时已被剔除，因此不进入统计；
- 允许重复填写时，每份答卷分别计数；
- 统计只对创建者开放，输出不含填写者身份。
"""

from conftest import register_login as _login


def _draft(client, title="统计问卷"):
    return client.post("/api/surveys", json={"title": title, "description": ""}).json()


def _add(client, survey_id, payload):
    return client.post(f"/api/surveys/{survey_id}/questions", json=payload).json()


def _publish(client, survey_id, **config):
    return client.post(f"/api/surveys/{survey_id}/publish", json=config)


def _submit(client, survey_id, answers):
    return client.post(
        f"/api/surveys/{survey_id}/responses", json={"answers": answers}
    )


def _respond(client, survey_id, username, answers):
    """换一个账号登录并提交一份答卷。"""
    client.post("/api/logout")
    _login(client, username, f"{username}-pass")
    return _submit(client, survey_id, answers)


def _as_creator(client, username="zhang", password="secret123"):
    client.post("/api/logout")
    _login(client, username, password)


def _stats(client, survey_id):
    return client.get(f"/api/surveys/{survey_id}/stats")


def _qstats(client, survey_id, qid):
    return client.get(f"/api/surveys/{survey_id}/questions/{qid}/stats")


def _question(response, index=0):
    return response.json()["questions"][index]


SINGLE = {
    "title": "你最喜欢哪门课？",
    "type": "single",
    "options": [{"text": "数据库"}, {"text": "操作系统"}],
}


def test_stats_for_survey_without_responses_is_empty(client):
    """无答卷时统计为空（全零 / 空列表）而不是报错。"""
    _login(client)
    survey = _draft(client)
    question = _add(client, survey["id"], SINGLE)
    _publish(client, survey["id"])

    response = _stats(client, survey["id"])

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["response_count"] == 0
    assert [q["qid"] for q in body["questions"]] == [question["qid"]]
    single = _question(response)
    assert single["answered_total"] == 0
    assert [option["count"] for option in single["options"]] == [0, 0]


def test_single_choice_counts_and_answered_total(client):
    """单选统计各选项人数与有效回答总数。"""
    _login(client)
    survey = _draft(client)
    question = _add(client, survey["id"], SINGLE)
    _publish(client, survey["id"])
    jia, yi = (option["oid"] for option in question["options"])
    for name in ("li", "wang"):
        _respond(client, survey["id"], name, [{"qid": question["qid"], "oid": jia}])
    _respond(client, survey["id"], "zhao", [{"qid": question["qid"], "oid": yi}])

    _as_creator(client)
    response = _stats(client, survey["id"])

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["response_count"] == 3
    single = _question(response)
    counts = {option["oid"]: option["count"] for option in single["options"]}
    assert counts == {jia: 2, yi: 1}
    assert single["answered_total"] == 3


MULTI = {
    "title": "你选哪些课？",
    "type": "multi",
    "options": [{"text": "甲"}, {"text": "乙"}, {"text": "丙"}],
}


def test_multi_choice_counted_per_option_once_per_response(client):
    """多选统计各选项被选择的次数；一份答卷里同一选项只计一次。"""
    _login(client)
    survey = _draft(client)
    question = _add(client, survey["id"], MULTI)
    _publish(client, survey["id"])
    a, b, c = (option["oid"] for option in question["options"])
    _respond(client, survey["id"], "li", [{"qid": question["qid"], "oids": [a, b]}])
    _respond(client, survey["id"], "wang", [{"qid": question["qid"], "oids": [a]}])

    _as_creator(client)
    counts = {
        option["oid"]: option["count"]
        for option in _question(_stats(client, survey["id"]))["options"]
    }

    assert counts == {a: 2, b: 1, c: 0}


TEXT = {"title": "说说理由", "type": "text"}


def test_text_stats_list_all_contents_and_skip_empty(client):
    """文本题列出全部填空内容；未填（空）不计为回答。"""
    _login(client)
    survey = _draft(client)
    question = _add(client, survey["id"], TEXT)
    _publish(client, survey["id"])
    _respond(client, survey["id"], "li", [{"qid": question["qid"], "text": "喜欢实验"}])
    _respond(client, survey["id"], "wang", [{"qid": question["qid"], "text": "课程有意思"}])
    # 可选题留空：不提交该题答案。
    _respond(client, survey["id"], "zhao", [])

    _as_creator(client)
    text = _question(_stats(client, survey["id"]))

    assert text["answered_total"] == 2
    assert sorted(text["texts"]) == ["喜欢实验", "课程有意思"]


NUMBER = {"title": "每周学习多久？", "type": "number"}


def test_number_stats_average_and_empty_average_is_none(client):
    """数字题给出平均值；无回答时平均值为空（不是零）。"""
    _login(client)
    survey = _draft(client)
    question = _add(client, survey["id"], NUMBER)
    _publish(client, survey["id"], allow_multiple=True)

    empty = _question(_stats(client, survey["id"]))
    assert empty["answered_total"] == 0
    assert empty["average"] is None

    _as_creator(client)
    for name, value in (("li", 10), ("wang", 20), ("zhao", 15)):
        _respond(client, survey["id"], name, [{"qid": question["qid"], "number": value}])

    _as_creator(client)
    number = _question(_stats(client, survey["id"]))
    assert number["answered_total"] == 3
    assert number["average"] == 15


def test_skipped_question_not_counted(client):
    """被跳过的题不计为回答。"""
    _login(client)
    survey = _draft(client)
    q1 = _add(
        client,
        survey["id"],
        {
            "title": "第一题",
            "type": "single",
            "required": True,
            "options": [{"text": "甲"}, {"text": "乙"}],
        },
    )
    q2 = _add(client, survey["id"], {"title": "第二题", "type": "number"})
    jia = q1["options"][0]["oid"]
    client.post(
        f"/api/surveys/{survey['id']}/questions/{q1['qid']}/rules",
        json={"rules": [{"condition": {"op": "is", "oid": jia}, "target": "end"}]},
    )
    _publish(client, survey["id"])
    # 选“甲”命中结束：第二题被跳过，不回答。
    _respond(client, survey["id"], "li", [{"qid": q1["qid"], "oid": jia}])

    _as_creator(client)
    body = _stats(client, survey["id"]).json()
    q2_stat = next(q for q in body["questions"] if q["qid"] == q2["qid"])
    assert q2_stat["answered_total"] == 0
    assert q2_stat["average"] is None


def test_offpath_answer_excluded_from_stats(client):
    """伪造的路径外答案在提交时被剔除，不进入统计。

    本项验证工单 04 的失效答案清理在统计侧的效果：即使客户端提交了本应被
    跳过的题的答案，统计里该题仍为 0。
    """
    _login(client)
    survey = _draft(client)
    q1 = _add(
        client,
        survey["id"],
        {
            "title": "第一题",
            "type": "single",
            "required": True,
            "options": [{"text": "甲"}, {"text": "乙"}],
        },
    )
    q2 = _add(
        client,
        survey["id"],
        {"title": "第二题", "type": "single", "options": [{"text": "丙"}, {"text": "丁"}]},
    )
    q3 = _add(
        client,
        survey["id"],
        {"title": "第三题", "type": "single", "options": [{"text": "戊"}, {"text": "己"}]},
    )
    jia = q1["options"][0]["oid"]
    client.post(
        f"/api/surveys/{survey['id']}/questions/{q1['qid']}/rules",
        json={"rules": [{"condition": {"op": "is", "oid": jia}, "target": q3["qid"]}]},
    )
    _publish(client, survey["id"])
    # 选“甲”后第二题应被跳过；仍伪造提交第二题的答案。
    _respond(
        client,
        survey["id"],
        "li",
        [
            {"qid": q1["qid"], "oid": jia},
            {"qid": q2["qid"], "oid": q2["options"][0]["oid"]},
            {"qid": q3["qid"], "oid": q3["options"][0]["oid"]},
        ],
    )

    _as_creator(client)
    body = _stats(client, survey["id"]).json()
    q2_stat = next(q for q in body["questions"] if q["qid"] == q2["qid"])
    assert q2_stat["answered_total"] == 0
    assert [option["count"] for option in q2_stat["options"]] == [0, 0]


def test_multiple_responses_counted_separately(client):
    """允许重复填写时，同一账号的每份答卷分别计数。"""
    _login(client)
    survey = _draft(client)
    question = _add(client, survey["id"], SINGLE)
    _publish(client, survey["id"], allow_multiple=True)
    jia = question["options"][0]["oid"]
    _respond(client, survey["id"], "li", [{"qid": question["qid"], "oid": jia}])
    _respond(client, survey["id"], "li", [{"qid": question["qid"], "oid": jia}])

    _as_creator(client)
    body = _stats(client, survey["id"]).json()

    assert body["response_count"] == 2
    single = _question(_stats(client, survey["id"]))
    assert single["answered_total"] == 2
    assert single["options"][0]["count"] == 2


def test_stats_do_not_leak_respondent_identity(client):
    """匿名问卷的统计不泄露填写者身份。"""
    _login(client)
    survey = _draft(client)
    question = _add(client, survey["id"], SINGLE)
    _publish(client, survey["id"], anonymous=True)
    _respond(
        client,
        survey["id"],
        "secretrespondent",
        [{"qid": question["qid"], "oid": question["options"][0]["oid"]}],
    )

    _as_creator(client)
    body = _stats(client, survey["id"])

    assert body.status_code == 200
    assert "secretrespondent" not in body.text
    assert "respondent" not in body.text


def test_non_owner_cannot_view_stats(client):
    """非创建者查看统计统一 404。"""
    _login(client)
    survey = _draft(client)
    _add(client, survey["id"], SINGLE)
    _publish(client, survey["id"])
    client.post("/api/logout")
    _login(client, "li", "li-pass-1")

    assert _stats(client, survey["id"]).status_code == 404


def test_stats_require_login(client):
    """未登录读取统计返回 401。"""
    assert client.get("/api/surveys/000000000000000000000000/stats").status_code == 401


def test_question_level_stats_returns_single_question(client):
    """题目级接口返回指定题目的统计。"""
    _login(client)
    survey = _draft(client)
    q1 = _add(client, survey["id"], SINGLE)
    q2 = _add(client, survey["id"], MULTI)
    _publish(client, survey["id"])
    _respond(client, survey["id"], "li", [{"qid": q1["qid"], "oid": q1["options"][0]["oid"]}])

    _as_creator(client)
    response = _qstats(client, survey["id"], q2["qid"])

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["qid"] == q2["qid"]
    assert body["type"] == "multi"
    assert body["answered_total"] == 0


def test_question_level_stats_for_unknown_question_is_404(client):
    """题目级统计对不存在的题目返回 404。"""
    _login(client)
    survey = _draft(client)
    _add(client, survey["id"], SINGLE)
    _publish(client, survey["id"])

    assert _qstats(client, survey["id"], "q_not_exist").status_code == 404
