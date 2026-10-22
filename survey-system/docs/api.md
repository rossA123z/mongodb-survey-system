# API 与关键逻辑说明（第一阶段）

本文汇总接口清单与校验/跳转/统计口径。启动后可在 `/docs` 查看可交互的 OpenAPI
文档。网页路由见 `README.md` 的“网页”段。

## 1. 鉴权约定

- 需登录的接口未携带有效会话：`401`。
- 访问不属于自己的问卷、或问卷不存在：统一 `404`（不泄露存在性）。
- 发布后修改被冻结的内容：`409`；已关闭 / 已截止的提交：`409`。
- 请求体校验失败（如题型限制类型不对）：`400`，错误以中文提示返回。

## 2. 接口清单

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/api/register` | 注册新账号（成功 201，重复用户名 409） |
| POST | `/api/login` | 登录，下发 HttpOnly Cookie |
| POST | `/api/logout` | 退出，删除服务端会话并清 Cookie |
| POST | `/api/surveys` | 创建问卷草稿（`title`、`description`） |
| GET | `/api/surveys` | 列出当前账号自己的问卷 |
| GET | `/api/surveys/{id}` | 读取自己问卷的管理详情 |
| PATCH | `/api/surveys/{id}` | 修改草稿的标题/说明/配置（已发布 409） |
| POST | `/api/surveys/{id}/questions` | 按题型添加一道题 |
| PATCH | `/api/surveys/{id}/questions/{qid}` | 修改题目（题型不变） |
| DELETE | `/api/surveys/{id}/questions/{qid}` | 删除题目 |
| POST | `/api/surveys/{id}/questions/{qid}/move` | 排序：`{"direction": "up"|"down"}` |
| POST | `/api/surveys/{id}/questions/{qid}/rules` | 整题替换跳转规则：`{"rules": [...]}` |
| POST | `/api/surveys/{id}/publish` | 发布，返回 `share_url`（校验不过 400，已发布 409） |
| POST | `/api/surveys/{id}/deadline` | 设置/调整/清除截止时间（UTC 存储） |
| POST | `/api/surveys/{id}/close` | 手动关闭（终态） |
| POST | `/api/surveys/{id}/responses` | 提交答卷（校验不过 400；重复或已关闭/已截止 409） |
| GET | `/api/surveys/{id}/responses` | 创建者查看答卷（匿名问卷不含身份） |
| GET | `/api/surveys/{id}/stats` | 问卷级统计 |
| GET | `/api/surveys/{id}/questions/{qid}/stats` | 题目级统计（题目不存在 404） |

## 3. 题型与校验口径

一道题由 `type` 决定题型；不属于该题型的限制字段写入时即丢弃。

| 题型 | `type` | 限制字段 | 提交时校验 |
| --- | --- | --- | --- |
| 单选 | `single` | 无 | 答案必须是本题目录下的一个有效 `oid`；可选题为空放行，必答为空拒绝 |
| 多选 | `multi` | `min_choices`/`max_choices` | 答案是**不重复**的有效 `oid` 集合；固定数量（上下限相等）要求数量精确相等，否则校验最少/最多 |
| 文本 | `text` | `min_length`/`max_length` | 按**字符**计数（`len(text)`）落在区间内；空串视为未填 |
| 数字 | `number` | `min_value`/`max_value`/`integer` | 解析为**有限**数值（布尔、非数字文本、`inf`/`nan` 一律拒绝），校验范围（含两端）与整数要求 |

发布校验对限制配置的额外约束：多选上下限不为负、下限≤上限且下限≤选项总数；文本
上下限不为负且下限≤上限；数字最小值≤最大值。矛盾配置在**发布时**拒绝（草稿阶段只
做单字段类型规整）。

错误以 `{"detail": {"message": ..., "errors": {qid: "第 N 题“题干”..."}}}` 返回，提示
定位到具体题目。

（接口路径的 `{id}` 为 URL 中的 id 字段；路径模板里的 `{qid}` 是题干前出现的题目稳定标识。）

## 4. 跳转规则口径

规则内嵌在题目上（`rules`），只有单选、多选、数字题可配置，文本题不参与。

- **顺序即优先级**：按数组顺序从上到下判断，**第一条命中生效**；都不命中进下一题；
  最后一题之后进入确认页。
- **只可向后**：目标为位于本题目之后的题目 `qid`，或 `end`；回跳与循环在发布校验时
  被拒绝，引用已删除题目/选项的规则也被拒绝。
- **可选题为空不触发**：留空不命中任何条件，按默认进下一题。
- **跳转到 `end`** 只进入确认页，不自动提交。

条件形态（按题型）：单选 `{op:"is", oid}`；多选 `{op:"any"|"all", oids:[...]}`；
数字 `{op:"eq"|"gt"|"lt", value}` 或 `{op:"between", min, max}`（含两端）。

提交时服务端从第一题**重算有效路径**（`app/responses.py` 的 `compute_path`），只校验
路径上的题目；路径外的答案被忽略并剔除，不提交、不保存。

## 5. 统计口径

统计只覆盖**成功保存的答卷**及其**有效答案**：

- 被跳过的题、未填的可选题不计为回答；空文本不计入文本题；数字无回答时平均值
  为 `null`（界面显示“暂无（无回答）”，**不显示为零**）。
- 路径外答案在提交时已被剔除，因此不进入统计。
- 允许重复填写时，每份答卷分别计数（按答卷文档逐份累加）。
- 统计只对创建者开放；输出不含填写者身份（匿名与非匿名都不输出 `respondent`）。

各题型统计字段：

| 题型 | 字段 |
| --- | --- |
| 单选 | `answered_total`、`options[{oid, text, count}]` |
| 多选 | `answered_total`（一份答卷同一选项只计一次）、`options[{oid, text, count}]` |
| 文本 | `answered_total`、`texts[]` |
| 数字 | `answered_total`、`average`（无回答为 `null`） |

问卷级响应：`{survey_id, title, response_count, questions: [<逐题统计>]}`；题目级响应
为单条题目统计 `{qid, type, title, ...}`。

## 6. 生命周期与截止时间

状态 `draft` → `published` → `closed`，关闭为终态、无重开。截止时间按 UTC 存储、
网页按本地时间展示，`now >= deadline` 视为不可提交（闭区间）。打开填写页与每次提交
都重新核验状态。拒绝口径：非创建者操作 `404`；已关闭/已截止的填写与提交 `409` +
中文提示；格式非法 `400`。详见 `docs/adr/0001-survey-lifecycle-and-deadline.md`。
