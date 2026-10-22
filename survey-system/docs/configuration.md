# 配置与启动说明（第一阶段）

本项目的启动与配置步骤以 `survey-system/README.md` 为准；本文把其中与环境相关
的部分单独整理，便于交付核对。README 与本文件若有不一致，以 README 为准。

## 1. 环境要求

- Python 3.12（本机实测 3.12.3）
- 可连接的 MongoDB（本机实测 9.0.2，监听 `127.0.0.1:27017`）

## 2. 安装依赖

在 `survey-system/` 目录下：

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt      # Windows
# Linux / macOS 用 .venv/bin/python
```

依赖已固定在 `requirements.txt`（FastAPI、PyMongo、Uvicorn、Jinja2、pytest、httpx 等），
不做二次选择。密码哈希使用标准库 `hashlib.pbkdf2_hmac`，不额外引入哈希库。

## 3. 环境变量

应用配置全部来自环境变量（`app/config.py`），用于区分业务库与测试库：

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `MONGO_URI` | `mongodb://127.0.0.1:27017` | MongoDB 连接地址 |
| `SURVEY_DB_NAME` | `survey_db` | 业务数据库名 |

未设置时使用默认值。应用不读取 `.env` 文件（`config.py` 只读 `os.environ`）。

## 4. 启动

在 `survey-system/` 目录下：

```bash
.venv/Scripts/python -m uvicorn app.main:app --reload
```

- 启动时 `app/main.py` 的 lifespan 会调用 `ensure_indexes` 建立唯一索引（幂等）。
- 访问 <http://127.0.0.1:8000/> 使用中文网页。
- 访问 <http://127.0.0.1:8000/docs> 查看并试用 JSON 接口。

指定端口或让应用连别的库：

```bash
SURVEY_DB_NAME=survey_demo .venv/Scripts/python -m uvicorn app.main:app --port 8017
```

## 5. 数据库隔离

- 业务数据库默认 `survey_db`，只由应用启动时读写，不做任何清理。
- 自动测试使用**独立**数据库 `survey_test`（见下），测试开始前清空的是该测试库，
  不会连接或清理 `survey_db`。
- 多个 worktree 并行跑测试时，可用 `SURVEY_TEST_DB_NAME` 让各自的测试库互不影响。

## 6. 运行测试

```bash
.venv/Scripts/python -m pytest -q
# 并行 worktree 指定独立测试库：
SURVEY_TEST_DB_NAME=survey_test_xxx .venv/Scripts/python -m pytest -q
```

测试以 HTTP 接口为主要观察入口，不直接查库断言。真实结果见 `docs/test-record.md`。

## 7. 本机 MongoDB 说明（如实记录）

- 本机 MongoDB 9.0.2 未注册为 Windows 服务；`mongod` 需手工指定数据目录启动。
- 本项目使用的数据目录为项目根下的 `.mongodb/data`（已加入 `.gitignore`），端口 27017。
- 启动与停止数据库属于环境操作，应用本身不启动、不重启 MongoDB。
