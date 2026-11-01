import type { ReactNode } from "react";
import { explainCode } from "./copy";
import { monthRange } from "./bill-month";
import { productTypeLabel, type Fields, type UICard } from "./types";
import { Alert, money } from "./ui";

const fields = (value: unknown): Fields => value && typeof value === "object" && !Array.isArray(value) ? value as Fields : {};
const rows = (data: Fields, key: string): Fields[] => Array.isArray(data[key]) ? (data[key] as unknown[]).filter((value): value is Fields => !!value && typeof value === "object" && !Array.isArray(value)) : [];
const text = (value: unknown) => typeof value === "string" && value.trim() ? value : null;
const cash = (value: unknown) => typeof value === "number" && Number.isSafeInteger(value) ? `¥ ${money(value)}` : "—";
const decimal = (value: unknown) => typeof value === "string" && /^\d+(?:\.\d{1,4})?$/.test(value) ? value : "—";
const known = (labels: Record<string, string>, value: unknown, fallback: string) => typeof value === "string" && Object.prototype.hasOwnProperty.call(labels, value) ? labels[value] : fallback;

/** Display-only compatibility with historical cards. Unwrap one exact source
 * boundary, never parse HTML, remove fragments, or change the source payload. */
function displayLabel(value: unknown, source: string | undefined) {
  let label = text(value);
  if (label && source && /^src_[\w-]+$/.test(source)) {
    const open = `<untrusted_data source="${source}">`, close = "</untrusted_data>";
    if (label.startsWith(open) && label.endsWith(close)) label = label.slice(open.length, -close.length);
  }
  return text(label);
}

const shanghai = new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Shanghai", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hourCycle: "h23" });
function displayDate(value: unknown) {
  if (typeof value !== "string") return "日期暂不可用";
  // A bank trade/NAV day is a calendar date, not a UTC timestamp.
  if (/^\d{4}-\d{2}-\d{2}$/.test(value)) {
    const at = new Date(`${value}T00:00:00Z`);
    return Number.isFinite(at.getTime()) && at.toISOString().slice(0, 10) === value
      ? `${value.slice(0, 4)}年${value.slice(5, 7)}月${value.slice(8, 10)}日` : "日期暂不可用";
  }
  if (!/^\d{4}-\d{2}-\d{2}T(?:[01]\d|2[0-3]):[0-5]\d(?::[0-5]\d(?:\.\d+)?)?(?:Z|[+-]\d{2}:\d{2})$/.test(value)) return "日期暂不可用";
  const day = new Date(`${value.slice(0, 10)}T00:00:00Z`);
  if (!Number.isFinite(day.getTime()) || day.toISOString().slice(0, 10) !== value.slice(0, 10)) return "日期暂不可用";
  const at = new Date(value);
  if (!Number.isFinite(at.getTime())) return "日期暂不可用";
  const part = Object.fromEntries(shanghai.formatToParts(at).map(p => [p.type, p.value]));
  return `${part.year}年${part.month}月${part.day}日 ${part.hour}:${part.minute}（北京时间）`;
}
function Detail({ label, value }: { label: string; value: ReactNode }) {
  return <div className="read-card-detail"><span>{label}</span><strong>{value}</strong></div>;
}
function Amount({ data, field, label }: { data: Fields; field: string; label: string }) {
  return typeof data[field] === "number" && Number.isSafeInteger(data[field]) ? <Detail label={label} value={cash(data[field])} /> : null;
}
function When({ data, field, label }: { data: Fields; field: string; label: string }) {
  return data[field] == null ? null : <small className="read-card-date">{label}：{displayDate(data[field])}</small>;
}
function More({ count, pending }: { count: number; pending?: boolean }) {
  return <>{count > 5 && <p>另有 {count - 5} 条，可到业务页面查看完整记录。</p>}{pending && <p>尚有未读取记录，当前卡片不是完整列表。</p>}</>;
}

