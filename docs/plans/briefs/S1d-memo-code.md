# S1d 敏感备注黄级代码（准备稿）

PRD7.5要求memo/alias含至少8位数字或金额格式时升黄，但共享Code与9.8未给对应代码，W2严格xfail不能作为完整退出。共享owner在S1b/S1c后串行补最小代码契约；W2仍不改冻结contracts或registry结构。

独立分支feat/contracts-memo-code从最新main开始。允许Code枚举新增MEMO_SENSITIVE_CONTENT、契约freeze快照中该枚举的精确增量、PRD7.5/9.8登记YELLOW及说明、必要contracts测试与报告。保留既有code与全部写工具签名；不改政策阈值，不把它冒充INJECTION_SUSPECTED红级或TAINTED_SENSITIVE_ARG拒绝，不改W2业务文件。

先红后绿验证新code可在现有Finding/Violation/Decision模型按原schema往返、严格拒绝未知code，冻结快照仅预期枚举增量。共享组件退出lint/test/contracts-check；若现有模型发现风险由固定注册映射决定，报告具体缺口，不擅改registry结构。无迁移/新依赖/网络服务。

共享组件独立审核/协调者复跑/全绿CI合并后，原W2 owner同步并落实既有TODO及删除严格xfail，测试普通文本不升黄、8位边界/金额格式/memo和alias均升黄、来源限制不放松，W3整决策保留YELLOW、W4完整PlanView原文绑定仍需联测。共享代码可序列化不等于后续风险业务验收。

只本地提交，不push/PR/merge；不自行开agent、不改前端、不操作Docker卷。协调者按用户长期授权办理PR和merge。
