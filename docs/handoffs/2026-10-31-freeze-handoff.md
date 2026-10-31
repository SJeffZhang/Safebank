# SafeBank demo-v1 冻结交接（2026-10-31）

> 接手顺序：完整阅读本文件、根目录 `AGENTS.md`、`PRD.md`，再查 GitHub 和本机状态。
> 本文件记录截至北京时间 **2026-10-31 15:03 起核对**的事实。用户此时要求交接，已停止推进新功能。
> **基础版尚未冻结，没有完成六场景两轮彩排或断网回放。不要打 `demo-v1`。**
> 仓库：<https://github.com/SJeffZhang/Safebank>；账号 `SJeffZhang`。

## 1. 原计划及不可改变的优先级

用户已明确批准实施《SafeBank 基础功能冻结与演示交付计划》，并特别强调先冻结基础功能，暂不做对比。

1. **第一阶段：基础版 `demo-v1`。** 六场景真实跑通、通知审计、可重复初始化、真实时钟事件和离线回放。六场景必须从两个独立初始数据集连续完成两轮 live；完整43条表如实标通过/失败/未验证。
2. **第二阶段：攻击演示 `demo-v1.1`。** 基础版稳定后，独立分支实现 D6/D7、A2/A3/A6 的 B2 拦截、L3证据和控制台；保留 `demo-v1` 可随时演示。仍不做对比。
3. **最后可选：D8 和 B0/B2 小样本指标。** 前两阶段都稳定才做；不是前两版门禁。

原排期：10-31冻结基础版，11-01攻击/录制/材料，11-02只修阻塞并由用户上传。现在已经10-31下午，不能把时间目标写成已完成事实。削减顺序：对比指标→非核心只读修复→界面润色；不能削审批/OTP、资金一致性、06/36、独审、本机CI、真实状态验证和可重复初始化。

### 必须继续遵守

- 实现由接手同事本人完成，不用 DS worker；安全/资金路径用独立只读子代理评审。不要让模型决定授权。
- 分支用 `feat/`、`fix/`；本次文档用 `docs/`。不新建带 codex 的功能分支。历史带 codex 的分支不必改名。
- 每组件独立提交、验收、PR。修复证明新测试旧代码失败、新代码通过；最终完整 SHA 跑全本机CI。修复类还要干净栈 live、L3 SQL核对；评审后实质修改重验受影响部分。
- 长期栈和干净栈**串行部署**；不删卷、不删库、不删历史 worktree，不覆盖WIP/密钥。重置采用同PG实例创建新代际数据库，绝不是补种冒充恢复初始状态。
- DeepSeek仍为项目指定模型；遇402/403/429立即停止相关请求和批次，报告，不重试、不换模型。业务REPLAN/来源拒绝不是额度错误。
- 每个工作单元更新 AGENTS §3/§4；接口变化同步PRD；合并后更新公开进度。
- 不上传比赛平台，除非用户另行授权。不要把PIN、OTP、JWT、API key或数据库连接密码写进日志/录制/文档。

## 2. 真实代码/PR现场

远端main已核实为 **`74baf71e938fdbda7bf9432019ef21cd2a796c5e`**（#103合并）。本次基础冻结续作有两个草稿PR：

| 项目 | 分支/提交 | 现场与下一步 |
|---|---|---|
| #104 FORM G4/G8 | `feat/form-authorization`；本地和已推远端最新 **`7ceda71db5aae1c8293aa60ee6f526f54c1641e8`** | 独审、专项、真实干净栈表单转账/冻结已通过；**完整CI未完成，不得合并**。先重跑精确head完整CI，再收尾合并。 |
| #105 部分赎回 | `fix/redemption-sources`；**`48805167370b9779f7a828b7f8a077432fb6d7a9`** | 代码、红绿、独审和组合通过；**未跑真实L1赎回/结算、未跑最终完整CI**。基于main74baf71，尚不含#104；等#104合并后先rebase。 |
| #82 | `codex/pages-preview-deployment` | 无关旧PR，按用户要求不处理。 |

