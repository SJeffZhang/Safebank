# SafeBank 前端交接文档

银行卡最新视觉：三卡使用石墨黑/铂银/香槟金金属拉丝材质，前景与露出叠卡颜色一致。入场改为后排到前景逐张显现（每张错开320ms），结束或主动切卡后不再等待整套序列；下方信息随后淡入。减少动态效果保持降级，外观不代表产品等级或权限。最终397项/构建通过，详见验收末尾。

最新页面动效增量：页面分区入场错峰延至130–530ms，首页余额卡/辅助卡/快捷入口独立出现；LineChart绘制1500ms，面积渐变同步左至右展开，数据变化时重播，多图渐变ID独立。减少动态效果下静态完整显示。顶栏顺序为通知→主题，主题圆形切换1400ms。最终全量397 passed，详见ACCEPTANCE末尾。

2026-10-29 动效增量：只读参考相邻bank-agent-web的Pressable/cardIn/CountUp，当前按钮/快捷入口/底栏增加按压回弹，页面分区错峰入场；Money通过useAmountMotion.ts作展示递增与增减过渡，更新时从当前显示值续接，隐藏/卸载立即清理。Approval的Money显式animate=false，交易明细/导出仍使用原金额。尊重prefers-reduced-motion；本次浏览器该值为true，验证的是静态降级，普通动画由可控RAF测试验证。

更新：2026-10-29。面向接手的前端同事和新 Agent。本文说明当前源码，不把合成演示、单元测试或历史日志当成真实三层联调验收。

最新增量（依据用户视频纠正）：三个主题按钮发送用户明确选择的主题，右侧出现请求气泡，左侧显示处理进度、回复与业务内容卡片；手动输入同类需求也能展开。AssistantWorkspace.tsx复用read接口，账单月份/分类/明细、账户/资金表单、扣费预留/订阅可在当前对话内展开。默认当前模拟月，主题识别仅用于展示，不是授权或完整自然语言参数解析。写表单保持空值，复用原预演/确认；点击主题不产生写计划。气泡/卡片入场动效与stream进度思考状态替代上一版“办理引导”。Preview仍明确合成。详见ACCEPTANCE末尾。

先读本文件，再读 [NEXT_AGENT_PROMPT.md](NEXT_AGENT_PROMPT.md)、根目录 `AGENTS.md` 和 `PRD.md`。实际代码、当前 Git HEAD、最新 CI 和验收证据优先于历史聊天的旧数字。发布记录见本文末尾。

## 1. 任务背景与不可丢失的用户约束

1. **这是竖版银行 App，不是桌面 Web 工作台。** 桌面预览以 375×812 为设计基准，视口不足时受窗口高度约束；手机宽度下铺满屏幕，内容内部滚动，底部导航常驻。不要重做成侧栏仪表盘或横版网页。
2. **继承队友的 Graphite & Champagne 外观。** 原有石墨灰、香槟金、字体、圆角体系保留；不能自行改成深墨绿导航等新色板。基础令牌在 `src/styles/tokens.css`，验收中记录其原 SHA256；自绘小安图标除外观升级外仍用这套颜色。
3. **只在主项目 `frontend/` 写前端。** 当前项目远端为 `SJeffZhang/Safebank`。队友 `WsTwistzz/safebank-ui` 仅作为用户授权的设计/源码基线，不能向队友仓库推送。
4. **Codex/当前 Agent 亲自实现，不使用 DS worker。** 不擅自启动 DeepSeek worker、Claude CLI、后台自动任务或其他 Agent。本轮无子 Agent。
5. `SJeffZhang/finmate-preview` 仅参考交互/结构思路。已发现无 LICENSE，不能复制其图片、字体、压缩 JS/CSS 或整体外观。来源记录见 `docs/SOURCES.md`。
6. 既有授权内的修复、测试和实现应直接推进，避免反复请求“是否继续”；确实缺信息时，先查代码和已给资料。需要权限时说明准确原因，不能编造审批门槛。
7. 不触碰其他后端 worktree、stash、WIP refs、长期数据库卷、密钥或现有 worker 会话。后端现状以根 `AGENTS.md` 最新日志为准，本前端任务没有重新验收整体 P2。
8. 修改后更新根 `AGENTS.md` 的状态和进度日志；重要设计变更同步 PRD 与 ADR-005。测试失败、环境阻断、Mock 与真实未验收必须分开写。
9. 用户已授权本轮提交、PR、检查和合并；未来是否自动发布按新会话用户指令及仓库规则判断，不能从“接手”推断允许任意部署或消息发送。

