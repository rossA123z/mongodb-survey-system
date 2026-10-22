"""草稿题目：单选、多选、文本、数字四种题型的稳定标识、校验与增删改序。

题目与选项使用稳定标识（qid / oid），规则与答卷都引用标识而非显示序号；
排序只调整内嵌数组的顺序，不改动标识。仅草稿可编辑，发布后冻结。

每种题型相关的限制字段：

- 单选 single：options（选项）；
- 多选 multi：options + min_choices / max_choices（最少 / 最多选择数；
  固定数量用相同的上下限表达）；
- 文本 text：min_length / max_length（最少 / 最多字数，按字符计数）；
- 数字 number：min_value / max_value（范围，只接受有限数值）+ integer（是否整数）。

草稿阶段只做单个字段的类型规整（把表单字符串转成数字、布尔等），不做跨字段
的语义检查；矛盾或非法的限制配置统一在发布校验时拒绝，见
surveys.validate_for_publish。
"""

import math
import secrets
from datetime import datetime, timezone

from fastapi import HTTPException
from pymongo import ReturnDocument
from pymongo.database import Database

from .util import field_value, object_id

QUESTION_SINGLE = "single"
QUESTION_MULTI = "multi"
QUESTION_TEXT = "text"
QUESTION_NUMBER = "number"

QUESTION_TYPES = (QUESTION_SINGLE, QUESTION_MULTI, QUESTION_TEXT, QUESTION_NUMBER)
# 需要选项的题型；文本与数字题不带选项。
CHOICE_TYPES = (QUESTION_SINGLE, QUESTION_MULTI)
# 可以配置条件跳转的题型；文本题不参与条件跳转。
JUMPABLE_TYPES = (QUESTION_SINGLE, QUESTION_MULTI, QUESTION_NUMBER)
# 跳转目标之一：结束（进入提交确认页，不自动提交）。
RULE_TARGET_END = "end"
# 兼容早期只有单选题的数据：题目缺少 type 时按单选处理。
DEFAULT_QUESTION_TYPE = QUESTION_SINGLE

# 各题型相关的限制字段；不属于该题型的限制字段一律丢弃，避免脏数据混进题目。
LIMIT_FIELDS = {
    QUESTION_MULTI: ("min_choices", "max_choices"),
    QUESTION_TEXT: ("min_length", "max_length"),
    QUESTION_NUMBER: ("min_value", "max_value", "integer"),
}
ALL_LIMIT_KEYS = tuple(
    key for keys in LIMIT_FIELDS.values() for key in keys
)

# 选择题在草稿阶段至少需要一个选项；发布时要求至少两个。
MIN_OPTIONS_AT_EDIT = 1
MIN_OPTIONS_TO_PUBLISH = 2

_TYPE_LABELS = {
    QUESTION_SINGLE: "单选题",
    QUESTION_MULTI: "多选题",
    QUESTION_TEXT: "文本题",
    QUESTION_NUMBER: "数字题",
}


def type_label(qtype: str) -> str:
    return _TYPE_LABELS.get(qtype, "题目")


def _new_qid() -> str:
    return "q_" + secrets.token_hex(8)


def _new_oid() -> str:
    return "o_" + secrets.token_hex(8)


def _new_rid() -> str:
    return "r_" + secrets.token_hex(8)


def load_owned_draft(db: Database, owner: str, survey_id: str) -> dict:
    """读取属于该账号的草稿；不存在或不归本人统一 404，非草稿 409。"""
    doc = db["surveys"].find_one({"_id": object_id(survey_id), "owner": owner})
    if doc is None:
        raise HTTPException(status_code=404, detail="问卷不存在")
    if doc["status"] != "draft":
        raise HTTPException(
            status_code=409, detail="问卷已发布，题目与选项已冻结，不能修改"
        )
    return doc


def _clean_text(value) -> str:
    return (value or "").strip()


