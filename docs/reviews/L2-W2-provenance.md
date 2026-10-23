# W2 独立规格与质量评审

2026-10-23，review_w2，只读8555bf0..1699091e1a9c103e4bd8829811bb04776d1be79f。规格FAIL，质量REQUEST_CHANGES；2项P1、1项P2，无已证实P0。三个内存探针均由评审运行，未声称G4/L3已执行；首个探针缺PYTHONPATH导入失败，补路径后复现。协调者已读相关源码确认问题机制。

## 开放问题

1. **P1 删除明确授权的卡保护参数**，pipeline/intent.py:161–165。_fields_match遇value=None就跳过，scope明确online=False,atm=False，计划online=False,atm=None，实体/来源/意图后matched=True且findings=[]，当前ATM原为开启；卡限额同样受影响。区分未指定允许保持与删除已明确值，后者INTENT_SCOPE_EXCEEDED。
2. **P1 非转账scope缺一对一匹配**，intent.py:55,151。仅ActionKind计数+逐步any匹配，scope可重复用。scope产品A100元和B100元，计划A100元两次，IntentStage matched=True无finding；两卡冻结也可重复第一张遗漏第二张。动作/实体/精确字段需可消耗的一对一或多重集合匹配，不能仅按kind计数。谓词集合展开与派生多动作需保持绑定语义，不能为堵漏洞拒绝全部合法组合。
3. **P2 稳定的不同客户范围快照被误判竞争**，api/tools.py:67，intent.py:276调用。fetch_read steps=[]得到本客户versions，整dict对比计划含本行收款客户的expected.versions。稳定{"cus_a":0}对{"cus_a":0,"cus_b":0}且clock不变，仅读一次snapshot就503。按读取实际涉及范围核对，同时严格保持读前后版本/时钟一致；不能用修复名义忽略真正变化或缺失所需客户版本。

## 待共享集成（不归因W2越权责任）

- memo黄码缺MEMO_SENSITIVE_CONTENT，provenance.py:244–246仍空、严格xfail，完整退出不成立。
- reserve、真实义务/产品/AA投影、其余真实L3读、reset撤销、污染目标冷静期持久化待补与联调。
- **具体跨分支语义错配，不能模糊成未联调**：W1采纳hint/action后used_by写utterance ID，W2只接受空或plan ID；W1 loader的scope_ids为utterance IDs，W2按原hint/action ID查询，合法采纳来源不能找到。需统一服务端来源链，不能只宽松接受任意used_by。
- W3 form_plan的AuthorizationInputs为utterance_ids=()且无ACTIVE statuses，W2 intent找不到scope。需生产者消费者真实集成测试并保持F表单授权边界，不放宽W2掩盖。

正向核对：未发现跨客户读取放行；caller注入customer；read ledger有customer/sid/revoked过滤；不稳定夹读拒绝后不生source_ref；可信余额读取不能直接授金额写权限；W2未写Decision或冻结文件。真实reserve尚未接入，不能用纯替身作为生产能力证据。

协调者已复跑五退出：lint161；gateway119 passed/3 skipped/1 xfail；PG17 passed；all238 passed/51 skipped/1 xfail；W2 58 passed/1 xfail。不能覆盖上述反例，xfail不算通过。

## 修复轮1

原W2实现agent只改W2白名单，修1–3并补正常/攻击回归；源引用与表单跨分支问题先提出明确语义方案，协调后在各owner分支修改，禁止复制其他worker业务。本地提交，协调者复跑后原评审定点复审1699091..新HEAD。

## 修复轮1：27a067a

独立review_w2定点PASS/APPROVE，原三项均ADDRESSED：显式参数不能省略；scope许可完整二分一对一消费；read按实际客户子集比较expected且前后全部版本/时钟仍严格相等。评审实际定向23 passed，未发现本轮新增P0/P1/P2。协调者五退出全exit0，W2专项81 passed/1 xfail，其余计数见AGENTS §4。memo缺码、来源链/F适配、真实L3/reserve未解决，本轮批准不覆盖完整W2验收。
