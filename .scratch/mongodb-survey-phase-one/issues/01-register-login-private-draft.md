# 01 · 注册登录与私有问卷草稿

Status: done
Blocked by: 无

## 交付

用户能注册、登录、退出，创建标题和说明并在自己的问卷列表看到草稿；另一个账号无法读取或修改其管理数据。同时建立最小可启动应用、MongoDB 配置与隔离测试环境。

## 页面

- 注册页、登录页、退出入口。
- 我的问卷列表页：展示当前账号自己的问卷。
- 新建草稿页：填写标题与说明。

## 数据结构

- `users`：唯一用户名、密码哈希、注册时间。
- `surveys`：创建者、标题、说明、状态（草稿）、配置、有序题目与规则（本 ticket 内题可为空）。
- 登录会话如需持久化，也存于 MongoDB。
- 密码只存安全哈希；任何接口不返回密码哈希。

## 接口

- 注册、登录、退出。
- 创建问卷草稿。
- 列出当前账号自己的问卷。

## 测试

- 以 HTTP 接口为主要自动测试入口，使用独立测试数据库，禁止清理业务数据库。
- 覆盖成功路径与拒绝路径；不测试私有函数内部实现。

## 文档

- MongoDB 连接配置与项目数据库名说明。
- 测试运行方式。

## 验收条件

- [ ] 可以用新用户名注册并登录，退出后会话失效。
- [ ] 重复用户名被拒绝，错误密码被拒绝，错误信息不泄露内部细节。
- [ ] 创建标题和说明后，该问卷出现在创建者自己的列表中。
- [ ] 另一个账号无法读取或修改该问卷的管理数据。
- [ ] 应用重启后草稿仍然存在。
- [ ] 应用可从干净环境按说明启动，测试可用独立数据库运行。

## Comments

- 2026-10-16：按已批准的 7 项拆分发布，状态 ready-for-agent。
- 2026-10-16：实现完成并合入集成分支。提交 `4b6109e`（后端接口 + 测试）、`025fb0d`（中文网页 + CSRF）；merger 子 agent 以三方合并落成 merge commit `2b21a40`，无冲突。合并结果上 `pytest -q` 为 `29 passed`。逐条验收证据：
  - 注册登录与退出失效 —— `tests/test_auth.py::test_registered_user_can_login`（断言 HttpOnly）、`test_logout_invalidates_session`。
  - 重名与错误密码拒绝、不泄露内部细节 —— `test_duplicate_username_is_rejected`（409）、`test_wrong_password_is_rejected_without_leaking_details`（断言响应体不含 `pbkdf2` 与明文）、`test_unknown_username_gets_same_generic_message`（防枚举）。
  - 创建草稿并出现在自己列表 —— `tests/test_surveys.py::test_logged_in_user_can_create_draft`、`test_created_draft_appears_in_own_list`。
  - 越权隔离 —— `test_other_account_cannot_read_survey`、`test_other_account_cannot_modify_survey`。
  - 重启后草稿仍在 —— `test_draft_survives_app_restart`。
  - 页面交付 —— `tests/test_pages.py` 15 例，覆盖注册/登录/退出/我的问卷/新建草稿五个中文页面、CSRF 令牌校验与登录后返回原地址。
- 2026-10-16：存疑项（未决，留待 code-review 与后续 ticket 统一口径）—— 越权返回 404 而非 403（避免泄露问卷是否存在）；CSRF 仅覆盖浏览器表单路由，`/api/*` 未强制；输入长度/大小校验留待后续 ticket。这些子 agent 已如实标注，尚未判定是否需要修改。
- 依据 `implement-spec` 步骤 8，本 ticket 的 Status 统一在全部 ticket 完成并经 code-review 后翻转，现保持 ready-for-agent，以上为验证证据。
- 2026-10-17：验收通过，关闭。集成分支 `integration/mongodb-survey-phase-one` tip `0e07525`，全套 203 例通过。code-review 两条轴已跑，本 ticket 相关处置：
  - CSRF 未覆盖 `/api/*`（Spec 轴发现）→ 调查确认 `/api/*` 只接受 `application/json`，跨站表单无法构造该内容类型（实测表单编码与 text/plain 一律 422 且无副作用），风险已被挡住；**不加令牌**以免破坏既有 JSON 契约，新增测试固定该契约并写 ADR `survey-system/docs/adr/0003-api-csrf-via-content-type.md` 记录依据。
  - 越权返回 404 而非 403 的口径：确认为有意选择（不泄露资源存在性），保留。
  - 测试 helper 重复（Standards 轴）→ 已收进 `tests/conftest.py` 共享。
  - 输入长度/大小校验：规格《账号和访问控制》有提及但工单验收未要求，**未实现**，记为第一阶段遗留。