## 2. 交付与运行状态

- 新 App 的真实入口为 `src/main.tsx` → `src/App.tsx` → `src/mobile/SafeBankApp.tsx`。
- 新业务页面都在 `src/mobile/`；`src/pages/`、`src/components/`、旧 providers/router/services/tests 保留作迁移参考，**不是当前 App 的页面入口**。
- 当前默认 `VITE_BANK_MODE=preview`，显著标识合成数据。不要删除这个标识。
- Preview 银行业务数据位于模块内存，刷新重置。主题和卡片用途保存在浏览器；它们刷新仍在。
- 已完成六场景前端页面、同站 BFF、统一预演/确认/OTP、风险测评、账单报告和用户迭代交互。**真实 L1→L2→L3 业务联调未由本任务闭合。**
- Node 使用 22，本轮本机为 v22.22.0；沿用 React 18、TypeScript、Vite 5，不增加运行依赖来实现动画或 BFF。
- 包管理以 **npm + package-lock.json** 为当前 CI 基准。pnpm 文件属于保留的队友资料；不要两套安装器交替重写锁文件。

## 3. 本地启动、构建与测试

从仓库根目录运行：

```bash
cd frontend
npm ci
npm run dev -- --host 127.0.0.1
# http://127.0.0.1:5173/#overview
```

Preview 不需要 BFF、真实模型或真实银行服务。首次检查端口是否已有进程，复用本项目服务；不要盲目杀别人的进程。

验证：

```bash
npm test            # 新 App + 保留的旧组件/页面/服务测试
npm run test:app    # 只测 src/mobile，不能冒充全量测试
npm run test:bff    # Node 内置 test runner，假上游 + 真实本地 HTTP
npm run build       # tsc --noEmit 后 Vite build
```

本轮在提交整理时的最新本地结果：**全量 384 passed；其中新 App 61 passed；BFF 10 passed**。新 App 61 是全量 384 的子集，不要相加成 445。完整验收历史见 `docs/ACCEPTANCE.md`。后续新增测试请以自己的新输出更新数字。

CI：`.github/workflows/frontend.yml` 在前端相关 push/PR 运行 Node 22 / npm ci / npm test / BFF / build；仓库原后端 checks 继续保留。

### Live 模式

1. 将 `.env.example` 复制为 `.env`，只在本机填写服务端配置，禁止提交。
2. `VITE_BANK_MODE=live`；配置 `FRONTEND_SERVICE_TOKEN`、`GATEWAY_URL`、`AGENT_URL`、`PUBLIC_ORIGIN`。
3. 在 `frontend/` 分别启动 `npm run bff` 与 `npm run dev -- --host 127.0.0.1`。
4. Vite `/api` 代理到 `127.0.0.1:3001`；浏览器不得直接访问 L1/L2/L3。
5. 修改 VITE 环境变量后重启 Vite；生产环境 VITE 值在构建时注入，不能靠修改服务进程环境把已构建的 Preview 包变为 Live。
6. 对外 HTTPS 需设 `COOKIE_SECURE=true`，`PUBLIC_ORIGIN` 与实际浏览器来源严格一致。`localhost` 和 `127.0.0.1` 是不同 Origin。

不要将 API key/service token 放入 `VITE_*`。默认只绑定 loopback，不得擅自改为公网监听。

## 4. 文件地图

