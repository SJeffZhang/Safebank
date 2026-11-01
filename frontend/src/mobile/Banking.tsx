import { BillReport } from "./BillReport";
import { explainCode } from "./copy";
import { useEffect, useState, type ReactNode } from "react";
import { read } from "./api";
import { useLoad } from "./hooks";
import { parseAmountToCents } from "../services/transfer/money";
import type {
  Account,
  Payee,
  Profile,
  Fields,
  Transaction,
  Upcoming,
  Summary,
  Scheduled,
  Split,
} from "./types";
import {
  Modal,
  ChoiceSelect,
  MonthPicker,
  Icon,
  Button,
  Money,
  money,
  LineChart,
  Empty,
  Alert,
  Loading,
  Badge,
  date,
} from "./ui";
export interface PageProps {
  sid: string;
  profile: Profile;
  accounts: Account[];
  revision: number;
  onPropose: (kind: string, fields: Fields) => Promise<void>;
  busy: boolean;
  notify: (text: string) => void;
}
export function Panel({
  title,
  meta,
  children,
  className = "",
}: {
  title: string;
  meta?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={`panel ${className}`}>
      <div className="section-head">
        <h2>{title}</h2>
        {meta}
      </div>
      {children}
    </section>
  );
}
export function Field({
  label,
  children,
  wide = false,
}: {
  label: string;
  children: ReactNode;
  wide?: boolean;
}) {
  return (
    <label className={`field ${wide ? "span-2" : ""}`}>
      <span>{label}</span>
      {children}
    </label>
  );
}
export function Segments({
  items,
  value,
  onChange,
}: {
  items: { id: string; label: string }[];
  value: string;
  onChange: (s: string) => void;
}) {
  return (
    <div className="segments" role="tablist">
      {items.map((i) => (
        <button
          role="tab"
          aria-selected={value === i.id}
          className={value === i.id ? "active" : ""}
          onClick={() => onChange(i.id)}
          key={i.id}
        >
          {i.label}
        </button>
      ))}
    </div>
  );
}
export function TransactionList({
  items,
  compact = false,
}: {
  items: Transaction[];
  compact?: boolean;
}) {
  return items.length ? (
    <div className={`transaction-list ${compact ? "compact" : ""}`}>
      {items.map((t) => (
        <div className="transaction-row" key={t.id}>
          <span
            className={`merchant-avatar ${t.direction === "IN" ? "incoming" : ""}`}
          >
            {t.direction === "IN" ? (
              <Icon name="down" size={19} />
            ) : (
              t.counterparty.slice(0, 1)
            )}
          </span>
          <div className="tx-name">
            <strong>{t.counterparty}</strong>
            <small>
              {t.category} · {date(t.occurred_at)}
              {t.memo ? ` · ${t.memo}` : ""}
            </small>
          </div>
          <span className="tx-kind">
            {t.status === "POSTED" ? "已入账" : t.status}
          </span>
          <strong
            className={`tx-amount ${t.direction === "IN" ? "positive" : ""}`}
          >
            {t.direction === "IN" ? "+" : "−"}
            {money(t.amount_cents)}
          </strong>
        </div>
      ))}
    </div>
  ) : (
    <Empty title="暂无交易" detail="当前筛选范围内没有交易记录。" />
  );
}
export function Overview(p: PageProps) {
  const [hidden, setHidden] = useState(false);
  const [period, setPeriod] = useState("28");
  const month = p.profile.sim_now.slice(0, 7);
  const result = useLoad(async () => {
    const settled = await Promise.allSettled([
      read<{
        balance_history: {
          account_id: string;
          date: string;
          balance_cents: number;
        }[];
      }>(p.sid, "get_accounts", { history_days: Number(period) }),
      read<Upcoming>(p.sid, "upcoming_charges", { days: 7, account_id: null }),
      read<{ items: Transaction[] }>(p.sid, "get_transactions", {
        account_id: null,
        from_at: `${month}-01T00:00:00+08:00`,
        to_at: p.profile.sim_now,
        category: null,
        merchant_id: null,
        kind: null,
        cursor: null,
        limit: 5,
      }),
    ]);
    return settled;
  }, [p.sid, p.revision, period]);
  const history =
    result.value?.[0].status === "fulfilled"
      ? result.value[0].value.balance_history
      : [];
  const upcoming =
    result.value?.[1].status === "fulfilled" ? result.value[1].value : null;
  const transactions =
    result.value?.[2].status === "fulfilled" ? result.value[2].value.items : [];
  const failures =
    result.value?.filter((r) => r.status === "rejected").length ?? 0;
  const dates = [...new Set(history.map((h) => h.date))];
  const values = dates.map((d) =>
    history
      .filter((h) => h.date === d)
      .reduce((n, h) => n + h.balance_cents, 0),
  );
  return (
    <>
      <div className="page-heading">
        <div>
          <div className="eyebrow">您的银行，尽在掌握</div>
          <h1>
            你好，{p.profile.display_name}
            <span className="greeting-dot">。</span>
          </h1>
          <p>这里是您的账户与近期资金安排。</p>
        </div>
        <a href="#transfer" className="button">
          <Icon name="transfer" size={18} />
          转账汇款
        </a>
      </div>
      <div className="overview-grid">
        <section className="asset-panel">
          <div className="section-head">
            <span className="section-label">账户总余额 · CNY</span>
            <button
              className="icon-button"
              onClick={() => setHidden(!hidden)}
              aria-label={hidden ? "显示金额" : "隐藏金额"}
            >
              <Icon name="eye" size={18} />
            </button>
          </div>
          <Money
            value={p.accounts.reduce((n, a) => n + a.balance_cents, 0)}
            hide={hidden}
            className="asset-total"
          />
          <div className="asset-meta">
            <span>
              可用{" "}
              <b>
                {hidden
                  ? "••••"
                  : `¥ ${money(p.accounts.reduce((n, a) => n + a.available_cents, 0))}`}
              </b>
            </span>
            <span>
              冻结{" "}
              <b>
                {hidden
                  ? "••••"
                  : `¥ ${money(p.accounts.reduce((n, a) => n + a.pending_debit_cents, 0))}`}
              </b>
            </span>
          </div>
          <div className="chart-head">
            <span>账户余额走势</span>
            <ChoiceSelect
              label="曲线时间范围"
              value={period}
              onChange={setPeriod}
              compact
              options={[
                { value: "7", label: "近 7 天" },
                { value: "28", label: "近 28 天" },
                { value: "90", label: "近 90 天" },
              ]}
            />
          </div>
          {result.loading ? (
            <Loading />
          ) : hidden ? (
            <div className="chart-hidden">
              <Icon name="lock" />
              余额曲线已隐藏
            </div>
          ) : (
            <LineChart
              values={values}
              labels={
                dates.length
                  ? [
                      date(dates[0]),
                      date(dates[Math.floor(dates.length / 2)]),
                      date(dates[dates.length - 1]),
                    ]
                  : []
              }
            />
          )}
          <div className="account-strip">
            {p.accounts.map((a, i) => (
              <div key={a.id}>
                <span className="account-dot" data-index={i} />
                <span>
                  {a.display_name}
                  <small>{a.account_no_masked}</small>
                </span>
                <strong><Money value={a.available_cents} hide={hidden} className="account-strip-amount" /></strong>
              </div>
            ))}
          </div>
        </section>
        <div className="overview-aside">
          <section className="bank-card mini">
            <div className="bank-card-brand">
              <span className="brand-glyph">S</span>SafeBank<span>DEBIT</span>
            </div>
            <div className="chip">
              <i />
              <i />
            </div>
            <div className="bank-number">
              ••••　••••　••••　
              {p.accounts
                .find((a) => a.current_card_id)
                ?.account_no_masked.slice(-4) ?? "—"}
            </div>
            <div className="bank-card-bottom">
              <span>
                {p.profile.display_name}
                <small>日常所需，安心相伴</small>
              </span>
              <span className="card-circles">
                <i />
                <i />
              </span>
            </div>
          </section>
          <a className="upcoming-link" href="#security">
            <span className="soft-icon">
              <Icon name="clock" />
            </span>
            <div>
              <strong>未来 7 天待扣</strong>
              <small>
                {upcoming
                  ? `${upcoming.items.length} 笔订阅扣款，已计入预留`
                  : "查看扣款安排"}
              </small>
            </div>
            <span>
              {upcoming ? `¥ ${money(upcoming.reserve_total_cents)}` : "—"}
            </span>
            <Icon name="chevron" size={16} />
          </a>
          <a className="assistant-callout" href="#assistant">
            <span className="assistant-orb">
              <Icon name="assistant" size={24} />
            </span>
            <div>
              <h3>让小安帮您打理</h3>
              <p>分析账单、安排转账，复杂的事一起办。</p>
            </div>
            <Icon name="arrow" size={20} />
          </a>
        </div>
      </div>
      <div className="quick-actions">
        {[
          ["transfer", "transfer", "转账与收款", "即时转账 / 定时 / AA"],
          ["chart", "bills", "账单分析", "分类汇总 / 异常提醒"],
          ["card", "cards", "卡片管理", "限额 / 渠道 / 挂失"],
          ["wealth", "wealth", "理财投资", "产品 / 测评 / 持仓"],
        ].map(([icon, path, title, sub]) => (
          <a key={path} href={`#${path}`}>
            <span className="quick-icon">
              <Icon name={icon} />
            </span>
            <div>
              <strong>{title}</strong>
              <small>{sub}</small>
            </div>
            <Icon name="chevron" size={17} />
          </a>
        ))}
      </div>
      {failures > 0 && (
        <Alert>
          部分信息暂时无法读取，已展示的信息仍可查看。
          <button className="text-button" onClick={result.reload}>
            重新读取
          </button>
        </Alert>
      )}
      <Panel
        title="最近交易"
        meta={
          <a className="text-link" href="#bills">
            全部账单 <Icon name="chevron" size={15} />
          </a>
        }
      >
        {result.loading ? (
          <Loading />
        ) : (
          <TransactionList items={transactions.slice(0, 5)} compact />
        )}
      </Panel>
    </>
  );
}
export function Transfers(p: PageProps) {
  const [tab, setTab] = useState("instant");
  const [targetType, setTargetType] = useState("external");
  const [source, setSource] = useState("");
  const [target, setTarget] = useState("");
  const [amount, setAmount] = useState("");
  const [memo, setMemo] = useState("");
  const [period, setPeriod] = useState("MONTHLY");
  const [first, setFirst] = useState("");
  const [end, setEnd] = useState("");
  const [selected, setSelected] = useState<string[]>([]);
  const [include, setInclude] = useState(true);
  const [name, setName] = useState("");
  const [accountNo, setAccountNo] = useState("");
  const [newPayeeOpen, setNewPayeeOpen] = useState(false);
  const [newPayeeError, setNewPayeeError] = useState("");
  const [pendingPayee, setPendingPayee] = useState<{
    ids: string[]; name: string; bank: string; last4: string;
  }>();
  const [bank, setBank] = useState("");
  const [error, setError] = useState("");
  const query = useLoad(
    async () => ({
      payees: await read<{ items: Payee[] }>(p.sid, "list_payees", {
        query: null,
        cursor: null,
        limit: 100,
      }),
      scheduled:
        tab === "scheduled"
          ? await read<{ items: Scheduled[] }>(p.sid, "list_scheduled", {
              status: "ACTIVE",
              cursor: null,
              limit: 50,
            })
          : { items: [] },
      split:
        tab === "split"
          ? await read<{ items: Split[] }>(p.sid, "get_split_status", {
              request_id: null,
              cursor: null,
              limit: 50,
            })
          : { items: [] },
    }),
    [p.sid, p.revision, tab],
  );
  const payees = query.value?.payees.items ?? [];
  useEffect(() => {
    if (!pendingPayee || !query.value) return;
    const added = query.value.payees.items.find((item) =>
      !pendingPayee.ids.includes(item.id) && item.name === pendingPayee.name &&
      item.bank_name === pendingPayee.bank && item.account_no_masked.endsWith(pendingPayee.last4));
    if (!added) return;
    setTarget(added.id);
    setTargetType("external");
    setNewPayeeOpen(false);
    setPendingPayee(undefined);
    setName(""); setAccountNo(""); setBank("");
    p.notify(`已选择新收款人 ${added.name}，请核对转账金额后继续。`);
  }, [query.value, pendingPayee]);
  function openNewPayee() {
    setName(""); setAccountNo(""); setBank("");
    setNewPayeeError(""); setPendingPayee(undefined);
    setNewPayeeOpen(true);
  }
  async function addNewPayee() {
    if (p.busy) return;
    if (!name.trim() || !/^\d{12,32}$/.test(accountNo) || !bank.trim()) {
      setNewPayeeError("请填写姓名、12–32 位银行卡号和开户行。");
      return;
    }
    setNewPayeeError("");
    setPendingPayee({ ids: payees.map(item => item.id), name: name.trim(), bank: bank.trim(), last4: accountNo.slice(-4) });
    await p.onPropose("PAYEE_ADD", {
      name: name.trim(), account_no: accountNo, bank_name: bank.trim(), phone: null, alias: null,
    });
  }
  const chosen = p.accounts.find((a) => a.id === source);
  const destination =
    targetType === "self"
      ? p.accounts.find((a) => a.id === target)?.display_name
      : payees.find((a) => a.id === target)?.name;
  async function submit() {
    setError("");
    if (tab === "payees") {
      if (!name.trim() || !/^\d{12,32}$/.test(accountNo) || !bank.trim()) {
        setError("请填写姓名、12–32 位银行卡号和开户行。");
        return;
      }
      await p.onPropose("PAYEE_ADD", {
        name: name.trim(),
        account_no: accountNo,
        bank_name: bank.trim(),
        phone: null,
        alias: null,
      });
      return;
    }
    const parsed = parseAmountToCents(amount);
    if ("error" in parsed) {
      setError(parsed.error);
      return;
    }
    if (!source) {
      setError("请选择付款账户。");
      return;
    }
    if (tab === "split") {
      if (!selected.length) {
        setError("请选择至少一位参与人。");
        return;
      }
      await p.onPropose("SPLIT_CREATE", {
        account_id: source,
        total_cents: parsed.cents,
        participant_payee_ids: selected,
        include_self: include,
        memo: memo || null,
      });
      return;
    }
    if (!target || target === source) {
      setError("请选择有效的收款目标。");
      return;
    }
    if (tab === "instant" && parsed.cents > (chosen?.available_cents ?? 0)) {
      setError("金额超过当前可用余额。");
      return;
    }
    const fields = {
      from_account_id: source,
      payee_id: targetType === "external" ? target : null,
      to_account_id: targetType === "self" ? target : null,
      amount_cents: parsed.cents,
    };
    if (tab === "scheduled") {
      if (
        !first ||
        new Date(`${first}:00+08:00`).getTime() <=
          new Date(p.profile.sim_now).getTime()
      ) {
        setError("首次执行时间须晚于当前银行模拟时间。");
        return;
      }
      await p.onPropose("SCHEDULE_CREATE", {
        ...fields,
        period,
        first_run_at: `${first}:00+08:00`,
        end_at: end ? `${end}:00+08:00` : null,
      });
    } else
      await p.onPropose(
        targetType === "self" ? "TRANSFER_SELF" : "TRANSFER_EXTERNAL",
        { ...fields, memo: memo || null },
      );
  }
  return (
    <>
      <div className="page-heading">
        <div>
          <div className="eyebrow">TRANSFER & RECEIVE</div>
          <h1>转账与收款</h1>
          <p>每一笔安排，都先看清资金变化。</p>
        </div>
        <Badge>
          <Icon name="shield" size={13} />
          执行前安全预演
        </Badge>
      </div>
      <Segments
        items={[
          { id: "instant", label: "即时转账" },
          { id: "scheduled", label: "定时转账" },
          { id: "split", label: "AA 收款" },
          { id: "payees", label: "收款人" },
        ]}
        value={tab}
        onChange={(v) => {
          setTab(v);
          setError("");
        }}
      />
      <div className="form-layout">
        <Panel
          title={
            tab === "payees"
              ? "新增收款人"
              : tab === "split"
                ? "发起一笔 AA 收款"
                : tab === "scheduled"
                  ? "安排定时转账"
                  : "填写转账信息"
          }
        >
          {query.error && (
            <Alert tone="error">
              {query.error}
              <button className="text-button" onClick={query.reload}>
                重试
              </button>
            </Alert>
          )}
          <form
            className="bank-form"
            onSubmit={(e) => {
              e.preventDefault();
              void submit();
            }}
          >
            {tab === "payees" ? (
              <>
                <Field label="收款人姓名">
                  <input
                    value={name}
                    onChange={(e) => setName(e.target.value)}
                    autoComplete="off"
                    required
                  />
                </Field>
                <Field label="银行卡号">
                  <input
                    inputMode="numeric"
                    value={accountNo}
                    onChange={(e) =>
                      setAccountNo(e.target.value.replace(/\D/g, ""))
                    }
                    maxLength={32}
                    required
                  />
                </Field>
                <Field label="开户行">
                  <input
                    value={bank}
                    onChange={(e) => setBank(e.target.value)}
                    required
                  />
                </Field>
              </>
            ) : (
              <>
                <fieldset className="transfer-source-list span-2">
                  <legend>{tab === "split" ? "收款账户" : "付款账户"}</legend>
                  {p.accounts.length ? (
                    <div className="transfer-source-options">
                      {p.accounts.map((account) => (
                        <label key={account.id} className={`transfer-source-option ${source === account.id ? "selected" : ""}`}>
                          <input type="radio" name="transfer-source" value={account.id}
                            checked={source === account.id}
                            onChange={() => {
                              setSource(account.id);
                              if (target === account.id) setTarget("");
                            }} />
                          <span className="transfer-source-icon" aria-hidden="true">
                            <Icon name={account.current_card_id ? "card" : "wallet"} size={20} />
                          </span>
                          <span className="transfer-source-info">
                            <strong>{account.display_name}</strong>
                            <span className="transfer-source-number">{account.account_no_masked}</span>
                            <small>{account.current_card_id ? "银行卡账户" : "储蓄账户 · 未绑定银行卡"}</small>
                            <span className="transfer-source-balance">可用余额 <Money value={account.available_cents} /></span>
                          </span>
                          <span className="transfer-source-check" aria-hidden="true">
                            {source === account.id && <Icon name="check" size={12} />}
                          </span>
                        </label>
                      ))}
                    </div>
                  ) : <p className="muted">暂无可用账户</p>}
                </fieldset>
                {tab === "split" ? (
                  <div className="field span-2">
                    <span>参与人</span>
                    <div className="payee-choices">
                      {payees.map((a) => (
                        <label
                          className={selected.includes(a.id) ? "selected" : ""}
                          key={a.id}
                        >
                          <input
                            type="checkbox"
                            checked={selected.includes(a.id)}
                            onChange={(e) =>
                              setSelected(
                                e.target.checked
                                  ? [...selected, a.id]
                                  : selected.filter((id) => id !== a.id),
                              )
                            }
                          />
                          <span className="person-avatar">
                            {a.name.slice(0, 1)}
                          </span>
                          <div>
                            <strong>{a.name}</strong>
                            <small>{a.account_no_masked}</small>
                          </div>
                        </label>
                      ))}
                    </div>
                    <label className="check-row">
                      <input
                        type="checkbox"
                        checked={include}
                        onChange={(e) => setInclude(e.target.checked)}
                      />
                      包含我本人
                    </label>
                  </div>
                ) : (
                  <>
                    <div className="field span-2">
                      <span>收款方式</span>
                      <Segments
                        items={[
                          { id: "external", label: "转给他人" },
                          { id: "self", label: "本人互转" },
                        ]}
                        value={targetType}
                        onChange={(v) => {
                          setTargetType(v);
                          setTarget("");
                        }}
                      />
                    </div>
                    <div className="field span-2">
                      <div className="payee-target-heading"><span>收款目标</span>
                        {targetType === "external" && <button type="button" className="text-button add-payee-entry"
                          onClick={openNewPayee}><Icon name="plus" size={13} />添加新收款人</button>}
                      </div>
                      <ChoiceSelect
                        label="收款目标"
                        value={target}
                        onChange={(value) => value === "__add_payee__" ? openNewPayee() : setTarget(value)}
                        placeholder={
                          targetType === "self"
                            ? "请选择本人账户"
                            : "请选择收款人"
                        }
                        options={
                          targetType === "self"
                            ? p.accounts
                                .filter((a) => a.id !== source)
                                .map((a) => ({
                                  value: a.id,
                                  label: a.display_name,
                                  description: a.account_no_masked,
                                }))
                            : [...payees.map((a) => ({
                                value: a.id,
                                label: a.name,
                                description: `${a.bank_name} · ${a.account_no_masked}`,
                              })), { value: "__add_payee__", label: "＋ 添加新收款人", description: "使用姓名、银行卡号和开户行添加" }]
                        }
                      />
                    </div>
                  </>
                )}
                <Field
                  label={tab === "split" ? "AA 总金额（元）" : "转账金额（元）"}
                  wide
                >
                  <div className="amount-input">
                    <span>¥</span>
                    <input
                      aria-label={tab === "split" ? "AA 总金额" : "转账金额"}
                      inputMode="decimal"
                      placeholder="0.00"
                      value={amount}
                      onChange={(e) => setAmount(e.target.value)}
                      required
                    />
                  </div>
                </Field>
                {tab === "scheduled" ? (
                  <>
                    <Field label="执行周期">
                      <ChoiceSelect
                        label="执行周期"
                        value={period}
                        onChange={setPeriod}
                        options={[
                          { value: "ONCE", label: "仅一次" },
                          { value: "WEEKLY", label: "每周" },
                          { value: "MONTHLY", label: "每月" },
                        ]}
                      />
                    </Field>
                    <Field label="首次执行（北京时间）">
                      <input
                        type="datetime-local"
                        value={first}
                        onChange={(e) => setFirst(e.target.value)}
                        required
                      />
                    </Field>
                    <Field label="结束时间（选填）" wide>
                      <input
                        type="datetime-local"
                        value={end}
                        min={first}
                        onChange={(e) => setEnd(e.target.value)}
                      />
                    </Field>
                  </>
                ) : (
                  <Field label="备注（选填）" wide>
                    <input
                      maxLength={200}
                      value={memo}
                      onChange={(e) => setMemo(e.target.value)}
                      placeholder="为这笔安排留个备注"
                    />
                  </Field>
                )}
              </>
            )}
            {error && (
              <div className="span-2">
                <Alert tone="error">{error}</Alert>
              </div>
            )}
            <div className="form-footer span-2">
              <span>
                <Icon name="lock" size={14} />
                所有写操作由安全网关评估
              </span>
              <Button type="submit" disabled={p.busy || query.loading}>
                {p.busy ? "正在预演…" : "查看预演"}
                <Icon name="arrow" size={17} />
              </Button>
            </div>
          </form>
        </Panel>
        <aside className="form-aside">
          <span className="soft-icon">
            <Icon name="shield" size={28} />
          </span>
          <h3>确认之前，先有答案</h3>
          <p>
            预演将展示资金变化与风险。需要确认时，您可以在独立面板中核对全部细节。
          </p>
          <dl>
            <div>
              <dt>付款账户</dt>
              <dd>{chosen?.display_name ?? "待选择"}</dd>
            </div>
            <div>
              <dt>收款目标</dt>
              <dd>{destination ?? "待选择"}</dd>
            </div>
            <div>
              <dt>当前可用</dt>
              <dd>{chosen ? `¥ ${money(chosen.available_cents)}` : "—"}</dd>
            </div>
          </dl>
          <p className="helper">
            定时安排按银行模拟时间执行；AA 收款请求本身不会转出资金。
          </p>
        </aside>
      </div>
      {newPayeeOpen && (
        <Modal title="添加新收款人" onClose={() => { setNewPayeeOpen(false); setAccountNo(""); }}>
          <form className="modal-body new-payee-form" onSubmit={(event) => {
            event.preventDefault(); void addNewPayee();
          }}>
            <p className="muted">添加并确认后，返回当前转账，付款账户和金额会保留。</p>
            <Field label="收款人姓名"><input autoFocus value={name} autoComplete="off" required maxLength={80}
              onChange={(event) => setName(event.target.value)} placeholder="请输入姓名" /></Field>
            <Field label="银行卡号"><input value={accountNo} inputMode="numeric" autoComplete="off" required maxLength={32}
              onChange={(event) => setAccountNo(event.target.value.replace(/\D/g, ""))} placeholder="请输入完整银行卡号" /></Field>
            <Field label="开户行"><input value={bank} required maxLength={80}
              onChange={(event) => setBank(event.target.value)} placeholder="如：中国工商银行" /></Field>
            {newPayeeError && <Alert tone="error">{newPayeeError}</Alert>}
            <Button type="submit" disabled={p.busy}>{p.busy ? "正在处理…" : "预演并确认添加"}</Button>
            <Button variant="ghost" onClick={() => { setNewPayeeOpen(false); setAccountNo(""); }}>返回转账</Button>
          </form>
        </Modal>
      )}
      {tab === "scheduled" && (
        <Panel title="有效定时安排">
          {query.value?.scheduled.items.length ? (
            query.value.scheduled.items.map((s) => (
              <div className="data-row" key={s.id}>
                <Icon name="repeat" />
                <div>
                  <strong>{s.target_display}</strong>
                  <small>
                    {s.period} · 下次 {date(s.next_run_at)}
                  </small>
                </div>
                <Money value={s.amount_cents} />
                <Button
                  variant="secondary"
                  disabled={p.busy}
                  onClick={() =>
                    p.onPropose("SCHEDULE_CANCEL", { instruction_id: s.id })
                  }
                >
                  取消安排
                </Button>
              </div>
            ))
          ) : (
            <Empty title="暂无定时转账" detail="创建后可在这里管理执行周期。" />
          )}
        </Panel>
      )}
      {tab === "split" && (
        <Panel title="AA 收款进度">
          {query.value?.split.items.length ? (
            query.value.split.items.map((s) => (
              <div className="data-row" key={s.id}>
                <div>
                  <strong>AA 收款 · ¥{money(s.total_cents)}</strong>
                  <small>
                    已收到 ¥{money(s.received_cents)} · 待收 ¥
                    {money(s.outstanding_cents)}
                  </small>
                </div>
                <Badge>
                  {s.items.filter((i) => i.status === "PAID").length}/
                  {s.items.length} 人已付
                </Badge>
              </div>
            ))
          ) : (
            <Empty
              title="还没有 AA 收款"
              detail="选择参与人，发起第一笔收款请求。"
            />
          )}
        </Panel>
      )}
    </>
  );
}
export function Bills(p: PageProps & {initialMonth?: string}) {
  const [month, setMonth] = useState(p.initialMonth ?? p.profile.sim_now.slice(0, 7));
  const [reportPeriod, setReportPeriod] = useState<"month" | "year">();
  const [category, setCategory] = useState("");
  const [search, setSearch] = useState("");
  const [selected, setSelected] = useState<Transaction>();
  const [tab, setTab] = useState("all");
  const from = `${month}-01T00:00:00+08:00`;
  const parts = month.split("-").map(Number);
  const nextMonth =
    parts[1] === 12
      ? `${parts[0] + 1}-01`
      : `${parts[0]}-${String(parts[1] + 1).padStart(2, "0")}`;
  const to = `${nextMonth}-01T00:00:00+08:00`;
  const q = useLoad(async () => {
    const [summary, transactions, anomalies] = await Promise.allSettled([
      read<Summary>(p.sid, "get_spending_summary", { month, account_id: null }),
      read<{
        items: Transaction[];
        income_cents: number;
        expense_cents: number;
        next_cursor: string | null;
      }>(p.sid, "get_transactions", {
        account_id: null,
        from_at: from,
        to_at: to,
        category: category || null,
        merchant_id: null,
        kind: null,
        cursor: null,
        limit: 100,
      }),
      read<{
        items: {
          transaction: Transaction;
          codes: string[];
          evidence: {
            mean_cents?: number;
            threshold_cents?: number;
            baseline_count: number;
          };
        }[];
      }>(p.sid, "detect_anomalies", {
        from_at: from,
        to_at: to,
        account_id: null,
      }),
    ]);
    return { summary, transactions, anomalies };
  }, [p.sid, p.revision, month, category]);
  const summary =
    q.value?.summary.status === "fulfilled" ? q.value.summary.value : null;
  const categoryStats = summary
    ? [...summary.categories.filter((item) => item.category !== "其他"),
       summary.categories.find((item) => item.category === "其他") ?? {
         category: "其他", amount_cents: 0, share_bp: 0, previous_amount_cents: 0,
       }]
    : [];
  const transactions =
    q.value?.transactions.status === "fulfilled"
      ? q.value.transactions.value
      : null;
  const anomalies =
    q.value?.anomalies.status === "fulfilled"
      ? q.value.anomalies.value.items
      : [];
  const items = (transactions?.items ?? []).filter((t) =>
    `${t.counterparty}${t.memo ?? ""}`.includes(search),
  );
  return (
    <>
      <div className="page-heading">
        <div>
          <div className="eyebrow">BILLS & INSIGHTS</div>
          <h1>账单与分析</h1>
          <p>消费分类与异常提示，均来自账户交易记录。</p>
        </div>
        <MonthPicker value={month} onChange={setMonth} />
      </div>
      <div className="bill-report-entry" aria-label="账单报告">
        <div><Icon name="chart" size={19}/><span><strong>我的账单报告</strong><small>{month} · 月度与年度收支汇总</small></span></div>
        <div><Button variant="ghost" onClick={() => setReportPeriod("month")}>月度报告</Button><Button variant="ghost" onClick={() => setReportPeriod("year")}>年度报告</Button></div>
      </div>
      {reportPeriod && <Modal title={reportPeriod === "month" ? "月度账单报告" : "年度账单报告"} onClose={() => setReportPeriod(undefined)}>
        <BillReport sid={p.sid} month={month} period={reportPeriod} simMonth={p.profile.sim_now.slice(0,7)}/>
      </Modal>}
      {q.loading ? (
        <Loading />
      ) : (
        <>
          <div className="bill-overview">
            <div>
              <span>本月消费</span>
              <Money value={summary?.total_cents ?? 0} className="bill-total" />
              <small>
                {summary
                  ? `较上月${summary.mom_change_cents <= 0 ? "减少" : "增加"} ¥${money(Math.abs(summary.mom_change_cents))}`
                  : "消费汇总暂不可用"}
              </small>
            </div>
            <div>
              <span>本月收入</span>
              <Money value={transactions?.income_cents ?? 0} />
              <small>筛选范围内全部交易</small>
            </div>
            <div>
              <span>对外转出</span>
              <Money value={summary?.transfer_out_cents ?? 0} />
              <small>单独列示，不计入消费</small>
            </div>
            <div className="bill-anomaly">
              <span>异常提醒</span>
              <strong>
                {anomalies.length}
                <small> 笔</small>
              </strong>
              <button
                className="text-button"
                onClick={() => {
                  setTab("anomalies");
                  requestAnimationFrame(() =>
                    document
                      .querySelector(".table-toolbar")
                      ?.scrollIntoView({ behavior: "smooth", block: "start" }),
                  );
                }}
              >
                查看判断依据 <Icon name="arrow" size={15} />
              </button>
            </div>
          </div>
          {summary ? (
            <Panel
              title="消费分布"
              meta={<span className="helper">{month} · 按类别</span>}
            >
              <div className="spending-layout">
                <div className="category-bars">
                  {categoryStats.map((c, i) => (
                    <div className="category-row" key={c.category}>
                      <span className="category-index">0{i + 1}</span>
                      <strong>{c.category}</strong>
                      <div className="bar-track">
                        <div
                          style={{ width: `${c.amount_cents > 0 ? Math.max(1, c.share_bp / 100) : 0}%` }}
                        />
                      </div>
                      <span>{(c.share_bp / 100).toFixed(1)}%</span>
                      <b>¥ {money(c.amount_cents)}</b>
                    </div>
                  ))}
                </div>
                <div className="daily-bars" aria-label="每日消费柱状图">
                  {summary.daily.map((d) => (
                    <div
                      key={d.date}
                      title={`${d.date} ¥${money(d.amount_cents)}`}
                    >
                      <span
                        style={{
                          height: `${Math.max(3, (d.amount_cents / Math.max(...summary.daily.map((d) => d.amount_cents), 1)) * 100)}%`,
                        }}
                      />
                    </div>
                  ))}
                  <small>每日消费 · {month}</small>
                </div>
              </div>
            </Panel>
          ) : (
            <Alert>消费分析暂时无法读取，您仍可查看可用的流水信息。</Alert>
          )}
          <Panel
            title="交易明细"
            meta={<span className="helper">人民币 · 元</span>}
          >
            <div className="table-toolbar">
              <Segments
                items={[
                  { id: "all", label: "全部交易" },
                  { id: "anomalies", label: `异常交易 ${anomalies.length}` },
                ]}
                value={tab}
                onChange={setTab}
              />
              <div className="table-filters">
                <div className="search-box">
                  <Icon name="search" size={17} />
                  <input
                    aria-label="搜索交易"
                    placeholder="搜索商户或备注"
                    value={search}
                    onChange={(e) => setSearch(e.target.value)}
                  />
                </div>
                <ChoiceSelect
                  label="交易类别"
                  value={category}
                  onChange={setCategory}
                  compact
                  options={[
                    { value: "", label: "全部类别" },
                    ...[
                      "餐饮",
                      "交通",
                      "购物",
                      "娱乐",
                      "生活服务",
                      "数码",
                      "健身",
                      "其他",
                      "转账",
                    ].map((label) => ({ value: label, label })),
                  ]}
                />
              </div>
            </div>
            {tab === "all" ? (
              <>
                {transactions ? (
                  <div>
                    {items.map((t) => (
                      <button
                        className="transaction-button"
                        key={t.id}
                        onClick={() => setSelected(t)}
                      >
                        <TransactionList items={[t]} />
                      </button>
                    ))}
                    {!items.length && (
                      <Empty
                        title="没有匹配的交易"
                        detail="试着修改月份、类别或搜索词。"
                      />
                    )}
                    {transactions.next_cursor && (
                      <Alert>
                        当前显示前 100 条交易，请缩小筛选范围查看其余记录。
                      </Alert>
                    )}
                  </div>
                ) : (
                  <Alert tone="error">
                    流水读取失败。
                    <button className="text-button" onClick={q.reload}>
                      重试
                    </button>
                  </Alert>
                )}
              </>
            ) : anomalies.length ? (
              anomalies.map((a) => (
                <div className="anomaly-row" key={a.transaction.id}>
                  <span className="soft-icon">
                    <Icon name="shield" />
                  </span>
                  <div>
                    <strong>{a.transaction.counterparty}</strong>
                    <p>{a.codes.map(explainCode).join(" · ")}</p>
                    <small>
                      依据：{a.evidence.baseline_count} 条基线记录
                      {a.evidence.threshold_cents
                        ? ` · 阈值 ¥${money(a.evidence.threshold_cents)}`
                        : ""}
                    </small>
                  </div>
                  <Money value={a.transaction.amount_cents} />
                  <a href="#cards" className="text-link">
                    管理卡片 <Icon name="chevron" size={15} />
                  </a>
                </div>
              ))
            ) : (
              <Empty
                title="未发现异常交易"
                detail="当前时间范围内没有命中异常规则。"
              />
            )}
          </Panel>
        </>
      )}
      {selected && (
        <div className="inline-detail">
          <div className="section-head">
            <h3>交易详情</h3>
            <Button variant="ghost" onClick={() => setSelected(undefined)}>
              收起
            </Button>
          </div>
          <dl>
            {Object.entries({
              交易编号: selected.id,
              交易对方: selected.counterparty,
              金额: `¥ ${money(selected.amount_cents)}`,
              时间: selected.occurred_at,
              备注: selected.memo ?? "无",
              状态: selected.status,
            }).map(([k, v]) => (
              <div key={k}>
                <dt>{k}</dt>
                <dd>{v}</dd>
              </div>
            ))}
          </dl>
        </div>
      )}
    </>
  );
}
