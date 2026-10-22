"""工单 04：条件跳转规则与有效路径（接口层）。

接缝是 HTTP 接口：只通过接口保存规则、提交答卷并观察行为，不直接查询数据库。
规则引用题目与选项的稳定标识（qid / oid），顺序即数组顺序。
"""

from conftest import register_login as _login


def _draft(client, title="跳转问卷"):
    return client.post(
        "/api/surveys", json={"title": title, "description": ""}
    ).json()


def _add(client, survey_id, payload):
    return client.post(f"/api/surveys/{survey_id}/questions", json=payload)


def _rules(client, survey_id, qid, rules):
    return client.post(
        f"/api/surveys/{survey_id}/questions/{qid}/rules", json={"rules": rules}
    )


def _stored(client, survey_id, qid):
    detail = client.get(f"/api/surveys/{survey_id}").json()
    return next(q for q in detail["questions"] if q["qid"] == qid)


def _single(client, survey_id, title):
    return _add(
        client,
        survey_id,
        {"title": title, "type": "single", "options": [{"text": "甲"}, {"text": "乙"}]},
    ).json()


def _two_singles(client):
    """建一份含两道单选题的草稿，返回 (survey_id, q1, q2)。"""
    survey = _draft(client)
    q1 = _single(client, survey["id"], "第一题")
    q2 = _single(client, survey["id"], "第二题")
    return survey["id"], q1, q2


def test_creator_saves_single_rule_and_reads_back(client):
    """创建者给单选题配置一条跳转规则，读回条件与目标保持不变。"""
    _login(client)
    survey_id, q1, q2 = _two_singles(client)
    oid = q1["options"][0]["oid"]

    response = _rules(
        client,
        survey_id,
        q1["qid"],
        [{"condition": {"op": "is", "oid": oid}, "target": q2["qid"]}],
    )

    assert response.status_code == 200, response.text
    assert response.json()["rules"][0]["target"] == q2["qid"]
    stored = _stored(client, survey_id, q1["qid"])
    assert stored["rules"] == [
        {"rid": stored["rules"][0]["rid"], "condition": {"op": "is", "oid": oid}, "target": q2["qid"]}
    ]


def test_rules_order_is_preserved_and_updatable(client):
    """同一题规则按创建者排列顺序保存；再次保存可调整顺序。"""
    _login(client)
    survey = _draft(client)
    q1 = _single(client, survey["id"], "第一题")
    q2 = _single(client, survey["id"], "第二题")
    q3 = _single(client, survey["id"], "第三题")
    oid = q1["options"][0]["oid"]
    end_rule = {"condition": {"op": "is", "oid": oid}, "target": "end"}
    jump_rule = {"condition": {"op": "is", "oid": oid}, "target": q3["qid"]}

    _rules(client, survey["id"], q1["qid"], [jump_rule, end_rule])
    first = _stored(client, survey["id"], q1["qid"])["rules"]
    assert [r["target"] for r in first] == [q3["qid"], "end"]

    _rules(client, survey["id"], q1["qid"], [end_rule, jump_rule])
    second = _stored(client, survey["id"], q1["qid"])["rules"]
    assert [r["target"] for r in second] == ["end", q3["qid"]]


def test_text_question_rejects_rules(client):
    """文本题不参与条件跳转，配置规则被拒绝。"""
    _login(client)
    survey = _draft(client)
    text = _add(client, survey["id"], {"title": "文本", "type": "text"}).json()

    response = _rules(
        client, survey["id"], text["qid"], [{"condition": {"op": "is"}, "target": "end"}]
    )

    assert response.status_code == 400


def test_unknown_rule_target_is_rejected(client):
    """跳转目标必须是本问卷已有的题目标识或结束。"""
    _login(client)
    survey_id, q1, _ = _two_singles(client)
    oid = q1["options"][0]["oid"]

    response = _rules(
        client,
        survey_id,
        q1["qid"],
        [{"condition": {"op": "is", "oid": oid}, "target": "q_not_exist"}],
    )

    assert response.status_code == 400


