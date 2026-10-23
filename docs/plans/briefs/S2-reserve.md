# S2 共享预留纯函数实施计划

> For agentic workers: 按 subagent-driven-development 分组件验收；实现者不得自开子agent。

**Goal:** 为W2派生金额与W3预演/hint提供唯一的7天预留算法。

**Architecture:** 新建bankcore.reserve，消费现有不可变BankState中的完整义务投影；纯函数不做IO，不修改输入。生产数据完整性由下一组件S3的L3快照保证，当前生产入口仍明确阻塞，不能把未加载字段的空元组当客户无义务。

**Tech Stack:** 现有Python3.12和contracts，无新增依赖。

**Spec:** 已获批准的wave1-shared-gaps；PRD §7.7、§8.6、§2.2、§5.1/5.4。W3检查点进度文件可参考，不是额外需求来源。

## Global Constraints

- 独立分支`feat/l3-reserve`，从实施时最新main创建，只有此共享agent改bankcore。
- 仅可新增`packages/bankcore/src/safebank_bankcore/reserve.py`、必要包导出、`tests/bankcore/test_reserve.py`、`docs/progress/S2-reserve.md`、末尾Makefile目标`test-reserve`。
- 不改gateway业务、contracts、L3、迁移或PRD；如现有view不能表达精确周期语义，报告缺口而不臆测。
- 不调用外部服务或LLM。金额整数分；保留已有缺口，不把保护动作拦住。
- 只本地提交；不push/PR/merge、不运行make up/down/test-p1、不删/重建数据卷。协调者独立验收与评审。

## Review Focus

- 窗口左开右闭，恰好sim_now的扣费不重复计入，末端包含。
- 月末、闰年、周周期和end_at，不能从短月的28日永久漂移到28日。
- 实际账单amount而非授权max_amount，ACTIVE以外不计；忽略工资/基金等未来入账。
- 本人定时互转贷记仅目标本人账户；跨客户不算本客户担保收入，排序使用整数rank与稳定id。
- 负max_outflow和既有shortfall保留数学含义；上层只能收缩正金额，不把负上限变成可执行金额。

## Task 1: 义务展开与报告

接口（供W2/W3固定消费）：
- `projected_shortfall(state: BankState, *, days: int = 7) -> ShortfallReport`。
- `ShortfallReport.accounts: Mapping[str, AccountProjection]`，`total_shortfall_cents: int`，`total_reserve_cents: int`，`obligations: tuple[Obligation,...]`。
- `AccountProjection.reserve_cents: int`，`shortfall_cents: int`，`max_outflow_cents: int`。
- `Obligation.due_at: datetime`，`rank: int`，`id: str`，`account_id: str`，`delta_cents: int`。
- `scheduled_occurrences(instructions, start: datetime, end: datetime)`返回按(due_at,3,id)排序的定时义务；类型在reserve.py统一定义，供以后30天承诺与首次到期预演复用。

- [ ] 先写测试覆盖窗口、ACTIVE过滤、每月/每周/一次、月末/闰年、end_at、只用真实amount、不计未来收入。
- [ ] 实现义务序列：(sim_now,sim_now+days]；定时rank3、商户rank4；按(due_at,rank,id)排序。商户next_due_at包含执行时刻，不能把用户指定的非06:00时刻强行改成种子06:00。
- [ ] 实现每账户前缀和：reserve=max(0,max(-prefix_delta))；shortfall=max(0,reserve-available)；max_outflow=available-reserve；总缺口为账户之和。
- [ ] 处理本人定时互转的两条delta，保持确定排序；未知所有权/无效周期数据显式报错而不静默漏义务。
- [ ] 运行`make test-reserve`通过，输入深度不变断言通过，再本地提交。

## Task 2: PRD场景与共享消费证明

- [ ] 构造完整义务样本，张三600000分、王五转出300000分后，窗口内3000+1500分扣费，报告赵六max_outflow=295500分。
- [ ] 计划前后shortfall比较，扩大缺口可判SCHEDULED_SHORTFALL，已有缺口下保护动作不增加缺口；不在bankcore返回该L2策略违规。
- [ ] 本人定时互转可减少目标缺口，同时源余额峰值约束保持；不同输入顺序报告一致。
- [ ] 一次函数结果可供W2金额模板和W3hint同源消费，测试不复制另一套算法。

## Exit

`make lint && make test && make test-reserve && make contracts-check`；本任务只新增纯函数，不触及L3写路径。报告写实际结果与生产尚待S3完整数据的限制。协调者亲自复跑，另派只读评审，通过后单独PR与CI。

后续S3才补商户/账单/指令存储、真实snapshot/reads与缺失定时写；此处不提前实现或声称这些已完成。
