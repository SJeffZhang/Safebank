# Gateway测评、当前声明根、手机号授权匹配独立安全评审

日期：2026-10-31。评审者未实现以下三个组件。作者工作树 `/Users/jet/.codex/worktrees/safebank-demo-runner/WeBank` 始终只读；本人从 `git archive 5e589b561b26d64d803b74081f8f6550fad48e86` 创建 `/private/tmp/sbg-demo-repairs-20261031/gateway-review-5e589b5` 快照验证。基线：`22a87799c2b422a6c2186490a5972e11bc05dce4`。

未发现需要阻塞这三个提交的P0/P1/P2问题。各节结论针对本次静态与纯测范围，不代表完整CI或真实六场景端到端验收。

## 1. 测评权威面板 f8839c8

**APPROVE** — `f8839c8dde11214301f9d57dc72ed94df476a4b2`。

依据：`approval/planview.py:108` 的 `_assessment_fields` 仅允许既有 app_form 来源，使用contracts `Assessment`校验固定rq_v1、六个唯一问题和合法选项，再要求答案集合与权威QUESTIONNAIRE完整相同。问题顺序、题干和所选答案文案来自版本化contracts，未采纳调用方标签或LLM文案；缺失题干/答案、非法版本/问题/选项或重复metadata均fail closed。该函数只增加展示，未变更FORM登记、权限、适当性或审批授权。

`_content_dict`/`content_hash`将问卷版本及逐题key/label/value纳入既有语义hash；修改所选答案、题干或所选选项文案都会改变hash。view仍保留YELLOW、risk_ack_required和现有OTP/有效期规则。agent origin明确拒绝，不把L1自报值变成表单授权。

本人红绿复核：精确快照中临时仅替换本文件为基线版本，合法展示/hash两条失败；恢复后通过。其余非法输入负控继续通过。新增独立metadata负控覆盖所选选项缺失、空题干、空答案和重复选项，均拒绝。

## 2. 当前声明根 0a1a89c

**APPROVE** — `0a1a89c45d2a573fe560ddb40949e1fa07f0c341`。

依据：`api/plans.py:252` 在G4原有入口先检查plan.utterance_ids均在assistant JWT.utts内；`:331`仅将本次plan声明ID传入loader，`:100`新增id IN过滤，不再把同会话其他ACTIVE SCOPED/NO_WRITE内容塞为额外根。默认无source_ids时装载空根，不能收养会话旧权限。

未改动且已核读的 `taint/authorization.py:103` 仍要求auth根集、scope根集和draft根集完全相同；检查SCOPED/ACTIVE、caller customer/origin/sid/scopes、JWT.utts、session customer/未撤销/epoch。`:198`逐个读取父链，要求同sid/epoch、ACTIVE、未过期、无环、深度/节点上限、父子有效期/创建时刻约束及hint/action持久来源绑定。父节点只能作为已声明根可达的来源证据，不能增加write scope；IntentStage仍仅从声明根构建允许的完整目标/金额/来源三元组，未改用宽松scope包含关系。

核读execution.py三处重装载（`:537`、`:705`、`:1471`）均显式传入persisted plan.utterance_ids，不依赖旧默认行为。改变查询没有使执行恢复重新装载任意会话根。

本人红绿复核：本组件四条合法当前根测试在基线loader失败，恢复后通过。JWT错误、跨客户/sid、过期/取消、旧epoch、会话撤销、父epoch/sid错误继续拒绝；独立新增CONSUMED/SUPERSEDED、父过期/缺失/循环和混入NO_WRITE根也全部拒绝。未声明的旧scope既不进入授权根，也不能被用作任意文本来源。

## 3. 手机号授权匹配 5e589b5

**APPROVE** — `5e589b561b26d64d803b74081f8f6550fad48e86`。

依据：新增 `utterance/phone.py:7` 只做NFKC后完整11位ASCII数字匹配，不去除内部空格、连字符、掩码、区号或附加内容。parser仅将现有transfer/schedule target解析扩展到已登记Payee.phone；`:508`对完整手机号形状优先且仅检查phone字段，不采用数字别名/姓名或账号巧合。候选在既有parse中已按当前customer过滤；同客户同号码多个Payee走原有clarification，未知号码无scope，跨客户同号不成为当前客户的授权或歧义来源。

`taint/text.py:119` 的新增scalar分支仅适用于payee_id；即使normalize后的文本像号码，也必须原始来源span本身是完整号码，且当前customer的匹配ID列表恰为[value]。没有把phone匹配泛化到account/product/participants等参数。该分支未绕开persisted typed来源证明：ProvenanceStage仍先通过上节权限/父链证据，再按真实原话span切片；旧未声明根、截短/扩展span拒绝。IntentStage完整三元组校验保持不变，手机号不能为另一收款人、另一金额或另一付款账户授权。

本人红绿复核：基线parser/text产生11条失败（parser10、真实ProvenanceStage来源1）；恢复后通过。原有引号、疑问、否定、条件句、取消、未知/重复/部分/掩码/格式化号码负控全部保留。独立补充+86、重复号码附加文本、零宽字符、全角数字混空格和跨客户同号，以及改变目标/金额/来源的IntentStage负控，均符合拒绝或仅当前客户唯一匹配的预期。

限制：当前seed联系人phone仍为null；这是针对合法已登记phone的窄授权补齐，未宣称现有演示数据已有这些号码。国际/部分/分隔号码不新增支持。

## 本人执行的验证

在精确SHA临时快照运行，未编辑作者WT：

1. `/Users/jet/.codex/worktrees/safebank-demo-runner/WeBank/.venv/bin/pytest tests/gateway/w1/test_*.py tests/gateway/w2/test_*.py tests/gateway/w3b/test_*.py tests/gateway/w4b1/test_*.py -q --tb=short -p no:cacheprovider` → **1933 passed in 5.39s**。未传任何DB开关、未包含db目录。
2. 独立新负控 `/private/tmp/sbg-demo-repairs-20261031/test_gateway_independent_boundaries.py`，使用`-c pyproject.toml -p no:cacheprovider` → **18 passed**。它与上项有交叉，不将数字相加为独立覆盖总数。
3. 快照内将planview/plans/parser/text四个生产文件临时换为基线精确内容，运行planview/current roots/phone parser/phone provenance四个专项文件：**17 failed, 59 passed**；`finally`恢复精确被审SHA后同命令 → **76 passed**。失败分类：面板2、声明根4、手机号11，符合作者红绿证据。
4. 五个被审生产文件Ruff check/format --no-cache → **通过**。作者WT前后`git status --short`为空。

日志：`gateway-review-pure.log`、`gateway-review-baseline-red.log`、`gateway-review-focused-green.log`，均位于本报告同目录。

## 外部证据与未覆盖

根协调者另报告真实PG G19→G4/两类上下文/planview **68 passed in 7.48s**；首次新fixture遗漏locks.configure导致1失败/67通过，随后仅补测试锁资源接线，产品逻辑未变。以上是协调者提供的外部证据，本人未执行DB验证，也不将其作为本人的纯测结果。

本人未运行DB、Docker、真实API/LLM、浏览器、部署、完整CI、生产录制或回放。最终集成SHA和完整本机CI仍由协调者验收；本报告没有评审本人实现的通知组件。
