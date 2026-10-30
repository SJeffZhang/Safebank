# ADR-012: 演示推进与周期事件身份（2026-10-30）

## 问题与证据

D3此前是503桩，L3推进不自动扫描未来义务，无事件时也不到target。
真实PG红测同时发现：同一实际时刻的+08/UTC period_key文本会产生重复job；
UNIQUE(payload_hash)则会阻止同一周期指令/商户账单以同一载荷产生下一期job。
旧码4项时钟红测失败；独审新增时区/调度竞争2项红测失败；事件身份2项红测失败。

## 决议

- 前向迁移0010_event_identity：载荷哈希只做完整性校验与索引，移除全局唯一；
  新增UNIQUE(kind,source_id,due_at)，保留source_id+period_key约束。旧数据冲突时迁移失败，
  不删除、合并或重写历史记录。
- 扫描按业务source+实际时间复用既有job，避免首期/后继任务的时区文本差异。
- 共享Scheduler进程内串行dispatch，保证同due的rank顺序；DB事务在S1回调前结束。
- 推进按事件边界移动，最后到target；基础设施重试有界，未完成保留RETRYING。
  统计从本批跟踪的L3终态读取，包含后台完成的任务。
- D3保持DEMO/EVAL与console JWT鉴权，写DEMO审计；同request_id参数冲突409，
  过去时间/无时区422。响应advance_job_id与PRD一致。
- 控制台保存作业身份；超时查询原作业，恢复使用同一request_id与target。
- 代际切换在build前保留前代容器image ID并固定其overlay，避免新版本构建覆盖tag后
  回滚把新schema运行时配到旧库。管理凭据仍只在本机忽略文件。

## 边界

本次不增加多网关、多Agent或对比功能。推进作业元数据仍为单进程内存状态；
重启后不凭404推断事件未执行，金融结果以L3持久化事件为准。生产HA恢复属后续范围。
不放宽任何用户授权、审批、OTP、金额或适当性规则。

## 2026-10-31 S1 contract closure

Real live advance exposed a mismatch hidden by rich event fakes: L3 returned no
instruction terms, merchant billing IDs were treated as transfer instruction IDs,
and refusal without a pipeline snapshot used empty version fencing. Associated
terms now come from L3 rows and are checked against a fresh snapshot. Merchant
preflight calls the same pure bankcore event applier as execution, with mandate
evidence. Execute/block outcomes are always reconciled to L3 terminal truth.

Seed consent is installed explicitly by the trusted local reset CLI into new,
unpublished databases, with exact fixture comparison and per-instruction hashes,
original first-run dates, dataset/manifest provenance and transactional audits.
There is no runtime seed bypass and no fabricated plan execution. Normal schedule
creation stores original instruction terms in its committed operation event;
backfill requires the corresponding real step result, approved evidence risk,
original terms, fingerprint and recurring due instant. Older incomplete history
fails closed. Source API access remains internal L2→L3 only.

Pure event appliers return consumed source cursors in obligation state; EventService now persists
that run's next_run_at/next_due_at in the same transaction as ledger effects/run/event terminal.
Both execute and block consume a period; rollback preserves all of them, replay consumes none.
