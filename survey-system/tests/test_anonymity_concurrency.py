"""工单 02：匿名脱敏与并发防重复。

并发用真实的多线程 + 真实 MongoDB 验证：两个提交同时发出，靠数据库唯一
约束只成功一次；多账号同时提交答卷不丢失。不使用 mock 代替数据库。
"""

import threading
from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient

from app.main import create_app

from conftest import register_login as _register_login


def _published_survey(client, anonymous=False, allow_multiple=False):
    _register_login(client, "zhang", "secret123")
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
        "answers": [
            {"qid": question["qid"], "oid": question["options"][0]["oid"]}
        ]
    }


def test_anonymous_survey_hides_respondent_from_creator(client):
    """匿名问卷下，创建者看不到填写者身份（服务端已移除）。"""
    survey_id, question = _published_survey(client, anonymous=True)
    _register_login(client, "li", "li-pass-1")
    client.post(f"/api/surveys/{survey_id}/responses", json=_answer(question))
    client.post("/api/logout")

    _register_login(client, "zhang", "secret123")
    listing = client.get(f"/api/surveys/{survey_id}/responses")

    assert listing.status_code == 200
    assert len(listing.json()) == 1
    saved = listing.json()[0]
    assert "respondent" not in saved
    assert saved["answers"] == _answer(question)["answers"]


def test_anonymous_survey_still_limits_repeat(client):
    """匿名问卷仍能识别账号以限制重复填写。"""
    survey_id, question = _published_survey(client, anonymous=True)
    _register_login(client, "li", "li-pass-1")

    first = client.post(f"/api/surveys/{survey_id}/responses", json=_answer(question))
    second = client.post(f"/api/surveys/{survey_id}/responses", json=_answer(question))

    assert first.status_code == 201
    assert second.status_code == 409


def test_concurrent_submits_only_one_succeeds(client):
    """同一账号两个请求并发提交时，只有一个成功（真实并发 + 真实数据库）。"""
    survey_id, question = _published_survey(client)
    answer = _answer(question)
    client.post("/api/register", json={"username": "li", "password": "li-pass-1"})

    app = create_app()  # 主线程构建一次，避免多线程同时生成 schema
    barrier = threading.Barrier(2)
    statuses = []
    lock = threading.Lock()

    def _submit():
        with TestClient(app) as worker:
            worker.post("/api/login", json={"username": "li", "password": "li-pass-1"})
            barrier.wait()  # 两个线程对齐后再同时发出提交
            response = worker.post(
                f"/api/surveys/{survey_id}/responses", json=answer
            )
            with lock:
                statuses.append(response.status_code)

    threads = [threading.Thread(target=_submit) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(statuses) == [201, 409]

    _register_login(client, "zhang", "secret123")
    assert len(client.get(f"/api/surveys/{survey_id}/responses").json()) == 1


def test_concurrent_multi_account_submissions_all_saved(client):
    """多个账号同时提交时，每份答卷都被保存，不丢失。"""
    survey_id, question = _published_survey(client)
    answer = _answer(question)
    respondents = [f"user{i}" for i in range(5)]
    for username in respondents:
        client.post(
            "/api/register", json={"username": username, "password": "pw-123456"}
        )

    app = create_app()  # 主线程构建一次，避免多线程同时生成 schema
    barrier = threading.Barrier(len(respondents))

    def _submit(username):
        with TestClient(app) as worker:
            worker.post("/api/login", json={"username": username, "password": "pw-123456"})
            barrier.wait()
            return worker.post(
                f"/api/surveys/{survey_id}/responses", json=answer
            ).status_code

    with ThreadPoolExecutor(max_workers=len(respondents)) as pool:
        statuses = list(pool.map(_submit, respondents))

    assert statuses == [201] * len(respondents)

    _register_login(client, "zhang", "secret123")
    saved = client.get(f"/api/surveys/{survey_id}/responses").json()
    assert len(saved) == len(respondents)
    assert sorted(item["respondent"] for item in saved) == sorted(respondents)
