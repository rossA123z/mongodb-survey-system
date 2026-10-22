# ADR 0004：跳转路径由前端与服务端各算一遍

- 状态：已采纳
- 日期：2026-10-17
- 相关工单：`.scratch/mongodb-survey-phase-one/issues/04-conditional-jump-and-back-edit.md`

## 背景

填写页需要在填写过程中即时按规则显示 / 隐藏题目，为此 `app/templates/fill.html`
内联了一段 JS 按规则推进路径；提交与预览时服务端又必须独立重算有效路径
（`app/responses.py` 的 `compute_path`）。两处实现同一套规则语义（第一条命中生效、
命中「结束」停在确认页、可选题为空不触发、规则只向后跳）。

## 决策

保留两份实现：前端 JS **仅供填写体验**（即时显隐），不作为权威；服务端始终从第一题
重算路径并据此校验、保存，客户端声称的路径与路径外答案一律不信任。

## 理由

- 服务端必须独立重算，否则伪造路径 / 伪造路径外答案即可绕过校验与统计。
- 前端若改为每次改动都调接口问路径，会变慢并增加往返与复杂度，收益仅是「少一份
  实现」；而显示正确性最终由服务端保证（预览与提交时都重算）。
- 因此这是一个有意为之的双实现，而非遗漏去重。

## 影响与取舍

- 存在**漂移**风险：规则语义若变更（例如新增条件 op），需同时改 `fill.html` 的 JS 与
  `responses.py`。漂移的后果只表现为「页面显示的题与服务端收下的题不一致」，不会写入
  错误数据——服务端仍是唯一权威，路径外答案会被剔除。
- 服务端剔除伪造路径已有测试覆盖：`tests/test_jump_pages.py::test_fill_confirm_excludes_skipped_question`
  （被跳过题不出现在确认页）、`tests/test_jump_rules.py::test_forged_offpath_answer_is_dropped`
  （伪造的路径外答案不校验、不进入答卷）、`tests/test_stats.py::test_offpath_answer_excluded_from_stats`
  （路径外答案不进入统计）。
- 若第二阶段引入更复杂的规则，可评估让服务端成为唯一权威（例如由服务端返回路径、前端只
  渲染），以彻底消除漂移；当前阶段不值得为此增加往返。
