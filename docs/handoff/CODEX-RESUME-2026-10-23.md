# Codex 暂停交接：2026-10-23 16:16 UTC

用户明确要求暂停开发，由另一个 agent 接续。本文件只保存现场和恢复顺序，不代表批准任何未验收组件。旧协调者不再开发、合并或派发实现。S1f 子 agent 已 interrupt；其他子任务均已结束本轮。无自动继续或定时任务。

## 接手原则

项目唯一代码仓库：https://github.com/SJeffZhang/Safebank 。优先复用本机 `/Users/jet/Downloads/WeBank` 与既有 worktrees；不要重新克隆覆盖现场，不要 reset/clean/删分支。**重要本地成果尚未推送，不能仅凭 GitHub main 恢复。** 如果不是同一台机器，须先取得下列本地提交和工作树；不要假装它们已经在远端。

协调者只负责分工、架构/接口裁定、文档、亲自复验、独立评审、push/PR/merge；不写业务代码。实现由独立 agent 在独立 worktree/分支完成，仅本地提交，由协调者统一发布。新接手 agent 不必能访问旧 agent 的对话；以实际 Git、进度/验收/评审文档为准。

用户长期授权：每完成组件，更新总 AGENTS.md §3/§4/§5，亲自复跑完整退出命令，另派只读评审对照 PRD，问题修完立即 push 并开 base main PR；精确 head 全部 CI 成功后 `gh pr merge --merge --match-head-commit <sha>`，不用逐 PR 再问。PRD允许正常迭代，必要变化同步 ADR/任务书/验收。没有跑过的命令不能写成通过。

稳健与架构优先，接口冲突时减少并行或串行。App/BFF/前端暂停，只有用户亲自下令才开工。共享冻结例外已获用户批准，但仅指定共享 owner 逐组件串行；W1–W4不得自己改冻结区/迁移。普通交叉签名不变，未合入依赖按契约替身并准确说明边界，不能假称生产通过。

最多4个活跃槽（含协调者），所以最多3个子 agent；评审需留槽。旧暂停树不得与接手后的新实现同时操作同一工作树。

## 已合并与主干

核实的 origin/main：`44ded2f2431bdb0ce1d3f90f2c5d7d1b2ac67697`，2026-10-23T16:06:07Z。

| PR | 组件 | merge commit | 已确认门禁 |
|---|---|---|---|
| #3 | 原交接文档 | 已合并，见Git历史 | 开始时已核查 |
| #4 | S1集中资源/撤销/部署 | 691cf880866076a0db9a5a77b752a785364560f6 | 亲验、独立审、四CI |
| #5 | S2七天预留纯函数 | a9d72cf49f518fbb2bd89d2e666f0d0e1196a4c9 | 亲验、独立审、四CI |
| #6 | S1b网关0002/受控CAS | 94e40fec12dd4496b978ad11e094ab321737a2fc | 亲验、独立审、四CI |
| #7 | W4a真实客户锁 | d5ac9edecc0d7c35e72567aab5eba5c8328c9b60 | 亲验、独立审、四CI |
| #8 | S1c显式Settings PIN接线 | 304adda5bde72437ccc6afb147cd4e96ee36a3be | 亲验五退出、独立审、四CI |
| #9 | S1e缺evaluation孤儿清理 | 681554752215dcf82b6e768f4a3f788c19469834 | 亲验六退出、独立审修复闭环、四CI |
| #10 | S1d memo敏感内容黄级Code | 44ded2f2431bdb0ce1d3f90f2c5d7d1b2ac67697 | 亲验三退出、独立审、四CI |

PR均已附加旧任务。GitHub允许auto_merge，但main没有保护、没有等待CI后合并的workflow；实际以协调者在全绿后执行上述gh命令实现门禁，不能开检查前就直接merge。

本机主工作树分支 `docs/codex-wave1-integration`。暂停前检查点 `58c45928721256237e12e43310dc3a25b6d6f3be`；本次交接文档提交在其之后，以 `git log -1` 为准。根分支含尚未推送的W1撤销错误PRD/common/设计与S1f任务。相对main待合文档应保留，尤其不要用旧main的规则覆盖它们。

## 必须保留的分支/工作树

