# X2 同会话上下文失败：只读诊断

核验日期：2026-11-01 UTC。核对源码：`origin/main=22a87799c2b422a6c2186490a5972e11bc05dce4`。本报告针对本轮一份真实浏览器计划，不推断所有 X2 请求均失败，也不将其表述为 X2 未实现。

**结论：本次失败由 G4 把同一会话上一条仍有效的 NO_WRITE 账单原话装入授权根集合引起。L1 的 X2 计划已声明正确的当前原话、账户读取、派生金额和周期/日期来源；L2 parser 也已完整支持并解析出该 X2 作用域。** 授权证据装载器随后要求“根集合 = SCOPED 集合 = 当前计划声明集合”，发现前一条 NO_WRITE 原话混入后返回空证据；两个原话来源参数因此被标为 UNKNOWN，意图阶段也因空证据直接拒绝。

本次没有新发起 DeepSeek 请求、确认审批、执行银行操作或修改数据库。SQL 连接均强制 `default_transaction_read_only=on`。另在部署容器内调用了只读授权装载函数和纯日历来源匹配函数，没有调用任何银行 HTTP 入口或 pipeline 执行入口。只写入本报告，不修改共享 AGENTS/PRD 或业务代码。

## 1. 本次运行与原话时间线

| 项目 | 实际值 |
|---|---|
| 代际 | `demo_20261101050549_93b880b1` |
| gateway DB | `gateway_demo_20261101050549_93b880b1` |
| bank DB | `bank_demo_20261101050549_93b880b1` |
| PostgreSQL 容器 | `safebank-e2e-1029-postgres-bank-1` |
| 客户 | `cus_zs` |
| 会话 | `ses_01M6AR66KG4FKD3937RNTSB15S` |
| 失败计划 | `pln_01M6ARFJ8EK4597QWNR1V2SEYK` |
| 计划 origin / status | `agent` / `REPLAN` |
| 评估时间 | `2026-11-01T05:14:26.446361+00:00` |
| 评估 | `evl_01M6ARFJAZQ0WD4H02ZRXB3AAY`，revision 1 |

同一会话在评估时存在以下两条 ACTIVE、未过期的 typed 原话。不是另一个会话的记录。

| 原话 | ID | parse_status | 创建时间 UTC | 过期时间 UTC |
|---|---|---|---|---|
| 给我出一份9月的账单报告 | `utt_01M6AR67783KHD9R6B7M5Z4Z14` | `NO_WRITE` | 05:09:20.211314 | 05:19:20.211314 |
| 找出没用的订阅取消掉，省下来的钱每月1号自动转到我的储蓄账户 | `utt_01M6ARF4E9SRKAA9KJQKGQ978P` | `SCOPED` | 05:14:12.266117 | 05:24:12.266117 |

两条原话的 `parent_ids=[]`、`source_id=null`、`authorization_epoch=0`。会话 epoch 同为 0、未撤销。计划的 `utterance_ids` 和持久化 `scope_ids` **都仅声明 X2 当前原话**，没有把账单原话当作授权来源。

## 2. L2 已保存的 X2 normalized_scope

```json
{
  "action_kinds": ["MANDATE_CANCEL", "SCHEDULE_CREATE"],
  "from_account_id": "acc_zs_chk",
  "transfers": [
    {
      "from_account_id": "acc_zs_chk",
      "target_fingerprint": "int:acc_zs_sav",
      "amount_cents": 0
    }
  ],
  "period": "MONTHLY",
  "channel": null,
  "fields": {
    "predicates": {"idle_subscriptions": ["si_zs_gym"]},
    "instruction_ids": ["si_zs_gym"],
    "derived_template": "cancelled_monthly_fees",
    "first_run_at": "2026-11-01T08:00:00+08:00",
    "to_account_id": "acc_zs_sav"
  }
}
```

`amount_cents=0` 是该派生模板作用域的占位，不是漏解析 199 元。意图阶段会在金额来源已由 provenance 确认为 `derived` 时取实际派生金额，代码见 [intent.py](/Users/jet/.codex/worktrees/safebank-freeze-ci/WeBank/services/gateway/app/pipeline/intent.py:85)。本次在这一步之前因空授权证据返回。

## 3. L1 实际计划与读取来源

