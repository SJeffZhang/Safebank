# SafeBank Gateway：队友接续交接

2026-10-23。本文件面向接手的人类队友，是当前交接入口。业务开发按用户要求暂停；本次仅推送已有检查点和交接文档，不把未评审成果合并到main。仓库：https://github.com/SJeffZhang/Safebank 。

## 从GitHub开始

无需访问原机器。先clone仓库并fetch全部分支，切到 `docs/codex-wave1-integration` 阅读本文件、AGENTS和交接材料。交接文档PR仅文档；审阅通过且当前head的unit/p1-postgres都成功后再以merge commit合入main。main截至交接前为 `44ded2f2431bdb0ce1d3f90f2c5d7d1b2ac67697`，PR #3–#10已经合并。

```sh
git clone https://github.com/SJeffZhang/Safebank.git
cd Safebank
git fetch origin
git switch --track origin/docs/codex-wave1-integration
```

已有克隆则复用，先核git status，保留所有本地改动，不要reset/clean。随后完整阅读：

1. 本文件和 `docs/handoff/CODEX-RESUME-2026-10-23.md`。后者是16:16 UTC本机暂停技术快照；其中‘未推送’和本机路径仅为当时历史，当前远端状态以下表为准。
2. AGENTS.md全文、PRD.md、docs/plans/L2-plan.md、docs/plans/briefs/wave1-common.md、四份W1–W4任务书。
3. services/gateway/README.md、docs/reviews/L2-W0-architecture.md的接口约定/第三轮定点复核，以及下文各任务的追加任务/报告。

## 已推送检查点

下表六个分支已原子推送并逐项用git ls-remote核对。没有强推，没有改main。推送会触发CI，**检查点保存不等于组件验收通过**；交接时没有等待新触发的CI全部完成，也没有为这些未完成分支开功能合并PR。旧W3/W4分支尚未同步新main，已知失败和缺口仍须处理。

| 分支 | 远端提交 | 状态及接续门禁 |
|---|---|---|
| feat/l2-identity | 1722995095a6eb5260865761898435c36dba96cc | W1新撤销接线；实现者自报五退出绿，最新提交尚未经协调者完整复跑/独立代码审，且依赖S1f |
| feat/l2-provenance | 8063f27a1a5f7759dc20615f843210d97003d314 | W2新memo/alias黄级步；协调者亲验五退出，最新memo尚未独立审；完整W1/S3联调仍缺 |
| feat/l2-decision | 3bcbe8ea3e2aabc1d8990d1c7f7343e1c369162b | W3早期部分实现；同步main后继续，不可直接合并 |
| feat/l2-execution | 3856a4c899fc6e9747f42cc6488484649c9158dc | W4早期审批部分；G8/UNKNOWN等尚缺，有已知普通suite失败，不可直接合并 |
| feat/l2-demo-route-guard | 58c45928721256237e12e43310dc3a25b6d6f3be | S1f仅准备分支和任务，尚无实现/验收提交；已停止旧agent |
| feat/l3-obligations-data | 681554752215dcf82b6e768f4a3f788c19469834 | S3a仅准备分支；模型提案另存交接分支，尚无实现 |

交接材料与最新协调裁定在 `docs/codex-wave1-integration`；不要把main旧文档覆盖这份交接分支的新PRD/common/任务书。无需原agent的对话或临时目录。

## 当前成果与剩余工作

P1/L3核心、W0网关骨架已合并。共享S1运行层、S2七天预留、S1b网关0002/CAS、W4a客户锁、S1c显式PIN Settings、S1e孤儿任务清理、S1d敏感memo黄级Code，均已逐组件亲验、独立审、精确head四CI成功并merge commit。详见暂停快照的PR/merge表和docs/acceptance。不要重新追加0002或重做已合组件。

### 先完成S1f，再最终验W1

S1f任务：`docs/plans/briefs/S1f-demo-route-mount.md`。当前非演示模式只handler404、G22仍注册；需每app条件挂载并修coverage条件。MATRIX权限条目/lookup/match/签名不变，不碰W1 auth.py，不污染全局可重用router。共享实现只开这一条，完成六退出、独立审、CI/PR合并后再启动S3a共享代码。

