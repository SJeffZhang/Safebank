# BankSense 竖版 App

本目录是 BankSense 主项目的前端，继承队友原版 **Graphite & Champagne**（石墨 × 香槟金）视觉令牌。桌面展示为 375×812 手机视口，手机上铺满屏幕，页面内部滚动、底部导航常驻。没有桌面工作台。

代码直接在本主仓库 `frontend/` 中维护。没有嵌套 Git 仓库，也没有推送队友 GitHub。旧页面、组件和测试保留供迁移参考，新入口在 `src/mobile/`。不复制 FinMate 的图片、字体、品牌或压缩代码。

## 接手入口

完整交接：[HANDOFF.md](HANDOFF.md)；可直接复制给新 Agent 的约束和任务 Prompt：[NEXT_AGENT_PROMPT.md](NEXT_AGENT_PROMPT.md)。

## 启动预览

```sh
cd frontend
npm ci
npm run dev -- --host 127.0.0.1
# http://127.0.0.1:5173
```

默认 `VITE_BANK_MODE=preview`，在进程内使用合成数据。可体验转账/本人互转、定时、AA、收款人、账单/异常、卡限额/渠道/冻结/挂失、订阅、产品比较/测评/申购/赎回、统一确认面板与安全记录。演示状态刷新即重置。红级演示点击获取验证码后使用 `123456`。小安预览是说明性回复，不冒充真实模型规划。

## 连接真实后端

复制 `.env.example` 为 `.env`（已忽略），设置 `VITE_BANK_MODE=live` 及 **服务端** `FRONTEND_SERVICE_TOKEN`、`GATEWAY_URL`、`AGENT_URL`，然后重启 Vite。两个终端分别运行：

```sh
npm run bff  # localhost:3001
npm run dev -- --host 127.0.0.1  # localhost:5173，/api 代理至 BFF
```

浏览器只调用同站 `/api/*`，不接触任何服务令牌或 app/assistant JWT。BFF 使用 HttpOnly、SameSite=Strict cookie，POST 校验固定 Origin + 自定义头；登录、确认、OTP 直达 L2，助手消息登记后才调用 L1。服务端保留原次助手令牌并绑定中断审批，恢复时再核对 L2 计划归属与 L1 当前中断。

当前 L1 实际使用 `input.utterances`、`messages/values`，BFF 以 L2 登记后的原话构造请求，将 `messages` 转成 PRD `updates.reply`，过滤原始 `values`，并将中断事件置于最后。问卷和所有写表单经 G19→G4；App 不提交 steps 或来源自报。

真实模式不回退到演示数据。未实现/未配置接口会显示错误。**本次没有运行真实 L1→L2→L3 写操作或真实模型请求**，不能据此认定整体 P2 闭合。W5 的控制台/通知/审计等仍可能返回依赖不可用。

## 构建与检查

```sh
npm run build
npm run test:app      # 新 App 组件与契约
npm run test:bff      # 本地假上游，验证 BFF HTTP 边界
npm test             # 同时包含队友旧页面测试
```

生产预览：`npm run build` 后设 `PUBLIC_ORIGIN` 为实际站点地址，HTTPS 下设 `COOKIE_SECURE=true`，启动 BFF（可设 `PORT=3000`），由它提供 dist 静态文件及 /api。CSP 在 BFF 响应头设置。当前 BFF 会话存在内存中，重启需重新登录；多实例与持久会话未验收。当前本机启动只监听 127.0.0.1，不改变现有 Docker 网络或后端部署。

详细范围、失败与未覆盖项见 `docs/IMPLEMENTATION.md`。队友原 README 和旧 Pages 工作流保存在 `docs/`，不参与主仓库自动部署。

查看完整演示卡号需先输入6位PIN：`123456`（合成预览专用）。连续错误5次暂停1分钟，关闭后再次查看需重新输入；真实验密与完整卡号接口仍未实现。
