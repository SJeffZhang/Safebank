# W5：定时授权、通知、审计与D接口（待W4合并后启动）

依据L2-plan §3 W5、PRD7.11/7.12/9.5/9.5a/9.7/10/11，及ADR-007/008/009。当前仅任务书准备，不代表实现授权已越过依赖门禁。每个子组件完成后先亲验/独审/PR/四CI再进入下项，不能整批堆齐。实现与评审均由本机DeepSeek CLI，协调者不写业务。

## 分解与门禁

| 组件 | 输入→输出 | 必测失败/边界 |
|---|---|---|
| W5a 客户审计与通知API | 真实gateway PG→G11/G12/G13/G16分页、客户隔离、未读计数、request幂等；复用audit链不另写hash算法 | 缺/错F+app、A越权、跨客户/不存在统一404、篡改检出、重复标读不重复副作用、时间/游标非法 |
| W5b outbox消费者 | 真实L3已提交outbox→按event_id幂等的workflow/grant/通知及ack | ack前崩溃重放；不同客户序号逆序提交仍能补齐（每轮从0扫描，after非永久水位）；grant精确内容不可扩大；不能靠无PG内存去重 |
| W5c S1到期授权 | S令牌+bank-net回调event_id/attempt→自己读L3不可变事件、客户锁内检查grant/风险/版本后主动执行或block；终态先查event_decisions | 伪金额/extra字段、事件未到期/哈希不符、grant缺失补建与REVOKED区分、风险升级SKIPPED、余额不足、同事件重放不补扣、先成功后重放不覆盖、基础设施失败RETRYING不推进周期/写run |
| W5d D接口 | DEMO/EVAL且console凭证→白名单场景/事件/时间/计划比较与断言代理 | 两开关互斥、关闭不注册、A/F客户凭证不可当console；D6仅case原sid/plan进同G4/G8/G5，不代批；bank_ro固定断言无任意SQL；OTP不进LLM/日志；D9仅已有本地报告 |

## 所有权与依赖

开始前协调者核最新main冻结具体文件清单。默认仅gateway app/notify、internal/scheduled、api/notifications、api/audit、demo目录与专属tests/gateway/w5；可读共享客户锁、L3Client、audit/状态接口。shared runtime/main路由挂载、矩阵、表/迁移、contracts、L3/scheduler、依赖锁全冻结；缺字段/状态原语或路由装配先报告，另派共享owner串行补，W5不得自行越界。

S1依赖L3 /internal/events、event execute/block、outbox；尚未交付时按PRD契约fake验证并列待联调，不计真实S1完成。到期执行不能生成run_token或让scheduler持可复用写许可。不得直接写银行库或从agent网连内部口。

全栈/网络/实际调度验收由协调者在main-baseline串行执行。W5独立PG使用W0 tmpfs机制；专项必须零skip。退出：每子组件make lint、test-gateway、test-gateway-db、test、test-w5（仅自身真实PG/纯测）与actionlint；涉及实际银行写后追加协调者P1并发/一致性、真实HTTP幂等/失败后重放。验收报告区分fake与真实未覆盖。

单条shell命令，不用 ;/&&。只本地提交，末尾Co-Authored-By: DeepSeek V4 Pro via Claude Code <noreply@anthropic.com>。进度docs/progress/W5-ops.md，最终简短。前端/BFF不开工；禁止数据卷删除重建、prune、reset/TRUNCATE现有业务库或worker运行全栈命令。
