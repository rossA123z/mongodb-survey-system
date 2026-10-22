# MongoDB Schema 与建模依据（第一阶段）

本文说明第一阶段业务数据的集合划分、字段、索引与建模取舍。字段名与
`survey-system/app/` 下的实现和 `README.md` 的“数据模型”段一致；索引定义见
`app/db.py` 的 `ensure_indexes`。

## 1. 集合划分

业务数据分四个集合：`users`、`sessions`、`surveys`、`responses`。

| 集合 | 一条文档代表 | 增长方式 | 主要访问方式 |
| --- | --- | --- | --- |
| `users` | 一个账号 | 注册时新增 | 按 `username` 精确查 |
| `sessions` | 一次登录会话 | 登录新增、退出/过期删除 | 按 `token` 精确查 |
| `surveys` | 一份问卷（含题目、选项、规则） | 创建草稿新增；编辑原地更新 | 按 `_id` 或 `owner` 查 |
| `responses` | 一次成功提交的答卷 | 每次提交新增 | 按 `survey_id` 查 |

划分依据是**生命周期与增长节奏**：账号、问卷、答卷的主体各自独立，删除或修改
一份问卷不应牵动别的问卷的答卷；答卷是唯一会高频、并发、只增不改的数据，单独
成集合，避免随问卷文档一起被反复读写。

## 2. 字段定义

### 2.1 `users`

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `_id` | ObjectId | 主键 |
| `username` | string | 用户名，唯一 |
| `password_hash` | string | `pbkdf2_sha256$260000$<salt hex>$<digest hex>`，标准库 `pbkdf2_hmac`（`app/security.py`） |
| `created_at` | datetime(UTC) | 注册时间 |

密码只存不可逆哈希，接口从不返回该字段。

### 2.2 `sessions`

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `_id` | ObjectId | 主键 |
| `token` | string | `secrets.token_urlsafe(32)`，唯一 |
| `username` | string | 会话所属账号 |
| `created_at` | datetime(UTC) | 创建时间 |
| `expires_at` | datetime(UTC) | 过期时间（默认 7 天，`app/sessions.py`） |

会话保存在服务端，客户端只持有 `HttpOnly` Cookie（`survey_session`）。

### 2.3 `surveys`

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `_id` | ObjectId | 主键 |
| `owner` | string | 创建者 `username` |
| `title` | string | 标题 |
| `description` | string | 说明 |
| `status` | string | `draft` / `published` / `closed` |
| `config` | object | `{anonymous: bool, allow_multiple: bool}` |
| `questions` | array | 有序内嵌题目数组（见 2.4） |
| `deadline` | datetime(UTC) \| null | 截止时间，统一存 UTC；无则 `null` |
| `created_at` | datetime(UTC) | 创建时间 |
| `updated_at` | datetime(UTC) | 最近更新时间 |
| `published_at` | datetime(UTC) | 发布时间（草稿期不存在） |
| `closed_at` | datetime(UTC) | 关闭时间（未关闭不存在） |

### 2.4 `surveys.questions[]`（内嵌数组，顺序即题目顺序）

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `qid` | string | 稳定标识（`q_` + 16 位十六进制），规则与答卷都引用它 |
| `type` | string | `single` / `multi` / `text` / `number` |
| `title` | string | 题干 |
| `required` | bool | 是否必答（只在有效路径上生效） |
| `options` | array | 选项数组，仅选择题有；`[{oid, text}]`，`oid` 为稳定标识 |
| `rules` | array | 跳转规则，仅单选/多选/数字题可非空；`[{rid, condition, target}]` |
| 限制字段 | 按题型 | 多选 `min_choices`/`max_choices`；文本 `min_length`/`max_length`；数字 `min_value`/`max_value`/`integer` |

`rules[].condition` 按题型：

- 单选 `{op: "is", oid}`；
- 多选 `{op: "any"|"all", oids: [...]}`；
- 数字 `{op: "eq"|"gt"|"lt", value}` 或 `{op: "between", min, max}`。

`rules[].target` 为目标题目的 `qid` 或字符串 `"end"`。不属于某题型的限制字段在写入
时即被丢弃（`app/questions.py` 的 `_clean_limits`），不让脏字段混入题目。

### 2.5 `responses`

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `_id` | ObjectId | 主键 |
| `survey_id` | ObjectId | 指向 `surveys._id` |
| `respondent` | string | 内部填写者 `username`（匿名问卷也不会删除该字段） |
| `answers` | array | 只含**有效路径上**的有效答案 |
| `submitted_at` | datetime(UTC) | 提交时间 |
| `once` | bool | 仅**禁止重复**的答卷存在，值为 `true`（部分唯一索引的过滤键） |

