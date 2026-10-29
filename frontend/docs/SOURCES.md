# 来源与复用

- 用户指定的团队前端基线：[WsTwistzz/safebank-ui](https://github.com/WsTwistzz/safebank-ui)，读取时提交 `48378b249b67a3f5503830436656f5f59dbeb68d`。依用户明确授权，在本主项目 frontend/ 内重构；原 tokens、ThemeProvider、金额工具与旧组件/测试保留。未向队友仓库写入。
- 用户指定的交互参考：[SJeffZhang/finmate-preview](https://github.com/SJeffZhang/finmate-preview)，读取时提交 `174987c94757f995a45fe23290f62095a6d764e0`。其 README 明确为构建产物；未找到 LICENSE。未复制图片、字体、品牌、压缩 JS/CSS。整体外观继承团队前端。
- 业务与安全契约：本主项目 PRD §5/§6.5/§9、packages/contracts、services/gateway/app/schemas、services/gateway/app/approval/planview.py、services/agent/app/main.py、scripts/try_agent.py。
- 技术依赖沿用团队 React/TypeScript/Vite，版本由 package-lock.json 固定；BFF 只使用 Node 22 内置模块。Prettier 3.6.2 仅用于格式化，未作为运行依赖。
