# 来源与复用

- 2026-10-29 用户指定当前电脑相邻的 `bank-agent-web` 前端作为交互参考。只读查看其 `src/app/theme.tsx` 的 Pressable、cardIn、CountUp，以及首页与卡片的使用方式；提炼按压回弹、错峰入场与约1.2秒数值递增节奏。在本项目使用CSS和独立requestAnimationFrame实现，不复制其源码、视觉tokens、资产或依赖。参考工程未发现项目级LICENSE，不据此推断通用复制许可。金额始终以整数分为输入，动态数字仅为展示，不参与金融计算或授权。

- 用户指定的团队前端基线：[WsTwistzz/safebank-ui](https://github.com/WsTwistzz/safebank-ui)，读取时提交 `48378b249b67a3f5503830436656f5f59dbeb68d`。依用户明确授权，在本主项目 frontend/ 内重构；原 tokens、ThemeProvider、金额工具与旧组件/测试保留。未向队友仓库写入。
- 用户指定的交互参考：[SJeffZhang/finmate-preview](https://github.com/SJeffZhang/finmate-preview)，读取时提交 `174987c94757f995a45fe23290f62095a6d764e0`。其 README 明确为构建产物；未找到 LICENSE。未复制图片、字体、品牌、压缩 JS/CSS。整体外观继承团队前端。
- 业务与安全契约：本主项目 PRD §5/§6.5/§9、packages/contracts、services/gateway/app/schemas、services/gateway/app/approval/planview.py、services/agent/app/main.py、scripts/try_agent.py。
- 技术依赖沿用团队 React/TypeScript/Vite，版本由 package-lock.json 固定；BFF 只使用 Node 22 内置模块。Prettier 3.6.2 仅用于格式化，未作为运行依赖。
