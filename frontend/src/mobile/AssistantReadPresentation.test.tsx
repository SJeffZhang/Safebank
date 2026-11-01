import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";
import { Assistant } from "./Assistant";
import { request, stream } from "./api";
import type { PageProps } from "./Banking";
import type { UICard } from "./types";

vi.mock("./api", () => ({ isPreview: true, request: vi.fn(), stream: vi.fn(), read: vi.fn(), getPlan: vi.fn() }));
const props = { sid: "ses_read_display", profile: { sim_now: "2026-10-15T01:00:00Z" } as PageProps["profile"], accounts: [], revision: 0, busy: false, notify: vi.fn(), onPropose: vi.fn(), openApproval: vi.fn(), onNewSession: vi.fn(), onResumeRegister: vi.fn() };
const source = "src_verified";
const wrapped = (value: string) => `<untrusted_data source="${source}">${value}</untrusted_data>`;
// Actual old production read-card shapes: source wrappers and missing names are
// preserved here so this suite also protects restored/historical SSE payloads.
const tx = { id: "tx_internal", account_id: "acc_internal", direction: "OUT", kind: "CARD_PAYMENT", status: "POSTED", amount_cents: 2100, balance_after_cents: 1300000, available_after_cents: 1100000, counterparty: wrapped("云盘"), memo: null, category: "数码", merchant_id: "mch_cloud", payee_id: null, card_id: "crd_internal", occurred_at: "2026-10-14T18:13:00Z" };
beforeEach(() => { vi.clearAllMocks(); Element.prototype.scrollIntoView = vi.fn(); vi.mocked(request).mockResolvedValue({ clarification: null }); });
async function show(type: string, data: unknown) {
  const card: UICard = { id: "card_wire", type, source_ref: source, data, actions: [] };
  vi.mocked(stream).mockImplementation(async (_sid, _body, handle) => handle({ event: "custom", data: { type: "cards", cards: [card] } }));
  const user = userEvent.setup(); const view = render(<Assistant {...props} />);
  await user.type(screen.getByRole("textbox"), "查询业务详情"); await user.click(screen.getByRole("button", { name: "发送消息" }));
  return { card: view.container.querySelector<HTMLElement>(".safe-ui-card")!, payload: card, view };
}
it("renders source-matched transaction labels as text with Shanghai time and keeps the wire evidence intact", async () => {
  const data = { items: [tx], next_cursor: null, income_cents: 0, expense_cents: 2100, totals_scope: "filtered_all" }; const original = structuredClone(data);
  const { card, payload } = await show("TRANSACTIONS", data);
  expect(within(card).getByText("云盘")).toBeInTheDocument(); expect(card).toHaveTextContent("支出"); expect(card).toHaveTextContent("21.00");
  expect(card).toHaveTextContent("2026年10月15日 02:13（北京时间）"); expect(card).not.toHaveTextContent("untrusted_data"); expect(card).not.toHaveTextContent(source); expect(card).not.toHaveTextContent("tx_internal");
  expect(payload.source_ref).toBe(source); expect(data).toEqual(original); expect(props.onPropose).not.toHaveBeenCalled();
});
it("does not interpret HTML inside a source-matched display label", async () => {
  const label = '<img src=x onerror="alert(1)">云盘'; const { card } = await show("TRANSACTIONS", { items: [{ ...tx, counterparty: wrapped(label) }] });
  expect(card).toHaveTextContent(label); expect(card.querySelector("img,script,a")).toBeNull(); expect(card).not.toHaveTextContent("untrusted_data");
});
it("preserves ordinary user text containing markup-like fragments rather than globally deleting it", async () => {
  const label = '商户原文 <untrusted_data source="src_other">保留</untrusted_data> 尾注';
  const { card } = await show("TRANSACTIONS", { items: [{ ...tx, counterparty: label }] });
  expect(card).toHaveTextContent(label);
});
it("distinguishes book balance, available funds and pending debits on the actual AccountView", async () => {
  const { card } = await show("ACCOUNT", { accounts: [{ id: "acc_internal", type: "CHECKING", display_name: wrapped("活期账户"), account_no_masked: "****1234", currency: "CNY", balance_cents: 1300000, available_cents: 1100000, pending_debit_cents: 200000, pending_credit_cents: 0, current_card_id: "crd_internal", single_limit_cents: 500000, daily_limit_cents: 2000000 }], daily_outflow_cents: 0, daily_outflow_limit_cents: 2000000, remaining_daily_outflow_cents: 2000000, balance_history: [] });
  expect(card).toHaveTextContent("活期账户"); expect(card).toHaveTextContent("账面余额¥ 13,000.00"); expect(card).toHaveTextContent("可用余额¥ 11,000.00"); expect(card).toHaveTextContent("待扣资金¥ 2,000.00"); expect(card).not.toHaveTextContent("acc_internal"); expect(card).not.toHaveTextContent("untrusted_data");
});
it("does not replace a missing subscription name with an internal instruction ID or mislabel its last charge as its next charge", async () => {
  const { card } = await show("SUBSCRIPTIONS", { items: [{ merchant_id: "mch_gym", instruction_id: "si_zs_gym", billing_id: "bil_gym", period: "MONTHLY", typical_amount_cents: 19900, occurrences: 4, last_charged_at: "2026-09-24T22:00:00Z", is_idle: true, usage_evidence: { recent_usage_count: 0, previous_usage_count: 0 }, annual_cost_cents: 238800 }] });
  expect(card).toHaveTextContent("周期性扣费（名称暂不可用）"); expect(card).toHaveTextContent("典型扣费"); expect(card).toHaveTextContent("199.00"); expect(card).toHaveTextContent("最近扣费：2026年09月25日 06:00（北京时间）"); expect(card).not.toHaveTextContent("下次扣费"); expect(card).not.toHaveTextContent("si_zs_gym"); expect(card).not.toHaveTextContent("mch_gym");
});
it("shows restored merchant names and the actual next due date as text", async () => {
  const { card } = await show("SUBSCRIPTIONS", { items: [{ instruction_id: "si_zs_music", billing_id: "bil_music", account_id: "acc_internal", merchant_id: "mch_music", merchant_name: "音乐会员", period: "MONTHLY", amount_cents: 1500, max_amount_cents: 2000, next_due_at: "2026-10-19T22:00:00Z", status: "ACTIVE", annual_cost_cents: 18000 }] });
  expect(card).toHaveTextContent("音乐会员"); expect(card).toHaveTextContent("下次扣费：2026年10月20日 06:00（北京时间）"); expect(card).toHaveTextContent("每月扣费"); expect(card).toHaveTextContent("15.00"); expect(card).not.toHaveTextContent("si_zs_music");
});
it("keeps scheduled targets, merchant charges and reserved totals separate", async () => {
  const { card } = await show("SUBSCRIPTIONS", { items: [], scheduled_items: [{ instruction_id: "si_save", account_id: "acc_internal", target_display: wrapped("储蓄账户"), due_at: "2026-10-15T22:00:00Z", amount_cents: 10000 }], reserve_total_cents: 14500, charges_total_cents: 4500, scheduled_transfers_total_cents: 10000, by_account: [] });
  expect(card).toHaveTextContent("储蓄账户"); expect(card).toHaveTextContent("商户扣费合计¥ 45.00"); expect(card).toHaveTextContent("定时转账合计¥ 100.00"); expect(card).toHaveTextContent("预留合计¥ 145.00"); expect(card).not.toHaveTextContent("si_save"); expect(card).not.toHaveTextContent("untrusted_data");
});
it("renders the nested anomaly wire time in Shanghai and retains deterministic risk explanations", async () => {
  const { card } = await show("ANOMALIES", { items: [{ transaction: tx, codes: ["ANOMALY_NIGHT_LARGE"], evidence: { baseline_count: 5, mean_cents: 1000, threshold_cents: 10000, previous_transaction_id: null, window_minutes: null } }] });
  expect(card).toHaveTextContent("云盘"); expect(card).toHaveTextContent("2026年10月15日 02:13（北京时间）"); expect(card).toHaveTextContent("深夜"); expect(card).not.toHaveTextContent("2026-10-14T"); expect(card).not.toHaveTextContent("untrusted_data");
});
it("shows product facts with a human placeholder when the old projection has removed its name", async () => {
  const { card } = await show("PRODUCTS", { id: "prd_bond", product_type: "BOND", risk_level: 2, risk_label: "稳健", nav: "1.0520", nav_date: "2026-10-15", recent_return_bp: 12, confirm_days: 1, settle_days: 2, min_amount_cents: 10000, risk_disclosure: wrapped("不保证本金及收益"), disclosure_version: "v1", nav_history: [] });
  expect(card).toHaveTextContent("理财产品（名称暂不可用）"); expect(card).not.toHaveTextContent("prd_bond"); expect(card).toHaveTextContent("1.0520"); expect(card).toHaveTextContent("2026年10月15日"); expect(card).toHaveTextContent("T+1"); expect(card).toHaveTextContent("T+2");
});
it("does not treat pending orders as owned units or spendable cash and preserves a date-only Shanghai trade day", async () => {
  const { card } = await show("HOLDINGS", { holdings: [{ account_id: "acc_internal", product_id: "prd_mmf", units: "9000.0000", reserved_units: "100.0000", available_units: "8900.0000", cost_cents: 900000, reserved_cost_cents: 10000, available_cost_cents: 890000, nav: "1.0000", market_value_cents: 900000, unrealized_change_cents: 0, reserved_receivable_cents: 0 }], orders: [{ id: "ord_pending", account_id: "acc_internal", product_id: "prd_bond", type: "SUB", status: "PENDING_CONFIRM", amount_cents: 200000, units: "0.0000", nav: null, trade_date: "2026-10-15", confirm_at: "2026-10-15T16:00:00Z", settle_at: null, failure_code: null }] });
  expect(card).toHaveTextContent("可用份额8900.0000 份"); expect(card).toHaveTextContent("冻结份额100.0000 份"); expect(card).toHaveTextContent("待确认"); expect(card).toHaveTextContent("交易日：2026年10月15日"); expect(card).toHaveTextContent("待结算款不计入可用余额"); expect(card).not.toHaveTextContent("prd_"); expect(within(card).queryByRole("button", { name: /申购|赎回/ })).toBeNull();
});
it("renders assistant Markdown tables as safe semantic tables without creating links or HTML", async () => {
  const reply = '9月与8月消费占比：\n\n| 分类 | 9月 | 8月 |\n| --- | ---: | ---: |\n| 餐饮 | 13.03% | 24.03% |\n| <img src=x onerror="alert(1)"> | **54.59%** | [查看](javascript:alert(1)) |\n\n主要变化：数码占比上升。';
  vi.mocked(stream).mockImplementation(async (_sid, _body, handle) => handle({ event: "updates", data: { reply: { id: "reply_table", content: reply } } }));
  const user = userEvent.setup(); const view = render(<Assistant {...props} />); await user.type(screen.getByRole("textbox"), "查询业务详情"); await user.click(screen.getByRole("button", { name: "发送消息" }));
  const table = screen.getByRole("table"); expect(within(table).getByRole("columnheader", { name: "分类" })).toBeInTheDocument(); expect(within(table).getByRole("cell", { name: "13.03%" })).toBeInTheDocument(); expect(table).not.toHaveTextContent("---"); expect(table).toHaveTextContent('<img src=x onerror="alert(1)">'); expect(view.container.querySelector(".chat-message.assistant img,.chat-message.assistant a,.chat-message.assistant script")).toBeNull(); expect(screen.getByText("主要变化：数码占比上升。")).toBeInTheDocument();
});
it("leaves malformed tables and user-authored Markdown as literal text", async () => {
  const text = "| 分类 | 占比 |\n| ----- |\n| 餐饮 | 13% |";
  vi.mocked(stream).mockImplementation(async (_sid, _body, handle) => handle({ event: "updates", data: { reply: { id: "reply_literal", content: text } } }));
  const user = userEvent.setup(); render(<Assistant {...props} />); await user.type(screen.getByRole("textbox"), "**我的文字**"); await user.click(screen.getByRole("button", { name: "发送消息" }));
  expect(screen.queryByRole("table")).toBeNull(); expect(screen.getByText("**我的文字**")).toBeInTheDocument(); expect(screen.getByText((_, element) => element?.tagName === "P" && element.textContent === text)).toBeInTheDocument();
});