W1追加任务：`docs/plans/briefs/W1-revocation-deferred.md`；设计：`docs/reviews/L2-W1-revocation-design.md`；本分支进度：`docs/progress/W1.md`、`W1-identity.md`。1722995的1687 passed/158真实PG是实现者证据，必须亲自复跑并独立审。关键是预期CAS/savepoint、稳定坏claim时撤销仍提交+缓存409、错误分支撤销/审计/dedup原子、禁止新ID绕过/新授权、真实SQL错误不吞。完整细节以追加任务为准。S1f合入后同步main再最终验收开PR。

### W2先审memo，再消除真实接线缺口

新memo范围BASE50c1bdb..8063f27：MEMO_SENSITIVE_CONTENT/RISK_YELLOW、数字/金额格式/alias来源边界、移除严格xfail。协调者亲验：lint196、gateway317、基础PG17、all552、W2 202零skip/xfail（含48真实PG）。仍需独立审新diff。W1合并后须使用真实G3/load_for_plan和全局撤销，移除明确fixture substitute；S3数据/default reserve也须接上，不能称当前已全W2生产完成。

### S3a先复审模型，再实施

任务 `docs/plans/briefs/S3a-obligations-data.md`，ADR-009，框架审核 `docs/reviews/L2-S3a-obligations-design.md` 已APPROVE。具体字段提案 `docs/plans/briefs/S3a-model-proposal.md`，字段审核 `docs/reviews/L2-S3a-fields.md` 初始唯一F1：商户SUCCESS run必须instruction_id非空。owner已改提案并补矩阵，尚未定点复审；保留无授权BLOCKED和当前DELETED/DISABLED授权的历史SUCCESS。通过后、S1f合入且分支同步最新main，才实施四表/loader/S2最小兼容。

义务完整容器None/真正空必须区分，全scope完整加载、最小目标所有权、不递归第三方资料，DUES周期只认billing。银行0003只增量，不改历史迁移，不往既有P1 cus_a/b资金库播任意义务/重播历史。真实HTTP→PG→snapshot→S2证明06:00/295500等；R7/R10/R11、1–30天、公开signed字段、scheduler/领域写另组件。13读/7写缺口地图 `docs/plans/L3-backend-components.md`。

W3/W4之后按W1/W2→W3→W4依赖继续，旧分支先同步main复验。W5/W6仍未写任务书。App/BFF/前端暂停。

## 团队工作约定

稳健与架构优先，接口冲突时减少并行。每组件独立分支/worktree；冻结文件与交叉签名遵守common，必要例外由指定共享owner串行落地。正常PRD迭代要同步ADR/任务书/验收，不靠聊天记录代替设计。

每组件完成就更新AGENTS §3/§4/§5、记录真实退出命令、独立review、修好再push并开base main PR，精确提交unit/p1-postgres全部绿后merge commit。main没有保护/可靠等待自动合并workflow，需主动核CI再gh pr merge --merge，不能提前合并。已有这项长期授权。

若继续用AI：保留‘协调agent不写业务代码、另派实现与只读review、协调者亲自复跑’的工作流；原agent已停止，不能和新实现同时操作同一worktree。人类队友可自行选择开发工具，但独立review、测试证据和合并门禁保持。

## 新机器与环境

新clone先安装Docker和Python/uv等既定依赖，然后在最新main运行：

```sh
make setup && make keys && make up && make test && make test-gateway && make test-gateway-db
```

.secrets不在GitHub，新机必须make keys；不要复制/公开原机密钥。现有机器保留自己的.secrets。compose项目固定safebank，make up/down/test-p1等全栈控制不得并发；worker只跑既定lint/test/gateway/db/专项目标。禁止删除或重建任何Docker数据卷，禁止对原有业务库reset/TRUNCATE/重播资金；PG专项使用显式tmpfs与独立容器。Linux的0600挂载权限问题只会在真实Linux暴露，不能用macOS通过替代CI。

原机两个命名卷创建于2026-10-23T12:26:36Z，保留。原机长期栈只部署到S1c/W4a时代；S1e/S1d的真实up/P1/HTTP是在CI验过，W1尚未部署长期栈。区分三类证据：实现者自报、协调者亲跑、远端CI；mock/skip不当真实集成通过。

接手第一轮请先确认远端分支与上述SHA、文档PR及main状态，再安排S1f、W1最终审验、W2memo审/后续接线、S3a F1复审。无需访问原机或旧agent私有上下文。