计划只有两个步骤，依赖顺序正确：

```json
[
  {
    "step_id": "cancel_gym",
    "tool": "cancel_subscription",
    "args": {"instruction_id": "si_zs_gym"},
    "depends_on": [],
    "arg_sources": [
      {
        "param": "instruction_id",
        "source": {
          "kind": "read",
          "ref": "src_01M6ARF8WFSQDH7RXA9THPQQ1N",
          "pointer": "/items/1/instruction_id"
        }
      }
    ]
  },
  {
    "step_id": "schedule_transfer",
    "tool": "schedule_transfer",
    "args": {
      "from_account_id": "acc_zs_chk",
      "payee_id": null,
      "to_account_id": "acc_zs_sav",
      "amount_cents": 19900,
      "period": "MONTHLY",
      "first_run_at": "2026-11-01T08:00:00+08:00",
      "end_at": null
    },
    "depends_on": ["cancel_gym"],
    "arg_sources": [
      {
        "param": "from_account_id",
        "source": {"kind":"read","ref":"src_01M6ARF91WX6NREHMWJR2VV4EG","pointer":"/accounts/1/id"}
      },
      {
        "param": "to_account_id",
        "source": {"kind":"read","ref":"src_01M6ARF91WX6NREHMWJR2VV4EG","pointer":"/accounts/0/id"}
      },
      {
        "param": "amount_cents",
        "source": {
          "kind":"derived",
          "template":"cancelled_monthly_fees",
          "inputs":[
            {"kind":"step","ref":"cancel_gym"},
            {"kind":"read","ref":"src_01M6ARF8WFSQDH7RXA9THPQQ1N","pointer":"/items/1/typical_amount_cents"}
          ]
        }
      },
      {
        "param": "period",
        "source": {"kind":"utterance","ref":"utt_01M6ARF4E9SRKAA9KJQKGQ978P","span":[16,18]}
      },
      {
        "param": "first_run_at",
        "source": {"kind":"utterance","ref":"utt_01M6ARF4E9SRKAA9KJQKGQ978P","span":[18,20]}
      }
    ]
  }
]
```

实际同会话 `read_records` 核对：

- `src_01M6ARF8WFSQDH7RXA9THPQQ1N` 是 `detect_subscriptions({"lookback_days":180})`；`items[1]` 为 `si_zs_gym`、`is_idle=true`、`period=MONTHLY`、`typical_amount_cents=19900`。金额和 instruction_id 字段不在 untrusted 路径内；untrusted 仅四个商户名称。
- `src_01M6ARF91WX6NREHMWJR2VV4EG` 是 `get_accounts({"history_days":30})`；`accounts[0].id=acc_zs_sav`、`accounts[1].id=acc_zs_chk`。L1 的两个账户指针与实际响应完全相符。untrusted 为 display_name，不含 id。
- 两份读取均属于上述 `ses_...B15S`，`versions={"cus_zs":3}`，`observed_at=2026-10-15T01:00:00+00:00`；实际 evaluation versions 同为 3。
- 原文 `[16,18)` 恰好为“每月”，`[18,20)` 恰好为“1号”。不是日期/周期来源遗漏或索引偏移。

部署中的纯 `_schedule_calendar_value` 以 provenance 实际使用的 JSON 字符串值验证这两个 span，结果均 `true`。此控制只证明既有日历来源规则支持该声明，不授予执行权限。

## 4. 根因调用链与只读控制