def _normalize_options(raw_options, existing_options) -> list:
    """把输入选项规整为 {oid, text}。

    传入 {oid, text} 时沿用已知 oid（保持稳定），未给 oid 时新分配；
    选项文本为空则丢弃（草稿页留白输入框不算有效选项）。
    """
    known = {option["oid"] for option in existing_options}
    result = []
    seen = set()
    for entry in raw_options:
        if isinstance(entry, str):
            text, oid = entry, None
        else:
            text = field_value(entry, "text")
            oid = field_value(entry, "oid")
        text = _clean_text(text)
        if not text:
            continue
        if oid not in known or oid in seen:
            oid = _new_oid()
        seen.add(oid)
        result.append({"oid": oid, "text": text})
    return result


def _coerce_int(value, label: str):
    """把整型限制转成整数；空值返回 None，非整数则 400。"""
    if value is None:
        return None
    if isinstance(value, bool):
        raise HTTPException(status_code=400, detail=f"{label}必须是整数")
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value) or not value.is_integer():
            raise HTTPException(status_code=400, detail=f"{label}必须是整数")
        return int(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return int(text)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"{label}必须是整数")
    raise HTTPException(status_code=400, detail=f"{label}必须是整数")


def _coerce_number(value, label: str):
    """把数值限制转成有限数值；空值返回 None，布尔或非有限值则 400。"""
    if value is None:
        return None
    if isinstance(value, bool):
        # 布尔不是数字：True / False 不能被当作 1 / 0。
        raise HTTPException(status_code=400, detail=f"{label}必须是数字")
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            number = float(text)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"{label}必须是数字")
    elif isinstance(value, (int, float)):
        number = float(value)
    else:
        raise HTTPException(status_code=400, detail=f"{label}必须是数字")
    if not math.isfinite(number):
        raise HTTPException(status_code=400, detail=f"{label}必须是有限数字")
    return int(number) if number.is_integer() else number


def _coerce_bool(value) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "on", "yes")
    return bool(value)


def _clean_limits(qtype: str, source) -> dict:
    """按题型取出相关限制字段并规整类型；无关字段丢弃。"""
    cleaned = {}
    if qtype == QUESTION_MULTI:
        cleaned["min_choices"] = _coerce_int(field_value(source, "min_choices"), "最少选择数")
        cleaned["max_choices"] = _coerce_int(field_value(source, "max_choices"), "最多选择数")
    elif qtype == QUESTION_TEXT:
        cleaned["min_length"] = _coerce_int(field_value(source, "min_length"), "最少字数")
        cleaned["max_length"] = _coerce_int(field_value(source, "max_length"), "最多字数")
    elif qtype == QUESTION_NUMBER:
        cleaned["min_value"] = _coerce_number(field_value(source, "min_value"), "最小值")
        cleaned["max_value"] = _coerce_number(field_value(source, "max_value"), "最大值")
        cleaned["integer"] = _coerce_bool(field_value(source, "integer"))
    return cleaned


def current_limits(question: dict) -> dict:
    """取出一道题现有的全部限制字段（缺失为 None）。"""
    return {key: question.get(key) for key in ALL_LIMIT_KEYS}


def _clean_str(value):
    return value.strip() if isinstance(value, str) else value


