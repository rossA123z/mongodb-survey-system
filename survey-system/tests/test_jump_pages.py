"""工单 04：跳转规则与返回修改的页面行为（HTTP 层）。

接缝是网页响应：断言状态码与关键内容（跳过的题不出现在确认页、返回修改后
失效答案被清除），不断言整段 HTML 完全相等。
"""

from conftest import CSRF_COOKIE, csrf_token as _csrf, register_login as _register_login


def _draft(client):
    return client.post("/api/surveys", json={"title": "跳转问卷", "description": ""}).json()


def _add(client, survey_id, payload):
    return client.post(f"/api/surveys/{survey_id}/questions", json=payload)


def _single(client, survey_id, title, required=False):
    return _add(
        client,
        survey_id,
        {
            "title": title,
            "type": "single",
            "required": required,
            "options": [{"text": "甲"}, {"text": "乙"}],
        },
    ).json()


def _rules(client, survey_id, qid, rules):
    return client.post(
        f"/api/surveys/{survey_id}/questions/{qid}/rules", json={"rules": rules}
    )


def test_editor_shows_rule_configuration_controls(client):
    """草稿编辑页为可跳转题型提供规则配置控件（目标选择含“结束”）。"""
    _register_login(client)
    survey = _draft(client)
    question = _single(client, survey["id"], "第一题")

    body = client.get(f"/surveys/{survey['id']}/edit").text

    assert f"/surveys/{survey['id']}/questions/{question['qid']}/rules" in body
    assert 'name="rules-0-target"' in body
    assert 'value="end"' in body


def test_editor_does_not_show_rules_for_text_question(client):
    """文本题不参与条件跳转，编辑页不为它渲染规则表单。"""
    _register_login(client)
    survey = _draft(client)
    text = _add(client, survey["id"], {"title": "文本", "type": "text"}).json()

    body = client.get(f"/surveys/{survey['id']}/edit").text

    assert f"/surveys/{survey['id']}/questions/{text['qid']}/rules" not in body


