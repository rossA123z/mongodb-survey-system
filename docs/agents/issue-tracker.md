# Issue tracker: Local Markdown

本项目使用本地 Markdown 管理 Matt 原生规格与 tickets。

- spec：`.scratch/<feature-slug>/spec.md`。
- tickets：`.scratch/<feature-slug>/issues/<NN>-<slug>.md`，每张一文件，从 01 开始按依赖顺序编号。
- 当前 feature：`mongodb-survey-phase-one`。
- `Status:`：批准后可执行为 `ready-for-agent`，执行中为 `in-progress`，验收通过后为 `done`。
- `Blocked by:`：记录阻塞 ticket 的编号；只有全部阻塞项为 done 时才执行。
- 讨论与验证结果追加到 `## Comments`，保留历史。

“publish to the issue tracker”表示写本地文件；“fetch the relevant ticket”表示读取对应文件。关闭 ticket 前核对验收条件并记录证据，不创建远程工单。

项目自身的看板与进展记录保存在提交仓库之外的本地工作区，仓库内只保留原生产物。ticket 完成不自动代表整体交付完成，仍需核对完整交付要求。

无 Git/worktree 时不运行原版 implement-spec；准备后交接当前目录顺序实现与验证。
