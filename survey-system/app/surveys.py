"""问卷管理数据：草稿的创建、读取、修改与发布，按创建者隔离。

题目与规则以有序列表内嵌，供后续工单扩展。发布后标题、说明、题目、选项
与配置（匿名 / 是否允许重复）都冻结；仅草稿可编辑。
"""

from datetime import datetime, timezone
from typing import Optional

from fastapi import HTTPException
from pymongo import ReturnDocument
from pymongo.database import Database

from .questions import (
    CHOICE_TYPES,
    DEFAULT_QUESTION_TYPE,
    JUMPABLE_TYPES,
    MIN_OPTIONS_TO_PUBLISH,
    QUESTION_MULTI,
    QUESTION_NUMBER,
    QUESTION_TEXT,
    RULE_TARGET_END,
)
from .util import object_id

DEFAULT_CONFIG = {"anonymous": False, "allow_multiple": False}


def _validate_limits(qtype: str, question: dict, options: list, position: int) -> None:
    """按题型检查限制配置是否自相矛盾或非法；非法则 400。

    草稿阶段只规整单个字段的类型，跨字段一致性留到发布时统一拒绝。
    """

    def fail(message: str) -> None:
        raise HTTPException(status_code=400, detail=f"第 {position} 题{message}，无法发布")

    if qtype == QUESTION_MULTI:
        low = question.get("min_choices")
        high = question.get("max_choices")
        if low is not None and low < 0:
            fail("最少选择数不能为负数")
        if high is not None and high < 0:
            fail("最多选择数不能为负数")
        if low is not None and high is not None and low > high:
            fail("最少选择数不能大于最多选择数")
        if low is not None and low > len(options):
            fail("最少选择数不能超过选项总数")
    elif qtype == QUESTION_TEXT:
        low = question.get("min_length")
        high = question.get("max_length")
        if low is not None and low < 0:
            fail("最少字数不能为负数")
        if high is not None and high < 0:
            fail("最多字数不能为负数")
        if low is not None and high is not None and low > high:
            fail("最少字数不能大于最多字数")
    elif qtype == QUESTION_NUMBER:
        low = question.get("min_value")
        high = question.get("max_value")
        if low is not None and high is not None and low > high:
            fail("最小值不能大于最大值")


def _validate_rules(questions: list, index_of: dict, question: dict, position: int) -> None:
    """校验一道题的跳转规则：目标必须靠后或结束，条件引用必须有效。

    规则只可跳向后面的题目或结束，因此“形成循环”必然含一条反向边，会被
    同一条“只能向后”的判断拒绝，不需要单独做环检测。规则目标题被删除、
    条件引用的选项被删除，都在这里一并拒绝。
    """

    def fail(message: str) -> None:
        raise HTTPException(status_code=400, detail=f"第 {position} 题{message}，无法发布")

    rules = question.get("rules") or []
    if not rules:
        return
    qtype = question.get("type", DEFAULT_QUESTION_TYPE)
    if qtype not in JUMPABLE_TYPES:
        fail("的题型不支持跳转规则")
    source_index = index_of[question["qid"]]
    option_oids = {option["oid"] for option in question.get("options") or []}
    for rule in rules:
        target = rule.get("target")
        if target != RULE_TARGET_END:
            if target not in index_of:
                fail("的跳转目标不存在")
            if index_of[target] <= source_index:
                fail("的跳转规则只能指向后面的题目或结束")
        condition = rule.get("condition") or {}
        op = condition.get("op")
        if op == "is":
            if condition.get("oid") not in option_oids:
                fail("的跳转条件引用了不存在的选项")
        elif op in ("any", "all"):
            oids = condition.get("oids") or []
            if not oids or any(oid not in option_oids for oid in oids):
                fail("的跳转条件引用了不存在的选项")
        elif op in ("eq", "gt", "lt"):
            if condition.get("value") is None:
                fail("的跳转条件缺少比较值")
        elif op == "between":
            if condition.get("min") is None or condition.get("max") is None:
                fail("的跳转条件缺少区间端点")
        else:
            fail("的跳转条件无效")