| 文件/目录 | 职责 |
| --- | --- |
| `src/mobile/SafeBankApp.tsx` | 登录/bootstrap、hash 路由、导航/返回栈、页面公共 props、预演/确认入口、主题按钮 |
| `src/mobile/Banking.tsx` | 首页、转账/本人互转/定时/AA、收款人、账单/异常；共享 Panel/Field/Segments |
| `src/mobile/Services.tsx` | 银行卡叠卡/用途/申请/限制、订阅、理财/产品风险/持仓/问卷 |
| `src/mobile/Assistant.tsx` | 对话、结构化卡片、澄清、中断/恢复、停止接收；铺满内容区 |
| `src/mobile/Security.tsx` | 当前“我的”：姓名首字头像、用户名、订阅内嵌、安全记录分区 |
| `src/mobile/Approval.tsx` | L2 独立 PlanView、view_hash、停留时间、确认/拒绝、OTP、状态刷新 |
| `src/mobile/BillReport.tsx` | 月/年度报告，复用消费汇总和交易总计，缺月提示、txt 下载 |
| `src/mobile/api.ts` | same-origin BFF 客户端、读/提案/审批/SSE；Preview/Live 分流 |
| `src/mobile/preview.ts` | 独立合成银行 fixture 和演示计划状态机；不是后端实现 |
| `src/mobile/types.ts` | 当前前端 wire 类型，需与真实 contracts/响应核对 |
| `src/mobile/ui.tsx` | SVG 图标、Money、ChoiceSelect、MonthPicker、Modal portal 与焦点处理 |
| `src/mobile/app.css` | 竖版布局及逐轮修正，历史覆盖较多，改样式先查最终生效规则 |
| `src/mobile/card-purpose.ts` | 用途标签本地持久化，按客户与 card_id 隔离 |
| `src/app/ThemeProvider.tsx`、`themeTransition.*` | 主题持久化、右上角圆形柔边展开、减少动画/不支持时降级 |
| `server/bff.mjs`、`bff.test.mjs` | Node 22 原生 HTTP BFF 与边界测试 |
| `public/brand/xiaoan.svg` | 自绘小安“安芯”矢量资源；UI 线图版本在 ui.tsx |
| `docs/screenshots/` | 合成数据竖版、深色、交互和修正截图；不是实时状态 |
| `docs/ACCEPTANCE.md` | 按日期追加的实际结果/失败/未验收项，顶部为历史结果不是最新总数 |
| `docs/IMPLEMENTATION.md`、`docs/SOURCES.md` | 初始实施范围与来源；后续增量看验收和 ADR |

## 5. 当前导航与用户已确认的界面

| URL | 当前页面/规则 |
| --- | --- |
| `#overview` | 首页与账户/余额曲线 |
| `#transfer` | 即时、定时、AA、收款人；付款账户全部展开单选 |
| `#bills` | 账单、异常、消费分布、月度/年度报告 |
| `#cards` | 三张合成叠卡、用途、完整卡号/PIN、限额/渠道、申请新卡 |
| `#wealth` | 产品精选/风险匹配、持仓订单、风险测评 |
| `#assistant` | 整个内容区为聊天，新会话在聊天顶栏；消息区单独滚动 |
| `#security` | “我的”，默认订阅管理；标题已不叫安全中心 |
| `#security?tab=approvals` / `notifications` / `audit` | 我的 → 安全记录 → 对应子页 |
| `#subscriptions` | 旧订阅地址兼容渲染我的订阅，侧栏已删除独立订阅项 |

### 用户逐项定稿要求

- 二级页必须有返回键，使用 App 内路径栈，直接深链打开也能回首页；不要调用浏览器 back 把用户带出 App。
- 下拉统一 ChoiceSelect 底部面板；月份使用 MonthPicker。不引入风格突兀的原生 select。
- 风险测评完成后展示等级/有效期/更新入口；用户点击更新才出现空问卷。提交预演不等于测评已完成，依权威 profile/revision 更新。
- Money 符号和数字同行、baseline 对齐、不可断行；卡限额行用独立三列。
- 银行卡为可点击叠卡，不能恢复“当前卡”下拉。选卡后金额、渠道、PIN 查询等必须绑定该 card_id。
- 用途名称在卡面、卡下与叠卡保持一致；卡下名称旁放“修改”，**不要再增加独立的卡片用途面板**。现有编辑底部表单保留。
- 冻结/挂失属于卡状态操作；**交易限制/解锁入口在支付限额模块内部**，不能重新挤回三个并排按钮。
- 付款账户应把所有账户直接列出，区分未绑卡储蓄。用户说的“所有账号”指每张卡可选，**不是明文展示完整卡号**。
- 收款目标允许就地添加新收款人，添加后需执行确认并在权威列表看到新 ID 才选中；保留付款账户与已填金额。
- 消费分布末尾有“其他”；缺失时零值占位，不能随意从其他分类扣金额来凑数。
- 小安使用原创盾形+A核图标，保留统一用色；不要恢复旧通用星芒标识。
- 深/浅主题从右上切换按钮中心向外圆形扩散，800ms、柔化边缘；系统减少动态效果时直接切换。
- 助手外部标题/介绍已删除，不要再加回“小安，在这里”板块；聊天铺满公共导航之间的内容区，输入框固定，新会话位于对话内部。
- 我的顶部是 profile 姓名、首字头像、脱敏手机号；默认订阅，安全功能保留在安全记录；通知按钮仍直达通知列表。

