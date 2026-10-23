# W1 独立只读评审与修复记录

2026-10-23，review_w1，8555bf0..d9a1b56bc5fa7e41d4672ec00101356fabbddb52。规格 FAIL；代码质量 REQUEST_CHANGES；7个P1，均为W1自有缺陷，不能归因于共享依赖。只读源码和纯函数探针；没有执行资金写入，不宣称L3未授权转账已发生。

## 开放问题（原评审结论）

1. **P1 非指令和条件句产生可执行写作用域**。parser.py:114,292,322：`不需要给王五800`、`等工资到账后给王五800`、`假设给王五800`均生成80000分转账scope；`给王五800，赵六欠我900`生成两笔转账。全句关键词开启解析后，分句未独立验证动作。采用受支持的完整命令结构，逐分句确认动作、方向、条件，不以继续扩充否定词黑名单替代语法。
2. **P1 金额单位被忽略**。parser.py:81,97：`给王五800分`生成80000分。明确单位和币种语法，整数分精确换算；不支持单位不得产生scope，补分/角/元/其他币种边界。
3. **P1 已指定来源仍套用默认账户**。parser.py:143：`从储蓄给王五800`正确acc_save，但`从我的储蓄账户给王五800`错误acc_zs。区分未提及来源和提及但不能解析，后者必须澄清/表单，不默认。
4. **P1 收款人子串代替精确匹配**。parser.py:324：`给王五同事800`错误授权fp_ww。先提取完整目标槽位，再NFKC姓名/别名相等且唯一匹配，不能包含关系。
5. **P1 多收款人澄清跨目标错配金额**。parser.py:161,347；service.py:212：`给王吾800，给赵柳900`第一候选王五/王武却共享amount被覆盖90000；选王五即授权90000。`给王五800，给王吾900`合槽整体覆盖transfers，丢失已明确800元。缺槽绑定条目并保留目标—金额—来源关联；冻结模型不能表达多歧义时整体FORM_REQUIRED。
6. **P1 FORM_REQUIRED经空澄清升级SCOPED**。sessions.py:199；service.py:176,392：`明天给王五800`产生FORM_REQUIRED且missing_fields含first_run_at，但required_fields空；G19 kind=CLARIFICATION、action_kind=null、fields=[]被接受，G3采纳后无条件SCOPED。只允许SLOTS且确有待补字段走CLARIFICATION，FORM_REQUIRED必须完整FORM；合槽后重检完整性。纯函数确认空字段接受，HTTP/PG路径尚待回归。
7. **P1 无法解析的新改口不作废旧授权**。service.py:347,414：已有`给王五800`后`不对，改成给赵六九百`为INTENT_UNCLEAR、空action/required_fields，未调用supersede，旧原话仍ACTIVE同epoch。明确改口识别须与新指令解析分离，在客户锁/同事务作废旧scope，再澄清新指令；保留EXECUTING/UNKNOWN真实状态及claim。

协调者已亲自复现第1（除假设句）、2、3、4的六个输入输出。第5–7要求增加真实PG的G3/G19/loader路径验证。1376认证矩阵组合不能证明授权语义安全。

## 已有正向证据与限制

PIN错误计数可提交并行锁防丢失；JWT jti持久化，归属/撤销/期限检查；G18空utts；action/hint服务端读取与一次性/归属检查；L3 HTTP在SQL事务外；request_id统一幂等；取消保留EXECUTING/UNKNOWN及claim。共享运行、真实W4锁、真实L3业务数据、W3 hint生成仍待联调，非本轮W1缺陷。

协调者已亲自复跑d9a1b56五条退出：lint163文件；gateway1510 passed/11 skipped；PG17 passed；test1629 passed/59 skipped；test-w1 1457 passed/0 skipped。上述结果未覆盖七项缺陷，不能批准合并。

## 修复轮1

原实现agent修复上述全部问题，逐项记录覆盖测试、命令和输出；协调者复跑后由原评审定点复审d9a1b56..新HEAD。未通过前不开PR。

定点复审d9a1b56..cc32e55：1–6 ADDRESSED，7 NOT ADDRESSED，REQUEST_CHANGES。协调者五退出全部通过（W1 1514零skip，23真实PG），但以下同根问题未覆盖。

## 修复轮2：明确改口的分隔符边界

parser.py:105,112只识别“不对,”，service.py:364,457因而遗漏旧授权作废。`不对。改成给赵六九百`、`不对；改成给赵六九百`、换行、空格四种形式is_correction=False且INTENT_UNCLEAR空scope；旧“给王五800”仍ACTIVE，同epoch。评审纯函数探针及协调者亲自is_correction探针均复现，逗号为True、其余四种False。没有运行这些新增变体的HTTP/PG，不冒称该链已实际执行。

统一改口识别与前缀剥离的边界；明确“不对+分隔符+改成/改为/换成”即使后续金额不支持也先撤旧scope。参数化真实PG回归旧SUPERSEDED/旧JWT loader拒绝/G20不返回，新原话FORM_REQUIRED、执行状态与claim保留；L3不可用仍撤旧；不对吗/普通查询/引用内容不误撤。原实现agent修该剩余项，不扩大其他业务。修复后再定点复审cc32e55..新HEAD，再同步main并真实联调。

## 修复轮2：7250105

原问题7 ADDRESSED，独立review_w1定点APPROVE；句号、分号、空白和换行统一识别/剥离，查询与引用不误撤。评审实际18 passed/42 deselected及手动探针。协调者全部五退出exit0，W1 1560 passed零skip（54项真实PG）；其他计数见AGENTS §4。原七问题已关闭；真实S1运行接线、W4锁、后续状态集成尚未完成，不等于完整W1交付。
