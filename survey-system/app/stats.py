"""问卷统计：按成功保存的答卷与有效答案汇总，供创建者查看。

统计口径（与规格一致，逐条照做）：

- 只统计成功保存的 `responses` 及其**有效答案**；被跳过或未填的可选题不计为回答。
- 空文本不计为文本题回答；**数字题无回答时平均值为空**（不显示为零）。
- 路径外答案在提交时已按有效路径剔除，因此不会进入统计。
- 允许重复填写时，每份答卷分别计数（按答卷文档逐份累加）。
- 统计只对问卷创建者开放；输出不含填写者身份（匿名与非匿名都不输出 respondent）。
"""

from fastapi import HTTPException
from pymongo.database import Database

from .questions import (
    DEFAULT_QUESTION_TYPE,
    QUESTION_MULTI,
    QUESTION_NUMBER,
    QUESTION_SINGLE,
    QUESTION_TEXT,
)
from .util import object_id


def _owned_survey(db: Database, owner: str, survey_id: str) -> dict:
    """读取属于该账号的问卷；不存在或不归本人统一 404，不泄露存在性。"""
    doc = db["surveys"].find_one({"_id": object_id(survey_id), "owner": owner})
    if doc is None:
        raise HTTPException(status_code=404, detail="问卷不存在")
    return doc


def _responses(db: Database, survey: dict) -> list:
    """该问卷已成功保存的答卷（每份一个文档）。"""
    return list(db["responses"].find({"survey_id": survey["_id"]}))


def _answers_by_question(questions: list, responses: list) -> dict:
    """把每份答卷的有效答案按 qid 归拢；路径外答案已在提交时剔除。"""
    grouped = {question["qid"]: [] for question in questions}
    for response in responses:
        for answer in response["answers"]:
            qid = answer.get("qid")
            if qid in grouped:
                grouped[qid].append(answer)
    return grouped


def _single_stat(question: dict, answers: list) -> dict:
    options = question.get("options") or []
    counts = {option["oid"]: 0 for option in options}
    for answer in answers:
        oid = answer.get("oid")
        if oid in counts:
            counts[oid] += 1
    return {
        "answered_total": sum(counts.values()),
        "options": [
            {"oid": option["oid"], "text": option["text"], "count": counts[option["oid"]]}
            for option in options
        ],
    }


def _multi_stat(question: dict, answers: list) -> dict:
    options = question.get("options") or []
    counts = {option["oid"]: 0 for option in options}
    answered_total = 0
    for answer in answers:
        oids = answer.get("oids") or []
        # 一份答卷里同一选项只计一次（多选答案本身就是不重复集合）。
        selected = [oid for oid in dict.fromkeys(oids) if oid in counts]
        if selected:
            answered_total += 1
        for oid in selected:
            counts[oid] += 1
    return {
        "answered_total": answered_total,
        "options": [
            {"oid": option["oid"], "text": option["text"], "count": counts[option["oid"]]}
            for option in options
        ],
    }


def _text_stat(answers: list) -> dict:
    # 空文本不计为回答（提交时已剔除，这里再兜底过滤）。
    texts = [answer.get("text") for answer in answers if answer.get("text")]
    return {"answered_total": len(texts), "texts": texts}


def _number_stat(answers: list) -> dict:
    values = [answer.get("number") for answer in answers if answer.get("number") is not None]
    # 无回答时平均值为空，不显示为零。
    average = sum(values) / len(values) if values else None
    return {"answered_total": len(values), "average": average}


def _question_stat(question: dict, answers: list) -> dict:
    qtype = question.get("type", DEFAULT_QUESTION_TYPE)
    stat = {"qid": question["qid"], "type": qtype, "title": question["title"]}
    if qtype == QUESTION_MULTI:
        stat.update(_multi_stat(question, answers))
    elif qtype == QUESTION_TEXT:
        stat.update(_text_stat(answers))
    elif qtype == QUESTION_NUMBER:
        stat.update(_number_stat(answers))
    else:
        stat.update(_single_stat(question, answers))
    return stat


def survey_stats(db: Database, owner: str, survey_id: str) -> dict:
    """问卷级统计：整体汇总与逐题统计。"""
    survey = _owned_survey(db, owner, survey_id)
    responses = _responses(db, survey)
    grouped = _answers_by_question(survey["questions"], responses)
    return {
        "survey_id": survey_id,
        "title": survey["title"],
        "response_count": len(responses),
        "questions": [
            _question_stat(question, grouped[question["qid"]])
            for question in survey["questions"]
        ],
    }


def question_stats(db: Database, owner: str, survey_id: str, qid: str) -> dict:
    """题目级统计：指定一道题的统计。"""
    survey = _owned_survey(db, owner, survey_id)
    question = next((item for item in survey["questions"] if item["qid"] == qid), None)
    if question is None:
        raise HTTPException(status_code=404, detail="题目不存在")
    responses = _responses(db, survey)
    grouped = _answers_by_question(survey["questions"], responses)
    return _question_stat(question, grouped[qid])
