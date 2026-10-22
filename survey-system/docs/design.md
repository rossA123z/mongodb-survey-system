# 系统与数据库设计（第一阶段）

本文说明分层结构、请求流向和几个关键流程。接口清单见 `README.md` 的“接口”段与
`docs/api.md`；数据模型见 `docs/mongodb-schema.md`。

## 1. 分层结构

应用是单个 FastAPI 进程（`app/main.py` 的 `create_app()`），同源提供 JSON 接口与
中文网页，无独立前端构建、无队列、无缓存、无后台服务。

```
浏览器 / API 客户端
        │
        ▼
app/main.py          路由层：鉴权、CSRF、模板渲染、错误码映射
        │  调用
        ▼
app/surveys.py       问卷：草稿增删改、发布、截止时间、关闭、发布前校验
app/questions.py     题目：四种题型、选项、限制字段、跳转规则的规整与校验
app/responses.py     答卷：有效路径重算、按题型校验、提交、匿名脱敏、查看
app/stats.py         统计：按问卷与按题目汇总
        │  依赖
        ▼
app/db.py            连接与索引（业务数据全部经此读写）
app/sessions.py      会话（token 存 MongoDB）
app/security.py      密码哈希（标准库 pbkdf2_hmac）
app/csrf.py          CSRF 令牌（双提交 Cookie）
        │
        ▼
MongoDB（survey_db）
```

分层职责：

- **路由层**只做协议相关的事：从 Cookie 解析当前用户、校验 CSRF、把领域函数的
  返回值渲染成模板或 JSON、把 `HTTPException` 映射成状态码与中文提示。业务规则
  不写在这里。
- **领域层**（surveys/questions/responses/stats）承载全部业务规则，接收
  `Database` 句柄与普通 Python 值，便于被测试经 HTTP 间接覆盖。
- **基础设施**层（db/security/sessions/csrf）不依赖业务语义。

## 2. 请求流向

### 2.1 网页表单（写操作）

```
GET  /surveys/{id}/edit
  → _optional_username 解析会话（未登录 → 303 跳 /login?next=...）
  → 渲染时注入 CSRF 令牌（隐藏字段 + csrf_token Cookie 一致）

POST /surveys/{id}/questions
  → _optional_username
  → _check_csrf（Cookie 与表单字段不一致 → 403）
  → questions.add_question（校验题型/题干/选项/限制）
  → 303 重定向回编辑页（Post/Redirect/Get，避免刷新重复提交）
```

### 2.2 JSON 接口

```
POST /api/surveys/{id}/responses
  → Depends(_current_username)（无会话 → 401）
  → responses.submit
       → load_published（已发布？未关闭？未截止？否则 404/409）
       → validate_answers（从第一题重算有效路径并逐题校验）
       → insert_one（禁止重复时靠部分唯一索引拦并发）
  → 201 + 序列化后的答卷
```

鉴权统一：受保护路由经 `_current_username`（JSON，401）或 `_optional_username` +
跳转（网页）。越权访问他人问卷统一 `404`，不泄露“问卷是否存在”。

## 3. 关键流程

### 3.1 发布校验（冻结前的闸门）

`surveys.publish` 调 `validate_for_publish(questions)`，逐条检查：

1. 至少一道题；
2. 每题有非空题干；
3. 选择题至少两个选项；
4. 限制配置合法（`_validate_limits`：多选上下限不为负、下限≤上限且≤选项总数；
   文本上下限不为负且下限≤上限；数字最小值≤最大值）；
5. 跳转规则合法（`_validate_rules`：目标必须是**该题之后**的题目或 `end`；引用
   的选项必须仍存在）。

通过后状态置 `published`，`published_at` 落库，返回 `share_url = /s/{id}`。发布后
标题、说明、题目、选项与配置冻结（后续修改返回 409），仅 `deadline` 例外可调。

**为什么“只向后跳”就能同时禁止循环？** 若存在环，环中必有一条边指向不大于自己的
下标，即一条反向边，会被“目标必须靠后”这一条判断拒绝，因此无需单独做环检测
（`app/surveys.py` 的注释与实现一致）。

### 3.2 跳转路径重算（提交时）

填写者看到的路径由前端按规则动态显示/隐藏，但**服务端不信任客户端**：

`responses.compute_path(questions, collected)` 从第 0 题开始：

- 取当前题，若命中某条规则（按规则数组顺序，第一条命中生效）：
  - 目标为 `end` → 停在确认页（结束）；
  - 目标为某题 → 跳到该题下标；
- 无命中 → 下一题；
- 用 `seen` 去重防御异常数据造成的死循环。

`validate_answers` 只遍历 `compute_path` 返回的题目：路径上的题才校验、才可能进入
`valid`。**不在路径上的答案一律忽略并剔除**（既不校验也不保存）。因此“返回修改后
路径改变、旧路径答案失效”由重算路径天然保证——提交以重算结果为准。

`_condition_matches` 中“可选题为空不触发规则”：答案为 `None`/空列表/空串时不匹配
任何条件，按默认进入下一题。

### 3.3 匿名脱敏

匿名是呈现层语义。提交时无论匿名与否都写 `respondent`，以支持重复限制；查询答卷
与统计时，`_serialize_response` 与 `stats` 都不输出 `respondent`。因此匿名问卷的
创建者在答卷列表、统计页与接口响应中都看不到填写者身份，而系统内部仍能按账号拦重复。

### 3.4 并发防重复

不使用“先查再写”。禁止重复的提交写 `once: true`，落入 `responses` 的
**部分唯一索引** `(survey_id, respondent) where once=true`；两个并发请求中第二个
`insert_one` 触发 `DuplicateKeyError`，被映射为 `409`。允许重复的提交不写 `once`，
不进索引，可多次成功且互不覆盖。多人同时提交只是各自独立插入，答卷不丢失。

### 3.5 截止与关闭（提交前再次核验）

`load_published` 在**打开填写页**与**每次提交**时都重新读取问卷，核验：已发布
（否则 404）、未关闭、未截止（`now >= deadline` 视为不可提交，闭区间）。因此填写
过程中问卷被关闭或调整到已截止，后续提交仍被拒绝，而不是只在打开页面时检查一次。
关闭为终态，无重开路径（详见 `docs/adr/0001-survey-lifecycle-and-deadline.md`）。

## 4. 安全设计要点

- 密码 `pbkdf2_hmac` 加盐哈希，接口不返回哈希；登录失败对“用户不存在”与“密码错误”
  返回同一文案，避免枚举。
- 会话令牌不可预测、存服务端，Cookie 为 `HttpOnly` + `SameSite=Lax`，支持退出与过期。
- 网页写操作有 CSRF 防护（双提交 Cookie）；`SameSite=Lax` 作为第二层。
- 用户提交内容经 Jinja2 自动转义按文本显示，不执行 HTML（防存储型 XSS）。
- 服务端从第一题重算路径与校验，不信任客户端提交的题目范围。

## 5. 测试策略

自动测试以 HTTP 接口为主要入口，使用真实且独立的测试数据库（默认 `survey_test`，
可用 `SURVEY_TEST_DB_NAME` 指定），每例开始前清空该库。测试只经接口观察行为，不
直接查库断言；并发效果通过真实并发提交验证，不用 mock 代替。网页交互另有一套可
复现的手工验收步骤（`docs/acceptance-web.md`）。测试清单与真实结果见
`docs/test-record.md`。
