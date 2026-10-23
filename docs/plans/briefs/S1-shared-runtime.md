# S1 共享网关运行接线实施计划

> For agentic workers: 按 subagent-driven-development 分组件验收；本实现agent不得再开子agent，独立评审由协调者派发。

**Goal:** 补齐W1–W4共用的运行资源、全局撤销检查接线、澄清状态迁移与Linux秘密文件读取。

**Architecture:** 单进程集中创建并关闭DB/L3资源，公共/内部应用复用；请求处理器只消费app.state。JWT有效后仍须查持久撤销状态，依赖不存在拒绝。宿主私钥保持0600，运行容器非root。

**Tech Stack:** 现有Python3.12、FastAPI、SQLAlchemy、asyncpg、httpx；不增加依赖。

**Spec:** `wave1-shared-gaps.md`已获用户明确批准；PRD §7.3、§7.4、§7.10、§11.1；AGENTS.md。

## Global Constraints

- 不写业务身份发行、解析、审批、执行逻辑；这些属于W1–W4。
- 不改EvalContext、schemas、auth/matrix、tables、历史迁移、bankcore/contracts/L3，也不写任何新迁移。共享例外仅以下文件。
- 不改前端、L1。只本地提交，不push/PR/merge。不伪造DeepSeek署名。
- 禁止删除、重建任何Docker数据卷；不运行make up/down/test-p1。全栈协调者亲自串行跑。
- 原W1–W4签名不变；新增可注入参数需兼容原有构造测试，不添加生产绕过鉴权开关。
- 每小组件测试通过再下一项；mock、真实PG、容器权限分别报告。

## Files

可改：`services/gateway/app/main.py`、`config.py`、`auth/deps.py`、`state.py`；新建`services/gateway/app/runtime.py`（生命周期），但W1已有`auth/runtime.py`勿冲突；`Dockerfile`和`docker-compose.yml`仅gateway私有秘密适配；`scripts/`内独立gateway启动适配及权限验收脚本；`services/gateway/README.md`；`tests/gateway/shared_runtime/**`；必要现有相关骨架测试适配；Makefile末尾追加`test-shared-runtime`；`docs/progress/S1-shared-runtime.md`。AGENTS仅协调者改。

## Review Focus

- 部分启动失败/双app关闭资源是否泄露或重复close。
- 密钥缺失、权限错误、public/private不匹配应拒绝而非使用临时密钥。
- 有效JWT但已撤销/缺失持久会话应拒绝；无DB或W1检查函数尚未存在不得放行。
- 两个app共享资源而授权网络边界不变，公开health仍不读秘密。
- Linux上外部owner0600文件只有引导阶段读取，最终非root且tmpfs文件0700目录/0600文件；秘密不可输出。

## Task 1: 共享状态迁移

- [ ] 添加pending_clarifications的OPEN→RESOLVED/CANCELLED，终态无出边；使用已有ClarificationStatus，若schema无枚举不要改schema，选择兼容state实现。
- [ ] 先测试合法边、非法边ValueError、条件UPDATE竞争失败409；真实PG复用tests/gateway/db/conftest.py的随机临时容器（不使用卷）。
- [ ] 实现后复跑并本地提交，记录实测结果。

## Task 2: 资源生命周期与撤销接线

- [ ] 注入`app.state.db: AsyncEngine`、`l3: L3Client`、`wall_now: Callable[[], aware datetime]`；可选`jwt_private_key: bytes`、`pin_hashes: dict`，生产从配置/文件加载；只有集中生命周期建池。
- [ ] 使用现有GATEWAY_DATABASE_URL、BANK_BACKEND_URL、GATEWAY_SERVICE_TOKEN、JWT_PRIVATE_KEY_PATH、DEMO_PINS_FILE；不输出值。
- [ ] W1消费接口已落盘在`/Users/jet/.codex/worktrees/safebank-w1-identity/WeBank/services/gateway/app/auth/`，可只读核对，禁止复制业务实现到本分支：`tokens.check_revocation(conn,caller,*,now=None)->None`；`pin.configured_hashes()->dict`；`auth/runtime.private_key(request)`优先app.state值。
- [ ] `caller_dependency`验JWT后、handler前调用撤销检查，非客户凭证按矩阵正常工作；W1函数在当前main尚不存在，必须兼容分阶段部署并fail closed，不用成功占位函数。用签名一致替身证明接线；实际W1联调由协调者合并依赖后复跑，报告不能写已联调。
- [ ] 测试资源一次创建/一次关闭、启动中途异常释放、撤销/过期/缺失行拒绝、服务令牌路径、两app共享但路由隔离；复跑现有骨架测试。

## Task 3: Linux秘密读取

- [ ] 复用项目已有Postgres私有tmpfs适配思路，限定gateway：只引导进程以所需权限读宿主0600私钥与demo_pins，写gateway运行用户所有的私有tmpfs，再降权运行。不得把私钥改0644，不得放入镜像或.env版本控制。
- [ ] 独立容器测试模拟不同宿主uid与0600，证实当前错误与修复；检查最终uid和私有权限、缺文件拒绝、日志无私钥。不创建或清理任何Docker数据卷，不动safebank现有栈。
- [ ] compose最小接线与README配置说明同步。若现有cap_drop/no-new-privileges与降权步骤冲突，用最小必要方案并在报告解释，不扩大运行进程权限。

## Exit

`make lint && make test-gateway && make test-gateway-db && make test && make test-shared-runtime`。
最后目标必须实际启用自身PG测试，并执行独立Linux权限验收；需要协调者资源时明确报告未跑，不跳过冒充成功。

报告写`docs/progress/S1-shared-runtime.md`：所有文件、各实际命令/结果、与W1待联调项、权限边界、待决、提交SHA。仅本地提交；协调者亲自复跑+独立只读评审后才推送。S1可先合入main作为W1先决依赖，W1同步后必须实际验全局撤销。用户已批准本范围，无需再次确认。