def test_non_owner_cannot_save_rules(client):
    """另一账号不能给别人问卷的题目配置规则，统一 404。"""
    _login(client)
    survey_id, q1, q2 = _two_singles(client)
    oid = q1["options"][0]["oid"]
    client.post("/api/logout")
    _login(client, "li", "li-pass-1")

    response = _rules(
        client,
        survey_id,
        q1["qid"],
        [{"condition": {"op": "is", "oid": oid}, "target": q2["qid"]}],
    )

    assert response.status_code == 404


def test_published_survey_rules_are_frozen(client):
    """发布后跳转规则冻结，不能再修改。"""
    _login(client)
    survey_id, q1, q2 = _two_singles(client)
    oid = q1["options"][0]["oid"]
    _rules(
        client,
        survey_id,
        q1["qid"],
        [{"condition": {"op": "is", "oid": oid}, "target": q2["qid"]}],
    )
    client.post(f"/api/surveys/{survey_id}/publish")

    response = _rules(
        client,
        survey_id,
        q1["qid"],
        [{"condition": {"op": "is", "oid": oid}, "target": "end"}],
    )

    assert response.status_code == 409


# ---- 有效路径与校验 ----

REQUIRED_SINGLE = {
    "title": "必答第二题",
    "type": "single",
    "required": True,
    "options": [{"text": "丙"}, {"text": "丁"}],
}


def _publish_with_q2_required(client, q1_payload, rules, allow_multiple=False):
    """建 q1（自定义题型）+ 一道必答单选 q2，给 q1 配规则并发布，返回 (survey_id, q1, q2)。"""
    _login(client)
    survey = _draft(client)
    q1 = _add(client, survey["id"], q1_payload).json()
    q2 = _add(client, survey["id"], REQUIRED_SINGLE).json()
    if rules:
        assert _rules(client, survey["id"], q1["qid"], rules).status_code == 200
    publish = client.post(
        f"/api/surveys/{survey['id']}/publish", json={"allow_multiple": allow_multiple}
    )
    assert publish.status_code == 200, publish.text
    client.post("/api/logout")
    _login(client, "li", "li-pass-1")
    return survey["id"], q1, q2


def _submit(client, survey_id, answers):
    return client.post(
        f"/api/surveys/{survey_id}/responses", json={"answers": answers}
    )


SINGLE_Q1 = {
    "title": "第一题",
    "type": "single",
    "required": True,
    "options": [{"text": "甲"}, {"text": "乙"}],
}


def test_first_matching_rule_wins(client):
    """同一题两条规则都命中时，只有从上到下第一条生效。"""
    _login(client)
    survey = _draft(client)
    q1 = _add(client, survey["id"], SINGLE_Q1).json()
    q2 = _add(client, survey["id"], REQUIRED_SINGLE).json()
    q3 = _add(
        client,
        survey["id"],
        {
            "title": "必答第三题",
            "type": "single",
            "required": True,
            "options": [{"text": "戊"}, {"text": "己"}],
        },
    ).json()
    jia = q1["options"][0]["oid"]
    # 第一条命中跳第三题，第二条命中结束；若严格遵守顺序，第一条生效。
    _rules(
        client,
        survey["id"],
        q1["qid"],
        [
            {"condition": {"op": "is", "oid": jia}, "target": q3["qid"]},
            {"condition": {"op": "is", "oid": jia}, "target": "end"},
        ],
    )
    client.post(f"/api/surveys/{survey['id']}/publish")
    client.post("/api/logout")
    _login(client, "li", "li-pass-1")

    # 若不填第三题则报错，证明第三条题确实在有效路径上（不是被跳过的“结束”）。
    broken = _submit(client, survey["id"], [{"qid": q1["qid"], "oid": jia}])
    assert broken.status_code == 400
    assert q3["qid"] in broken.json()["detail"]["errors"]

    # 第一条生效 → 路径为第一、三题：第二题（必答）被跳过，不填也通过。
    ok = _submit(
        client,
        survey["id"],
        [
            {"qid": q1["qid"], "oid": jia},
            {"qid": q3["qid"], "oid": q3["options"][0]["oid"]},
        ],
    )
    assert ok.status_code == 201, ok.text