def _normalize_condition(question: dict, raw) -> dict:
    """按题型规整一条跳转条件；结构非法或引用不存在的选项一律 400。

    条件形态（引用稳定标识，不用显示序号）：

    - 单选：`{"op": "is", "oid": <oid>}`；
    - 多选：`{"op": "any" | "all", "oids": [...]}`（包含任一 / 包含全部）；
    - 数字：`{"op": "eq" | "gt" | "lt", "value": <数值>}` 或
      `{"op": "between", "min": <数值>, "max": <数值>}`（含两端的区间）。
    """
    qtype = question.get("type", DEFAULT_QUESTION_TYPE)
    op = _clean_str(field_value(raw, "op"))
    if qtype == QUESTION_SINGLE:
        oid = _clean_str(field_value(raw, "oid"))
        if not oid:
            raise HTTPException(status_code=400, detail="单选跳转条件请选择触发选项")
        if oid not in {option["oid"] for option in question["options"]}:
            raise HTTPException(status_code=400, detail="单选跳转条件引用了不存在的选项")
        return {"op": "is", "oid": oid}
    if qtype == QUESTION_MULTI:
        if op not in ("any", "all"):
            raise HTTPException(
                status_code=400, detail="多选跳转条件只能是包含任一或包含全部"
            )
        oids = field_value(raw, "oids") or []
        if isinstance(oids, str):
            oids = [oids]
        oids = [oid.strip() for oid in oids if isinstance(oid, str) and oid.strip()]
        if not oids:
            raise HTTPException(
                status_code=400, detail="多选跳转条件请至少选择一个选项"
            )
        valid = {option["oid"] for option in question["options"]}
        if any(oid not in valid for oid in oids):
            raise HTTPException(
                status_code=400, detail="多选跳转条件引用了不存在的选项"
            )
        return {"op": op, "oids": list(dict.fromkeys(oids))}
    if qtype == QUESTION_NUMBER:
        if op not in ("eq", "gt", "lt", "between"):
            raise HTTPException(
                status_code=400, detail="数字跳转条件只能是等于、大于、小于或区间"
            )
        if op == "between":
            low = _coerce_number(field_value(raw, "min"), "区间下限")
            high = _coerce_number(field_value(raw, "max"), "区间上限")
            if low is None or high is None:
                raise HTTPException(
                    status_code=400, detail="区间跳转条件请填写下限与上限"
                )
            if low > high:
                raise HTTPException(status_code=400, detail="区间下限不能大于上限")
            return {"op": "between", "min": low, "max": high}
        value = _coerce_number(field_value(raw, "value"), "比较值")
        if value is None:
            raise HTTPException(status_code=400, detail="数字跳转条件请填写比较值")
        return {"op": op, "value": value}
    raise HTTPException(status_code=400, detail="该题型不支持跳转规则")


def _normalize_rules(question: dict, questions: list, raw_rules) -> list:
    """整题替换式规整规则列表；顺序即数组顺序。

    目标必须是本问卷已有题目的 qid 或 "end"。是否“只可跳向后面的题目”留到
    发布校验时判定，因为题目顺序在草稿阶段仍可调整。
    """
    raw_rules = list(raw_rules or [])
    qtype = question.get("type", DEFAULT_QUESTION_TYPE)
    if qtype not in JUMPABLE_TYPES:
        if raw_rules:
            raise HTTPException(
                status_code=400, detail="只有单选、多选和数字题可以配置跳转规则"
            )
        return []
    known_qids = {item["qid"] for item in questions}
    known_rids = {rule.get("rid") for rule in (question.get("rules") or [])}
    rules = []
    for entry in raw_rules:
        target = _clean_str(field_value(entry, "target"))
        if not target:
            raise HTTPException(
                status_code=400, detail="每条跳转规则都要指定目标题目或结束"
            )
        if target != RULE_TARGET_END and target not in known_qids:
            raise HTTPException(status_code=400, detail="跳转目标不存在")
        condition = _normalize_condition(question, field_value(entry, "condition"))
        rid = field_value(entry, "rid")
        if not (isinstance(rid, str) and rid in known_rids):
            rid = _new_rid()
        rules.append({"rid": rid, "condition": condition, "target": target})
    return rules


def _validate_qtype(qtype) -> str:
    if qtype not in QUESTION_TYPES:
        raise HTTPException(status_code=400, detail="未知题型")
    return qtype


def _build_question(title, qtype, required, raw_options, existing_options, limits) -> dict:
    _validate_qtype(qtype)
    title = _clean_text(title)
    if not title:
        raise HTTPException(status_code=400, detail="题目题干不能为空")
    if qtype in CHOICE_TYPES:
        options = _normalize_options(raw_options, existing_options)
        if len(options) < MIN_OPTIONS_AT_EDIT:
            raise HTTPException(
                status_code=400, detail=f"{type_label(qtype)}至少需要一个选项"
            )
    else:
        options = []
    question = {
        "qid": _new_qid(),
        "type": qtype,
        "title": title,
        "required": bool(required),
        "options": options,
        "rules": [],
    }
    question.update(_clean_limits(qtype, limits))
    return question


def _save(db: Database, owner: str, survey_id: str, questions: list) -> dict:
    """把题目列表写回草稿；带状态守卫，避免与发布并发时写坏。"""
    doc = db["surveys"].find_one_and_update(
        {"_id": object_id(survey_id), "owner": owner, "status": "draft"},
        {"$set": {"questions": questions, "updated_at": datetime.now(timezone.utc)}},
        return_document=ReturnDocument.AFTER,
    )
    if doc is None:
        raise HTTPException(
            status_code=409, detail="问卷已发布，题目与选项已冻结，不能修改"
        )
    return doc


