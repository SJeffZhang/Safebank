# try-agent T1：命令行从真实API协议跑到L2确认面板（仅这一项）

用户2026-10-26授权E0与make try-agent同步推进，测试/驱动完成后先修L1再验收P2。你是DeepSeek V4 Pro实现worker，全新会话60轮，不用子agent，不通读巨大AGENTS/PRD。前5轮先写scripts/try_agent.py与测试骨架，按报错读文件。

T0报告有错误已由协调者否决：生产_build_production_ports确实使用RespondAdapter(llm)/OutputVerifier(llm)，main.py:200以后的旧_make_production_respond/_verify并未被生产调用，不得以此改L1；record/replay未接是真缺口但不阻塞先实现CLI协议驱动。80万元不是黄级测试；后续固定800元合成转账。compose已有agent.env生成路径，不读.env/.secrets核实。本任务禁止修改生产L1/L2/L3。

本任务白名单只6文件：scripts/try_agent.py；tests/try_agent/test_driver.py；tests/try_agent/__init__.py（若需）；Makefile末尾追加try-agent/test-try-agent；docs/progress/TryAgent-driver.md；docs/plans/P2-remaining-2026-10-26.md（纠正T0已知错误）。不修改packages/services/依赖/CI/AGENTS/PRD/evaluation。

输入/输出：可运行python -m scripts.try_agent，argparse接--gateway-url（默认http://gateway:8200）、--agent-url（默认http://agent:8100）、--customer-id、--text、--pin（也可getpass，禁止回显）、--out（可选输出脱敏事件）。从FRONTEND_SERVICE_TOKEN环境取F令牌，不读文件，不打印服务令牌/JWT/PIN。调用真实G1登录→G15会话→G3 typed原话→L1 POST /threads与/threads/{sid}/runs/stream（assistant JWT）→解析SSE→如有审批调用G9原样显示L2 PlanView后停止，返回明确PENDING_APPROVAL。可处理直接完成/CLARIFY/REPLAN/DENY/UNKNOWN/HTTP失败，但不能伪造成功。本T1不确认、不调用G6/G8（T2后加显式人工确认），所有退出有明确状态。

API已核事实：
- Gateway的服务头X-Service-Token=F token；app请求Authorization:Bearer app_token；L1请求只给G3 assistant_token（不把F token给L1）。
- G1 POST /auth/login请求Login模型见services/gateway/app/utterance/models.py；返回app_token（真实login赋予app/approve scope）。
- G15 POST /sessions body request_id，201/session_id。
- G3 POST /sessions/{sid}/utterances body request_id/kind=typed/text，201，返回{utterance:{id,...},assistant_token,...}；不是utterance_id顶层。
- L1 POST /threads body {thread_id:sid}；POST /threads/{sid}/runs/stream body {input:{utterances:[{id:utt,kind:typed,text:text}]}}；SSE可能event=updates（含__interrupt__）、values、custom/cards、messages、error；读main.py具体wire，不猜event名。不要仅靠HTTP200当成功。
- G9 GET /approvals/{approval_id}带F/app令牌；PlanView视图不得经LLM改写/重新生成。
- 读文件限：services/agent/app/main.py 265-500；services/gateway/app/api/auth.py/sessions.py/approvals.py（只查需要的handler）；services/gateway/app/utterance/models.py（Login）；现有scripts/p1_http_acceptance.py作请求错误参考；Dockerfile与docker-compose.yml仅看frontend脚本代码可用性/执行位置，不读.env。

Makefile：try-agent应适合当前两网拓扑，默认用docker compose exec frontend python -m scripts.try_agent（前端容器仅当受信驱动宿主，UI/BFF不实施），ARGS传CLI参数；也支持直接python -m...用于本地显式URL。不得新增端口/修改compose。test-try-agent仅pytest该目录。

测试T1本身允许httpx.MockTransport协议测试（明确不算真实P2）：验证完整G1/G15/G3/L1 SSE/G9请求，正确提取嵌套utterance、服务/JWT隔离、SSE分块/多行/错误事件、未确认零G6/G8请求、缺F token failclosed、输出无任何合成secret/JWT/PIN。先记录红测，再实现最小通过；测试不得导入脚本中内部implementation来重复逻辑造假。可注入httpx.Client/IO以便协议验证。禁止自动审批、静默调模型、替换L2 Decision。

退出：.venv/bin/pytest tests/try_agent -q；.venv/bin/ruff check scripts/try_agent.py tests/try_agent；.venv/bin/ruff format --check同文件；python -m scripts.try_agent --help。此阶段无Docker真实栈调用/live模型请求；未覆盖写进进度。单独命令，不用;或&&，只看汇总。完成本地提交白名单文件，回复SHA/红绿/未覆盖并停止。禁git push、长期up/down/reset/删卷、读取任意.env/.secrets。402/403/429即停。