def test_no_rule_match_advances_to_next_question(client):
    """无规则命中时进入下一题，下一题的必答照常校验。"""
    _login(client)
    survey = _draft(client)
    q1 = _add(client, survey["id"], SINGLE_Q1).json()
    q2 = _add(client, survey["id"], REQUIRED_SINGLE).json()
    jia = q1["options"][0]["oid"]
    _rules(
        client, survey["id"], q1["qid"],
        [{"condition": {"op": "is", "oid": jia}, "target": "end"}],
    )
    client.post(f"/api/surveys/{survey['id']}/publish")
    client.post("/api/logout")
    _login(client, "li", "li-pass-1")

    # 选“乙”未命中任何规则 → 进入第二题，必答为空被拒绝。
    rejected = _submit(client, survey["id"], [{"qid": q1["qid"], "oid": q1["options"][1]["oid"]}])
    assert rejected.status_code == 400
    assert q2["qid"] in rejected.json()["detail"]["errors"]

    # 选“甲”命中结束 → 第二题被跳过，不填也通过。
    skipped = _submit(client, survey["id"], [{"qid": q1["qid"], "oid": jia}])
    assert skipped.status_code == 201, skipped.text


def test_skipped_required_question_is_not_required(client):
    """被跳过的必答题无需填写。"""
    _login(client)
    survey = _draft(client)
    q1 = _add(client, survey["id"], SINGLE_Q1).json()
    _add(client, survey["id"], REQUIRED_SINGLE)
    _rules(
        client, survey["id"], q1["qid"],
        [{"condition": {"op": "is", "oid": q1["options"][0]["oid"]}, "target": "end"}],
    )
    client.post(f"/api/surveys/{survey['id']}/publish")
    client.post("/api/logout")
    _login(client, "li", "li-pass-1")

    response = _submit(client, survey["id"], [{"qid": q1["qid"], "oid": q1["options"][0]["oid"]}])

    assert response.status_code == 201, response.text


def test_forged_offpath_answer_is_dropped(client):
    """客户端伪造本应被跳过的题的答案，服务端剔除：不校验、不进入答卷。"""
    _login(client)
    survey = _draft(client)
    q1 = _add(client, survey["id"], SINGLE_Q1).json()
    q2 = _add(client, survey["id"], REQUIRED_SINGLE).json()
    q3 = _add(
        client, survey["id"],
        {"title": "第三题", "type": "single", "options": [{"text": "戊"}, {"text": "己"}]},
    ).json()
    jia = q1["options"][0]["oid"]
    _rules(
        client, survey["id"], q1["qid"],
        [{"condition": {"op": "is", "oid": jia}, "target": q3["qid"]}],
    )
    client.post(f"/api/surveys/{survey['id']}/publish")
    client.post("/api/logout")
    _login(client, "li", "li-pass-1")

    # 选“甲”后第二题应被跳过；仍提交第二题的答案，服务端应忽略它。
    response = _submit(
        client,
        survey["id"],
        [
            {"qid": q1["qid"], "oid": jia},
            {"qid": q2["qid"], "oid": q2["options"][0]["oid"]},
            {"qid": q3["qid"], "oid": q3["options"][0]["oid"]},
        ],
    )
    assert response.status_code == 201, response.text

    client.post("/api/logout")
    _login(client)
    stored = client.get(f"/api/surveys/{survey['id']}/responses").json()[0]
    assert [answer["qid"] for answer in stored["answers"]] == [q1["qid"], q3["qid"]]


MULTI_Q1 = {
    "title": "多选第一题",
    "type": "multi",
    "options": [{"text": "甲"}, {"text": "乙"}, {"text": "丙"}],
}


