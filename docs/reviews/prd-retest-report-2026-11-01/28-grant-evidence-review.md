# 28 现有代扣 grant 撤销现场独立复核

结论：**即时取消及现有grant持久撤销PASS**。另外保留“未来7天预留0”的**待核展示观察**，不把该显示值当作无未来义务的证明，不据此判定资金风险。本报告独立重算/比较原始receipts，未采用extra-grant-28.json的PASS字段作为判据。

head `8e2810e6764e3fd3e37406b31e694605751e0356`；代际 `demo_20261101131315_aca4156a`。本例plan `pln_01M6BMX3A37MGEEQD4VGVB5B6C`、step `cancel_music_sub`、customer cus_zs、目标si_zs_music/mer_music/blg_music。银行before/after采集时间13:30:58.308816/13:32:17.679109 UTC；六份收据的source head/代际一致，均独立RR+READ ONLY，两库非原子联合快照。

## 已核实事实

| 断言 | 独立结果 |
|---|---|
| 原句及作用域 | DOM为“取消乐听音乐的订阅”；独立SHA256=`a966f47d4825e37fdf877da6db3b0bcbca310b1ed5169d7d1026b2c715cee8d2`，匹配本轮typed原话utt_01M6BMWVCWGPCY65WYQ901NP5P及原canonical28。原话SCOPED仅MANDATE_CANCEL，instruction_ids=[si_zs_music]，from=acc_zs_chk。 |
| 确认前零写 | 28-before-bank与28-preconfirm-before-bank的全部已采语义投影相等，只有采集墙钟不同；当前计划尚无L3execution_intent/plan_execution/operation。grant也逐字段相同且仍ACTIVE、revoked_at为空；gateway当前计划PENDING_APPROVAL、approval PENDING/confirmed_at为空。 |
| 确认内容 | 真实面板目标si_zs_music/乐听音乐/月节省15元；余额及日限占用前后一致。DOM同时记录预留0（另见下文），最终页面显示同plan已完成、节省15/月和180/年。 |
| 确认绑定 | apv_01M6BMX3C37B48CX9EDZ8VE7ZM由PENDING→USED，confirmed_at=13:31:55.947241Z。L3evidence_summary的approval/approved_evaluation/plan_hash与网关一致；confirmed_view_hash=final_view_hash=`c56683566c7235e282d8a01dfcce42fcfaba675acd6be450acf267ec8184c14a`，与实际approval view_hash一致。risk=YELLOW、origin=agent、当前根scope/utterance均绑定；claim CONSUMED到本plan。 |
| 执行唯一性 | 当前plan/job均EXECUTED；L3唯一新execution_intent attempt1/COMMITTED，唯一新plan_execution及唯一取消step EXECUTED，结果instruction_id=si_zs_music、status=DELETED、no_op=false、monthly1500/annual18000。完整draft去arg_sources保留depends_on后独立重算digest=`1c832129c07413b8af8def37031b9ff8467aa8b9a83b9cde681c99d8d48c5325`，等于gateway steps_hash及L3request/steps digest。plan_hash在gateway/approval/L3一致。 |
| L3正确目标 | si_zs_music仅status ACTIVE→DELETED，其原customer/account/billing/merchant/max1800/source_plan/hash/时间字段不变；其他7条指令逐字段不变。merchant_billing不删、不失活、不改费率。 |
| operation | 只新增evt_7e77687d-4fd0-5de4-a2dc-0c82b807a88c（独立UUIDv5重算相同），action_kind=MANDATE_REVOKE、amount1500、target mer_music，准确绑定本plan/cancel_music_sub。 |
| 零资金移动 | accounts四累计量、184条transfers、175条transactions、ledger_totals全部相等；无新fund order/holding/reservation；活期600000分、储蓄2000000分、pending0保持。卡、客户/风险、收款人、拆账、产品、账单、事件job/execution/run、clock等所采非执行投影不变。只有目标status、当前执行记录、operation及cus_zs state_version3→4变化。 |

## 现有grant保留原创建证据

本例覆盖现有seed grant，并非08的缺失lazy墓碑分支：

| 字段 | before→after |
|---|---|
| status | ACTIVE→REVOKED |
| revoked_at | null→2026-11-01T13:31:56.153694Z |
| source_plan_id | pln_demo_seed，保持 |
| approved_risk | YELLOW，保持 |
| scope_hash | 30a149f8912bdf3143e8a344f272a3f61414e10c32b40eb53a708c7547e30d0d，保持 |
| scope_json | 原SEED_CONSENT、dataset_revision、manifest、完整授权条款及billing定义逐字段保持；没有替换成revocation墓碑 |
| created_at | 2026-11-01T13:13:32.148461Z，保持 |

