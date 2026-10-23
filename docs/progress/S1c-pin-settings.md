# S1c PIN Settings 接线进度

日期：2026-10-23；基线：main `94e40fe`；分支：`feat/l2-pin-settings`。

## 范围与验收契约

- 输入：当前 `Settings.demo_pins_file`、W1 的 `configured_hashes_from_file(pins_file: str)`；原 `pin_hashes` 显式注入保持不变。
- 输出：启动时一次加载并保留 runtime PIN 哈希快照；缺 W1 模块仍未配置；模块存在但新接口缺失或配置坏时 `DEPENDENCY_UNAVAILABLE` / 503，不能退回零参旧环境入口。
- 依赖：W1 loader 已在另一分支 `a7570e6` 冻结；main 尚未包含该模块，本组件用受控模块替身验证适配边界，不复制 PIN 算法。
- 失败条件：Settings 路径被环境覆盖、启动之间串用旧缓存、坏配置可启动、显式注入行为变化、已分配资源泄漏。
- 退出命令：`make lint`、`make test`、`make test-gateway`、`make test-gateway-db`、`make test-shared-runtime`。Docker 仅后台 CLI，专项 PGDATA 使用既有 tmpfs 夹具，禁止 up/down/test-p1 和删卷。

## 实际记录

- 已读 AGENTS、任务书、PRD §7.3 与 runtime/测试/README；初次读取不存在的 `tests/gateway/test_runtime.py` 返回 exit 1，随后定位实际 `tests/gateway/shared_runtime/test_runtime.py`。
- 首次普通沙箱写附加 worktree 因目录不在默认 writable roots 被拒绝；依既有任务授权申请受控写入。
- 红测：`.venv/bin/pytest tests/gateway/shared_runtime/test_runtime.py -q -k 'pin or w1'` → exit 1，`7 failed, 3 passed, 15 deselected in 0.69s`。失败为旧 loader 仍取预热的环境缓存、旧入口绕过缺接口/坏配置继续分配资源，以及缺新入口的 `AttributeError`；未出现 PIN 文件或真实密钥输出。
- 最小实现：runtime 将当前 Settings 传给私有适配器，要求 W1 新 loader 可调用并传入 `demo_pins_file`；缺接口 503，配置异常保留 W1 的 503，不改现有资源创建顺序。README 同步该边界。
- 新增 Fake Engine 用例显式选用记录 `locks.configure` hook 的局部 fixture；不对全文件 autouse，不遮盖真实 pool/hook 验收。
- 绿测：同一 PIN 定向命令 → exit 0，`10 passed, 15 deselected in 0.34s`。
- 已读 verification-before-completion 技能；以下全部是本 worktree 的实际运行结果，不使用其他 owner 的自报代替。

| 命令 | 实际结果 |
|---|---|
| `make lint` | exit 0；ruff check 通过，166 files already formatted |
| `make test` | exit 0；366 passed / 139 skipped，4.61s |
| `make test-gateway` | exit 0；137 passed / 91 skipped，3.48s |
| `make test-gateway-db` | exit 0；17 passed，2.93s |
| `make test-shared-runtime` | exit 0；59 passed / 零 skipped，12.07s |
| `PATH="/Applications/Docker.app/Contents/Resources/bin:$PATH" make test-gateway-db` | exit 0；17 passed，2.87s |
| `PATH="/Applications/Docker.app/Contents/Resources/bin:$PATH" make test-shared-runtime` | exit 0；59 passed / 零 skipped，8.36s |

最初两条 Docker 门禁使用已有 `/usr/local/bin/docker`；为严格复核任务指定的应用内 CLI 路径，又以上表显式 PATH 重跑。全部真实 PG 使用原有 opt-in 临时 tmpfs 夹具；本轮没有 up/down/test-p1、删卷或修改运行栈。

shared-runtime 两次均实际通过 7 项 Linux 秘密配置情形、Compose 非 root healthcheck、真实 PG 与现有服务器关闭门禁。`test_servers.py:68` 的取消场景按已有设计打印 ASGI `CancelledError`，两次命令仍 exit 0；没有隐藏该输出或改测试。普通 `make test` / `test-gateway` 的 skip 是未启用的 PG/Linux 目标，专项已明确启用；其他波次组件不在本轮验收范围。

`git diff --check` 已通过。生产改动仅 `_configured_pin_hashes(settings)` 新 loader 选择/路径参数及其一处调用，未修改 config、W1、授权矩阵或资源生命周期。

## 未覆盖

- 真实 W1 PIN 文件 + runtime + PostgreSQL 两路径登录，必须在 W1 同步本组件后单独联调；本报告不能作为该验收证据。
- 真实锁实现 W4a 尚未出现在本基线；新增 Fake Engine 测试通过显式 hook 记录验证原有接线，真实锁/资源集成需 W4a 合并后复跑。
- 组件自测通过，待协调者亲自复跑与独立只读评审。只做本地提交，未推送、未开 PR、未合并。