1. G4 A 路径调用 `_load_authorization(read_conn, sid, customer_id, now)`，**未传当前计划原话 ID 作为过滤条件**，见 [plans.py](/Users/jet/.codex/worktrees/safebank-freeze-ci/WeBank/services/gateway/app/api/plans.py:323)。
2. loader 查询该 sid 所有 `ACTIVE` 且未过期的原话；所有记录进入 `utterance_ids`，只有 SCOPED 记录进入 `scope_ids`，见 [plans.py](/Users/jet/.codex/worktrees/safebank-freeze-ci/WeBank/services/gateway/app/api/plans.py:88) 和 [plans.py](/Users/jet/.codex/worktrees/safebank-freeze-ci/WeBank/services/gateway/app/api/plans.py:108)。
3. `authorization_evidence` 要求根 ID 集合等于 scope ID 集合、等于当前 `plan_draft.utterance_ids`，且每根都是 SCOPED；否则返回空 evidence，见 [authorization.py](/Users/jet/.codex/worktrees/safebank-freeze-ci/WeBank/services/gateway/app/taint/authorization.py:108)。
4. 本次从同会话加载出 `utterance_ids=[账单,X2]`，`scope_ids=[X2]`，draft 也是 `[X2]`，所以在该集合检查即返回空。
5. provenance 的 `period` 与 `first_run_at` 都声明 `kind=utterance`；它只能从 `evidence.utterances` 取可信原话。空证据导致这两个参数保留 `trust=unknown`，见 [provenance.py](/Users/jet/.codex/worktrees/safebank-freeze-ci/WeBank/services/gateway/app/pipeline/provenance.py:209) 和 [provenance.py](/Users/jet/.codex/worktrees/safebank-freeze-ci/WeBank/services/gateway/app/pipeline/provenance.py:304)。读取来源和派生来源不依赖该字典，因此本次恰好出现两条 UNKNOWN。
6. intent 阶段再次取得空 evidence，直接增加 `INTENT_SCOPE_EXCEEDED` 并返回，见 [intent.py](/Users/jet/.codex/worktrees/safebank-freeze-ci/WeBank/services/gateway/app/pipeline/intent.py:46)。

为排除仅凭代码猜测，在**部署中的实际函数**上做了时间固定的只读诊断。数据库连接返回 `SHOW transaction_read_only=on`；`wall_now` 固定为原 evaluation 时间；从持久化计划构造相同 draft 和同客户/会话/原话集合的最小 synthetic caller（未读取/生成 JWT）。调用原 loader 和只读 evidence 函数：

```json
{
  "transaction_read_only": "on",
  "evaluation_time": "2026-11-01T05:14:26.446361+00:00",
  "loader_utterance_ids": [
    "utt_01M6AR67783KHD9R6B7M5Z4Z14",
    "utt_01M6ARF4E9SRKAA9KJQKGQ978P"
  ],
  "loader_scope_ids": ["utt_01M6ARF4E9SRKAA9KJQKGQ978P"],
  "draft_utterance_ids": ["utt_01M6ARF4E9SRKAA9KJQKGQ978P"],
  "original_evidence_utterance_count": 0,
  "original_evidence_source_count": 0,
  "controlled_requested_roots_evidence_count": 1
}
```

控制组**仅在内存副本**把 auth 的根、scope、refs、statuses 限为 draft 声明的 X2 ID，再调用同一只读 evidence 函数，即得到一条有效原话证据。未保存该副本、未修改 loader、未重评估/确认/执行真实计划，不能将这个控制组记为 X2 已修复或业务成功。

诊断过程的两个非业务失败也保留说明：首次尝试用了 asyncpg 的 `server_settings` 连接选项，但部署使用 psycopg，因此在连接配置校验处失败；改用 psycopg `options='-c default_transaction_read_only=on'` 后完成。首次纯 matcher 检查直接传 Pydantic datetime 得到 false；实际 pipeline 使用 `model_dump(mode='json')` 后的字符串，按真实表示重查时 period/first_run_at 均 true。两者均不是本次浏览器失败根因。

## 5. 审计与无执行 SQL

持久化 audit 只有该计划的 `EVALUATION_COMPLETED`：

```json
{
  "id": "evt_01M6ARFJB326G2ZNB5CMMB3N5Y",
  "seq": 25,
  "event_type": "EVALUATION_COMPLETED",
  "plan_id": "pln_01M6ARFJ8EK4597QWNR1V2SEYK",
  "created_at_wall": "2026-11-01T05:14:26.446361+00:00",
  "payload": {
    "decision": "REPLAN",
    "risk": "YELLOW",
    "approval_id": null,
    "revision": 1,
    "evaluation_id": "evl_01M6ARFJAZQ0WD4H02ZRXB3AAY",
    "findings": [
      {"code":"UNKNOWN_SENSITIVE_ARG","effect":"REPLAN","step_id":"schedule_transfer","resolver":"user"},
      {"code":"UNKNOWN_SENSITIVE_ARG","effect":"REPLAN","step_id":"schedule_transfer","resolver":"user"},
      {"code":"INTENT_SCOPE_EXCEEDED","effect":"REPLAN","step_id":null,"resolver":"user"}
    ]
  }
}
```

