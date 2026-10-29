# L3-C6-seed 独立评审

范围：`git diff origin/main..HEAD`（demo seed 新增六只理财产品）。
静态只读评审，未运行 Docker/PG 测试。

## Verdict

**APPROVE**

六只理财产品的 fixture 值与权威数据逐项一致；seedlib 校验完整（类型 / 非 float / 必填 / 唯一 ID / 业务范围），E0 加载路径（`evaluation/runner/seed.py`）不受影响；`seed_demo` 以 `on_conflict_do_nothing` 幂等插入、只写 `products` 表、不碰客户与账本；测试断言真实值（含 NAV 投影 1.2310→1.2311）。无 P0/P1，1 条 P2 硬化建议（`_parse_decimal` 仍接受 float 输入）。

## Findings

### P0

（无）

### P1

（无）

### P2

1. **`_parse_decimal` 接受 float 输入（seedlib.py:88-95）**：`Decimal(str(value))` 对 float 输入会静默丢精度——JSON 里若写成不带引号的 `"base_nav": 1.2310`，`json.loads` 得到 float `1.231`，`str()` 得 `"1.231"`，丢失末位 `0` 与 4 位小数尺度；且与同文件 `_require_int`（拒绝 float/bool）的 fail-closed 风格不一致，未落实「Decimal not float」契约。当前 fixture 全部用字符串，无实际数据错误，故不阻塞。建议把 `_parse_decimal` 收窄为只接受 `str`（可选 int/Decimal），float/bool 直接 `SeedError`。

## Checks

1. **fixture 值与权威数据逐项一致**：核对 `fixtures/l1_seed_v1.json` 的 `products` 六条，与权威数据完全吻合——`prd_mmf` 安心货币 MMF R1 保守 confirm0/settle0/min100/nav1.0000；`prd_sbond` 稳盈短债 BOND R2 稳健 1/1/100000/1.0520；`prd_stable` 稳健增利 BOND R3 稳健 1/1/100000/1.2310；`prd_mixed` 均衡混合 MIXED R3 平衡 1/2/100000/1.4875；`prd_growth` 成长股票 EQUITY R4 进取 1/2/100000/2.0310；`prd_tech` 科技先锋 EQUITY R5 激进 1/2/100000/1.8820。`base_date` 均为 `2026-10-15T12:00:00+08:00`，与 seed 日（fixture `sim_now=2026-10-15T09:00:00+08:00`）同一自然日，故种子日 NAV == base_nav。`base_nav` 以字符串存储，避免 JSON float 精度问题。通过。

2. **seedlib 校验完整、E0 加载路径不受影响**：`seedlib.py` 新增 `_parse_decimal`（非有限值拒绝）、`PRODUCT_TYPES` 白名单、risk_level 1..5 范围、confirm/settle/min 非负/正校验、必填字段经 `_non_empty_str`/`_require_int`/`_parse_dt`（缺字段或 None 均抛 `SeedError`）、`_unique_ids` 去重。`base_date` 用 `_parse_dt` 强制带时区。E0 加载器 `evaluation/runner/seed.py:43` 调用 `prepare_seed` 后只按白名单键（customers/accounts/cards/payees/merchants/transfers/transactions/sim_now/dataset_revision）写库，新增的 `products` 键被忽略、不破坏；`tests/evaluation/corpus/spec.py` 的 `validate_seed` 不拒绝未知键。`prepare_seed` 对 `products` 用 `seed.get("products") or []`，向后兼容无 products 的旧种子。通过。

3. **seed_demo 幂等插入、不碰客户/账本**：`seed_demo.py:135-141` 对 `prepared["products"]` 无条件执行 `pg_insert(m.products).values(**product).on_conflict_do_nothing()`，依赖 `products` 主键 `id`（models.py:726 `idcol(primary_key=True)`）幂等，无其它 unique 约束冲突面。该语句只 INSERT 进 `products` 表，不 UPDATE/DELETE 客户、账户、transfer、transaction 行；二次运行 `added_products == 0`（rowcount 为 0）。`install_demo` 中仅有的既存行写入（equity/clearing 账户增量、payee created_at 同步）均与 products 无关且受 `new_customer_ids`/`skipped_customer_ids` 门控。通过。

4. **测试断言真实值**：`tests/obligations_data/test_seed_demo.py` 新增 `test_seed_demo_installs_products_idempotently`，用 `EXPECTED_PRODUCTS` 断言六只产品的 name/product_type/risk_level/risk_label/confirm_days/settle_days/min_amount_cents/base_nav（`Decimal("…")`）/base_date 日期/disclosure_version，并断言 `[id] == sorted(EXPECTED_PRODUCTS)`（恰好六条、无多余）；首装 `added_products == 6`、重装 `== 0`；用 bankcore `get_product` 断言 `prd_stable` 种子日 NAV `1.2310`、次一交易日（2026-10-16，周四+1=周五，交易日）`1.2311`，与 nav.py 的 `base_nav + trading_days_between * 0.0001`（`NAV_STEP=0.0001`）一致。`base_date` 读回 `.date()==2026-10-15`（+08:00 正午转 UTC 不跨日）。断言均为真实业务值，无放宽。通过。