整个grant行只改变status/revoked_at；所有其他grant逐字段不变。这吻合`gateway/app/grant_revocation.py:82`对已存在同客户grant只更新status及coalesce首次revoked_at的归档逻辑。没有将原source改成取消plan或把YELLOW伪升RED。此次是首次撤销；同一事件重放/重复取消保持首次revoked_at的行为未现场验证。

## 待核展示观察：未来7天预留0

**现场值及来源已确认。** pending和result DOM均显示“未来7天预留 ¥0.00”；28-preconfirm-before-gateway中原评估evl_01M6BMX3BZADMX5ZFBN08DCGKW的`preflight.scheduled_reserve_cents`确实为整数0；执行复演的同字段仍0。因此这不是单纯前端缺字段时自行补0。

**实际义务未归零。** 银行时钟仍2026-10-15 09:00+08；si_zs_video仍ACTIVE，blg_video仍active=true/amount3000，next_due_at=2026-10-18 06:00+08，落在7日窗口；本轮赵六指令已DELETED，妈妈11/5、云盘11/1、健身10/25均在窗外。音乐取消后仍有已知视频3000分义务；取消前音乐1500+视频3000为4500分。3000是从已采账单/指令推导的窗口义务，未在本复核调用live reserve接口。

**源码解释是未计算时保留默认值。** 精确head中：

- `gateway/app/pipeline/preflight.py:88`每次新建PreflightResult；其`context.py:118`的scheduled_reserve_cents默认0，公共`schemas/decision.py:80`也默认0。
- `preflight.py:97`的affects_reserve只包含transfer、subscribe_product、schedule_transfer；本例只有cancel_subscription，因而`109`不会调用_reserve，字段留0。成功取消预演本身仍正确更新影子instruction。
- `pipeline/decision.py:152`按该值构造公共preflight；`frontend/src/mobile/Approval.tsx:241`无“不适用/未计算”标识直接以“未来7天预留”展示该字段。
- 若进入_reserve，`preflight.py:212`赋的是**report_before.total_reserve_cents**，不是after值。因此不能未经接口语义决定就断言面板应一律改为3000；如沿既有before语义应4500，如表达取消后义务则3000，如纯取消不计算则需明确不适用/未知。

可支持的结论是：本例0来自“纯取消未跑reserve投影”的默认值，并不能表达未来没有扣费；字段/标签的产品语义需要后续确认。已采账本零变、取消严格仅缩权，没有任何证据表明这个显示值被用来绕过授权、导致短缺或产生未授权资金移动。本任务仅记录观察，不修代码、不将观察升级成资金漏洞。

## 未覆盖与采证边界

没有推进clock：10/20原音乐MERCHANT_CHARGE被MANDATE_NOT_ACTIVE拦截、CHARGE_BLOCKED唯一通知、到期event replay、UNKNOWN恢复、重复取消首次revoked_at保留均仍未现场验证。不得把本次即时PASS扩写成这些分支已PASS。

collector对自由文本与完整read response仅存摘要/哈希；evidence_summary没有完整authorization_evidence/epoch/evidence_history，未采outbox。本结论限于实际已采字段/摘要；不宣称全库逐列或完整授权证据逐字节独验，也不由DB/DOM推断物理点击者身份。只读文件/源码核验，全程未调用服务、数据库、模型、网络或Docker，仅写本报告。

## 证据文件摘要

| 文件 | SHA256 |
|---|---|
| receipts/28-before-bank.json | `0d071e563bfd0cd5be721be05442c7babeb368920ef3b1d15ccba9aeda44f5a5` |
| receipts/28-preconfirm-before-bank.json | `a9b56eb152e4bfcb6016f14d8f447036cc243419aa33dd3db8969da85e3ca153` |
| receipts/28-after-bank.json | `98f194b76dada18d1a2b228283c481a076637dd96a71d123688387f675e82fe4` |
| receipts/28-before-gateway.json | `4b8e7ebe39818093323a2aa6b74c1b74f45627f5631fff2339c46d3c223be95e` |
| receipts/28-preconfirm-before-gateway.json | `b340265aa143d35ccb0f551e19df0731a8a7f66496152aa853107e2c5c1651a1` |
| receipts/28-after-gateway.json | `edb81123d9f3d04708bb282a1ea98d94bd090fdf3f74f0ca8491a7838849ba4f` |

辅助记录：extra-grant-28.json、screens/28-pending.txt、screens/28-result.txt；二者结果声明均经原始receipts独立重核。