公共前缀 `/Users/jet/.codex/worktrees/`，下表路径在此前缀下；主工作树在 `/Users/jet/Downloads/WeBank`。

| 任务 | 工作树后缀 | 分支 | 暂停时HEAD/状态 |
|---|---|---|---|
| W1 | safebank-w1-identity/WeBank | feat/l2-identity | **1722995095a6eb5260865761898435c36dba96cc，干净，未push** |
| W2 | safebank-w2-provenance/WeBank | feat/l2-provenance | **8063f27a1a5f7759dc20615f843210d97003d314，干净，未push** |
| S1f | safebank-shared-runtime/WeBank | feat/l2-demo-route-guard | 58c45928721256237e12e43310dc3a25b6d6f3be，interrupt后检查干净，无新提交/验收证据 |
| S3a | safebank-reserve/WeBank | feat/l3-obligations-data | 681554752215dcf82b6e768f4a3f788c19469834，干净，**只读提案，无实现** |
| W3 | safebank-w3-decision/WeBank | feat/l2-decision | 3bcbe8ea3e2aabc1d8990d1c7f7343e1c369162b，早期部分检查点 |
| W4 | safebank-w4-execution/WeBank | feat/l2-execution | 3856a4c899fc6e9747f42cc6488484649c9158dc，早期部分检查点 |
| 旧W4a | safebank-w4-locks/WeBank | feat/l2-customer-locks | 37db47df6bd92548ee52f9c3da1b9638fc2cd30d，已合PR7 |
| 旧S1e | safebank-wave1-state/WeBank | feat/l2-orphan-reconcile | a156c53b31fee77d2987b140e1c8a7d0c914c867，已合PR9 |

S1f/S1d/S1c复用同一共享runtime工作树，S3a复用已完成S2工作树；旧分支和提交仍在Git，不要误以为被覆盖。最新S3a分支还没有快进44ded2f，真正实施前必须同步最新main。

## W1：当前最接近交付

原任务 `docs/plans/briefs/W1-identity.md`；最新精确追加需求在根分支 `docs/plans/briefs/W1-revocation-deferred.md`，独立设计 `docs/reviews/L2-W1-revocation-design.md`。工作树进度 `docs/progress/W1.md` 和 `W1-identity.md`。

W1已移除所有客户锁/状态替身，用真实集中Runtime/PG；显式Settings两PIN文件、相反env与跨启动缓存的8项PG，旧协调者曾亲自跑过（3.22s）并actionlint通过。

新冻结1722995已消费S1e、补完整deferred撤销。实现者最终五退出自报全部exit0：lint202；gateway1692/412skip；all1927/460skip；基础PG17；W1 **1687 passed、零skip（含158真实PG）**。**这份最新提交尚未经协调者亲自完整复跑，尚无独立代码评审，不得据自报开PR。** 其代码检查点c618d17，合main44ded2f为7afd963，再docs形成1722995。

必须保留的设计：
- 正常revision/status竞争用savepoint和有界重读（实现选3次），普通/孤儿CAS不伪造revision。
- 稳定坏scope/claim清理失败仍提交合法撤销，继续其他plans/sids/审批/UI清理，不补数据/放宽helper，不发replacement scope/JWT/used_by、不遗留替代RESOLVED。
- 固定409 STATE_CHANGED，details={authorization_revoked:true,cleanup_pending:true}。producer返回GatewayError.body给原request_idempotent缓存并commit；事务外重建raise，用本次trace。前置来源/解析错误、SQL/连接异常、ValueError不吞成这种409。
- 仅deferred错误分支用现有audit.chain.append(conn,...)与撤销/dedup同无HTTP短事务原子审计。G3 AUTHORIZATION_REVOKED，G14 AUTH_LOGOUT；不改冻结函数。其他成功路径不顺便重构。
- 每次发布新写scope重扫sid现存live jobs，防replaced=False或新request_id绕过。正常UNKNOWN合法幂等通过不一刀切禁新授权。
- 旧logout JWT重试401；新登录JWT重放旧request_id只回缓存，不能再次注销新会话。响应丢失/SQL故障/审计原子性/多plan多sid与CAS竞争均需新评审核证。