it("does not normalize an invalid timestamp into an invented bank day", async () => {
  const { card } = await show("ANOMALIES", { items: [{ transaction: { ...tx, occurred_at: "2026-02-30T18:13:00Z" }, codes: [], evidence: {} }] });
  expect(card).toHaveTextContent("日期暂不可用"); expect(card).not.toHaveTextContent("03月03日");
});
it("retains real read-linked fund names and Decimal precision while missing money stays unknown", async () => {
  const { card } = await show("HOLDINGS", { holdings: [{ account_id: "acc_internal", product_id: "prd_mmf", product_name: "安心货币", product_name_source_ref: "src_products", available_units: "9007199254740993.0000", reserved_units: "0.0000" }], orders: [] });
  expect(card).toHaveTextContent("安心货币"); expect(card).toHaveTextContent("9007199254740993.0000 份"); expect(card).not.toHaveTextContent("9007199254740992"); expect(card).not.toHaveTextContent("¥ 0.00"); expect(card).not.toHaveTextContent("src_products"); expect(card).not.toHaveTextContent("prd_mmf");
});
it("does not invent zero balances when an account amount is absent or not integer cents", async () => {
  const { card } = await show("ACCOUNT", { accounts: [{ id: "acc_internal", display_name: "活期账户", balance_cents: "1300000", available_cents: 1100000.5 }] });
  expect(card).toHaveTextContent("活期账户"); expect(card).not.toHaveTextContent("13,000.00"); expect(card).not.toHaveTextContent("11,000.00"); expect(card).not.toHaveTextContent("0.00");
});
