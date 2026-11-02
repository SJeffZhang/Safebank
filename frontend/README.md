# BankSense App 与 BFF

移动端优先的银行 Web App，采用石墨与香槟金配色。桌面以 375×812 手机视口展示，手机端铺满屏幕，页面内部滚动，底部导航常驻。当前产品入口为 `src/mobile/BankSenseApp.tsx`。

App 提供转账、账单、卡片、订阅、理财和跨场景助手入口；所有写操作使用统一的预演与确认面板。截图见[仓库首页](../README.md)。

## 独立预览

需要 Node.js 22：

```bash
cd frontend
npm ci
npm run dev -- --host 127.0.0.1
```

打开 [http://127.0.0.1:5173](http://127.0.0.1:5173)。默认 `VITE_BANK_MODE=preview`，只使用内存合成数据，刷新恢复初始状态。预览 PIN 与验证码为 `123456`；小安使用说明性回复，不发起真实模型请求。

## 完整服务模式

推荐在根目录按[部署指南](../docs/deployment.md)启动 Docker 演示栈。前端镜像以 `VITE_BANK_MODE=live` 构建，Node BFF 同时提供静态文件和 `/api/*`。

浏览器只访问 BFF。BFF 在服务端管理 app / assistant JWT，使用 HttpOnly、SameSite cookie，并对写请求检查 Origin 与自定义头。原话登记、表单、审批和 OTP 直接连接 L2，助手过程连接 L1。真实模式遇到接口错误不会回退到合成预览。

## 本地 BFF 联调

已有可达上游服务时，将 `.env.example` 复制为 `.env`，填写配置并开两个终端：

```bash
npm run bff
```

```bash
npm run dev -- --host 127.0.0.1
```

Vite 将 `/api` 代理到 `127.0.0.1:3001`。基础 Compose 不发布 L1/L2 的宿主端口，因此本地 BFF 必须另有受控端口转发或在相同 Docker 网络中运行。

| 配置 | 用途 |
|---|---|
| `VITE_BANK_MODE` | `preview` 或 `live`，属于构建时配置 |
| `GATEWAY_URL` / `AGENT_URL` | BFF 视角可达的上游地址 |
| `FRONTEND_SERVICE_TOKEN` | 仅服务端使用的 F 身份令牌 |
| `PUBLIC_ORIGIN` | 浏览器访问的完整 Origin，必须精确匹配 |
| `PORT` | BFF 监听端口，本地默认配置为 3001 |
| `COOKIE_SECURE` | HTTPS 部署设为 `true` |

不要把密钥和服务令牌放进 `VITE_*` 变量。当前 BFF 会话保存在内存中，重启后重新登录，多实例与持久会话未验收。

## 构建与测试

```bash
npm test
npm run test:app
npm run test:bff
npm run build
```

2026-11-02 本轮回归：前端 494 项、BFF 16 项通过，TypeScript 与构建通过。BFF 套件使用本地测试上游，真实业务结果另见[验证说明](../docs/validation.md)。

完整卡号与 PIN 查看流程目前只在合成预览提供；live 模式没有完整 PAN 接口，不将该演示交互当作真实银行鉴权。开户、真实短信与真实支付不属于当前后端接入范围。

历史前端验收与详细边界保存在 [`docs/`](docs/)；维护交接见 [HANDOFF.md](HANDOFF.md)。