剩余共享G22条件挂载由S1f解决。W1完整退出建议等S1f合并后同步主干再最终复验；可以先独立审1722995的W1代码。CI已有精确一行make test-w1。真实W4 HTTP恢复/终态及L3谓词生产领域数据仍不属于此W1局部验收；原任务明确允许未实现L3读按契约fake并登记，不能额外等全银行领域才交W1，也不能把fake写成生产数据。

## W2：最新memo步已亲验，尚未独立审

原已评审来源链检查点c945cd8推送过；同步main44ded2f为50c1bdb。最新8063f27仅4个W2白名单文件：provenance TODO、新memo/alias矩阵、删除严格xfail、进度。新Code/RISK_YELLOW已接，NFKC、7/8数字、元/人民币/CNY/RMB/¥/￥金额格式；untrusted仍DENY、unknown仍REPLAN。

旧协调者**亲自完整五退出**均exit0（log `/private/tmp/sbg-w2-memo-root.log`）：lint196；gateway317/302skip（6.22s）；PG17（2.37s）；all552/350skip（6.47s）；W2 **202 passed、零skip/xfail（7.15s，含原48真实PG）**。当前尚未给新memo diff派独立审，未push最新8063f27、未开完整W2 PR。

新review BASE50c1bdb..8063f27，先对照S1d任务/PRD7.5核内容与来源不放宽。真实W1 G3/load_for_plan→W2以及G2全局撤销仍待W1合并后撤掉明确fixture substitute；S3完整数据/default reserve接线仍未完成，不能把当前步骤称完整W2生产通过。完整W2需专用CI实际运行PG，未来按窄例外加make test-w2。

## S1f：下一共享运行组件，优先于S3a代码

任务 `docs/plans/briefs/S1f-demo-route-mount.md`。原W1待决：main总include auth.router，非DEMO/EVAL仅handler404，路由仍注册；coverage_errors还总要求G22存在。

仅main公共router装配、auth/matrix.py的coverage_errors条件校验及限定测试/README/证据；**MATRIX任何key/凭证条目、lookup/match、签名均不变**，不碰W1正在改的auth.py。两开关均false不实际挂GET /auth/demo-customers，任一true正常挂载；每app装配独立，不修改全局router导致on/off构建互相污染。当前FastAPI是lazy/nested router，需读实际依赖实现再选安全装配。

已派s1f_demo_mount但用户暂停时interrupt；检查HEAD58c4592、clean；未取得任何新的实现/测试提交，不要自行认为已完成。接手后新派实现者从该worktree恢复即可，不要让旧暂停agent同时跑。

退出：make lint/test/test-gateway/test-gateway-db/test-shared-runtime/contracts-check，专项实际启用；协调者同六退出+actionlint、独立只读审、单组件PR/全CI/merge。

## S3a：只读模型提案已保存，F1修后未复审

读取顺序：S3a-obligations-data任务、ADR-009、L2-S3a-obligations-design框架APPROVE、`docs/plans/briefs/S3a-model-proposal.md`、`docs/reviews/L2-S3a-fields.md`。后二文件从/tmp保存，防下轮丢失。

字段审核初始唯一F1 REQUEST_CHANGES：MERCHANT_MANDATE+instruction_id=null+SUCCESS可穿nullable FK。owner已改提案为SUCCESS强制instruction_id非空；无授权BLOCKED可保存；历史SUCCESS对应授权当前DELETED/DISABLED仍能加载，并补三项矩阵。**修正文档未定点复审，且没有写任何实现/跑测试。** 下一步先给新只读reviewer核F1，再确认模型；S1f合并后、S3a分支快进最新main并纳入确定设计才派实现。

已批准框架：BankState.obligations nullable typed完整容器，None未知、全scope明确空才是真的无义务；全部scope的原始四表与最小targets，同一snapshot事务加载。DUES仅billing作周期真源，weekly weekday周一0。目标映射不加载其他客户完整联系人、不递归第三方余额，只有OWN保证贷记。S2保留公开签名/7天、同源前缀算法、负max_outflow；旧Views仅派生。

银行0003只四表及必要父候选唯一/复合FK、只追加历史、readiness与当前head断言精确适配；历史0001/0002/schema_v1字节不变。现有P1 cus_a/b库不重播seed/资金/任意义务，不冒充PRD baseline；精确06:00/295500用fresh隔离fixture。source_plan_id/event_id目前无本地父表，只逻辑ID，不伪造第五表。R7/R10/R11、1–30天/signed公开字段、scheduler/领域写/完整baseline留后续。