def add_question(
    db: Database, owner: str, survey_id: str, title, qtype, required, options, limits=None
) -> dict:
    """添加一道题；qtype 缺省按单选，limits 为 dict（缺失的键视为未设置）。"""
    doc = load_owned_draft(db, owner, survey_id)
    question = _build_question(title, qtype, required, options, [], limits)
    questions = list(doc["questions"]) + [question]
    _save(db, owner, survey_id, questions)
    return question


def update_question(
    db: Database,
    owner: str,
    survey_id: str,
    qid: str,
    title,
    required,
    options,
    limits=None,
) -> dict:
    """修改题目；题型创建后保持不变，限制字段按提供的键局部更新。

    limits 为 None 时保留原有限制；给 dict 时只覆盖其中出现的键，
    其余限制沿用当前值，因此提交空串可将某项限制清空为 None。
    """
    doc = load_owned_draft(db, owner, survey_id)
    questions = list(doc["questions"])
    index = next((i for i, item in enumerate(questions) if item["qid"] == qid), None)
    if index is None:
        raise HTTPException(status_code=404, detail="题目不存在")
    current = questions[index]
    qtype = current.get("type", DEFAULT_QUESTION_TYPE)

    updated = {
        "qid": current["qid"],
        "type": qtype,
        "title": _clean_text(title) if title is not None else current["title"],
        "required": current["required"] if required is None else bool(required),
        # 跳转规则随题目保留；其目标与顺序是否合法留到发布校验判定。
        "rules": list(current.get("rules", [])),
    }
    if qtype in CHOICE_TYPES:
        updated["options"] = (
            current["options"]
            if options is None
            else _normalize_options(options, current["options"])
        )
    else:
        updated["options"] = []

    if not updated["title"]:
        raise HTTPException(status_code=400, detail="题目题干不能为空")
    if qtype in CHOICE_TYPES and len(updated["options"]) < MIN_OPTIONS_AT_EDIT:
        raise HTTPException(
            status_code=400, detail=f"{type_label(qtype)}至少需要一个选项"
        )

    merged = current_limits(current)
    if limits:
        merged.update({key: value for key, value in limits.items() if key in ALL_LIMIT_KEYS})
    updated.update(_clean_limits(qtype, merged))

    questions[index] = updated
    _save(db, owner, survey_id, questions)
    return updated


def delete_question(db: Database, owner: str, survey_id: str, qid: str) -> None:
    doc = load_owned_draft(db, owner, survey_id)
    questions = [item for item in doc["questions"] if item["qid"] != qid]
    if len(questions) == len(doc["questions"]):
        raise HTTPException(status_code=404, detail="题目不存在")
    _save(db, owner, survey_id, questions)


def move_question(db: Database, owner: str, survey_id: str, qid: str, direction: str) -> dict:
    doc = load_owned_draft(db, owner, survey_id)
    questions = list(doc["questions"])
    if direction not in ("up", "down"):
        raise HTTPException(status_code=400, detail="排序方向只能是 up 或 down")
    index = next((i for i, item in enumerate(questions) if item["qid"] == qid), None)
    if index is None:
        raise HTTPException(status_code=404, detail="题目不存在")
    target = index - 1 if direction == "up" else index + 1
    if 0 <= target < len(questions):
        questions[index], questions[target] = questions[target], questions[index]
    _save(db, owner, survey_id, questions)
    return {"questions": questions}


def set_rules(db: Database, owner: str, survey_id: str, qid: str, rules) -> dict:
    """整题替换一道题的跳转规则，返回该题；顺序即传入数组顺序。"""
    doc = load_owned_draft(db, owner, survey_id)
    questions = list(doc["questions"])
    index = next((i for i, item in enumerate(questions) if item["qid"] == qid), None)
    if index is None:
        raise HTTPException(status_code=404, detail="题目不存在")
    question = questions[index]
    question["rules"] = _normalize_rules(question, questions, rules)
    questions[index] = question
    _save(db, owner, survey_id, questions)
    return question