## 6. Preview 数据和明确边界

### 合成数据

- 基础：张三、日常活期 6,000 元、未绑卡储蓄 20,000 元；追加出行备用 3,500 元、生活消费 1,280 元。
- 卡：0818、6612、3906，3906 初始冻结；每卡独立限额和支付渠道。
- 产品：8 款，R1–R5；初始风险等级 R2，因此 R1/R2 共5款可进一步了解，超等级禁用申购。
- 持仓：安心活钱 1000.0000 份（活期），稳健增利债券 800.0000 份（储蓄）。它们是已有合成持仓，不是本次自动申购。
- 增加7–9月合成收支。10月消费 3,554.80；2026年1–10月累计入账48,000、消费8,614.80。新增/取消/转账后可能变化，不能把这些常量硬编码到页面。
- 完整卡号验证 PIN 与红级 OTP 都使用 `123456`，但属于**两个不同的演示交互**，不要混成真实认证凭据。旧 legacy 登录 fixture 的张三 PIN 为 `111111`，新 App 不是这个旧登录流程。
- Preview 打开时自动建立合成会话，`/auth/login` 是说明性的预览响应；不要宣称 Preview 登录在做真实密码认证。

### 仅本地/仅 Preview

| 能力 | 目前边界 |
| --- | --- |
| 查看完整卡号 | 后端 CardView 仅 last4；完整号码与6位PIN校验只在 preview。5次错误暂停60秒，刷新可重置内存；不构成真实安全边界。Live明确不可用。 |
| 新卡申请 | `CARD_APPLY` 仅 preview 演示计划，不是冻结 contracts 的 ActionKind。经演示确认/OTP后生成零余额 CURRENT 账户及新卡。Live显示未接入，不调用真实开户。 |
| 用途标签 | localStorage，按客户和卡ID；不限制真实交易、不进入L1、不跨设备同步。 |
| 理财一键 | 是直达填写/预演入口，不是跳过确认成交。赎回预填账户/可用份额仍需用户核对；待到账不得当作可用余额。 |
| 订阅识别 | 页面调用 list_subscriptions / detect_subscriptions / upcoming_charges，Preview返回合成识别及闲置证据，不是实测全银行自动识别。 |
| 续费提醒 | 页面显示未来7天与下次扣费日期；短信/系统推送未验收，不要说已自动推送。 |
| 报告 | 组合现有读接口，全部账户，当前年度截止模拟月份；缺月明确部分数据。下载txt，未实现PDF。 |
| 助手 | Preview 为说明性回复，不调用真实模型；真实SSE只做适配，真实模型稳定性不由UI测试证明。 |

持久化：主题 `sb-theme`；用途 `safebank:card-purpose:v1:<customer_id>`。不要在 localStorage 保存 PIN、OTP、app/assistant JWT 或服务令牌。旧 legacy 还有 sb-demo-session、mock-* 等键，仅供旧实现/测试使用；不要误认当前 Live 凭据模型。

## 7. BFF 与安全边界

链路：浏览器 → 同站BFF → L2；助手原话先登记L2 → 取受限助手令牌 → L1规划 → L2预演/审批/执行 → L3。不能让L1绕过L2或拿浏览器授权能力。

