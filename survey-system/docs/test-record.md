# 测试记录与真实结果（第一阶段）

本文记录第一阶段的自动回归与网页操作验收的**真实执行过程与结果**。所有结果均为
实际运行输出，未预填、未编造；命令原样照抄即可复现。

## 1. 环境

| 项 | 值 |
| --- | --- |
| 操作系统 | Windows 11 |
| Python | 3.12（本机 3.12.3） |
| MongoDB | 9.0.2，`127.0.0.1:27017`（已运行，未由测试启动/重启） |
| 代码版本 | 集成分支 `integration/mongodb-survey-phase-one` tip `e698bc9`（起点） |
| 测试数据库 | `survey_test_t07`（经 `SURVEY_TEST_DB_NAME` 指定，与业务库隔离） |

## 2. 从干净环境启动

按 `README.md` 从零安装并启动（真实执行）：

```bash
cd survey-system
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt
.venv/Scripts/python -m uvicorn app.main:app --reload
```

- 依赖安装：`Successfully installed ... fastapi-0.143.3 pymongo-4.19.0 uvicorn-0.54.0 ...`（全部命中缓存）。
- 启动日志（真实输出）：

  ```
  INFO:     Started server process [...]
  INFO:     Waiting for application startup.
  INFO:     Application startup complete.
  INFO:     Uvicorn running on http://127.0.0.1:8000 (Press CTRL+C to quit)
  ```

- 结论：在干净虚拟环境中按 README 可成功启动，启动时建立索引。

## 3. 完整回归测试

命令：

```bash
SURVEY_TEST_DB_NAME=survey_test_t07 .venv/Scripts/python -m pytest -q
```

| 次序 | 说明 | 实际输出（尾部） | 结论 |
| --- | --- | --- | --- |
| 起点核对 | 用既有 venv 跑集成分支 tip `e698bc9` | `198 passed, 1 warning in 70.08s` | 起点绿 |
| 干净环境 | 新建 venv 装依赖后跑 | `198 passed, 1 warning in 98.96s` | 干净环境同样全过 |
| 本 ticket 修改后 | 见第 5 节的两处修改 | `200 passed, 1 warning in 82.54s` | 全过，无残缺 |

- 唯一 warning 为第三方库提示：`StarletteDeprecationWarning: Using httpx with starlette.testclient is deprecated`，与本系统实现无关。
- 测试按文件分布（修改后）：

  | 文件 | 用例数 | 文件 | 用例数 |
  | --- | --- | --- | --- |
  | test_answer_validation.py | 28 | test_surveys.py | 8 |
  | test_lifecycle.py | 24 | test_questions.py | 8 |
  | test_jump_rules.py | 21 | test_fill_pages_types.py | 8 |
  | test_question_types.py | 17 | test_stats_pages.py | 7 |
  | test_pages.py | 17 | test_jump_pages.py | 7 |
  | test_fill_pages.py | 14 | test_auth.py | 6 |
  | test_stats.py | 13 | test_anonymity_concurrency.py | 4 |
  | test_publish.py | 9 | | |
  | test_fill_submit.py | 9 | **合计** | **200** |

- 结论：**已完成**。完整回归 200 例全部通过，无跳过、无残缺。

## 4. 网页操作验收（真实执行记录）

手工点击步骤见 `docs/acceptance-web.md`。为保证可核验，这里用一段脚本驱动**真实运行
的 uvicorn 服务 + 真实 MongoDB**走完整流程（脚本为一次性验证工具，不入库），
逐条记录步骤 / 输入 / 预期 / 实际输出 / 结论。

