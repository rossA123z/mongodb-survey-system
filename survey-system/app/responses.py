"""答卷：多题型问卷的提交校验、保存与创建者查看。

每次成功提交写一条 `responses` 文档，保存问卷、内部填写者身份、有效答案
与提交时间。禁止重复的问卷用唯一索引保证同账号并发提交最多成功一次；
匿名问卷在服务端移除填写者身份后才返回给创建者。

提交校验从第一题重算，不信任客户端：单选校验有效选项，多选校验不重复的
有效选项集合与最少 / 最多 / 固定数量，文本按字符计数校验字数，数字只接收
有限数值并校验范围与整数限制，不把布尔值视作数字。可选题为空时不触发规则。
"""

import math
from datetime import datetime, timezone

from fastapi import HTTPException
from pymongo.database import Database
from pymongo.errors import DuplicateKeyError

from .questions import (
    DEFAULT_QUESTION_TYPE,
    QUESTION_MULTI,
    QUESTION_NUMBER,
    QUESTION_TEXT,
    RULE_TARGET_END,
)
from .surveys import deadline_passed
from .util import field_value, object_id


def load_published(db: Database, survey_id: str) -> dict:
    """按链接取可填写的已发布问卷。

    不存在或仍是草稿一律 404，不泄露存在性；已关闭或已截止返回 409，
    让填写者在打开页面与提交时都得到明确状态提示。
    """
    doc = db["surveys"].find_one({"_id": object_id(survey_id)})
    if doc is None or doc["status"] == "draft":
        raise HTTPException(status_code=404, detail="问卷不存在或未发布")
    if doc["status"] == "closed":
        raise HTTPException(status_code=409, detail="问卷已关闭，不再接受答卷")
    if deadline_passed(doc.get("deadline")):
        raise HTTPException(status_code=409, detail="问卷已截止，不再接受答卷")
    return doc


def survey_for_fill(db: Database, survey_id: str) -> dict:
    """填写页需要的已发布问卷视图（不含创建者管理字段）。"""
    doc = load_published(db, survey_id)
    return {
        "id": str(doc["_id"]),
        "title": doc["title"],
        "description": doc["description"],
        "questions": doc["questions"],
    }


def _label(question: dict, index: int) -> str:
    return f"第 {index + 1} 题“{question['title']}”"


def _validate_single(question, answer, index):
    label = _label(question, index)
    oid = field_value(answer, "oid")
    if not oid:
        if question.get("required"):
            return None, f"{label}为必答题，请选择一项"
        return None, None
    valid_oids = {option["oid"] for option in question["options"]}
    if oid not in valid_oids:
        return None, f"{label}的选项无效，请重新选择"
    return {"qid": question["qid"], "oid": oid}, None


def _validate_multi(question, answer, index):
    label = _label(question, index)
    oids = field_value(answer, "oids")
    if oids is None:
        oids = []
    if not isinstance(oids, list):
        return None, f"{label}的答案格式不正确"
    if not oids:
        if question.get("required"):
            return None, f"{label}为必答题，请至少选择一项"
        return None, None
    valid_oids = {option["oid"] for option in question["options"]}
    if any(not isinstance(oid, str) or oid not in valid_oids for oid in oids):
        return None, f"{label}的选项无效，请重新选择"
    if len(set(oids)) != len(oids):
        return None, f"{label}不能重复选择同一选项"
    low = question.get("min_choices")
    high = question.get("max_choices")
    if low is not None and high is not None and low == high:
        if len(oids) != low:
            return None, f"{label}必须选择 {low} 项"
    else:
        if low is not None and len(oids) < low:
            return None, f"{label}至少选择 {low} 项"
        if high is not None and len(oids) > high:
            return None, f"{label}最多选择 {high} 项"
    return {"qid": question["qid"], "oids": list(oids)}, None


def _validate_text(question, answer, index):
    label = _label(question, index)
    text = field_value(answer, "text")
    if text is None:
        text = ""
    if not isinstance(text, str):
        return None, f"{label}必须是文本"
    if text == "":
        if question.get("required"):
            return None, f"{label}为必答题，请填写"
        return None, None
    length = len(text)
    low = question.get("min_length")
    high = question.get("max_length")
    if low is not None and length < low:
        return None, f"{label}至少 {low} 个字（当前 {length} 个）"
    if high is not None and length > high:
        return None, f"{label}最多 {high} 个字（当前 {length} 个）"
    return {"qid": question["qid"], "text": text}, None


def _validate_number(question, answer, index):
    label = _label(question, index)
    raw = field_value(answer, "number")
    if raw is None or (isinstance(raw, str) and raw.strip() == ""):
        if question.get("required"):
            return None, f"{label}为必答题，请填写数字"
        return None, None
    if isinstance(raw, bool):
        # 布尔不是数字：True / False 不能被当作 1 / 0。
        return None, f"{label}必须是数字"
    if isinstance(raw, str):
        try:
            value = float(raw.strip())
        except ValueError:
            return None, f"{label}必须是数字"
    elif isinstance(raw, (int, float)):
        value = float(raw)
    else:
        return None, f"{label}必须是数字"
    if not math.isfinite(value):
        return None, f"{label}必须是有限数字"
    if question.get("integer") and not value.is_integer():
        return None, f"{label}必须是整数"
    low = question.get("min_value")
    high = question.get("max_value")
    if low is not None and value < low:
        return None, f"{label}不能小于 {low}"
    if high is not None and value > high:
        return None, f"{label}不能大于 {high}"
    stored = int(value) if value.is_integer() else value
    return {"qid": question["qid"], "number": stored}, None


