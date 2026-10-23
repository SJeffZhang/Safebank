# S1c PIN启动配置接线（准备稿）

问题：W1检查点42c86f5已采用集中runtime，仍存在runtime._configured_pin_hashes()零参调用configured_hashes()读取进程环境，忽略显式Settings.demo_pins_file。普通from_env启动两者一致，但构造Settings或测试/宿主传入不同文件时读取错误。不能靠请求期fallback掩盖。此项在完整W1验收前关闭；共享实施在S1b后串行。

保留既有configured_hashes()签名。原W1 owner在自己分支新增configured_hashes_from_file(pins_file: str)->dict[str,str]，精确读该文件并哈希/验证，调用期不从DEMO_PINS_FILE或DEMO_PIN_HASHES覆盖指定path；既有零参兼容入口如保留应复用校验，不复制算法。测试两个不同PIN文件/不同环境时显式path优先、缺失/非法拒绝、资源不在请求期重读、不同路径缓存不串用。仅W1 auth/pin及其测试/报告，由W1 owner实施，不越界runtime。

随后共享owner在独立feat/l2-pin-settings（最新main）只改runtime PIN装配，传settings.demo_pins_file给上述新函数，并更新gateway README及shared-runtime相关测试。W1模块缺失仍未配置，不提供假成功；模块存在但新loader不存在或坏配置必须failclosed，不能退回旧环境来源。不得改授权矩阵、JWT schema或其他资源生命周期。

专用测试验证传入的正是当前Settings路径、两个Settings彼此独立且无旧全局缓存污染、缺失/非法配置启动失败且资源正确释放、显式pin_hashes注入保留已定义行为。若main尚无W1模块，共享单测的受控loader替身只证明接线；必须随后在W1分支同步共享组件，真实文件+真实runtime+真实PG登录验证两个路径不同且环境相反，才能宣称完整集成。

共享退出make lint/test/test-gateway/test-gateway-db/test-shared-runtime；W1退出原五条并增加真实runtime路径回归。协调者亲自复跑、分别独立只读评审、各组件单独PR且当前head CI全绿merge。禁止up/down/test-p1并行、禁止删除重建数据卷、禁止前端、只本地提交不push。函数新签名已由协调者固定，此任务不改变既有交叉签名。
