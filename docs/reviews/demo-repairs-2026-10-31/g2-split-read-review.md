# G2 AA按ID读取：协调者独立只读复核

作者B，复核者root；目标cbc744b9（集成3439a630），独审无P0/P1/P2阻塞。生产diff只以真实state.splits和关联账户当前归属取代旧无条件404；没有信任请求中的customer_id，没有去除前后快照versions/clock一致性或下游L3的客户作用域。

新测试旧1失败/5原正负控通过，作者相关209纯测通过。协调者独立真实PG：生产prepare/execute创建AA，受控登记实际付款并执行，G2 fetch_read请求指定ID读出真实received/outstanding；其他客户同ID404。1 passed / 3.18s，文件tests/obligations_data/test_split_gateway_read.py；只证明G2函数与实际L3/PG数据，HTTP登录/JWT/read ledger由既有tests/gateway/w2/db/test_read_ledger.py另验。

不把这条自动化当作重新运行整场演示，不调用provider。最终完整SHA CI/PR发布回执另核。