地图 `docs/plans/L3-backend-components.md` 已逐项读底层确认13读/7写仍缺，分义务读、分析、定时、AA、问卷、基金。R9典型金额/5%基准与recent_return_bp窗口/舍入尚待相应任务前明确PRD，不能瞎填成既有规则。

## W3/W4与后续

W3仅早期部分实现，G4/replay/真实PG/预留295500/hint/cooldown/拒绝wall时间等未完整；W4原审批部分检查点仍有旧NotImplemented探针预期导致普通suite失败，G8/G5/UNKNOWN/真实HTTP竞争未实现。旧退出通过不代表这些现在可合并。

W2拥有①–④/G2，W3拥有⑤–⑨/G4；W3表单适配必须复用W1 scope_from_form完整fingerprint/周期，不只fields。A scope_ids为ACTIVE root utterance IDs，source_id/used_by另核持久父链；F仅真实F/app_form可用act IDs。不要改冻结EvalContext/schema/registry结构。

执行协议详见ADR-008/执行状态评审：同attempt成功prepare+明确STATE_CHANGED后原evidence/cap缩deadline到FENCE_AT，持久FENCING→PAUSED后才复演，续提交n+1；不明回包/失锁UNKNOWN只query/reconcile。claim只在真实L3终态后消费，CANCELLED/SUPERSEDED原话保持。旧G8 request_id不可触发新attempt，不把HTTP塞进idempotent SQL事务。W4真实HTTP/PG两顺序、迟到、断连、崩溃矩阵仍必需。

波次2 W5/W6任务书还没写。前端仍暂停。

## 环境和不可碰的持久数据

Docker Desktop已安装并后台运行；CLI PATH：`/Applications/Docker.app/Contents/Resources/bin:$PATH`。不要关旧Docker后台长进程。Compose项目固定safebank，仅协调者串行make up/down/test-p1等全栈控制；worker只允许既定lint/test/gateway/db/专项目标，临时PG全部显式tmpfs、唯一容器。

禁止删除或重建任何Docker数据卷。safebank_pg-bank与safebank_pg-agent CreatedAt都为2026-10-23T12:26:36Z。禁止对现存业务库reset/TRUNCATE/重播资金。原测试脚本的隔离测试数据库不等同现存业务库。

.secrets已存在且忽略，必须保留且不能输出；新克隆才make keys，现有路径make keys只应preserve。共享runtime worktree的.secrets是指向root的被忽略symlink。所有worktrees有venv78依赖，UV_CACHE_DIR=/private/tmp/sbg-uv-cache，py3.12。actionlint在/private/tmp/sbg-actionlint-iysg7h9r/actionlint。普通沙箱对附加worktree/git/socket可能拒绝，按已有授权申请提升，不把环境失败或自报算测试通过。

最新**本机长期栈部署**只到S1c/W4a时代（main304adda代码）；make up/test-integration当时亲跑成功、六服务healthy、named卷未变。S1e/S1d及W1尚未部署本机长期栈；PR9/10 CI已实际up/PG/P1/HTTP全绿。不要混淆本机部署、CI与隔离fixture。

## 建议接手第一轮

1. 读本文件、AGENTS全文、原CODEX-HANDOFF、L2-plan、wave1-common、当前S1f/W1-deferred/S3a材料；核git status/worktree/log与origin/main，先保存本地未推送成果。
2. 简短给出S1f→W1、W2memo审/后续联调、S3a字段复审→共享实现的顺序；随后恢复，不要重做已合PR3–10。
3. S1f唯一共享实现可与W1/W2只读审/个人复验并行；不与S3a共享实现并行。保留独立评审槽。W1新代码需要协调者亲跑五退出及独立PRD/code审，不能仅采信1687自报；同步S1f主干后再最终验收发布。
4. W2新memo可以独立审当前diff，保持真实W1/S3待决，按组件真实完成程度发布，禁止假称全W2完整。
5. 逐组件把本根分支尚未入main的协调文档安全纳入对应PR，保留AGENTS双方追加记录，不覆盖PRD新裁定。完整验收→立即GitHub PR→精确head全CI→merge commit。
