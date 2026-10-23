# agent（P0）

当前仅启动进程探针，公共实现位于 `scripts/health_app.py`，无业务接口。

启动：根目录 `make setup && make keys && make up`；端口 8100 只在容器网络内。
测试：`make test`；真实网络/凭证/数据库边界：`make test-integration`。
配置：Compose 显式映射本服务 `.secrets/` env 文件；不全局加载 .env。
银行业务、鉴权状态机和数据库迁移进入后续阶段，健康探针不代表业务完成。
