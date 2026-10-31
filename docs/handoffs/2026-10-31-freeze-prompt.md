你接手 SafeBank 基础功能冻结任务，请直接继续实施，不要重新讨论架构或先做对比。

仓库：https://github.com/SJeffZhang/Safebank，GitHub账号SJeffZhang。
先fetch并完整阅读交接分支 docs/freeze-handoff-2026-10-31 的 docs/handoffs/2026-10-31-freeze-handoff.md，再读AGENTS.md、PRD.md。交接里有完整原计划、每步进度、两个未合PR、精确SHA、环境、证据和命令；以实际远端和本机状态核对，勿覆盖之后的新工作。

优先级：demo-v1六场景基础功能→demo-v1.1攻击→最后才D8/指标。main交接时74baf71；#96/#98/#99/#100/#101/#102/#103已验收合并。未冻结，没有两轮六场景或断网回放，不要提前tag。

立即先收尾#104：原checkout /Users/zhangshijie/Desktop/Project/Fintech2026，feat/form-authorization，最新已推7ceda71。表单G4/G8、永久绑定、真实OTP/恢复/污点/捕获epoch已实现、独审和干净部署live通过；完整CI第一次失败的迁移断言已修，第二次日志停W3b且交接核对时没有进程，必须重跑最终完整SHA的全部本机CI，取得PASSED后才合并。

然后#105：/Users/zhangshijie/.codex/worktrees/schedule-provenance/Fintech2026，fix/redemption-sources，4880516；红绿/独审/专项通过，但尚未live或最终完整CI。先rebase已合104的新main，再新数据集真实李四赎回安心货币1000份，验证预留、订单、时钟结算、最终份额/现金和账本。实查seed李四已有10000份，不要沿用“李四没持仓”的旧误推断。

接着09 AA：最新实际memo已null，仍UNKNOWN_SENSITIVE_ARG/REPLAN，plan pln_01M67TJ1JH6GGE9YZMHK6NH0TF，include_self=true缺来源最可疑但尚未验证；从SQL/trace写红测定位，不能泛化信任任意scope值。

最后完成两次独立reset的六场景live、43条真实清单、生产LLM录制/断网回放接线与make demo、冻结清单，全部门禁通过后才tag demo-v1。攻击和对比仍不抢跑。

实现本人完成，不用DS worker；资金/安全改动独立只读评审；修复旧fail新pass，最终head完整本机CI+干净栈live+SQL。不要删库/卷/worktree/WIP，不要并行部署两套栈；当前干净数据集已被转账和冻结修改。local_ci.sh必须传具体完整SHA，不能传字面HEAD。DeepSeek402/403/429立即停相关请求/整批，不重试不换模型。每单元更新AGENTS §3/§4、接口同步PRD，合并后公开进度。不要上传比赛平台。