_VALIDATORS = {
    QUESTION_MULTI: _validate_multi,
    QUESTION_TEXT: _validate_text,
    QUESTION_NUMBER: _validate_number,
}


def _number_value(raw):
    """把原始答案解析成有限数值用于条件匹配；不可解析（含布尔、空）返回 None。"""
    if raw is None or isinstance(raw, bool):
        return None
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return None
        try:
            value = float(text)
        except ValueError:
            return None
    elif isinstance(raw, (int, float)):
        value = float(raw)
    else:
        return None
    if not math.isfinite(value):
        return None
    return value


def _condition_matches(condition: dict, answer) -> bool:
    """判断一条跳转条件是否命中；可选题为空时不触发规则（不匹配）。"""
    if not answer:
        return False
    op = condition.get("op")
    if op == "is":
        return field_value(answer, "oid") == condition.get("oid")
    if op in ("any", "all"):
        oids = field_value(answer, "oids")
        if not isinstance(oids, list):
            return False
        chosen = {oid for oid in oids if isinstance(oid, str)}
        wanted = set(condition.get("oids") or [])
        if not wanted:
            return False
        return bool(chosen & wanted) if op == "any" else wanted <= chosen
    value = _number_value(field_value(answer, "number"))
    if value is None:
        return False
    if op == "eq":
        return value == condition.get("value")
    if op == "gt":
        return value > condition.get("value")
    if op == "lt":
        return value < condition.get("value")
    if op == "between":
        low = condition.get("min")
        high = condition.get("max")
        return low is not None and high is not None and low <= value <= high
    return False


def _rule_target(question: dict, answer):
    """按创建者排列顺序返回第一条命中规则的目标；无命中返回 None。"""
    for rule in question.get("rules") or []:
        if _condition_matches(rule.get("condition") or {}, answer):
            return rule.get("target")
    return None


def compute_path(questions: list, collected: dict) -> list:
    """从第一题按规则重算有效路径，返回路径上有序的题目列表。

    服务端不信任客户端声称的路径：只从第一题起推进；命中“结束”即停在确认页，
    无命中进入下一题。发布校验已保证规则只向后跳，这里再加一层防御，
    避免异常数据造成死循环（seen 去重）。
    """
    index_of = {question["qid"]: i for i, question in enumerate(questions)}
    path = []
    seen = set()
    idx = 0
    while 0 <= idx < len(questions) and idx not in seen:
        seen.add(idx)
        question = questions[idx]
        path.append(question)
        target = _rule_target(question, collected.get(question["qid"]))
        if target == RULE_TARGET_END:
            break
        if target is None or target not in index_of:
            idx += 1
        else:
            idx = index_of[target]
    return path


def validate_answers(survey: dict, answers) -> tuple:
    """从第一题重算有效路径与校验，返回 (按 qid 的错误, 有效答案列表)。

    服务端不信任客户端提交的题目范围与路径：先按规则重算有效路径，只校验并
    收下路径上的题目；不在路径上的答案一律忽略、剔除（既不校验也不保存）。
    题目编号按其在问卷中的位置定位，与显示序号一致。可选题为空时不触发规则、
    不计入答案。
    """
    collected = {}
    for answer in answers:
        qid = field_value(answer, "qid")
        if qid:
            collected[qid] = answer

    questions = survey["questions"]
    position = {question["qid"]: i for i, question in enumerate(questions)}
    errors = {}
    valid = []
    for question in compute_path(questions, collected):
        index = position[question["qid"]]
        qtype = question.get("type", DEFAULT_QUESTION_TYPE)
        validator = _VALIDATORS.get(qtype, _validate_single)
        value, error = validator(question, collected.get(question["qid"]), index)
        if error:
            errors[question["qid"]] = error
        elif value is not None:
            valid.append(value)
    return errors, valid


def submit(db: Database, survey_id: str, respondent: str, answers) -> dict:
    """提交答卷：校验有效路径后插入；重复提交按唯一约束拒绝。"""
    survey = load_published(db, survey_id)
    errors, valid = validate_answers(survey, answers)
    if errors:
        raise HTTPException(
            status_code=400, detail={"message": "答卷校验未通过", "errors": errors}
        )

    anonymous = bool(survey["config"].get("anonymous"))
    doc = {
        "survey_id": survey["_id"],
        "respondent": respondent,
        "answers": valid,
        "submitted_at": datetime.now(timezone.utc),
    }
    if not survey["config"].get("allow_multiple"):
        # 仅禁止重复的答卷带此标记，落入唯一部分索引；靠数据库而非先查后写。
        doc["once"] = True
    try:
        result = db["responses"].insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(status_code=409, detail="你已经提交过这份问卷，不能重复提交")
    doc["_id"] = result.inserted_id
    return _serialize_response(doc, anonymous)


def list_responses(db: Database, owner: str, survey_id: str) -> list:
    """创建者查看本问卷答卷；匿名问卷在服务端移除填写者身份。"""
    oid = object_id(survey_id)
    survey = db["surveys"].find_one({"_id": oid, "owner": owner})
    if survey is None:
        raise HTTPException(status_code=404, detail="问卷不存在")
    anonymous = bool(survey["config"].get("anonymous"))
    cursor = db["responses"].find({"survey_id": oid}).sort("submitted_at", 1)
    return [_serialize_response(doc, anonymous) for doc in cursor]


def _serialize_response(doc: dict, anonymous: bool) -> dict:
    out = {
        "id": str(doc["_id"]),
        "answers": [dict(answer) for answer in doc["answers"]],
        "submitted_at": doc["submitted_at"].isoformat(),
    }
    if not anonymous:
        out["respondent"] = doc["respondent"]
    return out
