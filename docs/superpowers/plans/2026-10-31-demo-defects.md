# Demo 报告缺陷与六场景开发修复计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 修复22a8779真实浏览器报告已证实问题，逐项核对29需求和生产调用链，补齐基本业务缺口。
**Architecture:** 保留现有L1→L2→L3边界；修复在原有组件中，不改变确定性授权判定权。三个独立工作树并行，协调者串行集成、真实DB自动化、完整CI与发布。
**Tech Stack:** Python/FastAPI/LangGraph/PostgreSQL；TypeScript/React/Vite。
**Spec:** PRD §§5/6/7/9；docs/reviews/demo-report-2026-10-31/四份原始报告及SHA256。

## Global Constraints

- 金额整数分，份额Decimal；不泛化信任scope，不放宽来源/JWT/epoch/确认/OTP绑定。
- 所有旧库/卷/worktree/未提交素材保留，不并行部署完整栈；子agent不调用真实API/模型/DB。
- provider402/403/429立即停止相关调用/批次，不重试不换模型；不用DS worker。
- 本轮开发修复与自动化验证，不重演截图/彩排；实际两轮六场景/43/生产断网仍须单独证据，未全过不tag。
- 每单元根协调者更新AGENTS §3/§4，接口变更同步PRD；公开进度暂停。

## Review Focus

- 已执行100元与读到5000元限额共存时不能替换业务金额，部分失败/错plan不能报成功。
- 问卷内容与版本均绑定确认hash，未知版本/缺题/重复仍拒绝；Agent无测评写权。
- NO_WRITE→SCOPED只装当前凭据声明根，旧写原话不扩权，父链/过期/撤销/epoch边界保留。
- UI月范围取真实请求/读取元数据，审批UNKNOWN/失败/终态不混显；未知产品类型不伪装成混合。
- 事件/通知按客户、不可变事件定义、模拟时间及唯一键绑定，失败重试/恢复不漏报不重复、不泄漏秘密。

## Task 1 — 成功回执事实（独立runtime工作树）

Files: agent llm/respond.py、verify.py、新私有helper；tests/agent相关；独立acceptance。
- [ ] 实际10000分/余额590000/限额500000旧码红测，含多步骤/错绑定/部分失败负控。
- [ ] 当前plan/step绑定确定性回执，缺事实保守，不触发二次执行。
- [ ] 相关测试、独立只读评审、提交。

## Task 2 — 测评面板与当前原话根（独立gateway工作树）

Files: gateway approval/planview.py、api/plans.py；对应纯测和真实PG回归。
- [ ] 六题合法FORM旧503红测，权威问卷展示并绑定hash；非法/Agent负控保留。
- [ ] 同会话NO_WRITE→X2旧空证据红测，按声明/JWT装根和父链；旧SCOPED/跨客户等拒绝。
- [ ] 分单元提交、独立安全只读评审、真实PG由协调者串行。

## Task 3 — 前端一致性（独立frontend工作树）

Files: frontend/src/mobile组件、types/preview和测试；必要BFF另行划定。
- [ ] 真实四枚举与unknown旧错标签红测，统一映射。
- [ ] 9月/上月请求的实际读证据绑定内嵌报表；空结构化卡不渲染假事实。
- [ ] 聊天与确认弹窗使用当前权威终态，取消/失败/UNKNOWN及刷新负控。
- [ ] 六场景真实wire定点审查，专项/全量FE/BFF/build及独审。

## Task 4 — 基础事件入口（协调者工作树）

Files: gateway api/demo.py、l3/client.py、新路由测试；既有L3 demo.py规则复用。
- [ ] D4合法AA付款/外部事件旧stub红测，明确请求/响应契约、console+模式双门禁。
- [ ] 走L2代理注册L3不可变事件、S1相同bankcore执行，不在预演写状态；幂等/内容冲突/非法负控。
- [ ] AA进度读取与账本状态真实PG自动化，独立安全评审。

## Task 5 — 六类客户通知（后续空闲agent或协调者）

Files: gateway notify.py、internal/scheduled.py；bank scheduler.py/events.py；对应纯测与PG。
- [ ] 把现有audit结果和客户通知区分，红测扣费提醒/拒扣/定时/基金事件。
- [ ] 日模拟09:00三天扣费提醒唯一(账单,due_at,reminder)，只固定模板；事件终态与通知同事务、恢复补写幂等。
- [ ] 保留grant/订单/事件校验及客户隔离，PG恢复/重复/时钟边界与独立评审。

## Task 6 — 29需求遗漏核对与修复

- [ ] 逐项PRD ID→生产入口→核心逻辑→自动化证据，区分未验收与缺实现。
- [ ] 定点核手机号typed转账，按权威联系人phone匹配和原始span，不引入任意外部账号授权。
- [ ] 本轮发现且有重现的基本功能缺陷继续红绿修复；Preview专属能力与攻击/D8不扩为基础任务。

## Integration

- [ ] 各独立修复review通过，串行集成并更新AGENTS/PRD和最终矩阵。
- [ ] 旧测试库先唯一改名保留，最终完整40位SHA全部本机CI PASSED才发布/合并；实际Git远端状态复核。
- [ ] 合并后组合回归，保留未执行的演示冻结门禁和完整证据，当前工作树指向最终main。
