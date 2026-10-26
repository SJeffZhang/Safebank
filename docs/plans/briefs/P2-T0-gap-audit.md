# P2 / try-agent T0：只读缺口与首个驱动任务设计

用户2026-10-26最新指令：恢复工作，首目标P2；E0与make try-agent同步做，做完先修复L1再完成P2。前端/BFF与定时器仍暂停。你是本机DeepSeek V4 Pro worker，当前任务仅T0设计，不能实施业务。

只写 docs/plans/P2-remaining-2026-10-26.md。第一步（前3轮）建报告骨架，边核对边写。最多读12个明确相关文件，不通读巨大AGENTS/PRD历史、不联网、不读.env/.secrets、不调用模型。总30轮。不要使用子agent。

已知基线main00aca15：L1 C1a-e、L2 G3/G4/G5/G6/G8/G9/G10均已合；W4c38真实PG/L3通过；P2 live黄级转账/生产L1 HTTP+PG恢复/真实record-replay尚未闭合。E0b另worktree评测止于G4，本任务不写evaluation/**。

优先读：
1. docs/plans/P2-P3-autonomous-2026-10-24.md（只用最终验收清单，旧状态过时）
2. services/agent/app/main.py（生产组装、HTTP/SSE入参、resume/state、PG）
3. services/agent/app/llm/client.py 与 replay.py（记录/回放与真实model注入方式）
4. services/gateway/app/api/{sessions,auth,approvals}.py（仅所需端点签名/参数，不读全目录）
5. tests/gateway/w4c/db/support.py（真实L3、真实时钟，测试当前仅卡片）
6. tests/agent/test_service.py（真实PG saver/HTTP用法）
7. scripts/p1_http_acceptance.py（长期栈操作模式）
8. docs/decisions/ADR-004-output-replay.md、ADR-005*，先rg --files定路径。

输出：精简到最多150行，列P2各验收项已有证据/真实缺口；try-agent现有生产API具体请求与响应字段、令牌分配；最小可实现T1任务（一个职责，输入输出、最多6个文件白名单、真实红测判据、退出命令）；再列T2确认G8、T3错误/重启/record-replay、L1缺陷处理先后依赖。推荐驱动模式要可用于P2，但不要实现完整BFF/UI；默认不得自动确认，需要显式操作员确认；测试中可以明确自动确认已知合成固定场景。

特别核实：生产_make_ports是否接C1e ReplayLLM，record/replay应怎样接；L1服务真实HTTP/SSE接口如何收G3 assistant JWT；DEEPSEEK key只由协调者注入；真实栈种子仍p1_core_v1，不是PRD全种子；评测测试可以tmpfs合成，不许reset长期库。G8授权由L2决定，L1不得持F/GATEWAY/D写凭据。

禁止修改生产/测试/AGENTS/PRD/依赖/Makefile，禁运行make/up/down/docker/git切分支或提交推送，禁调用模型。结束只回复报告路径、3个最高优先缺口、建议T1。新会话，不resume。
