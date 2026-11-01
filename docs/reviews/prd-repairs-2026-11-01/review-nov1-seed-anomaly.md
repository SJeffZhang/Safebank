# November 1 canonical分类与seed异常承诺审查

精确对象：2b51bbe92a4b3692e1ab8557e6c2ec587b92ae5f，私有archive `/private/tmp/sbg-nov1-repairs/review-seed-anomaly`。只读源码、真实prepare_seed与完整合成交易进入bankcore；没有PG/服务/provider/模型/browser/Docker，未改产品、数据、原报告。

结论：production canonical分类和本次离群结果符合现行PRD统一八类规则；旧生成器的“原始细分类别互不相同，所以不会离群”前提失效，导致固定三条承诺不再满足。不能为旧count改回rawcategory、改阈值或利用后续duplicate隐藏第一笔。完整窗口还有txn_zs_012，第一个PG断言在Sep4已失败，所以原日志尚未执行full5断言。

## 数学与元数据

所有相关商户未指定MCC，prepare_seed明确默认0000；权威商户category外卖/零食/茶饮映射餐饮，零售/生鲜映射购物。没有merchant错绑或float金额。R5只读CARD_SPEND/POSTED，按该客户在交易前90天的历史取样，状态/未来数据均不进入本次基线。

| 交易 | 日期/分 | 真实信号 | 基线/阈值 |
|---|---|---|---|
| txn_zs_014 | Sep07 02:13 /128000 | NIGHT_LARGE + NEW_MERCHANT_LARGE | 夜间>50000；新商户>100000 |
| txn_zs_015 | Sep15 12:00 /26800 | CATEGORY_OUTLIER | 餐饮49笔=25×800+24×1800；μ1289.7959183673469、σ499.895865874118，μ+3σ2789.483515989701，公开取整阈2789，均值1290 |
| txn_zs_016 | Sep15 12:04 /26800 | DUPLICATE + CATEGORY_OUTLIER合并一条 | 加入015后n50，μ1800、σ3605.551275463989，阈12617；重复前笔015，4分钟 |
| txn_zs_017 | Sep22 15:30 /349900 | NEW_MERCHANT_LARGE | 新商户>100000 |
| txn_zs_012 | Oct13 19:00 /12800 | CATEGORY_OUTLIER | 购物此前35笔生鲜×3200，μ3200、σ0、阈3200 |

Sep精确014/015/016/017共4；[Sep1,seedNOW)另012共5，均crd_zs_1。原三条故意预埋仍为014/016/017，9月消费总额644800分不变。只把私有counterfactual的category比较临时换回旧rawcategory时Sep/full都变回014/016/017三条，证明变化来自口径合并；此反事实不是生产修复建议。

## 独立证据

私有纯seed probe `/private/tmp/sbg-nov1-repairs/probe_seed_anomaly.py`，数学日志review-seed-anomaly-math.log；私有4控制（精确4/5、015不依赖未来016、012真实基线、旧口径反事实）联合analytics与canonical/loader现有unit共83 passed，日志review-seed-anomaly-pure.log。纯构造保留seed所有175条prepared交易及权威merchants；cards/standingInstructions非R5计算输入，未把缺生产数据导致的差异当数学结论。首次纯构造covered_customer_ids未排序导致合同验证错误，修正后才得到上述结果，未计作产品旧红。

## 规范冲突与root决策

PRD1221的四规则/严格历史及1898的统一canonical要求保留；原1221/1701/1707固定三条承诺与冻结seed实际数学结果不能同时成立。若一定仍要求三条，只能另授权新fixture版本按canonical重验，并保留原13交易、账本/余额守恒；不能改既有客户数据或隐藏015。未经规范同步，单改expected为4并不能称符合完整现行PRD。

root现已明确决策：保留seed数据、production canonical/阈值/全部信号，授权test/docs-only精确更新，并由root同步PRD/AGENT，不修改原43报告三条历史观察。执行者将另开fix/nov1-canonical-seed-expectations从2b51，实现测试精确IDs/codes/evidence/card/月总额及generator过时注释；旧481隔离分支保持。新的生产模型现场/最终全CI仍未通过，不因数学证据升级旧结果。

建议root同步：PRD1221保留三故意预埋且列新增015/012及4/5窗口；1701scene_bill三条改真实Sep4；1707说明4/5均同卡，谓词卡集合与冻结行为不变；1898统一canonical条款保持。新acceptance须写明原报告属于旧aa7，不改其原输出。

原seed SHA256：`edbd72f12e179510529af3bab824351289fb1b7a358655614a18ab1193191d45`。

## 授权实施交付

root决策后已在独立branch `fix/nov1-canonical-seed-expectations` 从2b51提交 `7f3dd79c85148d70a7c48e922efda5b2202c8626`；WT `/Users/jet/.codex/worktrees/safebank-nov1-canonical-seed/WeBank` clean。仅测试精确ID/codes/evidence/金额/账户/卡/三个月总额/余额、generator注释/docstring及独立acceptance。旧实际seed-history assertion body在纯prepare_seed输入1红；新body及9篡改负控、独立数学和canonical/loader控制共21passed/4PGskip，ruff/format/diff过。seed bytes逐字节与2b51相同，generator去docstring后的可执行AST相同；未执行生成器main。真实PG、PRD/AGENTS同步、独审及新完整head CI仍由root负责。此交付不是对原43结果的重判。
