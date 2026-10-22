# MongoDB 问卷系统

数据库课程项目（第一阶段）：基于 MongoDB 的在线问卷系统。

支持用户注册登录、问卷创建与发布、单选 / 多选 / 文本 / 数字四种题型及各自的限制
校验、可配置条件跳转与返回修改、匿名与防重复填写、截止时间与手动关闭，以及问卷级与
题目级统计。全部业务数据存储于 MongoDB。

## 快速开始

见 [survey-system/README.md](survey-system/README.md)：安装依赖、启动 MongoDB、
运行服务与执行测试的完整步骤。

## 交付文档

- [项目报告](survey-system/docs/项目报告.md)
- [MongoDB Schema 与建模依据](survey-system/docs/mongodb-schema.md)
- [系统与数据库设计](survey-system/docs/design.md)
- [API 与关键逻辑](survey-system/docs/api.md)
- [配置与启动](survey-system/docs/configuration.md)
- [网页操作验收步骤](survey-system/docs/acceptance-web.md)
- [测试记录与真实结果](survey-system/docs/test-record.md)
- [AI 使用日志与总结](survey-system/docs/ai-usage-log.md)
- [设计决策记录（ADR）](survey-system/docs/adr/)

## 规格与工单

- [规格](.scratch/mongodb-survey-phase-one/spec.md)
- [任务拆分](.scratch/mongodb-survey-phase-one/ticket-proposal.md)
- [工单目录](.scratch/mongodb-survey-phase-one/issues/)

## 说明

课程原始资料、运行时数据、虚拟环境与凭据不纳入仓库。