def test_multi_any_rule_matches(client):
    """多选“包含任一”条件：选了任一指定选项即命中。"""
    _login(client)
    survey = _draft(client)
    q1 = _add(client, survey["id"], MULTI_Q1).json()
    _add(client, survey["id"], REQUIRED_SINGLE)
    a, b, c = (o["oid"] for o in q1["options"])
    _rules(
        client, survey["id"], q1["qid"],
        [{"condition": {"op": "any", "oids": [a, b]}, "target": "end"}],
    )
    client.post(f"/api/surveys/{survey['id']}/publish")
    client.post("/api/logout")
    _login(client, "li", "li-pass-1")

    # 只选“丙”（不在条件里）未命中 → 进入必答第二题，为空被拒绝。
    miss = _submit(client, survey["id"], [{"qid": q1["qid"], "oids": [c]}])
    assert miss.status_code == 400
    # 选“甲”（命中任一）→ 结束，第二题被跳过。
    hit = _submit(client, survey["id"], [{"qid": q1["qid"], "oids": [a]}])
    assert hit.status_code == 201, hit.text


def test_multi_all_rule_requires_every_listed_option(client):
    """多选“包含全部”条件：必须选中所有指定选项才命中。"""
    _login(client)
    survey = _draft(client)
    q1 = _add(client, survey["id"], MULTI_Q1).json()
    _add(client, survey["id"], REQUIRED_SINGLE)
    a, b, c = (o["oid"] for o in q1["options"])
    _rules(
        client, survey["id"], q1["qid"],
        [{"condition": {"op": "all", "oids": [a, b]}, "target": "end"}],
    )
    client.post(f"/api/surveys/{survey['id']}/publish")
    client.post("/api/logout")
    _login(client, "li", "li-pass-1")

    # 只选“甲”未满足全部 → 进入必答第二题，为空被拒绝。
    miss = _submit(client, survey["id"], [{"qid": q1["qid"], "oids": [a]}])
    assert miss.status_code == 400
    # 同时选“甲、乙”满足全部 → 结束。
    hit = _submit(client, survey["id"], [{"qid": q1["qid"], "oids": [a, b]}])
    assert hit.status_code == 201, hit.text


NUMBER_Q1 = {"title": "数字第一题", "type": "number"}


def _number_rule_survey(client, condition):
    return _publish_with_q2_required(
        client, NUMBER_Q1, [{"condition": condition, "target": "end"}], allow_multiple=True
    )


def test_number_eq_rule(client):
    """数字“等于”条件：只有相等才命中。"""
    survey_id, q1, _ = _number_rule_survey(client, {"op": "eq", "value": 10})
    assert _submit(client, survey_id, [{"qid": q1["qid"], "number": 11}]).status_code == 400
    assert _submit(client, survey_id, [{"qid": q1["qid"], "number": 10}]).status_code == 201


def test_number_gt_rule(client):
    """数字“大于”条件。"""
    survey_id, q1, _ = _number_rule_survey(client, {"op": "gt", "value": 10})
    assert _submit(client, survey_id, [{"qid": q1["qid"], "number": 10}]).status_code == 400
    assert _submit(client, survey_id, [{"qid": q1["qid"], "number": 11}]).status_code == 201


def test_number_lt_rule(client):
    """数字“小于”条件。"""
    survey_id, q1, _ = _number_rule_survey(client, {"op": "lt", "value": 10})
    assert _submit(client, survey_id, [{"qid": q1["qid"], "number": 10}]).status_code == 400
    assert _submit(client, survey_id, [{"qid": q1["qid"], "number": 9}]).status_code == 201


def test_number_between_rule_includes_both_ends(client):
    """数字“区间”条件含两端。"""
    survey_id, q1, _ = _number_rule_survey(
        client, {"op": "between", "min": 10, "max": 20}
    )
    assert _submit(client, survey_id, [{"qid": q1["qid"], "number": 21}]).status_code == 400
    assert _submit(client, survey_id, [{"qid": q1["qid"], "number": 9}]).status_code == 400
    assert _submit(client, survey_id, [{"qid": q1["qid"], "number": 10}]).status_code == 201
    assert _submit(client, survey_id, [{"qid": q1["qid"], "number": 20}]).status_code == 201


