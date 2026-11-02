# BankSense 项目更名验证（2026-11-02）

用户确认项目对外名称统一为 BankSense。当前分支 `feat/banksense-brand` 基于 main `ac0884e`，同时保留前一工作单元的可见演示控制台入口。

- 更新所有当前 App 页面、浏览器标题、卡面、助手名称、演示短信、账单导出文件名、品牌字母 B、后端服务标题、全部 README 中的品牌提法以及 PRD/当前交接文档。
- App 文件与函数改为 `BankSenseApp`；npm 包显示名改为 `banksense-app`，同步锁文件。
- 内部 Python 包、数据库/卷/Compose 名称、服务鉴权协议头与浏览器存储键保留兼容值；GitHub 地址仍指向实际存在的仓库。
- 原始实测/评审证据及其 SHA、历史规格与进度事实保留。更名不重写旧实验，也不表明最新版现场验收通过。

验证：前端 494 passed，BFF 16 passed，默认 Python 4977 passed / 1169 conditional skipped。Docker 两镜像构建成功；整站 7 服务 healthy。HTTP 实际 HTML/JS 中标题、助手、演示短信均为 BankSense；旧品牌只保留内部协议头，页面展示无旧名称。真实登录、正式 get_accounts 返回两账户、退出成功，三个后端部署入口文件与当前源码相同。

未调用生产模型、执行资金操作或重置数据。未跑完整条件 PostgreSQL CI；本次无授权/资金规则变化。日志位于本机 `/private/tmp/banksense-{frontend-test,bff-test,python-test,build,up}.log`。代码已推送 `feat/banksense-brand`，草稿 [PR #116](https://github.com/SJeffZhang/Safebank/pull/116)。代码提交为 `8b412bbbe6f2e70e76abc613258342e6ccc0543d`；完整最终head CI与合并仍待，不把本记录当冻结完成。

## 可重复初始化接线

用户随后要求本地补充数据直接进入演示初始化。`DemoEnvironment.reset` 现在在新库 canonical `seed_demo` 之后执行 `app.seed_showcase`，再沿用原 `seed_consents` → 新空 gateway 库 `seed_grants`，不把本机补数脚本或运行库导出当初始化来源。原始 `l1_seed_v1.json` 保持不变。新profile在generation manifest中记为 `banksense_showcase_v1`。

- 六产品、四订阅与未来7日45元来自原正式种子；张三三卡/零余额备用账户为附加展示profile。
- 初始化验证新增三卡及账户归属/current_card_id、零余额、六产品和四ACTIVE订阅门禁。
- 已使用账户、已修改/冻结卡片和异主同ID冲突均拒绝覆盖；完整重复安装无新增行且不改版本，补写缺失指针也递增版本。
- 新初始化/原seed回归/生命周期共33项通过（含真实临时PG，未使用当前手测库）；生产loader和read投影确认月265元/年3180元、未来7天两笔45元。
- 独立只读复审发现pointer补写漏版本P2，真实PG先红（1未升2）后修复转绿，复审APPROVE。没有新授予运行角色管理权限或修改授权判定。
- 当前正在手测的本地库没有reset；这里未声称已完成两轮整站live/断网冻结验收。
