# ADR-005：前端选型（暂停，占位）

日期：2026-10-25。状态：暂停——用户 2026-10-23 决定前端/BFF 暂停（AGENTS.md §2 第 13 条），选型由用户恢复前端工作时再定。

## 背景

PRD §9.10 列出了前端页面清单（13 个页面），接口已锁定在 PRD §9。

## 已锁定约束

以下安全边界无论前端框架如何选型都必须遵守（PRD §9.10）：

1. 不预填输入框（金额、收款人不能由链接参数或 L1 暗示预填，按钮无可见文字预填）
2. 按钮文字只来自 L2（G22a、G21、G9）
3. 卡片/确认面板不经 L1 生成（UICard/CardAction 由 L2 的 PlanView 生成，前端直接渲染结构，不插入 LLM 回复）
4. `app_form` 来源不嵌入任何自由文本字段，只能填入用户直接交互产生的值

## 待决定

以下由 P2 前端负责人决定：

- 框架选型（Next.js App Router + Tailwind 或 React SPA 或其他）
- 状态管理方案
- BFF 实现语言（沿用 Python Fastify 或改用 Node.js）
- 部署方式

## 相关

- PRD §9.10 前端页面清单
- PRD §9.6 BFF 接口 F1–F13
- AGENTS.md §2 第 13 条

Co-Authored-By: DeepSeek V4 Pro via Claude Code <noreply@anthropic.com>