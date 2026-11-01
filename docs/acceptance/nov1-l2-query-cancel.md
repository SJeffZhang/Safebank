# 11-01 正式原句的只读/取消/安全引导

基线aa7e5d26160ef3be1d7b9341a0398b2f0390d0b1；针对原报告02/07/08/10/12–15/17/24–27/29–32/34b及30/39/41安全引导。

- 15条正式只读完整问句只返回NO_WRITE，不创建空FORM；混合明确读+写仍只保留真实写scope，未知后缀/引用/疑问不会靠只读关键词获取授权。
- 08使用当前客户真实scheduled.payee_id→已登记收款人名/别名，不再把“他行收款人”展示当名字；源账户必须当前客户。唯一精确目标才SCOPED，多条要求选择instruction_id，误导display/外人payee不能授权。
- 02短姓名只成为当前客户已登记名的前缀候选，不因候选唯一而自动变精确授权；选择仍走G19真实父链及G3 action，不猜目标或金额。
- 30/39/41保留INTENT_UNCLEAR与零scope，G21 prompt_template_id给固定risk_assessment_form/register_payee_form/transfer_consultation；不接受客户端模板、不自动建收款人/测评/转账，也不把咨询说成已经获准。30先于bare数字简写分类，R5不能被读成转账数字。

红绿：初28条旧20失败/8正控通过；三引导旧3失败。新W1相关1705通过。真实网关PG当前4项/7.00s：15条G3只读逐条无clarification/plan/job，取消实际泛化他行投影能落确切instruction scope，三引导模板与空scope，近名/多指令用真实G19→G3恢复原目标与金额；无执行。首轮新PG fixture误把G19返回写为201，冻结wire实际200，已只修测试；风险引导首轮仍被原bare shorthand吞入后补前置分支，未计新权限。

测试L3使用契约fake快照，真实的是网关DB/JWT/G3/G19/G21；不称本轮L1或银行现场已通。最终独立安全评审和完整head CI另核，原正式CSV不改。
