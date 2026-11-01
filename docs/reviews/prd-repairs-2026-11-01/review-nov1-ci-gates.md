# November 1 本机CI执行清单只读核验

结论：**没有发现测试目标或本轮PG flags遗漏**。当前脚本覆盖unit、应触发的frontend及checks.yml全部27个Make目标；S1/clock/notifications虽不在首个固定循环，确实由第二个循环追加执行。root随后已补齐完整40位SHA与双工作树clean收据检查；frontend diff失败处理也已补齐；剩余是明确Compose调用环境与记录工具版本的配置边界，不是放宽或跳过测试。

## 核验对象与方法

- 只读root当前 `.github/workflows/checks.yml`、`frontend.yml`、`Makefile`及 `/private/tmp/sbg-local-ci-jet.sh`，观察root HEAD `8d1cf67e1719bcff58fa84bed062c752bd5b5915`。这不是宣称该head已CI通过。
- 最终只读复核脚本SHA256：`ea34d079200889de14516cd389c69f8ee9b09e8d1543855f9bf6263b0254818c`；首轮旧脚本为`038ab15db392e5623693d3e6f0168ca00cd4a0fd086f970032939c85eaf1b219`，下述SHA门禁问题已闭合。
- 使用纯文本解析比较目标并检查新测试fixture/pytest mark；不调用CI runner、pytest、模型、浏览器、网络服务、DB或Docker，不修改脚本/工作树，也未读取.env或任何密钥文件。

## 工作流与执行目标

| 工作流阶段 | 脚本实际执行 | 结论 |
|---|---|---|
| unit | unit-sync：uv sync --frozen --group poc --python3.12；unit-pytest：全默认pytest；ruff check及format --check | 全部存在，与工作流命令对应 |
| frontend | npm ci、npm test、npm run test:bff、npm run build | 四步存在；当前origin/main...HEAD能正确求diff且含5个frontend文件，此head会执行 |
| p1固定循环 | setup、test-postgres-bootstrap、up、test-gateway-db、test-customer-locks、test-wave1-state、test-shared-runtime、test-agent-db、test-w1、test-obligations、test-w2、test-w3a、test-w3b、test-w4b1、test-w4b2b、test-w4b2c、test-w5-sms、test-w4c、test-e0b、test-p1、test-p1-concurrency、test-p1-consistency、test-integration、contracts-check | 24个目标，逐个检查Makefile定义并用step处理失败 |
| p1追加循环 | 从checkout后的checks.yml提取make test-*并减去固定集合，当前剩test-s1、test-w5-clock、test-w5-notify | 三个确实会运行；总共27，workflow集合减执行集合为空 |
| 原P1 DB/构建 | test-p1依赖test-p1-build+test-p1-db；build含镜像构建/两次migration；另跑concurrency/consistency与HTTP/network | 依赖路径存在，没有因test-p1自身无recipe而漏掉 |

追加三项运行顺序在固定目标/contracts-check之后，和云工作流位置不同，但没有丢门禁；它们使用独立临时PG fixture，不据顺序差异判作产品错误。云工作流失败日志与always compose down是收尾动作；本机runner保留单safebank栈，本报告不把未down当缺测试。

## 本轮新测试能否进入真实PG阶段

| 新单元 | 包含它的真实执行目标 | flag/selection核对 |
|---|---|---|
| T5 hints G4→G22a→G3→G4/G9与篡改/单次 | test-w3b → tests/gateway/w3b（含db/test_nov1_hint_publication.py） | GATEWAY_DB_TESTS=1；引用gateway_db fixture要求的正是此flag |
| legacy未绑定hint拒绝、parser/G19/G3/JWT | test-w1 → tests/gateway/w1（含db/test_utterances.py和test_nov1_query_cancel.py） | GATEWAY_DB_TESTS=1 |
| grant COMMITTED归档/回滚/orphan | test-wave1-state → tests/gateway/wave1_state/test_nov1_grant_revocation.py | GATEWAY_DB_TESTS=1 |
| S1 revoked拒扣与backfill真实冲突 | 追加test-s1 → tests/gateway/s1/test_nov1_revocation_pg.py | GATEWAY_DB_TESTS=1；非仅unit边界文件 |
| 安全SSE真实PG checkpoint | test-agent-db → tests/agent -m db | AGENT_DB_TESTS=1；test_nov1_sse_errors.py显式pytestmark=db，2个参数实例不会因-m漏掉 |
| 日期投影trade_date、凌晨/正午R13/R18、午夜NAV | test-obligations → tests/obligations_data/test_nov1_trade_date_pg.py | GATEWAY_DB_TESTS=1、OBLIGATIONS_DB_TESTS=1；obligations_db fixture要求后者 |
| W5通知/clock | 追加test-w5-notify、test-w5-clock，另test-w5-sms固定循环 | 每项GATEWAY_DB_TESTS=1，目录分别w5/w5_clock/w5_sms |
| 共享真实runtime/secret隔离 | test-shared-runtime | GATEWAY_DB_TESTS=1、GATEWAY_SECRET_TESTS=1 |
| 历史MCC、merchant loader、定向query/负环比、REPLAN/shape、安全反馈、目标/日期unit | unit-pytest默认全tests；相关PG目录又经上述定向目标 | 不依赖-m db的pure新文件由unit完整收集；没有只列旧白名单而漏新文件 |
| FE澄清协议/双月卡/typed singleflight | frontend npm test；BFF与build另步 | 当前路径条件成立，不仅执行Python门禁 |

