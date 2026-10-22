"""FastAPI 应用入口：JSON 接口 + 同源中文网页。"""

import re
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote

from fastapi import Depends, FastAPI, Form, HTTPException, Query, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from pymongo.errors import DuplicateKeyError

from .csrf import CSRF_COOKIE, issue_token, tokens_match
from .db import ensure_indexes, get_db
from .questions import (
    ALL_LIMIT_KEYS,
    DEFAULT_QUESTION_TYPE,
    QUESTION_MULTI,
    QUESTION_NUMBER,
    QUESTION_TEXT,
    add_question,
    delete_question,
    load_owned_draft,
    move_question,
    set_rules,
    update_question,
)
from .responses import (
    list_responses,
    submit,
    survey_for_fill,
    validate_answers,
)
from .security import hash_password, verify_password
from .stats import question_stats, survey_stats
from .sessions import (
    SESSION_COOKIE,
    create_session,
    delete_session,
    username_for_token,
)
from .surveys import (
    close_survey,
    create_draft,
    get_owned,
    list_own,
    publish,
    set_deadline,
    update_owned,
)

TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


class RegisterRequest(BaseModel):
    username: str
    password: str


class LoginRequest(BaseModel):
    username: str
    password: str


class SurveyRequest(BaseModel):
    title: str
    description: str = ""


class SurveyConfigInput(BaseModel):
    anonymous: Optional[bool] = None
    allow_multiple: Optional[bool] = None