def test_editor_form_saves_rules_in_row_order(client):
    """编辑页表单按行顺序保存规则，调整行顺序即调整规则优先级。"""
    _register_login(client)
    survey = _draft(client)
    q1 = _single(client, survey["id"], "第一题")
    q2 = _single(client, survey["id"], "第二题")
    q3 = _single(client, survey["id"], "第三题")
    jia = q1["options"][0]["oid"]
    client.get(f"/surveys/{survey['id']}/edit")
    token = client.cookies.get(CSRF_COOKIE)

    response = client.post(
        f"/surveys/{survey['id']}/questions/{q1['qid']}/rules",
        data={
            "csrf_token": token,
            "rules-0-op": "is",
            "rules-0-oid": jia,
            "rules-0-target": "end",
            "rules-1-op": "is",
            "rules-1-oid": jia,
            "rules-1-target": q3["qid"],
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    stored = client.get(f"/api/surveys/{survey['id']}").json()["questions"][0]
    assert [rule["target"] for rule in stored["rules"]] == ["end", q3["qid"]]


def _rule_fields(rules):
    """把已存规则拼成编辑页规则表单的字段（单选题型，含稳定 rid）。"""
    data = {}
    for i, rule in enumerate(rules):
        data[f"rules-{i}-rid"] = rule["rid"]
        data[f"rules-{i}-oid"] = rule["condition"]["oid"]
        data[f"rules-{i}-target"] = rule["target"]
    return data


def test_editor_rule_move_reorders_rules(client):
    """规则行的上移 / 下移控件交换相邻规则顺序；越界时不改动。"""
    _register_login(client)
    survey = _draft(client)
    q1 = _single(client, survey["id"], "第一题")
    q3 = _single(client, survey["id"], "第三题")
    jia = q1["options"][0]["oid"]
    _rules(
        client, survey["id"], q1["qid"],
        [
            {"condition": {"op": "is", "oid": jia}, "target": "end"},
            {"condition": {"op": "is", "oid": jia}, "target": q3["qid"]},
        ],
    )
    stored = client.get(f"/api/surveys/{survey['id']}").json()["questions"][0]
    body = client.get(f"/surveys/{survey['id']}/edit").text
    assert 'name="rule_move"' in body
    token = client.cookies.get(CSRF_COOKIE)
    url = f"/surveys/{survey['id']}/questions/{q1['qid']}/rules"

    data = {"csrf_token": token, "rule_move": "1-up", **_rule_fields(stored["rules"])}
    assert client.post(url, data=data, follow_redirects=False).status_code == 303
    after = client.get(f"/api/surveys/{survey['id']}").json()["questions"][0]
    assert [rule["target"] for rule in after["rules"]] == [q3["qid"], "end"]

    # 第一条再上移是越界操作，顺序保持不变。
    data = {"csrf_token": token, "rule_move": "0-up", **_rule_fields(after["rules"])}
    client.post(url, data=data, follow_redirects=False)
    again = client.get(f"/api/surveys/{survey['id']}").json()["questions"][0]
    assert [rule["target"] for rule in again["rules"]] == [q3["qid"], "end"]


def test_rule_move_changes_effective_match_order(client):
    """调整规则顺序后，实际命中的第一条规则随之改变（生效顺序确实变了）。"""
    _register_login(client)
    survey = _draft(client)
    q1 = _single(client, survey["id"], "第一题", required=True)
    q2 = _single(client, survey["id"], "第二题")
    q3 = _single(client, survey["id"], "第三题")
    jia = q1["options"][0]["oid"]
    # 两条规则条件相同、目标不同：原始顺序是先跳到第三题，其次才是结束。
    _rules(
        client, survey["id"], q1["qid"],
        [
            {"condition": {"op": "is", "oid": jia}, "target": q3["qid"]},
            {"condition": {"op": "is", "oid": jia}, "target": "end"},
        ],
    )
    stored = client.get(f"/api/surveys/{survey['id']}").json()["questions"][0]
    client.get(f"/surveys/{survey['id']}/edit")
    token = client.cookies.get(CSRF_COOKIE)
    # 把“跳到第三题”下移，使“结束”成为第一条命中的规则。
    client.post(
        f"/surveys/{survey['id']}/questions/{q1['qid']}/rules",
        data={"csrf_token": token, "rule_move": "0-down", **_rule_fields(stored["rules"])},
        follow_redirects=False,
    )
    reordered = client.get(f"/api/surveys/{survey['id']}").json()["questions"][0]
    assert [rule["target"] for rule in reordered["rules"]] == ["end", q3["qid"]]
    client.post(f"/api/surveys/{survey['id']}/publish")
    client.post("/api/logout")
    _register_login(client, "li", "li-pass-1")
    token = _csrf(client, f"/s/{survey['id']}")

    response = client.post(
        f"/s/{survey['id']}",
        data={"csrf_token": token, f"answer_{q1['qid']}": jia},
    )

    assert response.status_code == 200
    assert "确认提交" in response.text
    # “结束”先命中：路径止于第一题，第二、三题不再出现在确认页。
    assert q2["title"] not in response.text
    assert q3["title"] not in response.text


def test_editor_renders_existing_rules_for_each_jumpable_type(client):
    """多选与数字题的规则行也能正常渲染（含各条件的输入控件）。"""
    _register_login(client)
    survey = _draft(client)
    single = _single(client, survey["id"], "单选")
    multi = _add(
        client,
        survey["id"],
        {"title": "多选", "type": "multi", "options": [{"text": "甲"}, {"text": "乙"}]},
    ).json()
    number = _add(client, survey["id"], {"title": "数字", "type": "number"}).json()
    _rules(
        client, survey["id"], single["qid"],
        [{"condition": {"op": "is", "oid": single["options"][0]["oid"]}, "target": "end"}],
    )
    _rules(
        client, survey["id"], multi["qid"],
        [{"condition": {"op": "any", "oids": [o["oid"] for o in multi["options"]]}, "target": "end"}],
    )
    _rules(
        client, survey["id"], number["qid"],
        [{"condition": {"op": "between", "min": 1, "max": 5}, "target": "end"}],
    )

    response = client.get(f"/surveys/{survey['id']}/edit")

    assert response.status_code == 200
    assert 'value="between"' in response.text
    assert 'name="rules-0-oids"' in response.text


def _published_chain(client):
    """发布：q1 单选（甲→第三题）、q2 必答单选、q3 文本；返回 (survey_id, q1, q2, q3)。"""
    _register_login(client)
    survey = _draft(client)
    q1 = _single(client, survey["id"], "第一题", required=True)
    q2 = _add(
        client,
        survey["id"],
        {
            "title": "必答第二题",
            "type": "single",
            "required": True,
            "options": [{"text": "丙"}, {"text": "丁"}],
        },
    ).json()
    q3 = _add(client, survey["id"], {"title": "文本第三题", "type": "text"}).json()
    _rules(
        client, survey["id"], q1["qid"],
        [{"condition": {"op": "is", "oid": q1["options"][0]["oid"]}, "target": q3["qid"]}],
    )
    client.post(f"/api/surveys/{survey['id']}/publish")
    client.post("/api/logout")
    _register_login(client, "li", "li-pass-1")
    return survey["id"], q1, q2, q3


def test_fill_confirm_excludes_skipped_question(client):
    """按规则跳过后，被跳过的题不出现在确认页。"""
    survey_id, q1, q2, q3 = _published_chain(client)
    token = _csrf(client, f"/s/{survey_id}")
    jia = q1["options"][0]["oid"]

    response = client.post(
        f"/s/{survey_id}",
        data={
            "csrf_token": token,
            f"answer_{q1['qid']}": jia,
            # 伪造第二题的答案：它应被跳过，服务端剔除。
            f"answer_{q2['qid']}": q2["options"][0]["oid"],
            f"answer_{q3['qid']}": "第三题的答案",
        },
    )

    assert response.status_code == 200, response.text
    assert "确认提交" in response.text
    assert q3["title"] in response.text
    assert q2["title"] not in response.text


def test_jump_to_end_stops_at_confirm_page(client):
    """跳转到结束只进入提交确认页，不自动提交。"""
    _register_login(client)
    survey = _draft(client)
    q1 = _single(client, survey["id"], "第一题", required=True)
    _add(
        client,
        survey["id"],
        {
            "title": "必答第二题",
            "type": "single",
            "required": True,
            "options": [{"text": "丙"}, {"text": "丁"}],
        },
    )
    _rules(
        client, survey["id"], q1["qid"],
        [{"condition": {"op": "is", "oid": q1["options"][0]["oid"]}, "target": "end"}],
    )
    client.post(f"/api/surveys/{survey['id']}/publish")
    client.post("/api/logout")
    _register_login(client, "li", "li-pass-1")
    token = _csrf(client, f"/s/{survey['id']}")

    response = client.post(
        f"/s/{survey['id']}",
        data={"csrf_token": token, f"answer_{q1['qid']}": q1["options"][0]["oid"]},
    )

    assert response.status_code == 200
    assert "确认提交" in response.text
    assert "提交成功" not in response.text

    client.post("/api/logout")
    _register_login(client)
    assert client.get(f"/api/surveys/{survey['id']}/responses").json() == []


def test_back_edit_clears_answer_no_longer_on_path(client):
    """返回修改后路径改变，不再经过的答案被清除：不提交也不保存。"""
    _register_login(client)
    survey = _draft(client)
    q1 = _single(client, survey["id"], "第一题", required=True)
    _add(
        client,
        survey["id"],
        {"title": "第二题", "type": "single", "options": [{"text": "丙"}, {"text": "丁"}]},
    )
    q3 = _add(client, survey["id"], {"title": "文本第三题", "type": "text"}).json()
    _rules(
        client, survey["id"], q1["qid"],
        [{"condition": {"op": "is", "oid": q1["options"][0]["oid"]}, "target": "end"}],
    )
    client.post(f"/api/surveys/{survey['id']}/publish")
    client.post("/api/logout")
    _register_login(client, "li", "li-pass-1")
    token = _csrf(client, f"/s/{survey['id']}")
    yi = q1["options"][1]["oid"]
    jia = q1["options"][0]["oid"]

    # 先按“乙”作答，路径经过第三题并填了答案。
    response = client.post(
        f"/s/{survey['id']}",
        data={
            "csrf_token": token,
            f"answer_{q1['qid']}": yi,
            f"answer_{q3['qid']}": "第三题旧答案",
        },
    )
    assert response.status_code == 200
    assert "第三题旧答案" in response.text

    # 返回修改：重新渲染填写页并保留已填答案。
    back = client.post(
        f"/s/{survey['id']}/back",
        data={
            "csrf_token": token,
            f"answer_{q1['qid']}": yi,
            f"answer_{q3['qid']}": "第三题旧答案",
        },
    )
    assert back.status_code == 200
    assert "第三题旧答案" in back.text

    # 改第一题为“甲”后路径跳向结束，第三题不再经过；仍带着旧答案提交。
    submit = client.post(
        f"/s/{survey['id']}/submit",
        data={
            "csrf_token": token,
            f"answer_{q1['qid']}": jia,
            f"answer_{q3['qid']}": "第三题旧答案",
        },
    )
    assert submit.status_code == 200
    assert "提交成功" in submit.text

    client.post("/api/logout")
    _register_login(client)
    stored = client.get(f"/api/surveys/{survey['id']}/responses").json()[0]
    assert [answer["qid"] for answer in stored["answers"]] == [q1["qid"]]