`answers[]` 形态按题型：单选 `{qid, oid}`、多选 `{qid, oids:[...]}`、文本
`{qid, text}`、数字 `{qid, number}`。路径外答案在提交时已被剔除，不写入。

## 3. 索引

| 集合 | 索引 | 类型 | 用途 |
| --- | --- | --- | --- |
| `users` | `{username: 1}` | 唯一 | 用户名唯一，注册重名由数据库拦截 |
| `sessions` | `{token: 1}` | 唯一 | 会话令牌唯一 |
| `responses` | `{survey_id: 1, respondent: 1}` | **唯一 + 部分索引**（`partialFilterExpression: {once: true}`） | 禁止重复的问卷保证同账号每问卷最多一条答卷 |

`responses` 的部分唯一索引是第一阶段并发正确性的核心：只对带 `once: true` 的答卷
建唯一约束。禁止重复时提交带 `once: true`，同账号第二次并发插入会命中唯一冲突
（`DuplicateKeyError` → `409`）；允许重复时不写 `once`，文档不进入该索引，因此可
多次提交且互不覆盖。这满足了规格“用数据库唯一约束保证并发正确，不能仅靠先查后写”
的要求（`app/responses.py` 的 `submit` 直接 `insert_one`，不预查询）。

## 4. 冗余与嵌入取舍的理由

**为什么把题目/选项/规则内嵌进 `surveys`，而不是照搬关系数据库拆表？**

- 问卷是**整体读、整体写**的文档：编辑页一次性渲染全部题目；发布时一次性校验全部
  题目与规则；发布后整份内容冻结。内嵌让这些操作是单文档读写，天然原子，不需要跨表
  事务，也避免了加载一份问卷要 join 四张表。
- 题目、选项、规则**只在问卷内部有意义**，不会被别的问卷共享；它们没有独立生命周期。
  这属于 MongoDB 文档模型里典型的“一对多、且‘多’随‘一’被整体访问”的嵌入场景。
- 顺序敏感（题目顺序、规则优先级顺序）用**数组顺序**直接表达，比关系模型里的
  `position` 列更自然。

**为什么又保留稳定标识 `qid`/`oid`/`rid`，而不是纯用数组下标？**

- 规则与答卷需要跨时间指向某道题/某个选项。若用下标，排序（上移/下移）会改变含义，
  历史答卷与规则就会指错。稳定标识与显示序号解耦：排序只改数组顺序，不改标识，
  已经有测试覆盖这一行为。
- 这也让“答案剔除路径外题目”“统计按 qid 归拢”有可靠的连接键。

**为什么 `responses` 单独成集合、而不是内嵌进 `surveys`？**

- 答卷只增不改、数量随填写增长，且是高并发写入；内嵌会让单文档持续膨胀，并让每次
  提交都要更新问卷文档（写热点），还可能触达单文档 16MB 上限。独立集合使每次提交是
  一次独立插入，并发天然分散。
- 统计（`app/stats.py`）按 `survey_id` 取回该问卷全部答卷后在应用层汇总，与问卷结构
  解耦。

**为什么 `surveys.owner` 存 `username` 字符串而非引用 `users._id`？**

- 本阶段用户名已保证唯一且不可改，直接存用户名让鉴权查询（`find_one({... "owner": username})`）
  少一次解析，读起来也更直观。代价是若第二阶段允许改名，需要迁移；这一点作为已知
  取舍记录，不影响第一阶段。

**为什么 `respondent` 在匿名问卷也保留？**

- 匿名是**呈现层**语义（对创建者隐藏身份），不是删除身份。系统必须能识别账号以执行
  重复限制，因此内部仍记录 `respondent`，只在序列化给创建者时按 `anonymous` 决定是否
  输出（`app/responses.py` 的 `_serialize_response`）。这直接对应术语“匿名填写”。

## 5. 已知限制（如实记录）

- 统计查询 `responses.find({"survey_id": ...})` 没有单独的 `survey_id` 索引；部分唯一
  索引的 `survey_id` 前缀只覆盖 `once: true` 的文档。在本阶段数据规模下不构成问题，
  若答卷量增大可补一个 `{survey_id: 1}` 索引。
- 会话过期靠应用按 `expires_at` 判断，未使用 MongoDB TTL 索引；过期文档不会自动回收。
- 网页表单的本地时间→UTC 折算依赖服务器本地时区，适用于本机运行的第一阶段（见 ADR 0001）。