只读查询的真实状态：

| 数据库事实 | 值 |
|---|---|
| 该 plan 的 gateway approvals | 0 |
| 该 plan 的 gateway execution_jobs | 0 |
| 该 plan 的 L3 plan_executions | 0 |
| 该 plan 的 L3 transfers | 0 |
| 该 plan 的 L3 operation_events | 0 |
| `standing_instructions.source_plan_id=该plan` | 0 |
| `si_zs_gym.status` | `ACTIVE` |
| 张三活期已过账余额 | 590000 分 |
| 张三储蓄已过账余额 | 2000000 分 |
| 两账户 pending debit / credit | 均为 0 |

590000 分包含本轮此前真实100元转账的影响；不应当把它归因于 X2。evaluation 中 `preflight.executable=true`、两个模拟 step 的 `status=EXECUTED` 都只是预演结果，不能覆盖以上 L3 零执行证据。

可复核的只读 SQL（示例保持完整 plan ID，未包含任何凭据）：

```sql
-- gateway_demo_20261101050549_93b880b1
SELECT p.id,p.sid,p.utterance_ids,p.scope_ids,e.created_at_wall,
       u.id,u.parse_status,u.status,u.created_at_wall,u.expires_at_wall
FROM plans p JOIN evaluations e ON e.plan_id=p.id
JOIN utterances u ON u.sid=p.sid
WHERE p.id='pln_01M6ARFJ8EK4597QWNR1V2SEYK'
  AND u.status='ACTIVE' AND u.expires_at_wall>e.created_at_wall
ORDER BY u.created_at_wall,u.id;

SELECT event_type,payload,created_at_wall FROM audit_events
WHERE plan_id='pln_01M6ARFJ8EK4597QWNR1V2SEYK' ORDER BY seq;
SELECT count(*) FROM approvals
WHERE plan_id='pln_01M6ARFJ8EK4597QWNR1V2SEYK';
SELECT count(*) FROM execution_jobs
WHERE plan_id='pln_01M6ARFJ8EK4597QWNR1V2SEYK';

-- bank_demo_20261101050549_93b880b1
SELECT count(*) FROM plan_executions
WHERE plan_id='pln_01M6ARFJ8EK4597QWNR1V2SEYK';
SELECT count(*) FROM transfers
WHERE plan_id='pln_01M6ARFJ8EK4597QWNR1V2SEYK';
SELECT count(*) FROM operation_events
WHERE plan_id='pln_01M6ARFJ8EK4597QWNR1V2SEYK';
SELECT count(*) FROM standing_instructions
WHERE source_plan_id='pln_01M6ARFJ8EK4597QWNR1V2SEYK';
SELECT id,status FROM standing_instructions WHERE id='si_zs_gym';
```

实际执行形式为 `docker exec -e 'PGOPTIONS=-c default_transaction_read_only=on' safebank-e2e-1029-postgres-bank-1 psql -X -U postgres -d <对应代际库> -v ON_ERROR_STOP=1 -tA -c '<固定SELECT>'`。以上没有任何写 SQL。

## 6. 结论边界与后续最小回归方向

本案可确认为“**同一浏览器会话内，先 NO_WRITE 账单，再 SCOPED X2，在前一原话10分钟有效期内遭授权根集合污染，导致正确 X2 计划被保守拒绝**”。没有跨客户或跨会话读取证据，没有发现本计划漏填日期/周期来源，也不是 parser 不支持该规范请求。

若后续获准修复，最小回归应包含真实 G4 的同一会话 `NO_WRITE → SCOPED`，并保证仅当前凭据绑定的计划原话作为授权根；同时保留“未声明的旧原话不得扩权、NO_WRITE不得授予写权限、撤销/过期/epoch不匹配仍拒绝”的负例。仅过滤 NO_WRITE 仍未证明多个 ACTIVE SCOPED 原话之间的声明集合一致性；应按实际当前 plan 声明和凭据绑定关系核查，而不是放宽 evidence 的集合检查。

本报告没有实施修复、发起新的 X2 或声称冻结门禁通过。协调者的新会话 X1 对照独立进行，不在本子任务追加任何模型请求。