链接：[PR104](https://github.com/SJeffZhang/Safebank/pull/104)、[PR105](https://github.com/SJeffZhang/Safebank/pull/105)。不要根据旧10-30交接仍说#96是打开草稿；#96等早已合并。

### 活动checkout

| 绝对路径 | 当前用途/状态 |
|---|---|
| `/Users/zhangshijie/Desktop/Project/Fintech2026` | #104，`feat/form-authorization`，7ceda71；核对时工作区干净。 |
| `/Users/zhangshijie/.codex/worktrees/schedule-provenance/Fintech2026` | #105，`fix/redemption-sources`，4880516；核对时工作区干净。目录名沿用06工作树，**当前已经不是06分支**。 |
| `/Users/zhangshijie/Desktop/Project/Fintech2026-wt/main-baseline` | 长期栈部署/本机CI专用；现在detached **7ceda71**，不是main。最后一次CI把它切到该提交；不要一边CI一边手动切换。 |
| `/Users/zhangshijie/Desktop/Project/Fintech2026-wt/e2e-deploy` | 干净环境运行checkout；detached **f6479e1**（#104真实live候选），含未跟踪本机覆盖配置，应保留。 |
| `/Users/zhangshijie/.codex/worktrees/freeze-handoff/Fintech2026` | 本次交接文档工作树，`docs/freeze-handoff-2026-10-31`，独立从main创建，未夹带#104/#105实现。 |
| `/Users/zhangshijie/Desktop/Project/safebank-progress` | 公开进度仓库main，最近已推 **38ea028**（记录#103）。 |

历史其他worktree很多，未清理。先 `git worktree list` 看，不要统一 reset/clean/archive。

## 3. 第一阶段计划 A–F 实际进度

| 原计划单元 | 当前结果 | 剩余 |
|---|---|---|
| A 接手基线/验收工具 | #97交接、#98驱动保护完成；单用例超时、quota中止、唯一日志和manifest已合 | 六场景/43条的最终证据包仍未形成；不能把脚本exit0当业务通过。 |
| B #96通知/审计 | 已合并并独审/本机CI/live验收 | 六场景中仍需观察通知与业务一致，回归不得倒退。 |
| C 可重复演示初始化 | #99已合并；代际新库、旧库保留、隔离角色、切换失败恢复、旧登录失效验证 | 最终彩排每轮重新reset；不要沿用已经修改的当前数据集。 |
| D D3真实时钟/S1 | #100已合并；跨两期定时/代扣/基金申购结算、取消后BLOCKED、账本/审计通过 | 34赎回订单和结算完整live仍待；最终六场景再覆盖时钟。 |
| E 06/36/23/34/09缺口 | 06 #101、36 #102、23 #103已合并；表单#104/#105赎回待收尾；09最新仍REPLAN | 优先#104→#105→09，再保留一条准确账单分析路径；非核心只读靠后。 |
| F 六场景彩排/回放/冻结 | **未完成** | 两个新数据集六场景live、43条清单、生产录制/断网回放接线、冻结清单、门禁满足后tag。 |

### 已合并组件与证据，不必重做实现

| PR | 功能/合并 | 已有验收 |
|---|---|---|
| #98 | 驱动额度/超时保护，0caeb03 | 最终4b1859a完整CI，unit3504；quota退出75/timeout124，批次中止。 |
| #96 | 通知/审计，b5ef51c | unit3508及真实PG/独审；真实L1转账出现待确认/结果通知，客户隔离、幂等、事务路径。 |
| #99 | 代际初始化，7c3c045 | 连续两次初始摘要相同，旧库保留已执行交易，旧token401，故障回滚与隔离。 |
| #100 | D3/S1，7b6d5ab | 最终a26c764完整CI；unit3536、前端400/BFF/build、obligations227/S1 29；16真实终态=14SUCCESS/2BLOCKED，两期代扣/定时、基金结算/零冻结、账本与审计一致。 |
| #101 | 06周期/首次时间来源，0646844 | 最终84aeb90完整CI；W2 269。真实每月20号赵六200元，10/11月各执行一次合计40000分，下次12-20 08:00；真实源计划/授权绑定。 |
| #102 | 36闲置取消+定时储蓄，a387c8e | 最终b644569完整CI，unit3541；真实L1仅取消健身，创建每月1日本人储蓄19900分，两期共39800分，健身扣费两期BLOCKED，其他三订阅ACTIVE。 |
| #103 | 23挂失，74baf71 | 最终63bfd47完整CI，unit3552/1040条件skip，W2 274；真实李四RED审批/OTP/确认，唯一卡LOST，无补卡/无转账，一条operation，账本平衡。 |

证据文件主要在 `docs/acceptance/`：`demo-clock.md`/`demo-clock-live.json`、`schedule-provenance.md`/`schedule-provenance-live.json`、`idle-subscription-savings.md`/`idle-subscription-savings-live.json`、`lost-card-source.md`/`lost-card-source-live.json`。精确CI以日志和PR评论为准，文档中的早期“pending”段落是过程记录，须向下读最终记录。

## 4. #104详细接手：优先收尾

### 已实现

- F只接受严格 `AppFormPlanRequest{request_id,session_id,form_action_ids}`，不接受浏览器上传steps/args/origin；由凭证决定app_form。
- 从持久化FORM生成步骤与action来源；仅内存 `UtteranceRef(kind=action)`，**不伪造utterance行**。
- G4校客户/sid/kind/期限/epoch/字段/used_by；最终session/action锁下再校，绑定与plan/审批/审计同事务。按请求顺序生成步骤，按排序锁来源。
- 同request_id先按原body摘要重放，不能因FORM已绑定误拒；不同请求不得复用。
- G8期限读FORM；三个执行/屏障/暂停恢复路径支持app_form；终态消费通用claims，保留used_by，不能把act_id UPDATE到utterances。
- 共享 `execution/replay.py` **创建fresh context后**，用 `fresh.bank_state()` 重建FORM来源与污点，和预演用同一个正式快照。A保持每次新快照的语义。
- FORM污染证据取本客户跨会话合法读记录及L3近90天不可信文字；不能一律信任F。G4末次比较完整授权视图，污染/范围/时间/epoch变化拒绝旧审批。
- 新增forward-only `0005_form_epoch`：nullable `plans.authorization_epoch`。G4捕获会话版本，执行要求它等于当前版本；旧agent不变；缺捕获版本的旧FORM fail-closed。scope_ids与来源IDs都须一致。

### 评审发现及已修复的坑

1. 末次只比FORM行/epoch会漏掉新跨会话污染：补完整授权比较，红绿验证。
2. G4来源快照与正式预演混用：改正式快照重建，旧测试YELLOW/新RED。
3. G8调用方缓存会被共享replay丢弃：不能仅在三个调用方先加载。已移入共享fresh replay；独立G8回归旧False/新True。
4. 已绑定FORM丢used_by仍加载：执行加载严格require_bound。
5. 仅撤销授权阶段epoch改变、cleanup未完成的helper级回归：用持久化捕获epoch拒绝，不依赖稍后清理。
6. 旧通用replay测试夹具没有FORM数据库却标app_form：改为agent，**没有为兼容夹具放松生产校验**。

独立评审 `/root/review_notify_audit` 最终APPROVE；schema增量亦APPROVE并亲跑DDL drift。评审结论在本会话，关键修正/验证已写acceptance，不要假设子代理会话可由新同事继续使用。

### 验证现状

- G4/来源/race/快照17项以及迁移组合20通过（当时组合包括不同套件数量，以日志为准）。
- 真实L2/L3两库8项：绿冻结、黄200元转账、红挂失OTP、UNKNOWN丢回包恢复、STATE_CHANGED→PAUSED→attempt2、撤销/过期/丢绑定拒绝；一次L3提交断言。
- 组件组合295通过；全量unit3552/1065条件skip。
- **干净部署live已过**：候选f6479e1，新代际`demo_20261031014249_e694d3ac`，F/app真实HTTP黄20000分转账与绿冻结；重复G4/G8不重复写。SQL两计划EXECUTED/epoch0、两永久绑定/two CONSUMED claims、零假utterance、一笔20000分、卡FROZEN、审批USED/YELLOW、账本平衡、迁移0005。这是F/app实际L2/L3，不能称六场景L1彩排。
- 文件在**#104分支**：`docs/acceptance/form-authorization.md`和`form-authorization-live.json`；本交接docs分支基于main，因此尚不包含这两个文件。

### CI最后停在哪里（必须诚实）

第一次完整CI，a9cb028，在 `test-wave1-state` 的旧0001升级保留测试失败：新增nullable epoch列没有列入测试增量列清单。补 `BIGINT/NULL`，保留原字段逐项相等；该迁移schema3项随后通过。修复提交7ceda71已推。

第二次完整CI精确7ceda71：`/private/tmp/sbg-form-final-ci2.log`。已通过unit/lint/format、bootstrap/up、gateway-db、customer-locks、wave1-state231、shared-runtime、agent-db、W1 1794、obligations227、W2 274、W3a45。**日志停在W3b，未出现LOCAL CI PASSED；15:03核对时无对应进程。** 原因未确认，不能说还在跑或已通过。

下一步：排查未完成W3b的日志/进程是否有外部中断，**重跑完整精确head CI**。不要只补后半段就宣称完整最终门禁。通过后更新PR104正文/评论、ready、完整head匹配合并；再将长期栈部署回合并main并同步公开进度。

## 5. #105详细接手：部分赎回

### 改动与理由

旧L1把 `redeem_product.units` 强制来源于get_holdings，总持仓会取代用户明确的部分赎回；原话明确1000份时反而被局部拒绝。

- 份额只取typed原话的明确数字span；本地检查精确Decimal数量并要求先读get_holdings。持仓只提供实体及可用上限，不是授权数量。
- 网关 `taint/text.matches` 对units精确Decimal匹配，允许带“份”的数值；继续所有外层JWT、原话期限、客户/会话、scope、产品/账户与可用份额检查。没有“任意scope相等就可信”捷径。
- `from_strict_plan`只对真正 `type(x) is int` 的redeem units精确转四位字符串；float/bool拒，冻结strict schema未改。
- 原针对无效span的负测试仍保留；不接受无数量时默认全部赎回。

旧Agent/转换测试3fail3pass，新专项11pass；真实G3/JWT/PG旧1fail5pass，新6pass（整数/小数、数量篡改、产品span、过期、跨会话，且完整IntentStage正负断言）；Agent/契约718pass39skip，W2+真实基金执行/事件289pass。独立只读评审APPROVE并亲跑11。评审建议的0/负/超4位精度/numeric上界/无holdings/带份span边界可按需要补，但不应为赶时间削安全。

**尚未做**：任何#105的干净栈真实L1、审批、份额预留、订单、推进结算及SQL；最终精确head完整CI。不能合并。

### 正用例数据纠正

实际读取当前 `services/bank-backend/fixtures/l1_seed_v1.json`：**李四 `cus_ls` / `acc_ls_chk` / `prd_mmf` 安心货币已有10000.0000份**。此前上下文“李四没持仓、改张三”是误推断，已实查纠正，不要沿用。原43条中的34可直接用 `赎回安心货币1000份`。

下一步：#104合并后，把#105 rebase到新main，解决AGENTS/PRD冲突时两边日志都保留；先专项复验，再新代际live34。预期只赎1000、不赎全10000；验证available/reserved units、PENDING_SETTLEMENT订单、结算时点、最终持仓/现金、账本守恒；再最终完整CI、独审增量、合并。

## 6. 09 AA现场新发现

在#104的当前干净数据集上复测：`昨晚聚餐400元，我和小林、小周、小陈AA`，run tag `aa-f6479e1-09`，**REPLAN，未执行**。

- plan `pln_01M67TJ1JH6GGE9YZMHK6NH0TF`。
- 模型实际memo已经为null，不再是交接中“推断聚餐memo”的老现象。
- account_id合法read、total_cents40000原话span `[4,8]`、三个participant IDs原话span `[11,19]`；`include_self=true` **没有arg_source**。
- 持久化findings仅一个 `UNKNOWN_SENSITIVE_ARG`，未标param。**最可疑是include_self缺源，尚未写红测或修改，不能当已验证根因。**

先查该plan及parser scope/策略敏感参数，用新增测试定位；“我和”对应include_self需确定性证明，不能泛化所有bool或信任模型。保持memo逐字/留空、参与人总额分摊一致。用新数据集验证真实AA结果与L3数据库。此用例当前没有业务写入，不是通过。

## 7. 环境、初始化、日志与秘密边界

### 两套栈

- 长期：`main-baseline`，compose project `safebank`；网段172.29.*。
- 干净：`e2e-deploy`，project `safebank-e2e-1029`；网段172.30.*，驱动GW_PUBLIC=172.30.10.12。
- 此处地址以现有compose为准，运行前只列服务/端口，不把完整环境展开到日志。不要 `docker compose config` 输出含密码的全文。

干净栈核对时7服务running/healthy：agent、bank-backend、frontend、gateway、operator、postgres-agent、postgres-bank。healthy不等于业务验收。

当前generation：`demo_20261031014249_e694d3ac`；bank/gateway/agent数据库名均为`<kind>_demo_20261031014249_e694d3ac`；模拟时间2026-10-15 09:00北京时间，seed l1_seed_v1.0。**已有表单转账和冻结修改，不能作为下一轮初始状态。** 当前checkout代码f6479e1/gateway0005。机器摘要在同目录 `2026-10-31-state.json`。

### 密钥位置（只描述位置，不复制内容）

沿用旧交接与本机`.env/.secrets`。常用演示PIN文件：`/Users/zhangshijie/Desktop/Project/Fintech2026-wt/W0-foundation/.secrets/demo_pins`，JSON客户→PIN。e2e-deploy的`.secrets`为既有符号链接，保留。代际私钥/JWT及连接覆盖文件位于e2e-deploy的私有 `.demo-state/<generation>/compose.yml`等，mode0600，不入库、不cat到对话。旧库/overlay/镜像标签都保留用于恢复。

### 本机CI（不要传字面HEAD）

在目标PR checkout工作区干净时：

```sh
cd /Users/zhangshijie/Desktop/Project/Fintech2026
verified_ci_sha=$(git rev-parse HEAD)
bash docs/handoffs/2026-10-30-tools/local_ci.sh \
  /Users/zhangshijie/Desktop/Project/Fintech2026 "$verified_ci_sha"
```

对#105把上面两处路径换成其managed worktree。脚本将main-baseline切到传入的SHA并重启长期栈，**必须传完整具体SHA**，不能传`HEAD`让deploy checkout自行解释。日志 `/Users/zhangshijie/Desktop/Project/Fintech2026-wt/.ds-logs/local-ci-<shortSHA>/`。前端未变时按workflow path filter跳过；改变前端就必须跑npm test/test:bff/build等门禁。

GitHub Actions此前因账号额度无法跑runner，AGENTS已有使用本机完整等价门禁的授权。不要把远端因额度失败说成代码CI已通过；以本机最终日志与退出码为证。

### 初始化/启动

在干净部署checkout选定候选提交后：

```sh
cd /Users/zhangshijie/Desktop/Project/Fintech2026-wt/e2e-deploy
git checkout --detach <已验证候选完整SHA>
.venv/bin/python -m scripts.demo_environment reset
# 或 make demo-reset；demo-up只启动/验证当前generation，不恢复初始业务状态
```

reset会保留旧库，在现有PG内建新库/角色、迁移/seed/验证后切换；失败恢复旧连接与镜像。禁止同时部署长期栈、禁止down -v、禁止drop库。

### 单例live与证据

原工具已经在main `docs/handoffs/2026-10-30-tools/`：`local_ci.sh`、`run_full2.sh`、`drive_try_agent.py`、`snap3.sh`、43条`full_cases2.tsv`。脚本选择数据集来自当前运行容器，输出manifest/前后快照/脱敏trace。

```sh
# 在目标源码checkout；仅生成单条表，不改43条基表
python3 - <<'PY'
from pathlib import Path
rows=Path('docs/handoffs/2026-10-30-tools/full_cases2.tsv').read_text().splitlines(True)
Path('/private/tmp/sbg-case34.tsv').write_text(next(r for r in rows if r.startswith('34\t')))
PY
RUN_TAG=redeem-<shortSHA>-<unique> CASES=/private/tmp/sbg-case34.tsv \
  bash docs/handoffs/2026-10-30-tools/run_full2.sh
```

不要重复run tag。批次exit0不代表业务全部通过；rc9=驱动确认执行路径、rc3=REPLAN，均须读trace和L3状态。75额度/124超时停止整批；先查在途plan状态再考虑新请求。

SQL可通过维护脚本固定服务访问（不要打印连接URL）：

```python
from pathlib import Path
from scripts.demo_environment import DemoEnvironment
x=DemoEnvironment(Path('/Users/zhangshijie/Desktop/Project/Fintech2026-wt/e2e-deploy'))
current=x.current()
# 两个数据库都在postgres-bank实例，服务名不是postgres-gateway
print(x.sql('postgres-bank',current['databases']['bank'],'select sim_now from clock;'))
```

`plan_executions`没有status列，执行结果在`result_json->>'status'`；与execution_intents按customer_id/plan_id连接。借贷守恒同时检查accounts四累计量的posted/pending两组总和。不能仅依赖model reply或driver summary。

### 新复制到仓库的辅助脚本

`docs/handoffs/2026-10-31-tools/`：

- `clock_advance_proof.py`：在gateway容器内部执行，从env读取控制台凭据（不输出），验证D0+D3幂等、冲突、过去时间拒绝与job完成；默认固定三次推进至12-06。**仅对新初始化数据集按脚本时序使用**，会真实改变模拟时间/触发业务；旧数据集会因时间/固定request_id冲突拒绝。
- `form_live_smoke.py`：在operator中执行，PIN从stdin传入，F服务token从env取；真实创建200元赵六转账和冻结。只输出合成业务ID/结果。它会真实改变数据，不是只读检查；只在新隔离数据集用。
- 未复制任何/tmp日志、密钥、密码或私有compose覆盖文件。当前脚本本身不等于最终六场景runner。

旧10-30交接中的原端口、临时WIP参考仍可查，但现场SHA/PR/用例事实以本文件和实查为准。

## 8. 基础冻结剩余工作，严格顺序

1. **收尾104**：推送状态已齐；确认7ceda71，完整本机CI重新从头跑并取得PASSED；若改代码/新增提交重新最终验证。通过后合并，恢复长期main，更新AGENTS及公开进度。
2. **收尾105**：rebase新main；独审已有，仅安全增量再审；新数据集原34李四1000份实际L1+真实审批/OTP（按返回风险）→订单预留→D3结算→SQL；最终完整CI后合并。
3. **修09**：从实际trace/DB定位include_self等来源；旧fail/newpass+独审+新数据集live；不重复修已不存在的memo问题。
4. **账单准确路径**：核固定查询范围、合计与明细，至少一条可靠月报/商户/异常分析；13/15/32/39/41等非核心只读问题不拖无限冻结。
5. **两轮六场景固定彩排**，每轮独立reset：
   - 转账：表单/助手/定时；正确金额目标，审批有效，实际一次。
   - 账单：月报/商户/异常，与固定SQL范围/合计/明细相同。
   - 卡：冻结解冻、限额、境外开关、挂失，正确卡，红级真实OTP。
   - 订阅：列表/识别闲置/取消，只有授权目标取消，到期真实BLOCKED。
   - 理财：李四申购与赎回、孙八越级申购被拒；冻结款/份额/订单/结算正确，拒绝无订单。
   - 跨场景：取消并定时储蓄；冻结可疑卡并本人互转；同计划审批，金额派生，失败整体回滚。
6. **完整43条表**：候选上全跑一次并给每条通过/失败/未验证证据，不能用脚本退出码冒充业务通过。两轮已通过核心用例不得回归。
7. **生产录制/回放接线和make demo**：已有replay.py组件，当前实际服务`make_llm()`尚未见录制/回放transport接线；`make demo`仍是占位。先读代码确认。回放只替换LLM，真实L2/L3/审批/时钟照跑；未命中明确失败，不fallback live；实际阻断LLM网络验证。易变ID需canon/rebind，不能重放旧会话/原话ID当授权；录制不含凭据/OTP。
8. 形成冻结清单：commit、seed/policy版本、脚本/回放摘要、已知限制、启动/初始化/恢复命令。全部门禁通过才tag `demo-v1`；冻结后只接有复现与回归的bugfix。

**冻结门禁缺一不可**：两轮六场景live、真实时钟、代际初始化、最终本机CI、安全独审、断网回放。当前只完成其中组件，不应把“组件通过”合计为冻结通过。

## 9. 冻结后计划（尚未开工，不要抢跑）

### 攻击版demo-v1.1

- D6白名单case启动/恶意计划提交/查询，走真实G15/G3/G4/G5；绑定case/customer/sid/plan，不伪造用户审批，不绕G8。
- D7按PRD `{case_id,checkpoint:BEFORE/AFTER}`固定注册表断言，不收任意SQL；与独立L3 SQL核对转账/收款人/定时，以及基金订单/冻结款/持仓。
- A2退款备注：真实L1读注入备注，模型不出写计划时才显式D6网关测试兜底，区分两种路径。
- A3：新增钱八→新会话放松限额并OTP确认→快进40分钟→新会话转账；第三步命中RISKY_SEQUENCE、攻击者入账0。前两步合法可能已新增收款人，不能错误展示“攻击者收款人不存在”。
- A6孙八申购R4：SUITABILITY_MISMATCH，无订单/冻结/持仓增加。
- 控制台沿用竖版tokens，显示阶段/真实决策/中文原因/DB证据，仅live演示+控制台登录开放，禁并发重复点击；凭据不进模型或录制。

### 可选D8/指标及材料

前两版都稳后才做同进程同计划/授权/快照的B0/B1/B2只评估对比，不执行不造审批不污染正式状态。实际展示ALLOW/REQUIRE_APPROVAL/REPLAN/DENY/错误，不预设基线放行。先每攻击3条、每正常场景1条，余时再扩72攻击；决策结果不能叫真实资金攻击成功率。

最终材料仍需8–10分钟话术/点击顺序、六场景与攻击录像/日志、启动/初始化/回放指南、实测限制；上传由用户完成。

## 10. 接手第一小时建议与风险

- 先确认远端104=7ceda71、105=4880516及CI是否已有后续变化。本文是快照，不应覆盖同事之后的提交。
- 保留本次docs分支，阅读后回各自实现checkout；不要在交接分支上混入资金修改。
- 本机104完整CI两次都没有最终通过：一次真实测试清单错误已修，一次中途无进程。必须取得最终head完整完成证据。
- 104合并前，105不要部署回老0004代码去使用当前0005数据库；先rebase，后新generation。
- 一个只读review代理最后报 **Codex账号用量上限**（提示14:21后重试，当前时间已更晚但未复核额度）；这是代理运行失败，不是DeepSeek额度错误，也不是评审结果。34后来由另一自有只读代理完成APPROVE。若新同事无法安排独审，不得绕门禁。
- 当前09仍失败；六场景、43条、生产回放与最终交付物均未完成。不要复用已有数据当初始彩排，不要以healthy、CONFIRMED文字或模型成功回复代替SQL。
- 公开进度 <https://sjeffzhang.github.io/safebank-progress/> 已到103，104/105尚不能标done。

## 11. 文档交付范围

本交接分支只交付文档/辅助脚本和AGENTS交接日志；不合并104/105、不创建冻结tag、不清理工作区/数据，也不声称整体计划已完成。启动提示词另存 `2026-10-31-freeze-prompt.md`，同样直接提供给用户。
