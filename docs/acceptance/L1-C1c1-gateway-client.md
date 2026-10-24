# L1 C1c1 Gateway协议客户端验收

2026-10-24；业务候选9ebfa127cc25a26f7d910c857355c1c8a50a03da；同步main26dea40后9f192ad4110279704e5e13836e71e3f0657486ee。协调者亲验及原DeepSeek独立复审APPROVE，无未闭P0/P1/P2。完整报告docs/reviews/C1c1-gateway-client-review.md保留各轮发现/修复。

G20/G21/G2/G4/G8客户端只调用配置的L2，A服务令牌与每调用runtime助手JWT分离；sid/plan绑定、18读工具生产参数/返回严格验证，不创建原话/审批/OTP。Decision/shadow递归、scope整数分与闭集状态、完整错误409新decision验证保留；202只返回UNKNOWN，不轮询、不自动重试提交。有限timeout和禁止重定向逐请求强制，注入client不能覆盖。

初审发现跨域重定向转发A令牌；修复后复审发现httpcore要求timeout字典而非Timeout对象，曾被MockTransport全绿掩盖。原worker已修为标准as_dict，3项真实loopback默认HTTPTransport测试验证本地200、连接拒绝安全错误、跨域302原服务恰一次/目标零请求；四阶段timeout均有限。错误不透传任意details和原ValidationError输出，trace排除已知配置凭据；所有先前反例保留回归，未降低断言。

亲验9ebfa12：lint274、agent212零skip/xfail（2.71s）、全量2331/531skip（10.98s）、contracts/actionlint全部exit0。原reviewer亲跑129客户端及212 Agent回归后APPROVE。同步main仅AGENTS冲突、双方历史均完整保留，agent代码/测试/依赖零diff；组合lint285、agent212（2.57s）、全量2369/538skip（10.82s）、W3a真实45（3.50s）、W2真实246（10.93s）、义务30（4.62s，既有FK环warning）、contracts/actionlint均exit0。日志/private/tmp/sbg-codex-c1c1-r2-20261024及/private/tmp/sbg-c1c1-main-combination-20261024。

范围：协议测试主体MockTransport；3个真实HTTP测试仅合成loopback服务，不是真L2/L3。G4/G8生产端仍stub；C1c2 ingest/完整路由/定向REPLAN补读、C1d PG saver/HTTP/SSE和C1e输出仍后续。前端/BFF暂停，不宣称P2/P3完成。PR仍须精确head两事件的unit/p1-postgres四项COMPLETED SUCCESS才合并。