- 金额必须整数分 `*_cents`；输入字符串解析复用 `src/services/transfer/money.ts`，不要 Number(value)*100 随意四舍五入。
- 份额最多四位小数，以字符串/BigInt比较，不能用浮点承载正式份额运算。
- 所有实际写操作经已有 FORM登记与 plans/evaluate；界面不能自报“trusted”、生成审批、把助手文字当成功证据。
- Approval只凭approval_id重新读取PlanView，view_hash绑定、最短确认停留、风险披露、OTP不可删。UNKNOWN/202是确认中，409应按实际新Decision处理，不得反复执行原写。
- 浏览器只持HttpOnly cookie；app JWT/assistant JWT/service token保留在BFF进程内。POST要求精确Origin与X-SafeBank-Client头。
- BFF只允许显式路由和读工具集合，线程归属检查、同线程并发拒绝、恢复时绑定原助手令牌与当前中断。不要为了调通而改成通配代理。
- BFF流式适配处理messages/values/custom/updates并过滤内部状态；当前后端可能继续变化，**重新读取最新 agent/main.py 和 scripts/try_agent.py**，不要根据旧 wire 文档臆测。
- 网关/助手地址、服务端token、Cookie Secure、CSP生产部署仍需端到端确认；BFF会话只存在单进程内存，重启会重新登录，不能宣称支持多实例持久会话。
- 真实接口不通时显示错误，不能自动退回Preview来伪装成功。
- LLM限定DeepSeek V4 Pro，多模态才DeepSeek V4.1 Flash；本轮UI任务没有请求模型。遇模型402/403/429按根规则停止，不换模型反复请求。

## 8. 过往的坑与排查顺序

1. **方向错**：最初曾偏成桌面工作台；用户多次明确App/竖版。先看手机内容边界，再改组件。
2. **配色错**：用户否决深绿新主题；继承tokens，不重新做品牌。
3. **位置错**：用户只要主项目frontend，不是队友GitHub/另一个clone/根目录临时demo。
4. **改错入口**：旧pages/router还在；改它们不一定影响新App。先确认src/App导出。
5. **CSS覆盖多**：app.css后面的375px规则会覆盖早期桌面/响应式规则。先rg全部同名规则，再检查computed style。
6. **原生select怪异**：统一底部选择面板，不能每个页面自己拼另一个风格。
7. **金额符号换行**：data-row small曾把¥变块级。Money组件已经分开币种/数字并inline-flex，不再手写类似结构。
8. **用途不匹配**：卡面是用途、卡下曾是账户名。现在同一purposeOf(card.id)，切卡/自定义必须同步。
9. **重复用途区**：用户要求删除独立模块，只留卡下修改按钮。
10. **三个操作乱并排**：限制/解锁已归入限额模块，不能恢复原布局。
11. **全局current假设**：现在多账户可各有current卡；补卡要按account_id找当前卡，不能find(c.current)当作唯一全局卡。
12. **全局空持仓假设**：现在有种子持仓；测试目标产品时按product_id查，不能断言所有holdings为空。
13. **追加收款人过早选中**：onPropose返回只是计划已评估，需执行后query刷新找到新增ID才算成功。
14. **测评过早完成**：不能问卷提交立刻显示已完成，需权威profile刷新。
15. **快照动画失败卡住主题**：实现了try/finally清理与fallback；如重构，保留减少动画/不支持API/失败路径。
16. **外层滚动挤走聊天输入**：助手workspace、page-content、chat-panel必须有min-height:0；只让chat-history滚动。桌面高度不足不能写固定540px。
17. **测试角色误用**：ChoiceSelect是button+dialog/listbox，不是原生combobox；Testing Library getByRole选项没有exact，字符串name默认精确。
18. **TS目标ES2020**：Array.at曾导致typecheck失败；不要只跑Vitest就说构建通过。
19. **旧路由测试超时**：旧登录键盘先渲染，客户目录异步300ms。demoLogin helper要等待并选择已加载张三再输入PIN，不能用加大超时时间掩盖竞态。
20. **BFF EPERM**：沙箱可能禁止127.0.0.1临时监听，10项测试会全报listen EPERM；在授权的本机环境复跑，不能改测试绕过HTTP。
21. **gh登录假失效**：沙箱内gh auth status曾报token无效，宿主机keyring验证实际有效；别立即要求用户重新登录，更不能打印token。
22. **主分支已远远前进**：本轮整理PR时本地比origin/main落后166个提交。先fetch/确认差异，保留远端后端与日志，解决冲突不能整份覆盖AGENTS/PRD。
23. **文档是追加历史**：ACCEPTANCE顶部20/27/31等是过去轮次，未删；当前数目看末尾和新测试输出。
24. **同一轮验证顺序**：曾在加测试前跑build、加测试后只跑Vitest，遗漏TS测试参数错误。本轮发布检查必须在最终文件状态重新跑全套。
25. **不能因UI完整就报真实联调完成**：真实开户/完整PAN/推送/全三层仍有边界。当前远端后端比早期记录更新，接手要重核不能复述旧“未实现”结论。

