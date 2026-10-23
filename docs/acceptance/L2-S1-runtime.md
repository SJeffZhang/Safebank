# L2 S1 共享运行层协调者验收

日期：2026-10-23。实施分支feat/l2-shared-runtime；最终业务代码e99406d，报告HEAD0a95872。协调者亲自执行，非采信worker自报。

## 最终退出命令

在S1独立worktree，Docker CLI PATH指向后台Docker Desktop：

`make lint && make test-gateway && make test-gateway-db && make test && make test-shared-runtime`

| 命令 | 实际结果 |
|---|---|
| lint | 通过，156 files formatted |
| test-gateway | 109 passed、6 skipped，3.49s；PG/Linux显式启用项此处跳过 |
| test-gateway-db | 17 passed，2.01s，真实PG、tmpfs |
| test | 228 passed、54 skipped，3.92s；不把集成跳过算通过 |
| test-shared-runtime | 51 passed、0 skipped，7.87s |

专项涵盖共享资源与撤销接线失败关闭、澄清/claim状态及真实主键、真实生产池18个并存PG连接、真实Uvicorn监听与请求清理、第二次SIGINT强制退出、七种Linux秘密文件场景、实际Compose healthcheck的UID/GID/能力位/HTTP。故意超时的合成请求打印CancelledError是该测试的预期日志。

## 已部署集成验收

修复轮1代码80fc3bf实际`make up`通过，保留既有数据集，六服务healthy；`make test-integration`通过真实网络/凭证/DB角色隔离、P1 HTTP→PG→prepare/transfer/outbox、L3进程重启恢复及幂等回放。两个持久卷safebank_pg-bank/pg-agent创建时间前后均为2026-10-23T12:26:36Z，未删重建。

最初复用.wheels符号链接导致Docker构建上下文COPY失败、make up exit2；仅移除临时链接并复制44个既有锁定wheel后重跑通过。秘密目录只复用原文件，未输出秘密；本地Git exclude补覆盖.secrets符号链接。最终N1修复仅关闭路径五行，以上部署集成结果不能冒写为最终代码重新部署；最终代码有完整专项与退出复跑。

## 独立评审与阶段限制

见`docs/reviews/L2-S1-runtime.md`。首轮四项修复后新增强制退出P2，再经第二轮修复和定点复审：最终PASS/APPROVE，全部问题关闭，评审定向回归1 passed。远端CI结果另记AGENTS §4，本文不预称通过。

本组件不包含W1身份/原话、W4审批/执行业务；生产缺W1撤销函数时客户路径拒绝，不用替身放行。真实登录→会话→撤销及W4锁实现联调必须待对应组件同步main后实际验收。前端和L1仍暂停，没有把健康检查当完整业务验收。
