# ADR 0003：`/api/*` 写接口以「只接受 JSON」作为 CSRF 防护

- 状态：已采纳
- 日期：2026-10-17
- 相关工单：`.scratch/mongodb-survey-phase-one/issues/01-register-login-private-draft.md`

## 背景

规格《账号和访问控制》要求「同源写操作具备 CSRF 防护」。网页表单路由
（`/register`、`/surveys` 等）用双提交 Cookie 令牌（`app/csrf.py`）防护；
`/api/*` 的写接口（创建 / 发布 / 截止 / 关闭 / 题目 / 规则 / 提交）用 Pydantic
模型接收 JSON 正文，目前不校验令牌。需要判断这是否构成 CSRF 缺口。

## 决策

不为 `/api/*` 增加 CSRF 令牌；以「请求体必须是 `application/json`」作为这些
写接口的 CSRF 防护，并用测试固定这一契约。

## 理由

- CSRF 攻击由受害者浏览器带着会话 Cookie 发出**跨站构造**的请求。HTML 表单能
  发出的编码只有三种：`application/x-www-form-urlencoded`、`multipart/form-data`、
  `text/plain`，都不是 `application/json`。
- FastAPI 对声明了 Pydantic 模型的路由按 JSON 解析请求体：非 `application/json`
  的请求体一律 422，处理器不会执行，因此不会产生副作用。`text/plain` 即使把正文
  写成合法 JSON 也解析失败——编码决定解析分支。
- 跨站的 `fetch` 若要发 `application/json`，会触发 CORS 预检（OPTIONS）；服务端
  只服务同源、未声明允许的 CORS 来源，浏览器在预检阶段即阻止真正的请求发出。
- 因此跨站站点既「发不出」也「发不通」这些写请求。对它再加令牌属于重复防护，还会
  破坏既有以 JSON 契约编写的 200 余个测试。

## 验证

`tests/test_pages.py::test_api_write_rejects_cross_site_form_encodings` 固定该契约：
表单编码与 `text/plain` 打 `/api/surveys`、`/api/register` 均 422 且无副作用，而同源
JSON 请求成功（201）。若将来给 `/api/*` 增加 CORS，或允许表单编码，则本决策失效，
需重新评估并补令牌。

## 影响与取舍

- 这些接口只对同源 AJAX / 页面脚本可用；外部系统要使用接口需先补 CORS + 令牌策略。
- 网页表单路由仍保留令牌防护（表单确实能发 urlencoded，适用场景不同），二者并存。
