# GitHub Pages 合成演示

2026-10-29：用户明确授权新增 `.github/workflows/pages-preview.yml`，这是此次仅限 frontend 修改范围的特定例外。未修改其他根配置或后端。

工作流在 PR 上执行前端测试、BFF测试及静态构建，不部署；在 main 的相关文件更新或手动运行时，验证成功后上传 frontend/dist 并部署到 github-pages 环境。固定 VITE_BANK_MODE=preview，构建使用相对资源路径以适配项目子目录；App 使用现有 hash 路由。

这里只发布合成数据演示，不部署 BFF、真实银行接口或模型服务。不要配置银行令牌到 Pages 或 VITE 环境变量。

## 仓库所有者需完成的设置

1. 处理 GitHub Settings → Billing 的付款/Actions额度或支出上限阻塞。当前所有失败 job 都未启动，不能视为代码测试失败或通过。
2. Settings → Pages，确认当前账号套餐支持私有仓库 Pages，然后将 Source 设置为 GitHub Actions。保持仓库私有，无需为此公开源码。若使用环境保护规则，允许 main 部署并完成必要的环境审批。
3. 部署 PR 合并后，在 Actions → Pages preview 手动运行 main，或等待 main 推送触发。
4. 成功后使用 deploy job/environment 提供的实际 URL；部署前不将预期 URL 当作已上线地址。

当前 WsTwistzz 只有 WRITE、没有 admin/maintain 权限；Pages API 返回404，尚不能确认站点已启用或套餐符合要求。上述账号设置需所有者处理。

## 本地验证

在 frontend 使用 Node22：`VITE_BANK_MODE=preview npm run build -- --base ./`（PowerShell使用 `$env:VITE_BANK_MODE='preview'`）。构建包含TypeScript检查；本地预览 `npm run preview -- --host 127.0.0.1`。构建目录仅包含静态前端产物。

新增部署配置的远端执行结果以 PR/Actions 实时记录为准。工作流不能绕过 GitHub 账单、套餐或管理员权限限制。

本轮基于 origin/main 99031da，前端与 PR81 合并版本无差异。重新运行397项前端测试、10项BFF测试均通过，Preview相对路径构建（TypeScript+Vite）与diff检查通过。根AGENTS/PRD/进度站更新继续交协调者；没有修改这些只读文件。
