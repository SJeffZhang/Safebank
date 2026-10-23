# W1显式PIN文件loader子组件

2026-10-23，a7570e6。configured_hashes_from_file按传入路径加载并验证，不读取环境或跨启动缓存；零参入口兼容，共用校验和HASHER。

协调者亲自五退出全部exit0：lint181、all1867/112skip（6.55s）、gateway1638/64skip（5.26s）、PG17（2.69s）、W1 1587零skip（23.16s）。review_w1_pin_loader独立spec+quality APPROVE，无本范围P0/P1/P2；实际定向19 passed。

仅helper获批，后续共享runtime尚须按Settings调用，并在真实runtime/PG以不同PIN文件与相反环境验证登录；客户锁及后续生命周期联调仍开放。推送检查点留存，不作为完整W1合并交付。