class SurveyUpdateRequest(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    config: Optional[SurveyConfigInput] = None


class PublishRequest(BaseModel):
    anonymous: Optional[bool] = None
    allow_multiple: Optional[bool] = None


class DeadlineRequest(BaseModel):
    deadline: Optional[str] = None


class OptionInput(BaseModel):
    oid: Optional[str] = None
    text: str


class QuestionInput(BaseModel):
    title: str
    type: str = DEFAULT_QUESTION_TYPE
    required: bool = False
    options: list[OptionInput] = []
    # 各题型的限制字段；不相关的字段会被 questions 层丢弃。用 Any 接收，
    # 再由 questions 层规整类型并给出 400（而不是 pydantic 的 422）。
    min_choices: Optional[Any] = None
    max_choices: Optional[Any] = None
    min_length: Optional[Any] = None
    max_length: Optional[Any] = None
    min_value: Optional[Any] = None
    max_value: Optional[Any] = None
    integer: Optional[Any] = None


class QuestionUpdateRequest(BaseModel):
    title: Optional[str] = None
    required: Optional[bool] = None
    options: Optional[list[OptionInput]] = None
    min_choices: Optional[Any] = None
    max_choices: Optional[Any] = None
    min_length: Optional[Any] = None
    max_length: Optional[Any] = None
    min_value: Optional[Any] = None
    max_value: Optional[Any] = None
    integer: Optional[Any] = None


class MoveRequest(BaseModel):
    direction: str


class RuleConditionInput(BaseModel):
    # 条件按题型使用其一：单选 oid；多选 op(any/all) + oids；数字 op + value / min / max。
    # 用 Any 接收数值，由 questions 层规整类型并给出 400（而不是 pydantic 的 422）。
    op: str = ""
    oid: Optional[str] = None
    oids: Optional[list[str]] = None
    value: Optional[Any] = None
    min: Optional[Any] = None
    max: Optional[Any] = None


class RuleInput(BaseModel):
    rid: Optional[str] = None
    condition: RuleConditionInput
    target: str


class RulesRequest(BaseModel):
    rules: list[RuleInput] = []


class AnswerInput(BaseModel):
    qid: str
    # 按题型使用其一：单选 oid；多选 oids；文本 text；数字 number。
    # number 用 Any 接收，布尔值能否视为数字交由校验层判定（拒绝布尔）。
    oid: Optional[str] = None
    oids: Optional[list[str]] = None
    text: Optional[str] = None
    number: Optional[Any] = None


class SubmitRequest(BaseModel):
    answers: list[AnswerInput] = []


@asynccontextmanager
async def _lifespan(app: FastAPI):
    ensure_indexes(get_db())
    yield


def _current_username(request: Request) -> str:
    """从会话 Cookie 解析当前登录用户名，未登录则 401（供 JSON 接口使用）。"""
    username = username_for_token(get_db(), request.cookies.get(SESSION_COOKIE))
    if username is None:
        raise HTTPException(status_code=401, detail="请先登录")
    return username


def _optional_username(request: Request) -> Optional[str]:
    """解析当前登录用户名，未登录返回 None（供网页使用，可改为跳转登录）。"""
    return username_for_token(get_db(), request.cookies.get(SESSION_COOKIE))


def _safe_next(value: str) -> str:
    """只接受站内相对路径作为登录后跳转目标，避免开放重定向。"""
    if value.startswith("/") and not value.startswith("//") and "\\" not in value:
        return value
    return "/surveys"


def _check_csrf(request: Request, form_token: str) -> None:
    """校验表单回传的 CSRF 令牌与 Cookie 是否一致，否则 403。"""
    if not tokens_match(request.cookies.get(CSRF_COOKIE), form_token):
        raise HTTPException(status_code=403, detail="表单校验失败，请刷新页面后重试")


def _render(request: Request, name: str, context: dict, status_code: int = 200) -> HTMLResponse:
    """渲染模板，注入当前用户与 CSRF 令牌（缺失时当场签发并写 Cookie）。

    令牌先算好再渲染，保证隐藏字段与随附 Cookie 一致。
    """
    ctx = {"current_user": _optional_username(request)}
    ctx.update(context)
    existing = request.cookies.get(CSRF_COOKIE)
    token = existing or issue_token()
    ctx["csrf_token"] = token
    response = templates.TemplateResponse(request, name, ctx, status_code=status_code)
    if existing != token:
        response.set_cookie(CSRF_COOKIE, token, samesite="lax", path="/")
    return response


def _login_redirect(request: Request, target: str) -> RedirectResponse:
    """未登录访问受保护页面 → 跳登录页并记住原地址。"""
    return RedirectResponse(f"/login?next={quote(target)}", status_code=303)


def _error_page(request: Request, status_code: int, message: str) -> HTMLResponse:
    return _render(request, "error.html", {"message": message}, status_code)


def _local_deadline_fields(survey: dict) -> dict:
    """把 UTC 存储的截止时间转成本地时间，供页面显示与 datetime-local 预填。"""
    raw = survey.get("deadline")
    if not raw:
        return {"deadline_display": None, "deadline_value": ""}
    local = datetime.fromisoformat(raw).astimezone()
    return {
        "deadline_display": local.strftime("%Y-%m-%d %H:%M"),
        "deadline_value": local.strftime("%Y-%m-%dT%H:%M"),
    }


def _form_deadline_to_utc(text: str):
    """把浏览器 datetime-local 的本地时间字符串转成 UTC 感知时间；空串表示清除。"""
    text = (text or "").strip()
    if not text:
        return None
    try:
        naive = datetime.fromisoformat(text)
    except ValueError:
        raise HTTPException(status_code=400, detail="截止时间格式无效")
    # 无时区的本地时间：astimezone() 按系统本地时区补全，再折算为 UTC。
    return naive.astimezone().astimezone(timezone.utc)


def _editor_response(
    request: Request,
    username: str,
    survey_id: str,
    status_code: int = 200,
    error: Optional[str] = None,
) -> HTMLResponse:
    """渲染草稿编辑页；已发布时附带可分发链接与截止时间 / 关闭控件。"""
    survey = get_owned(get_db(), username, survey_id)
    share_url = f"/s/{survey_id}" if survey["status"] == "published" else None
    context = {"survey": survey, "share_url": share_url, "error": error}
    context.update(_local_deadline_fields(survey))
    return _render(request, "survey_edit.html", context, status_code)


def _limits_from_model(payload) -> dict:
    """只取显式设置的限制字段，未提供的键不覆盖已有值。"""
    return {
        key: getattr(payload, key)
        for key in ALL_LIMIT_KEYS
        if key in getattr(payload, "model_fields_set", set())
    }


def _limits_from_form(form) -> dict:
    """表单路径：总是给出全部限制键，空字符串经规整后即为“未设置”。"""
    return {key: form.get(key) for key in ALL_LIMIT_KEYS}


def _answers_from_form(survey: dict, form) -> list:
    """按题型从表单取值：多选取同名的多个值，文本 / 数字取单值。"""
    answers = []
    for question in survey["questions"]:
        qtype = question.get("type", DEFAULT_QUESTION_TYPE)
        key = f"answer_{question['qid']}"
        if qtype == QUESTION_MULTI:
            answers.append({"qid": question["qid"], "oids": list(form.getlist(key))})
        elif qtype == QUESTION_TEXT:
            answers.append({"qid": question["qid"], "text": form.get(key)})
        elif qtype == QUESTION_NUMBER:
            answers.append({"qid": question["qid"], "number": form.get(key)})
        else:
            answers.append({"qid": question["qid"], "oid": form.get(key) or None})
    return answers


_RULE_FIELD = re.compile(r"^rules-(\d+)-target$")


def _rules_from_form(question: dict, form) -> list:
    """按行号顺序从表单取出规则；全空的行表示“未添加”，直接跳过。

    条件字段按题型取：单选 `oid`；多选 `op` + `oids`；数字 `op` + `value` / `min` / `max`。
    """
    qtype = question.get("type", DEFAULT_QUESTION_TYPE)
    indices = sorted(
        int(match.group(1))
        for key in form.keys()
        if (match := _RULE_FIELD.match(key))
    )
    rules = []
    for i in indices:
        target = form.get(f"rules-{i}-target", "")
        op = form.get(f"rules-{i}-op", "")
        oid = form.get(f"rules-{i}-oid", "")
        oids = list(form.getlist(f"rules-{i}-oids"))
        value = form.get(f"rules-{i}-value", "")
        low = form.get(f"rules-{i}-min", "")
        high = form.get(f"rules-{i}-max", "")
        if not any([target, op, oid, oids, value, low, high]):
            continue
        if qtype == QUESTION_MULTI:
            condition = {"op": op, "oids": oids}
        elif qtype == QUESTION_NUMBER:
            condition = {"op": op, "value": value, "min": low, "max": high}
        else:
            condition = {"op": "is", "oid": oid}
        rules.append(
            {
                "rid": form.get(f"rules-{i}-rid"),
                "condition": condition,
                "target": target,
            }
        )
    return rules


def _apply_rule_move(rules: list, directive: str) -> list:
    """按上移 / 下移指令交换相邻两条规则的顺序；指令形如 "{index}-up"。

    顺序即匹配优先级，第一条命中生效，因此调整顺序会改变生效结果。
    越界（首条上移 / 末条下移）按原样返回，不报错。
    """
    parts = directive.rsplit("-", 1)
    if len(parts) != 2 or parts[1] not in ("up", "down"):
        raise HTTPException(status_code=400, detail="规则排序指令无效")
    try:
        index = int(parts[0])
    except ValueError:
        raise HTTPException(status_code=400, detail="规则排序指令无效")
    target = index - 1 if parts[1] == "up" else index + 1
    if 0 <= index < len(rules) and 0 <= target < len(rules):
        rules[index], rules[target] = rules[target], rules[index]
    return rules


def _values_map(answers: list) -> dict:
    """把提交的答案按 qid 归拢，供填写页在报错后回填各题型控件。"""
    values = {}
    for answer in answers:
        for key in ("oid", "oids", "text", "number"):
            if answer.get(key) is not None:
                values[answer["qid"]] = answer[key]
                break
    return values


def _selections(survey: dict, valid: list) -> list:
    """确认页用：把有效答案按题型格式化为可读文本。"""
    chosen = {answer["qid"]: answer for answer in valid}
    selections = []
    for question in survey["questions"]:
        answer = chosen.get(question["qid"])
        if answer is None:
            continue
        option_texts = {option["oid"]: option["text"] for option in question["options"]}
        if "oids" in answer:
            text = "、".join(option_texts.get(oid, "") for oid in answer["oids"])
        elif "oid" in answer:
            text = option_texts.get(answer["oid"], "")
        elif "text" in answer:
            text = answer["text"]
        else:
            text = str(answer["number"])
        selections.append({"title": question["title"], "text": text})
    return selections


def create_app() -> FastAPI:
    app = FastAPI(title="MongoDB 问卷系统", lifespan=_lifespan)

    @app.post("/api/register", status_code=201)
    def register(payload: RegisterRequest):
        try:
            get_db()["users"].insert_one(
                {
                    "username": payload.username,
                    "password_hash": hash_password(payload.password),
                    "created_at": datetime.now(timezone.utc),
                }
            )
        except DuplicateKeyError:
            raise HTTPException(status_code=409, detail="用户名已被占用")
        return {"username": payload.username}

    @app.post("/api/login")
    def login(payload: LoginRequest, response: Response):
        user = get_db()["users"].find_one({"username": payload.username})
        if user is None or not verify_password(payload.password, user["password_hash"]):
            raise HTTPException(status_code=401, detail="用户名或密码错误")
        token = create_session(get_db(), payload.username)
        response.set_cookie(
            SESSION_COOKIE, token, httponly=True, samesite="lax", path="/"
        )
        return {"username": payload.username}

    @app.post("/api/surveys", status_code=201)
    def create_survey(payload: SurveyRequest, username: str = Depends(_current_username)):
        return create_draft(get_db(), username, payload.title, payload.description)

    @app.get("/api/surveys")
    def list_surveys(username: str = Depends(_current_username)):
        return list_own(get_db(), username)

    @app.get("/api/surveys/{survey_id}")
    def read_survey(survey_id: str, username: str = Depends(_current_username)):
        return get_owned(get_db(), username, survey_id)

    @app.patch("/api/surveys/{survey_id}")
    def edit_survey(
        survey_id: str,
        payload: SurveyUpdateRequest,
        username: str = Depends(_current_username),
    ):
        return update_owned(
            get_db(),
            username,
            survey_id,
            payload.title,
            payload.description,
            payload.config.model_dump() if payload.config else None,
        )

    @app.post("/api/surveys/{survey_id}/publish")
    def publish_survey(
        survey_id: str,
        payload: PublishRequest = PublishRequest(),
        username: str = Depends(_current_username),
    ):
        return publish(
            get_db(), username, survey_id, payload.anonymous, payload.allow_multiple
        )

    @app.post("/api/surveys/{survey_id}/deadline")
    def set_survey_deadline(
        survey_id: str,
        payload: DeadlineRequest,
        username: str = Depends(_current_username),
    ):
        return set_deadline(get_db(), username, survey_id, payload.deadline)

    @app.post("/api/surveys/{survey_id}/close")
    def close_survey_api(
        survey_id: str,
        username: str = Depends(_current_username),
    ):
        return close_survey(get_db(), username, survey_id)

    @app.post("/api/surveys/{survey_id}/questions", status_code=201)
    def create_question(
        survey_id: str,
        payload: QuestionInput,
        username: str = Depends(_current_username),
    ):
        return add_question(
            get_db(),
            username,
            survey_id,
            payload.title,
            payload.type,
            payload.required,
            payload.options,
            _limits_from_model(payload),
        )

    @app.patch("/api/surveys/{survey_id}/questions/{qid}")
    def edit_question(
        survey_id: str,
        qid: str,
        payload: QuestionUpdateRequest,
        username: str = Depends(_current_username),
    ):
        return update_question(
            get_db(),
            username,
            survey_id,
            qid,
            payload.title,
            payload.required,
            payload.options,
            _limits_from_model(payload),
        )

    @app.post("/api/surveys/{survey_id}/questions/{qid}/rules")
    def set_question_rules(
        survey_id: str,
        qid: str,
        payload: RulesRequest,
        username: str = Depends(_current_username),
    ):
        return set_rules(get_db(), username, survey_id, qid, payload.rules)

    @app.delete("/api/surveys/{survey_id}/questions/{qid}")
    def remove_question(
        survey_id: str,
        qid: str,
        username: str = Depends(_current_username),
    ):
        delete_question(get_db(), username, survey_id, qid)
        return {"status": "ok"}

    @app.post("/api/surveys/{survey_id}/questions/{qid}/move")
    def reorder_question(
        survey_id: str,
        qid: str,
        payload: MoveRequest,
        username: str = Depends(_current_username),
    ):
        return move_question(get_db(), username, survey_id, qid, payload.direction)

    @app.post("/api/surveys/{survey_id}/responses", status_code=201)
    def submit_response(
        survey_id: str,
        payload: SubmitRequest,
        username: str = Depends(_current_username),
    ):
        return submit(get_db(), survey_id, username, payload.answers)

    @app.get("/api/surveys/{survey_id}/responses")
    def read_responses(
        survey_id: str,
        username: str = Depends(_current_username),
    ):
        return list_responses(get_db(), username, survey_id)

    @app.get("/api/surveys/{survey_id}/stats")
    def read_survey_stats(
        survey_id: str,
        username: str = Depends(_current_username),
    ):
        return survey_stats(get_db(), username, survey_id)

    @app.get("/api/surveys/{survey_id}/questions/{qid}/stats")
    def read_question_stats(
        survey_id: str,
        qid: str,
        username: str = Depends(_current_username),
    ):
        return question_stats(get_db(), username, survey_id, qid)

    @app.post("/api/logout")
    def logout(request: Request, response: Response):
        delete_session(get_db(), request.cookies.get(SESSION_COOKIE))
        response.delete_cookie(SESSION_COOKIE, path="/")
        return {"status": "ok"}

    # ---- 同源中文网页 ----

    @app.get("/", include_in_schema=False)
    def index():
        return RedirectResponse("/surveys", status_code=303)

    @app.get("/register", include_in_schema=False)
    def register_page(request: Request):
        if _optional_username(request):
            return RedirectResponse("/surveys", status_code=303)
        return _render(request, "register.html", {})

    @app.post("/register", include_in_schema=False)
    def register_submit(
        request: Request,
        username: str = Form(""),
        password: str = Form(""),
        csrf_token: str = Form(""),
    ):
        _check_csrf(request, csrf_token)
        username = username.strip()
        errors = {}
        if not username:
            errors["username"] = "请填写用户名"
        if not password:
            errors["password"] = "请填写密码"
        if errors:
            return _render(
                request, "register.html", {"errors": errors, "username": username}, 400
            )
        try:
            get_db()["users"].insert_one(
                {
                    "username": username,
                    "password_hash": hash_password(password),
                    "created_at": datetime.now(timezone.utc),
                }
            )
        except DuplicateKeyError:
            return _render(
                request,
                "register.html",
                {"errors": {"username": "用户名已被占用"}, "username": username},
                400,
            )
        return RedirectResponse("/login", status_code=303)

    @app.get("/login", include_in_schema=False)
    def login_page(request: Request, next_url: str = Query("/surveys", alias="next")):
        if _optional_username(request):
            return RedirectResponse(_safe_next(next_url), status_code=303)
        return _render(request, "login.html", {"next_url": _safe_next(next_url)})

    @app.post("/login", include_in_schema=False)
    def login_submit(
        request: Request,
        username: str = Form(""),
        password: str = Form(""),
        csrf_token: str = Form(""),
        next_url: str = Form("/surveys", alias="next"),
    ):
        _check_csrf(request, csrf_token)
        user = get_db()["users"].find_one({"username": username})
        if user is None or not verify_password(password, user["password_hash"]):
            return _render(
                request,
                "login.html",
                {
                    "error": "用户名或密码错误",
                    "username": username,
                    "next_url": _safe_next(next_url),
                },
                401,
            )
        token = create_session(get_db(), username)
        response = RedirectResponse(_safe_next(next_url), status_code=303)
        response.set_cookie(
            SESSION_COOKIE, token, httponly=True, samesite="lax", path="/"
        )
        return response

    @app.post("/logout", include_in_schema=False)
    def logout_submit(request: Request, csrf_token: str = Form("")):
        _check_csrf(request, csrf_token)
        delete_session(get_db(), request.cookies.get(SESSION_COOKIE))
        response = RedirectResponse("/login", status_code=303)
        response.delete_cookie(SESSION_COOKIE, path="/")
        return response

    @app.get("/surveys", include_in_schema=False)
    def surveys_page(request: Request):
        username = _optional_username(request)
        if username is None:
            return _login_redirect(request, "/surveys")
        return _render(
            request, "surveys.html", {"surveys": list_own(get_db(), username)}
        )

    @app.get("/surveys/new", include_in_schema=False)
    def new_survey_page(request: Request):
        if _optional_username(request) is None:
            return _login_redirect(request, "/surveys/new")
        return _render(request, "survey_new.html", {})

    @app.post("/surveys", include_in_schema=False)
    def new_survey_submit(
        request: Request,
        title: str = Form(""),
        description: str = Form(""),
        csrf_token: str = Form(""),
    ):
        username = _optional_username(request)
        if username is None:
            return _login_redirect(request, "/surveys/new")
        _check_csrf(request, csrf_token)
        title = title.strip()
        if not title:
            return _render(
                request,
                "survey_new.html",
                {
                    "errors": {"title": "请填写问卷标题"},
                    "title": "",
                    "description": description,
                },
                400,
            )
        create_draft(get_db(), username, title, description)
        return RedirectResponse("/surveys", status_code=303)

    # ---- 草稿编辑、发布与填写 ----

    @app.get("/surveys/{survey_id}/edit", include_in_schema=False)
    def edit_survey_page(request: Request, survey_id: str):
        username = _optional_username(request)
        if username is None:
            return _login_redirect(request, f"/surveys/{survey_id}/edit")
        try:
            return _editor_response(request, username, survey_id)
        except HTTPException as exc:
            return _error_page(request, exc.status_code, exc.detail)

    @app.post("/surveys/{survey_id}/questions", include_in_schema=False)
    async def add_question_form(request: Request, survey_id: str):
        form = await request.form()
        username = _optional_username(request)
        if username is None:
            return _login_redirect(request, f"/surveys/{survey_id}/edit")
        try:
            _check_csrf(request, form.get("csrf_token", ""))
            add_question(
                get_db(),
                username,
                survey_id,
                form.get("title", ""),
                form.get("type", DEFAULT_QUESTION_TYPE),
                form.get("required") == "on",
                [{"text": text} for text in form.getlist("option")],
                _limits_from_form(form),
            )
        except HTTPException as exc:
            if exc.status_code == 404:
                return _error_page(request, 404, exc.detail)
            return _editor_response(request, username, survey_id, exc.status_code, exc.detail)
        return RedirectResponse(f"/surveys/{survey_id}/edit", status_code=303)

    @app.post("/surveys/{survey_id}/questions/{qid}", include_in_schema=False)
    async def question_action_form(request: Request, survey_id: str, qid: str):
        form = await request.form()
        username = _optional_username(request)
        if username is None:
            return _login_redirect(request, f"/surveys/{survey_id}/edit")
        action = form.get("action", "update")
        try:
            _check_csrf(request, form.get("csrf_token", ""))
            db = get_db()
            if action == "delete":
                delete_question(db, username, survey_id, qid)
            elif action in ("up", "down"):
                move_question(db, username, survey_id, qid, action)
            else:
                options = [
                    {"oid": oid, "text": text}
                    for oid, text in zip(form.getlist("oid"), form.getlist("option"))
                ]
                update_question(
                    db,
                    username,
                    survey_id,
                    qid,
                    form.get("title", ""),
                    form.get("required") == "on",
                    options,
                    _limits_from_form(form),
                )
        except HTTPException as exc:
            if exc.status_code == 404:
                return _error_page(request, 404, exc.detail)
            return _editor_response(request, username, survey_id, exc.status_code, exc.detail)
        return RedirectResponse(f"/surveys/{survey_id}/edit", status_code=303)

    @app.post("/surveys/{survey_id}/questions/{qid}/rules", include_in_schema=False)
    async def set_rules_form(request: Request, survey_id: str, qid: str):
        form = await request.form()
        username = _optional_username(request)
        if username is None:
            return _login_redirect(request, f"/surveys/{survey_id}/edit")
        try:
            _check_csrf(request, form.get("csrf_token", ""))
            db = get_db()
            draft = load_owned_draft(db, username, survey_id)
            question = next(
                (item for item in draft["questions"] if item["qid"] == qid), None
            )
            if question is None:
                raise HTTPException(status_code=404, detail="题目不存在")
            rules = _rules_from_form(question, form)
            move = form.get("rule_move")
            if move:
                rules = _apply_rule_move(rules, move)
            set_rules(db, username, survey_id, qid, rules)
        except HTTPException as exc:
            if exc.status_code == 404:
                return _error_page(request, 404, exc.detail)
            return _editor_response(request, username, survey_id, exc.status_code, exc.detail)
        return RedirectResponse(f"/surveys/{survey_id}/edit", status_code=303)

    @app.get("/surveys/{survey_id}/publish", include_in_schema=False)
    def publish_page(request: Request, survey_id: str):
        username = _optional_username(request)
        if username is None:
            return _login_redirect(request, f"/surveys/{survey_id}/publish")
        try:
            survey = get_owned(get_db(), username, survey_id)
        except HTTPException as exc:
            return _error_page(request, exc.status_code, exc.detail)
        if survey["status"] != "draft":
            return RedirectResponse(f"/surveys/{survey_id}/edit", status_code=303)
        return _render(request, "survey_publish.html", {"survey": survey})

    @app.post("/surveys/{survey_id}/publish", include_in_schema=False)
    async def publish_form(request: Request, survey_id: str):
        form = await request.form()
        username = _optional_username(request)
        if username is None:
            return _login_redirect(request, f"/surveys/{survey_id}/publish")
        try:
            _check_csrf(request, form.get("csrf_token", ""))
            publish(
                get_db(),
                username,
                survey_id,
                anonymous=("anonymous" in form),
                allow_multiple=("allow_multiple" in form),
            )
        except HTTPException as exc:
            if exc.status_code == 404:
                return _error_page(request, 404, exc.detail)
            survey = get_owned(get_db(), username, survey_id)
            return _render(
                request,
                "survey_publish.html",
                {"survey": survey, "error": exc.detail},
                exc.status_code,
            )
        return RedirectResponse(f"/surveys/{survey_id}/edit", status_code=303)

    @app.post("/surveys/{survey_id}/deadline", include_in_schema=False)
    async def set_deadline_form(request: Request, survey_id: str):
        form = await request.form()
        username = _optional_username(request)
        if username is None:
            return _login_redirect(request, f"/surveys/{survey_id}/edit")
        try:
            _check_csrf(request, form.get("csrf_token", ""))
            set_deadline(
                get_db(),
                username,
                survey_id,
                _form_deadline_to_utc(form.get("deadline", "")),
            )
        except HTTPException as exc:
            return _error_page(request, exc.status_code, exc.detail)
        return RedirectResponse(f"/surveys/{survey_id}/edit", status_code=303)

    @app.post("/surveys/{survey_id}/close", include_in_schema=False)
    async def close_survey_form(request: Request, survey_id: str):
        form = await request.form()
        username = _optional_username(request)
        if username is None:
            return _login_redirect(request, f"/surveys/{survey_id}/edit")
        try:
            _check_csrf(request, form.get("csrf_token", ""))
            close_survey(get_db(), username, survey_id)
        except HTTPException as exc:
            return _error_page(request, exc.status_code, exc.detail)
        return RedirectResponse(f"/surveys/{survey_id}/edit", status_code=303)

    @app.get("/surveys/{survey_id}/stats", include_in_schema=False)
    def survey_stats_page(request: Request, survey_id: str):
        username = _optional_username(request)
        if username is None:
            return _login_redirect(request, f"/surveys/{survey_id}/stats")
        try:
            data = survey_stats(get_db(), username, survey_id)
        except HTTPException as exc:
            return _error_page(request, exc.status_code, exc.detail)
        return _render(request, "stats.html", data)

    @app.get("/surveys/{survey_id}/stats/{qid}", include_in_schema=False)
    def question_stats_page(request: Request, survey_id: str, qid: str):
        username = _optional_username(request)
        if username is None:
            return _login_redirect(request, f"/surveys/{survey_id}/stats/{qid}")
        try:
            data = survey_stats(get_db(), username, survey_id)
            question = question_stats(get_db(), username, survey_id, qid)
        except HTTPException as exc:
            return _error_page(request, exc.status_code, exc.detail)
        return _render(
            request,
            "question_stats.html",
            {
                "title": data["title"],
                "response_count": data["response_count"],
                "survey_id": survey_id,
                "question": question,
            },
        )

    @app.get("/s/{survey_id}", include_in_schema=False)
    def fill_page(request: Request, survey_id: str):
        if _optional_username(request) is None:
            return _login_redirect(request, f"/s/{survey_id}")
        try:
            survey = survey_for_fill(get_db(), survey_id)
        except HTTPException as exc:
            return _error_page(request, exc.status_code, exc.detail)
        return _render(request, "fill.html", {"survey": survey, "errors": {}, "values": {}})

    @app.post("/s/{survey_id}", include_in_schema=False)
    async def fill_preview(request: Request, survey_id: str):
        form = await request.form()
        if _optional_username(request) is None:
            return _login_redirect(request, f"/s/{survey_id}")
        try:
            _check_csrf(request, form.get("csrf_token", ""))
            survey = survey_for_fill(get_db(), survey_id)
        except HTTPException as exc:
            return _error_page(request, exc.status_code, exc.detail)
        answers = _answers_from_form(survey, form)
        errors, valid = validate_answers(survey, answers)
        if errors:
            return _render(
                request,
                "fill.html",
                {"survey": survey, "errors": errors, "values": _values_map(answers)},
                400,
            )
        return _render(
            request,
            "fill_confirm.html",
            {"survey": survey, "answers": valid, "selections": _selections(survey, valid)},
        )

    @app.post("/s/{survey_id}/back", include_in_schema=False)
    async def fill_back(request: Request, survey_id: str):
        """确认页“返回修改”：重新渲染填写页并回填已填答案。"""
        form = await request.form()
        if _optional_username(request) is None:
            return _login_redirect(request, f"/s/{survey_id}")
        try:
            _check_csrf(request, form.get("csrf_token", ""))
            survey = survey_for_fill(get_db(), survey_id)
        except HTTPException as exc:
            return _error_page(request, exc.status_code, exc.detail)
        answers = _answers_from_form(survey, form)
        return _render(
            request,
            "fill.html",
            {"survey": survey, "errors": {}, "values": _values_map(answers)},
        )

    @app.post("/s/{survey_id}/submit", include_in_schema=False)
    async def fill_submit(request: Request, survey_id: str):
        form = await request.form()
        username = _optional_username(request)
        if username is None:
            return _login_redirect(request, f"/s/{survey_id}")
        try:
            _check_csrf(request, form.get("csrf_token", ""))
            survey = survey_for_fill(get_db(), survey_id)
            submit(get_db(), survey_id, username, _answers_from_form(survey, form))
        except HTTPException as exc:
            if exc.status_code in (404, 409):
                return _error_page(request, exc.status_code, exc.detail)
            detail = exc.detail if isinstance(exc.detail, dict) else {}
            return _render(
                request,
                "fill.html",
                {
                    "survey": survey,
                    "errors": detail.get("errors", {}),
                    "values": _values_map(_answers_from_form(survey, form)),
                },
                400,
            )
        return _render(request, "submitted.html", {"survey": survey})

    return app


app = create_app()