unit的PG条件skip不能当PG通过；但这些文件随后有真实flags目标，静态清单未见永久漏跑。这里仅证明“被选中且flag对上”，不承诺fixture运行时健康或最终passed数量。

## 确实缺少的约束 / 配置风险

1. **最终40位SHA与双工作树收据：已闭合。** root修改后独立只读复核：新:10在任何CI/checkout之前强制`^[0-9a-f]{40}$`；:16/17保留PR WT HEAD/clean；:31在deploy切换前确认clean，:33/34切换后确认HEAD逐字符等于完整参数且clean；:43–46结束前再次逐个核WT/DEPLOY的完整HEAD和clean，成功语句只在其后。此前允许短ref和deploy残留的缺口已关闭。当前deploy只读状态干净；实际运行会传root明确的最终完整SHA。root另报告bash -n和非法ref输入拒绝已验证，本审查只读代码确认，未执行runner。

2. **Compose单项目依赖外部环境。** DEPLOY硬编码路径`/Users/jet/.codex/worktrees/safebank-freeze-ci/WeBank`存在，当前为clean detached aa7；script会checkout指定SHA。该目录和root的docker-compose.yml静态`name: safebank`及172.29网络一致，未发现适配错路径。脚本没有约束COMPOSE_PROJECT_NAME/COMPOSE_FILE/COMPOSE_PROFILES等环境覆盖；本审查按限制未读.env或运行compose，不能保证外部环境未把命令导向其他项目/文件。实际调用需root确认固定此部署WT及safebank，不得用另一个持久栈的环境包装运行。

3. **frontend diff失败静默skip：已闭合。** 最新脚本先执行`changed_paths=$(git diff --name-only origin/main...HEAD) || { ...; exit 1; }`，成功后再把已取得的paths交grep匹配。assignment保留git diff退出状态，因此缺origin/main或无法求merge-base时直接失败，不能落“无frontend变化”分支。当前实际diff有效且含5个frontend路径，FE四步会运行；路径正则已实测能匹配frontend和frontend.yml。此修复不改变以origin/main为比较基准的既有条件，因此实际最终运行仍应在root当前未合并分支执行，不把已被origin/main包含后的空diff当这轮FE验收。root报告bash -n通过；本次仅只读核行，未执行runner。

4. **工具版本未由runner钉住。** workflow用Node22及uv0.12.23；本机脚本调用PATH中的npm/uv，没有版本检查。不是漏测试，但不能仅凭相同命令宣称运行环境完全等价；root应把实际Node/uv/Python版本写入最终收据。

## P1旧库保留的实际前置条件

Makefile test-p1-build显式调用`scripts.p1_database --fresh-test`，该模块会执行`DROP DATABASE IF EXISTS bank_p1_test WITH (FORCE)`后重建。脚本不包含旧bank_p1_test的保留步骤。root已明确计划先无损改名保留；必须在跑test-p1之前完成并记录，不能把旧库仍叫bank_p1_test时直接运行runner。本次未访问任何DB或验证实际改名结果。

## 交付边界

不建议删除任何PG目标或为了绿测改变flags。root最终实际40位head、测试退出状态、PG计数、版本/Compose项目与旧库改名证据仍由串行执行产生；本报告只是清单和选择条件审核，不是CI PASSED证明。

## 最后复核结论

最新私有脚本补丁只增加SHA/clean检查，27个Make目标与所有PG flags选择范围保持。最终40位提交和双WT一致性门禁已经具备；未执行任何CI、服务或DB。frontend diff失败门禁也已闭合。root明确实际调用会固定COMPOSE_PROJECT_NAME=safebank，并把COMPOSE_FILE指向部署WT的docker-compose.yml；旧bank_p1_test先无损rename。这是实际执行前置约定，非本次运行证明。未见测试清单/PG选择遗漏；剩余需最终收据记录实际环境和工具版本。

最新快速复核：脚本SHA256 `ea34d079200889de14516cd389c69f8ee9b09e8d1543855f9bf6263b0254818c` 与root提供值一致；只改frontend diff取值的失败处理，不改变测试目标、PG flags、完整SHA或clean门禁。旧中间版哈希c224db04e9ed83fa9d76433e861747e47cc6d00a9405911bb3c8f733bc7dce2b已被此版取代。
