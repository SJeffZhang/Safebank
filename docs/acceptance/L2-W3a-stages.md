# L2 W3a：快照、预演、工作流、决策阶段验收

2026-10-24；业务438c0352fd57257062e4ba7954b0f409523d09e8；同步main22aeb3f后7951daaa903106569137873eaf11d254bc1de80e。原独立DeepSeek reviewer最终APPROVE，无残留；报告docs/reviews/W3a-stages-review.md保留初审与两轮修复。

四阶段复用唯一快照、共享bankcore.apply_plan/projected_shortfall；累计与B1独立预演、跨会话成功历史及冷静期、DENY优先和只收缩hint。真实PG/HTTP45项验证余额600000/reserve4500与hint295500；早DENY/六profile无preflight不崩溃，不伪造executable；300000+298000仅将第二步降295500并重新预演通过；金额展示全整数；YAML阈值可变生效；duplicate用plan/job双EXECUTED终态updated_at_wall（sim时间不参与），慢执行/自身/跨客户/未来/过期/状态不一致和终态CAS重试不刷新均有真实PG回归。

初轮发现preflight=None崩溃、hint扩大金额及float/死配置/重复时窗错误已由原worker红绿修复，并独审关闭。PreflightResult仅owner增加7个带默认字段，经协调者精确批准与独审；未改EvalContext/Stage/旧字段或冻结契约。

亲验438c035九退出全绿：lint257，gateway1928/478skip（7.76s），PG17（2.07s），全量2179/538skip（8.32s），W3a45真PG/HTTP零skip（3.49s），W2246（11.04s），义务30（4.59s），contracts/actionlint。同步main仅AGENTS文档冲突、双方历史均保留，gateway/tests/packages零diff；组合亲验lint273、gateway1928/478skip（7.75s）、PG17（2.07s）、全量2240/538skip（10.86s）、W3a45（3.46s）、W2246（10.89s）、义务30（4.60s）、contracts/actionlint全绿，既有FK环warning保留。日志/private/tmp/sbg-codex-w3a-r2-20261024及/private/tmp/sbg-w3a-main-pr19-combination-20261024。

边界：只有阶段与独立临时数据库/真实L3 snapshot HTTP，无完整G4/审批/执行；evaluation_id/revision由后续W3b分配。领域写只P1子集，其余TOOL_NOT_ALLOWED不伪造成功。W4b→W3b→W4c、真实L1三层闭环、P3整体攻击验收仍待后续；不称P2/P3完成。前端/BFF暂停，长期业务卷未变。