| # | 步骤 | 输入 | 预期 | 实际输出 | 结论 |
| --- | --- | --- | --- | --- | --- |
| 1 | 注册并登录创建者 | `creator_*` / `pw123456` | 303 跳转 | `303` | 通过 |
| 2 | 创建草稿 | 标题“数据库课程问卷” | 303 回列表 | `303 Location=/surveys` | 通过 |
| 2b | 列表可见并可进入编辑 | — | 列表含标题且有编辑链接 | `含标题=True；到编辑页链接数=1` | 通过 |
| 3 | 添加四种题型 | 单选/多选/数字/文本 + 限制 | 均成功 | 4 题 qid 返回 | 通过 |
| 4 | 配置跳转规则 | Q1 选“关系型数据库”→第 3 题 | 303 保存 | `303` | 通过 |
| 5 | 发布（匿名，禁重复） | 勾选匿名 | 状态 published | `status=published config={'anonymous': True, 'allow_multiple': False}` | 通过 |
| 6 | 未登录打开填写链接 | GET `/s/{id}` | 跳登录并带 next | `303 Location=/login?next=/s/{id}` | 通过 |
| 6c | 登录后回到问卷 | next=`/s/{id}` | 跳回填写页 | `303 Location=/s/{id}` | 通过 |
| 7 | 中文表单渲染 | — | 四种控件齐全 | `radio/checkbox/textarea/number=True` | 通过 |
| 8 | 缺必答多选 | 不选 Q2 | 400 且定位到题 | `400；错误定位到题目=True` | 通过 |
| 9 | 数字越界 | Q3=999 | 400 且提示范围 | `400；提示=True` | 通过 |
| 10 | 正确作答 | Q1 文档库/Q2 副本集/Q3=5/Q4=第一阶段 | 200 确认页 | `200；进入确认页=True；含返回修改=True` | 通过 |
| 11 | 返回修改 | 点“返回修改” | 200 回填 | `200；回填文本题=True；回填单选=True` | 通过 |
| 12 | 确认提交 | 点“确认提交” | 200 成功页 | `200；成功页=True` | 通过 |
| 13 | 同账号重复提交 | 再提交一次 | 409 拒绝 | `409；提示=True` | 通过 |
| 14 | 路径 B（跳过必答多选） | Q1=关系型库 | 确认页不含 Q2 | `200；确认页不含被跳过题=True` | 通过 |
| 14b | 路径 B 提交 | — | 成功 | `200；提交成功` | 通过 |
| 15 | 统计（问卷级） | — | 答卷数、逐题统计 | `response_count=2`；单选 关系型=1 文档型=1；多选 副本集=1；数字 有效=2 平均=7.5；文本 内容=['第一阶段'] | 通过 |
| 16 | 匿名不泄露身份 | — | 响应无 respondent | `含 respondent 字段=False` | 通过 |
| 17 | 越权看统计 | 非创建者 | 404 | `404` | 通过 |
| 18 | 题目级统计页 | — | 显示该题统计 | `200；平均值行=True` | 通过 |
| 19 | 关闭问卷 | 创建者点关闭 | 303 | `303` | 通过 |
| 19b | 关闭后填写 | GET `/s/{id}` | 409 提示已关闭 | `409；提示=True` | 通过 |

- 结论：**已完成**。关键交互（中文表单、错误提示、规则配置、登录返回、返回修改、
  确认提交、统计展示）在真实服务上全部复现通过，第三方可按 `docs/acceptance-web.md`
  逐步照抄。

## 5. 本 ticket 的代码改动（如实记录）

为让“完整演示”可以纯点击复现，本 ticket 做了两处**最小、附加**的修改，并各配一个
测试。除此之外未改动既有代码行为，198 个既有用例不受影响（回归全过）。

| 文件 | 改动 | 原因 |
| --- | --- | --- |
| `app/templates/surveys.html` | “我的问卷”列表每条增加“管理 / 查看”链接指向 `/surveys/{id}/edit`；状态文案改为中文（草稿/已发布/已关闭） | 原来列表页只有标题与状态，没有进入编辑页的链接，用户无法从列表点进问卷管理，“完整演示”无法仅靠点击复现 |
| `tests/test_pages.py` | 新增 `test_surveys_page_links_to_edit_page`、`test_surveys_page_shows_chinese_status` | 为上述改动建立行为契约 |

改动前后行为差异仅限列表页新增链接与中文状态文案；未改任何接口、数据模型或校验逻辑。

## 6. 与自动测试的边界

- 自动测试经 HTTP 接口观察行为，不直接查库断言；并发效果用真实并发提交验证。
- 网页交互由第 4 节与 `docs/acceptance-web.md` 覆盖。
- 接口测试不代替浏览器交互验收，浏览器步骤也不代替回归测试。