def validate_for_publish(questions: list) -> None:
    """发布前校验：至少一道题，每题有题干、选项数符合题型且限制配置合法。

    此外校验跳转规则：目标必须是后面的题目或结束（因此禁止回跳与循环），
    条件引用的选项必须仍存在。
    """
    if not questions:
        raise HTTPException(status_code=400, detail="发布前至少需要一道题")
    index_of = {question["qid"]: i for i, question in enumerate(questions)}
    for index, question in enumerate(questions):
        position = index + 1
        if not (question.get("title") or "").strip():
            raise HTTPException(
                status_code=400, detail=f"第 {position} 题缺少题干，无法发布"
            )
        qtype = question.get("type", DEFAULT_QUESTION_TYPE)
        options = question.get("options") or []
        if qtype in CHOICE_TYPES and len(options) < MIN_OPTIONS_TO_PUBLISH:
            raise HTTPException(
                status_code=400,
                detail=f"第 {position} 题至少需要两个选项，无法发布",
            )
        _validate_limits(qtype, question, options, position)
        _validate_rules(questions, index_of, question, position)


def parse_deadline(value) -> Optional[datetime]:
    """把接口/表单传入的截止时间规整为 UTC 感知时间；空值表示不设截止。

    接受带时区偏移或 'Z' 的 ISO 8601 字符串，也接受已解析的 datetime。
    无时区信息的值按 UTC 解释；带偏移的值折算为 UTC，从而统一按 UTC 存储。
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        text = str(value).strip()
        if not text:
            return None
        try:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            raise HTTPException(status_code=400, detail="截止时间格式无效")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def deadline_passed(deadline: Optional[datetime], now: Optional[datetime] = None) -> bool:
    """截止时间是否已到；now >= deadline（含相等）视为不可提交。"""
    if deadline is None:
        return False
    if now is None:
        now = datetime.now(timezone.utc)
    return now >= deadline


def _iso(value) -> Optional[str]:
    return value.isoformat() if value is not None else None


def _serialize(doc: dict) -> dict:
    """管理视图：含创建者可见字段，不含内部凭据。"""
    return {
        "id": str(doc["_id"]),
        "title": doc["title"],
        "description": doc["description"],
        "status": doc["status"],
        "config": doc["config"],
        "questions": doc["questions"],
        "deadline": _iso(doc.get("deadline")),
        "expired": deadline_passed(doc.get("deadline")),
    }


def create_draft(db: Database, owner: str, title: str, description: str) -> dict:
    now = datetime.now(timezone.utc)
    doc = {
        "owner": owner,
        "title": title,
        "description": description,
        "status": "draft",
        "config": dict(DEFAULT_CONFIG),
        "questions": [],
        "created_at": now,
        "updated_at": now,
    }
    result = db["surveys"].insert_one(doc)
    doc["_id"] = result.inserted_id
    return _serialize(doc)


def list_own(db: Database, owner: str) -> list:
    """列出该账号自己的问卷，按创建时间升序。"""
    cursor = db["surveys"].find({"owner": owner}).sort("created_at", 1)
    return [_serialize(doc) for doc in cursor]


def get_owned(db: Database, owner: str, survey_id: str) -> dict:
    """读取属于该账号的问卷管理详情。

    不存在或不归当前账号时统一 404，避免通过状态码泄露问卷是否存在。
    """
    doc = db["surveys"].find_one({"_id": object_id(survey_id), "owner": owner})
    if doc is None:
        raise HTTPException(status_code=404, detail="问卷不存在")
    return _serialize(doc)


def update_owned(
    db: Database,
    owner: str,
    survey_id: str,
    title: Optional[str] = None,
    description: Optional[str] = None,
    config: Optional[dict] = None,
) -> dict:
    """修改属于该账号的草稿；仅更新显式提供的字段。

    非本人 404；已发布 409（发布后标题、说明与匿名/重复配置都冻结）。
    """
    oid = object_id(survey_id)
    doc = db["surveys"].find_one({"_id": oid, "owner": owner})
    if doc is None:
        raise HTTPException(status_code=404, detail="问卷不存在")
    if doc["status"] != "draft":
        raise HTTPException(status_code=409, detail="问卷已发布，内容已冻结")

    updates = {"updated_at": datetime.now(timezone.utc)}
    if title is not None:
        updates["title"] = title
    if description is not None:
        updates["description"] = description
    if config is not None:
        merged = dict(doc["config"])
        merged.update({key: value for key, value in config.items() if value is not None})
        updates["config"] = merged

    updated = db["surveys"].find_one_and_update(
        {"_id": oid, "owner": owner, "status": "draft"},
        {"$set": updates},
        return_document=ReturnDocument.AFTER,
    )
    if updated is None:
        raise HTTPException(status_code=409, detail="问卷已发布，内容已冻结")
    return _serialize(updated)


def publish(
    db: Database,
    owner: str,
    survey_id: str,
    anonymous: Optional[bool] = None,
    allow_multiple: Optional[bool] = None,
) -> dict:
    """发布问卷：校验题目，冻结内容，返回可分发链接。

    非本人 404；已发布 409；校验不通过 400。发布时可一并确定匿名与重复策略。
    """
    oid = object_id(survey_id)
    doc = db["surveys"].find_one({"_id": oid, "owner": owner})
    if doc is None:
        raise HTTPException(status_code=404, detail="问卷不存在")
    if doc["status"] != "draft":
        raise HTTPException(status_code=409, detail="问卷已发布，不能重复发布")

    validate_for_publish(doc["questions"])

    config = dict(doc["config"])
    if anonymous is not None:
        config["anonymous"] = anonymous
    if allow_multiple is not None:
        config["allow_multiple"] = allow_multiple

    now = datetime.now(timezone.utc)
    updated = db["surveys"].find_one_and_update(
        {"_id": oid, "owner": owner, "status": "draft"},
        {
            "$set": {
                "status": "published",
                "config": config,
                "published_at": now,
                "updated_at": now,
            }
        },
        return_document=ReturnDocument.AFTER,
    )
    if updated is None:
        raise HTTPException(status_code=409, detail="问卷已发布，不能重复发布")
    result = _serialize(updated)
    result["share_url"] = f"/s/{survey_id}"
    return result


def set_deadline(db: Database, owner: str, survey_id: str, value) -> dict:
    """设置、调整或清除问卷的截止时间（统一存 UTC）。

    非本人或不存在 404；已关闭 409（关闭为终态）；格式非法 400。
    草稿与已发布问卷都可设置，草稿沿用既有冻结规则、仅此字段例外。
    """
    oid = object_id(survey_id)
    doc = db["surveys"].find_one({"_id": oid, "owner": owner})
    if doc is None:
        raise HTTPException(status_code=404, detail="问卷不存在")
    if doc["status"] == "closed":
        raise HTTPException(status_code=409, detail="问卷已关闭，不能再调整截止时间")

    deadline = parse_deadline(value)
    now = datetime.now(timezone.utc)
    updated = db["surveys"].find_one_and_update(
        {"_id": oid, "owner": owner, "status": {"$in": ["draft", "published"]}},
        {"$set": {"deadline": deadline, "updated_at": now}},
        return_document=ReturnDocument.AFTER,
    )
    if updated is None:
        raise HTTPException(status_code=409, detail="问卷已关闭，不能再调整截止时间")
    return _serialize(updated)


def close_survey(db: Database, owner: str, survey_id: str) -> dict:
    """手动关闭问卷。关闭为终态，第一阶段不提供重开。

    非本人或不存在 404；尚未发布或已关闭 409。状态守卫写在更新条件里，
    避免与并发操作争用后写坏状态。
    """
    oid = object_id(survey_id)
    doc = db["surveys"].find_one({"_id": oid, "owner": owner})
    if doc is None:
        raise HTTPException(status_code=404, detail="问卷不存在")
    if doc["status"] == "closed":
        raise HTTPException(status_code=409, detail="问卷已关闭，无需重复关闭")
    if doc["status"] != "published":
        raise HTTPException(status_code=409, detail="问卷尚未发布，不能关闭")

    now = datetime.now(timezone.utc)
    updated = db["surveys"].find_one_and_update(
        {"_id": oid, "owner": owner, "status": "published"},
        {"$set": {"status": "closed", "closed_at": now, "updated_at": now}},
        return_document=ReturnDocument.AFTER,
    )
    if updated is None:
        raise HTTPException(status_code=409, detail="问卷已关闭，无需重复关闭")
    return _serialize(updated)
