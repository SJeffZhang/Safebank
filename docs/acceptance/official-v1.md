# V1 官方要求核实

2026-10-23；来源为官网公开资源，不涉及登录页面。

- 官网：https://fintechathon.g-ican.com/
- 规则资源：https://fintechathon.g-ican.com/js/chunk-4516c2dd.7f79eeba.js
- SHA-256：`8d4956529b8a8b473c2d857dce04d63f4d65e9a0e71d6179bb5d1079f8c06202`；定位 `domesticPanels`、`domesticFlows` 的中文人工智能赛道分支。
- AI 赛题资源：https://fintechathon.g-ican.com/js/app.83e69cd7.js ，定位 `aiDomestic` 与 `银行 AI 智能体`。
- 评分：任务完成率40%、安全合规30%、创新与交互体验30%。
- 报名截至11-01，作品截至11-02；技术文档、答辩PPT（汇报≤10分钟）、源代码（README/部署/测试）、安全自评为必交；≤5分钟演示视频为可选。
- 官方页面题意要求至少覆盖3个核心场景，鼓励全部；团队六场景目标保持。
- 决赛页面列11-21至11-22现场黑客马拉松与答辩，具体到场流程仍以通知为准。
- 资源给出技术文档、安全自评等模板相对路径，但本轮未下载/审阅模板，不宣称已验证模板详细格式、上传大小、提交入口权限。

访问方式：GET官网200→解析script/link→读取22个公开JS资源。原下载临时保存在 /private/tmp/sbg-official，不依赖其长期存在；以上版本化URL与hash可重新获取核对。