# ---- 发布校验：只可向后跳 ----

def test_publish_accepts_forward_jump_and_end(client):
    """指向后面题目或结束的规则可以通过发布校验。"""
    _login(client)
    survey = _draft(client)
    q1 = _single(client, survey["id"], "第一题")
    q2 = _single(client, survey["id"], "第二题")
    jia, yi = (o["oid"] for o in q1["options"])
    _rules(
        client, survey["id"], q1["qid"],
        [
            {"condition": {"op": "is", "oid": jia}, "target": q2["qid"]},
            {"condition": {"op": "is", "oid": yi}, "target": "end"},
        ],
    )

    response = client.post(f"/api/surveys/{survey['id']}/publish")

    assert response.status_code == 200, response.text


def test_publish_rejects_backward_jump(client):
    """指向前面题目的反向跳转在发布校验时被拒绝。"""
    _login(client)
    survey = _draft(client)
    q1 = _single(client, survey["id"], "第一题")
    q2 = _single(client, survey["id"], "第二题")
    _rules(
        client, survey["id"], q2["qid"],
        [{"condition": {"op": "is", "oid": q2["options"][0]["oid"]}, "target": q1["qid"]}],
    )

    response = client.post(f"/api/surveys/{survey['id']}/publish")

    assert response.status_code == 400


def test_publish_rejects_rule_cycle(client):
    """互相指向形成的循环含反向边，发布校验拒绝。"""
    _login(client)
    survey = _draft(client)
    q1 = _single(client, survey["id"], "第一题")
    q2 = _single(client, survey["id"], "第二题")
    _rules(
        client, survey["id"], q1["qid"],
        [{"condition": {"op": "is", "oid": q1["options"][0]["oid"]}, "target": q2["qid"]}],
    )
    _rules(
        client, survey["id"], q2["qid"],
        [{"condition": {"op": "is", "oid": q2["options"][0]["oid"]}, "target": q1["qid"]}],
    )

    response = client.post(f"/api/surveys/{survey['id']}/publish")

    assert response.status_code == 400


def test_publish_rejects_rule_to_deleted_question(client):
    """规则目标题被删除后，发布校验拒绝。"""
    _login(client)
    survey = _draft(client)
    q1 = _single(client, survey["id"], "第一题")
    q2 = _single(client, survey["id"], "第二题")
    q3 = _single(client, survey["id"], "第三题")
    _rules(
        client, survey["id"], q1["qid"],
        [{"condition": {"op": "is", "oid": q1["options"][0]["oid"]}, "target": q3["qid"]}],
    )
    client.delete(f"/api/surveys/{survey['id']}/questions/{q3['qid']}")

    response = client.post(f"/api/surveys/{survey['id']}/publish")

    assert response.status_code == 400


def test_publish_rejects_rule_to_removed_option(client):
    """跳转条件引用的选项被删除后，发布校验拒绝。"""
    _login(client)
    survey = _draft(client)
    q1 = _single(client, survey["id"], "第一题")
    _single(client, survey["id"], "第二题")
    kept_oid = q1["options"][0]["oid"]
    removed = q1["options"][1]["oid"]
    _rules(
        client, survey["id"], q1["qid"],
        [{"condition": {"op": "is", "oid": removed}, "target": "end"}],
    )
    # 改选项：保留甲、把乙换成丙，使规则引用的乙不复存在。
    client.patch(
        f"/api/surveys/{survey['id']}/questions/{q1['qid']}",
        json={"options": [{"oid": kept_oid, "text": "甲"}, {"text": "丙"}]},
    )

    response = client.post(f"/api/surveys/{survey['id']}/publish")

    assert response.status_code == 400
