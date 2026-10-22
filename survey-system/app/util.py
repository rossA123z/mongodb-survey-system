"""跨模块共享的低层工具：问卷标识解析与字段取值。

放在这里是为了避免同一个小函数在 questions / responses / surveys / stats
各写一遍（`object_id` 曾逐字重复四份，`field_value` 曾重复三份）。本模块
不依赖应用内其他模块，供各处按需引入。
"""

from bson import ObjectId
from bson.errors import InvalidId
from fastapi import HTTPException


def object_id(survey_id: str) -> ObjectId:
    """把路径里的问卷 id 转成 ObjectId；非法一律 404，不泄露存在性。"""
    try:
        return ObjectId(survey_id)
    except (InvalidId, TypeError):
        raise HTTPException(status_code=404, detail="问卷不存在")


def field_value(source, key):
    """从 dict 或带同名属性的对象取字段值；source 为 None 时返回 None。

    表单、Pydantic 模型与 Mongo 文档混用时用同一种读法（缺失即 None）。
    """
    if source is None:
        return None
    if isinstance(source, dict):
        return source.get(key)
    return getattr(source, key, None)