/** Read-card presentation never supplies action values or authority. */
export function AssistantReadData({ card, requestedMonths }: { card: UICard; requestedMonths?: string[] | null }) {
  const data = fields(card.data);
  const name = (value: unknown, fallback: string) => displayLabel(value, card.source_ref) ?? fallback;
  if (card.type === "TRANSACTIONS" && typeof data.month === "string") {
    if (!/^\d{4}-(0[1-9]|1[0-2])$/.test(data.month) || !Number.isSafeInteger(data.total_cents) ||
      (requestedMonths !== undefined && !requestedMonths?.includes(data.month)))
      return <Alert tone="error">月报月份或数据与本次请求不一致，请重新读取。</Alert>;
    return <><p>{data.month} 月度消费</p><Amount data={data} field="total_cents" label="消费合计" />
      {rows(data, "categories").map((category, i) => <Detail key={i} label={text(category.category) ?? "未分类"} value={cash(category.amount_cents)} />)}</>;
  }
  if (card.type === "ANOMALIES") {
    const anomalies = rows(data, "items");
    if (requestedMonths !== undefined) {
      if (!requestedMonths?.length) return <Alert tone="error">请明确所选月份后重新读取异常记录。</Alert>;
      const ranges = requestedMonths.map(monthRange);
      if (anomalies.some(item => {
        const at = Date.parse(String(fields(item.transaction).occurred_at ?? ""));
        return !ranges.some(range => at >= Date.parse(range.from_at) && at < Date.parse(range.to_at));
      })) return <Alert tone="error">异常记录不在所选月份范围内，请重新读取。</Alert>;
    }
    if (!anomalies.length) return <p>所选范围内没有异常记录。</p>;
    return <>{anomalies.slice(0, 5).map((item, i) => {
      const tx = fields(item.transaction);
      return <div className="read-card-entry" key={i}><Detail label={name(tx.counterparty, "异常交易")} value={cash(tx.amount_cents)} />
        <When data={tx} field="occurred_at" label="交易时间" />
        <small>{Array.isArray(item.codes) ? item.codes.map(code => explainCode(String(code))).join("、") : "异常依据暂不可用"}</small></div>;
    })}<More count={anomalies.length} /></>;
  }
  if (card.type === "HOLDINGS") {
    const holdings = rows(data, "holdings"), orders = rows(data, "orders");
    return <>{holdings.length ? holdings.slice(0, 5).map((holding, i) => <div className="read-card-entry" key={i}>
      <strong className="read-card-name">{name(holding.product_name, "理财持仓（名称暂不可用）")}</strong>
      <Detail label="可用份额" value={`${decimal(holding.available_units)} 份`} />
      <Detail label="冻结份额" value={`${decimal(holding.reserved_units)} 份`} />
      <Amount data={holding} field="market_value_cents" label="持仓市值" />
    </div>) : <p>暂无持仓</p>}
      {orders.length > 0 && <><p>在途与近期订单 · 待结算款不计入可用余额</p>{orders.slice(0, 5).map((order, i) => <div className="read-card-entry" key={i}>
        <strong className="read-card-name">{name(order.product_name, "理财订单（名称暂不可用）")}</strong>
        <Detail label={known({ SUB: "申购", RED: "赎回" }, order.type, "理财订单")} value={known({ PENDING_CONFIRM: "待确认", PENDING_SETTLEMENT: "待结算", SETTLED: "已结算", FAILED: "失败", EXPIRED: "已过期" }, order.status, "状态待核实")} />
        <Amount data={order} field="amount_cents" label="订单金额" />
        <When data={order} field="trade_date" label="交易日" />
        <When data={order} field="confirm_at" label="确认时间" />
        <When data={order} field="settle_at" label="结算时间" />
      </div>)}</>}
      {holdings.length > 5 || orders.length > 5 ? <p>请到业务页面查看完整持仓与订单。</p> : null}</>;
  }
  const entries = card.type === "ACCOUNT" ? rows(data, "accounts")
    : card.type === "PRODUCTS" && typeof data.id === "string" ? [data] : rows(data, "items");
  const scheduled = card.type === "SUBSCRIPTIONS" ? rows(data, "scheduled_items") : [];
  const items = [...entries.map(item => ({ item, scheduled: false })), ...scheduled.map(item => ({ item, scheduled: true }))];
  if (!items.length) return <p>当前没有可展示的记录。</p>;
  return <>{card.type === "SUBSCRIPTIONS" && <div className="read-card-totals"><Amount data={data} field="charges_total_cents" label="商户扣费合计" /><Amount data={data} field="scheduled_transfers_total_cents" label="定时转账合计" /><Amount data={data} field="reserve_total_cents" label="预留合计" /></div>}
    {items.slice(0, 5).map(({ item, scheduled }, i) => {
      if (card.type === "ACCOUNT") return <div className="read-card-entry" key={i}>
        <strong className="read-card-name">{name(item.display_name, "银行账户（名称暂不可用）")}</strong>
        {text(item.account_no_masked) && <small>{text(item.account_no_masked)}</small>}
        <Amount data={item} field="balance_cents" label="账面余额" /><Amount data={item} field="available_cents" label="可用余额" />
        <Amount data={item} field="pending_debit_cents" label="待扣资金" /><Amount data={item} field="pending_credit_cents" label="待入账资金" />
      </div>;
      if (card.type === "TRANSACTIONS") return <div className="read-card-entry" key={i}>
        <Detail label={name(item.counterparty ?? item.merchant_name, "交易记录")} value={`${known({ IN: "收入", OUT: "支出" }, item.direction, "金额")} ${cash(item.amount_cents)}`} />
        <small>{text(item.category) ?? "未分类"} · {known({ POSTED: "已入账", PENDING: "待入账", SETTLED: "已结算", RELEASED: "已释放" }, item.status, "状态待核实")}</small>
        <When data={item} field="occurred_at" label="交易时间" />
      </div>;
      if (card.type === "CARD") return <div className="read-card-entry" key={i}>
        <Detail label={`银行卡 · 尾号${typeof item.last4 === "string" && /^\d{4}$/.test(item.last4) ? item.last4 : "暂不可用"}`} value={known({ ACTIVE: "正常", FROZEN: "已冻结", LOST: "已挂失" }, item.status, "状态待核实")} />
        <Amount data={item} field="single_limit_cents" label="单笔限额" /><Amount data={item} field="remaining_daily_cents" label="今日剩余额度" />
      </div>;
      if (card.type === "PRODUCTS") return <div className="read-card-entry" key={i}>
        <strong className="read-card-name">{name(item.name, "理财产品（名称暂不可用）")}</strong>
        <Detail label="风险与类型" value={`${typeof item.risk_level === "number" && Number.isInteger(item.risk_level) && item.risk_level >= 1 && item.risk_level <= 5 ? `R${item.risk_level}` : "风险待核实"} · ${productTypeLabel(item.product_type)}`} />
        <Detail label="单位净值" value={decimal(item.nav)} /><When data={item} field="nav_date" label="净值日期" />
        {typeof item.confirm_days === "number" && Number.isSafeInteger(item.confirm_days) && item.confirm_days >= 0 && <Detail label="申购确认" value={`T+${item.confirm_days}`} />}
        {typeof item.settle_days === "number" && Number.isSafeInteger(item.settle_days) && item.settle_days >= 0 && <Detail label="赎回结算" value={`T+${item.settle_days}`} />}
        <Amount data={item} field="min_amount_cents" label="起购金额" />
      </div>;
      if (card.type === "SUBSCRIPTIONS") return <div className="read-card-entry" key={i}>
        <strong className="read-card-name">{name(scheduled ? item.target_display : item.merchant_name, scheduled ? "定时转账（名称暂不可用）" : "周期性扣费（名称暂不可用）")}</strong>
        <Detail label={scheduled ? "定时转账金额" : item.typical_amount_cents !== undefined ? "典型扣费" : known({ MONTHLY: "每月扣费", WEEKLY: "每周扣费", YEARLY: "每年扣费" }, item.period, "本次扣费")} value={cash(item.typical_amount_cents ?? item.amount_cents)} />
        <When data={item} field="next_due_at" label="下次扣费" /><When data={item} field="due_at" label={scheduled ? "转账时间" : "扣费时间"} /><When data={item} field="last_charged_at" label="最近扣费" />
        <Amount data={item} field="annual_cost_cents" label="年化扣费" />{item.is_idle === true && <small>近期无使用记录，可能闲置</small>}
      </div>;
      return null;
    })}<More count={items.length} pending={typeof data.next_cursor === "string" && !!data.next_cursor} /></>;
}
