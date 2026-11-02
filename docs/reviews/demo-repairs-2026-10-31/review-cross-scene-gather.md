# 跨场景 L1 gather / G20 选择上下文独立复核

范围：源分支六提交 `2bbdec7519a94c41e077152e011ed46cb314d604`、`704bd506fd7c8aa511c326cde1a47abc32191790`、`32c9d02997b4c1978e699590d117ae873b716a62`、`2bca22b2bf0616709a80d817c7f6a960a4ab8015`、`27b23d302d920a32959aece4ba321320c6a6add7`、`9bec3be9a748fecc6d44d1fd5e88555ab9440389`。基线 `f4af1ba29be6839df2730a96a113aece0c9ded47`，最终精确 archive `/private/tmp/sbg-demo-repairs-20261031/cross-scene-review-9bec3be`。root 已集成映射至9f34180的事实由协调者提供，本审查未混入root其他WIP。评审者未实现该L1单元。

## 结论

**APPROVE**。未发现需要修改的 P1/P2 实质问题。只证明新增读取、结构化选择上下文和展示接线的代码/纯测边界；不证明真实模型必然生成正确计划，也不替代真实 G3/G20/G4/G8 授权、资金状态及最终集成CI。

## 源码核验

- X1/X4 固定补读只选择读取前提；仍要求单条完整原始 typed、SCOPED、needs_write=true、无真实澄清/echo。整句fullmatch拒绝引用、疑问/否定、附加外转后缀和多原话拼接。没有把 needs_write 改成false，没有新写权限或执行入口。
- X1 fresh profile决定上海自然月，读取完整月汇总与完整异常区间，再读卡。分析卡只在对应当前月/完整异常记录齐全时增加，保留审批/执行状态卡；没有用模型文字或分页首屏推总额/“无异常”。X4区间与实际G3 suspicious_cards谓词一致，读取全部本人账户/卡和异常，不自行选卡或替换应急金额。
- `current_x3_selection` 白名单来自当前G20 ACTION：运行时客户/会话一致、唯一当前ACTION空text、实际parent链、当前SCOPED、无澄清/echo、同id/kind的唯一scope，并限FUND_SUBSCRIBE+available_less_7d_charges。仅输出当前root/parents、product/account、模板；任意amount、memo、额外产品字段不进入提示的金融上下文。
- 实际编译图 ingest 总是用G20/G21覆盖输入的text/kind/hints/parent_ids，并清空旧reads/understanding/plan/execution。追加伪造初始hint/父链/读取/text的独立图测试确认实际G20选择和fresh读覆盖它们，计划只声明当前root，停在真实审批接口形状对应的中断，无execute调用。该测试的G20/G4是MockTransport，未冒充真实授权验收。
- 理解或规划忽略当前选择时，仅沿有界重生成给反馈；不直接改模型字段。账户/产品仍引用实际read，金额仍必须是原typed或既有derived模板；action/hint/read中的金额不能洗成申购资金来源。L2保存的scope/JWT/evidence、严格意图和派生金额重算未修改；G3实际注册完成的ACTION clarification_id为null，parents保留原请求，所用形状与生产服务一致。
- 新的X1/X3/X4每项必要read直接传播依赖失败；不读其他产品替代，不把无效测评改有效。模型invoke在校验try块之外，provider402/403/429不被结构化重生成吞掉。新增代码没有修改respond/verify或资金执行。
- X2后续仅修正测试夹具的真实typical_amount_cents/读取Schema，不改生产金额或周期语义。

## 实际测试

1. 首次照作者acceptance执行 `tests/agent -m 'not db'`：**857 passed, 40 deselected, 2 failed in 6.97s**。两个失败均为 `tests/agent/gateway/test_real_transport.py` 需要bind localhost端口，被sandbox `PermissionError: Operation not permitted` 拒绝；未建立真实上游/API请求，未申请放宽。该文件另有一项纯负控已通过。
2. 明确排除上述需要监听端口的整个文件后，只跑内存/MockTransport范围：

   ```text
   PYTHONDONTWRITEBYTECODE=1 .../.venv/bin/pytest tests/agent -m 'not db' \
     --ignore=tests/agent/gateway/test_real_transport.py -q --tb=short -p no:cacheprovider
   ```

   **856 passed, 40 deselected in 6.93s**。两次结果是重叠验证，不相加。日志分别为 `cross-scene-pure-review.log`、`cross-scene-pure-noio-review.log`，均在本报告同目录。
3. 自写 `/private/tmp/sbg-demo-repairs-20261031/test_cross_scene_review_boundaries.py`：**46 passed in 1.88s**。其中39条逐项覆盖X1四个、X4四个、X3五个必要read处的402/403/429，确认每个失败点仅一次且无模型回退；三条拒绝action/hint/read金额替代derived；三条planner quota单次止步；一条编译图丢弃伪造初始上下文且不执行。
4. 相关L2纯测试 `w1/test_predicates.py`、`w2/test_intent.py`、`w2/test_derived.py`、`w2/test_derived_cancelled.py`：**20 passed in0.79s**。这些是纯规则测试，部分授权证据由w2 fixture注入，不能替代真实持久化/JWT证据测试。
5. 本单元14个修改Python文件 `ruff check --no-cache` 通过，精确六提交增量 `git diff --check` 通过。

日志与独立负控均留在 `/private/tmp/sbg-demo-repairs-20261031/`。本次未运行DB、实际服务API、供应商模型、Docker、浏览器或部署；localhost监听测试被环境拒绝的事实保留，不虚报全部859项通过。
