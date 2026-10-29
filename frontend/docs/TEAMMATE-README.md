# SafeBank UI

React + TypeScript + Vite 实现的 SafeBank 移动端界面（首页 / 理财 / 卡片 / 订阅 / 账单 / 小安 / 个人 / 转账）。

## 本地开发

```bash
pnpm install
pnpm dev       # http://localhost:5173
pnpm build
pnpm preview   # http://localhost:4173
pnpm test
```

## 欢迎页

首次未登录访问会显示已确认的玻璃叠卡欢迎页。素材为无商家品牌银行卡，字标、登录／注册按钮和高光按压交互由 Web Component 提供。组件与素材在 `public/safebank/`，React 接入位于 `src/pages/Welcome.tsx`。页面样式在 Shadow DOM 内隔离。

按钮由 React Router 接管，入口地址通过 `useHref` 生成，兼容 GitHub Pages 的 `/safebank-ui/` 子目录。原型目前共用演示客户登录入口，注册按钮会保留 `location.state.intent = 'register'`，尚未提供真实注册、验证码或开户服务。

浏览器已访问过应用时会跳过欢迎页，这是现有会话规则。要重新查看首次访问效果，可使用无痕窗口，或清除本站的 `sb-has-visited` 和演示会话后重新打开。

主体卡片、大标题和状态栏目前属于静态插画；字标、按钮及交互为独立元素。