## 9. 测试和手动验收清单

自动：npm ci → npm test → npm run test:bff → npm run build。按更改范围运行具体测试；本轮已为前端加入CI。

手动至少：

- 375px与窄窗口，App无横向溢出；深浅色；右上角圆形主题过渡。
- 二级返回、直接深链、旧订阅链接、我的通知深链。
- 三卡切换后金额/状态/渠道一致；修改用途上下同步，取消不保存；查看卡号先PIN，关闭重开再验证。
- 冻结/挂失与限制入口位置；真实环境开户/PAN明确不可用，不出现假成功。
- 付款账户全列出；新收款人添加确认后选中并保留金额；无效输入不提交。
- 月报/年报范围和金额、缺月状态；其他分类无重复，零值不显示正条；txt下载。
- 风险评估、超等级不可申购、测评完成态与更新；赎回带正确账户和份额、超额拒绝、到账前不可用。
- 我的显示profile、订阅和提醒正常；取消授权仍进统一审批，不直接删数据。
- 助手内容区铺满、新会话在内部、忙时禁用、停止回复不承诺取消操作；UNKNOWN保持确认中。
- Modal嵌套只关闭顶层，键盘Tab/Escape、焦点恢复、自由文本不作为HTML执行。

截图索引：`docs/screenshots/assistant-full-workspace.png`、`theme-circular-reveal.png`、`my-profile-subscriptions.png`、`unlock-in-limits.png`、`cards-deck.png`、`card-purpose-inline.png`、`annual-report.png`、`monthly-report.png`、`wealth-enhanced.png`、`product-risk.png`、`quick-redeem.png`。截图均为合成数据，可能早于后续布局变化。

## 10. 接手后的推荐顺序

1. 读当前根AGENTS/PRD、本文与Prompt；核Git分支/远端和工作区，不复位或清理别人的工作。
2. 本地启动Preview，确认页面与截图/用户最新指示一致。
3. 跑全量和BFF/build，记录实际结果。
4. 与后端同事核对最新18读/14写、PlanView、SSE、会话/审批归属以及W5通知能力；先做读链路，再做受控确认写链路。
5. 真实新增卡申请、PAN/PIN、用途服务端存储、主动提醒需要先定接口/权限，不能只改前端伪造。
6. 若做真机，验证键盘遮挡、safe-area、iOS View Transitions降级、弹层焦点和屏幕高度；这些不能由桌面375px截图替代。
7. app.css适合逐步收敛，不建议一次性重写所有页面；任何重构先保持用户逐项确认的行为。

## 11. 发布记录与可验证入口

本轮PR：[SJeffZhang/Safebank #71](https://github.com/SJeffZhang/Safebank/pull/71)。源分支 `feat/frontend-mobile-app`，采用merge commit，目标main；不写队友仓库。

- 初始前端检查点：82d1f54。
- 本地原基线d1bb890落后远端166提交；已整合远端c1a8ce0，整合提交c3258cf。
- 文档冲突保留了双方日志；diff检查还清除了diff3基础分隔符残留。三个导入文件CRLF已统一LF，tokens原SHA256未变。
- 整合后重新验证：全量384 passed（新App61为子集）、BFF10 passed、TypeScript/Vite build通过。
- 发布说明及最终CI、merge commit、merged_at以PR71实时记录为准；不在合并前伪写合并结果。后续文档提交会改变PR head，必须核最终head的CI。

接手时可运行：

```bash
git status --short
git fetch origin main
git log -1 --oneline origin/main
gh pr view 71 --repo SJeffZhang/Safebank --json state,headRefOid,mergeCommit,mergedAt,url
gh pr checks 71 --repo SJeffZhang/Safebank
```

本轮归档证据：[docs/ACCEPTANCE.md](docs/ACCEPTANCE.md)，后续发布核对补记及PR讨论；不要从分支名或“已授权合并”推断远端已经合并。
