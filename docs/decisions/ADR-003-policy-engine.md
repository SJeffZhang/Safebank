# ADR-003：确定性 Python 规则引擎

日期：2026-10-23；状态：采纳设计，待 P3 实现。

遵照 PRD §4.3、§7：Python Rule + YAML，LLM 不决定授权。
不引入 Invariant/ML 注入分类器。前序 V-INV 已记录本地 POC 及语法失败，本轮不重复该 POC，也不声称实测了新策略。
所有后续规则必须带正常、攻击和边界用例，逐组件验收，阈值变化同步 PRD 和日志。
来源：https://github.com/invariantlabs-ai/invariant ，历史实验见 PRD §0.4。
