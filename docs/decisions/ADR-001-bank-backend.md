# ADR-001：L3 自研与共享纯函数

日期：2026-10-23；状态：采纳设计，尚未实现。

按 PRD §4.2/§8，自研精简 FastAPI/PostgreSQL 银行沙箱，以 packages/bankcore 共享预演与执行规则。
不 fork devbanksdk；前序源码调查记录在 PRD §0.4/§4，结论是缺认证、行锁、幂等与测试。本轮没有重新克隆验证。
TigerBeetle/Blnk/Fineract 仅按已登记语义逻辑移植，不部署其数据库/服务。
P0 只启动隔离的进程探针与独立 DB 角色；账户账本、业务事务与一致性测试是 P1 退出条件。

参考：https://github.com/ranjankumar-gh/devbanksdk 、https://github.com/tigerbeetle/tigerbeetle 、https://github.com/blnkfinance/blnk 。
