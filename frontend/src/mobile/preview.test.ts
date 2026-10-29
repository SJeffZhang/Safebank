import { beforeEach, expect, it, vi } from "vitest";
import type {
  Fields,
  Decision,
  PlanView,
  Account,
  BankCard,
  Split,
  Subscription,
} from "./types";
let request: typeof import("./preview").previewRequest;
beforeEach(async () => {
  vi.resetModules();
  request = (await import("./preview")).previewRequest;
});
async function read<T>(tool: string, args: Fields = {}): Promise<T> {
  return ((await request(`/tools/read/${tool}`, { args })) as { data: T }).data;
}
async function action(kind: string, fields: Fields) {
  const a = (await request("/sessions/ses_test/actions", {
    action_kind: kind,
    fields,
  })) as { action_id: string };
  const d = (await request("/plans/evaluate", {
    form_action_ids: [a.action_id],
  })) as Decision;
  if (d.approval_id) {
    const v = (await request(`/approvals/${d.approval_id}`)) as PlanView;
    await request(`/approvals/${d.approval_id}/confirm`, {
      view_hash: v.view_hash,
      risk_ack: true,
      otp: "123456",
    });
  }
  expect(d.decision).not.toBe("DENY");
  await request(`/plans/${d.plan_id}/execute`, {});
  return d;
}
it("scheduling creates an arrangement without debiting available funds", async () => {
  const { accounts } = await read<{ accounts: Account[] }>("get_accounts");
  const d = await action("SCHEDULE_CREATE", {
    from_account_id: accounts[0].id,
    payee_id: "pye_ww",
    to_account_id: null,
    amount_cents: 10000,
    period: "MONTHLY",
    first_run_at: "2026-11-01T09:00:00+08:00",
    end_at: null,
  });
  expect(d.preflight?.balances[0].available_after_cents).toBe(
    accounts[0].available_cents,
  );
  const after = await read<{ accounts: Account[] }>("get_accounts");
  expect(after.accounts[0].available_cents).toBe(accounts[0].available_cents);
  const list = await read<{ items: { amount_cents: number }[] }>(
    "list_scheduled",
    { status: "ACTIVE" },
  );
  expect(list.items[0].amount_cents).toBe(10000);
});
it("AA allocates integer remainders to initiator and excludes self share from receivable", async () => {
  await action("SPLIT_CREATE", {
    account_id: "acc_zs_current",
    total_cents: 10000,
    participant_payee_ids: ["pye_ww", "pye_zl"],
    include_self: true,
    memo: null,
  });
  const { items } = await read<{ items: Split[] }>("get_split_status");
  expect(items[0].self_share_cents).toBe(3334);
  expect(items[0].items.map((i) => i.amount_cents)).toEqual([3333, 3333]);
  expect(items[0].outstanding_cents).toBe(6666);
});
it("cancelled subscriptions disappear from future reserves and active totals", async () => {
  await action("MANDATE_CANCEL", { instruction_id: "si_music" });
  const list = await read<{
    items: Subscription[];
    total_monthly_cents: number;
  }>("list_subscriptions", { status: "ACTIVE" });
  expect(list.items.some((s) => s.instruction_id === "si_music")).toBe(false);
  const charges = await read<{ reserve_total_cents: number }>(
    "upcoming_charges",
    { days: 7 },
  );
  expect(charges.reserve_total_cents).toBe(2700);
});
it("immediate money market purchase creates four decimal units and debits once", async () => {
  await action("FUND_SUBSCRIBE", {
    account_id: "acc_zs_current",
    product_id: "prd_cash",
    amount_cents: 10000,
  });
  const { holdings, orders } = await read<{
    holdings: { product_id: string; units: string; market_value_cents: number }[];
    orders: { status: string }[];
  }>("get_holdings");
  expect(holdings.find(h=>h.product_id==="prd_cash")!.units).toBe("100.0000");
  expect(holdings.find(h=>h.product_id==="prd_cash")!.market_value_cents).toBe(10000);
  expect(orders[0].status).toBe("CONFIRMED");
  const { accounts } = await read<{ accounts: Account[] }>("get_accounts");
  expect(accounts[0].available_cents).toBe(590000);
});
it("pending bond purchase reserves funds without reducing booked balance", async () => {
  await action("FUND_SUBSCRIBE", {
    account_id: "acc_zs_current",
    product_id: "prd_bond",
    amount_cents: 10000,
  });
  const { accounts } = await read<{ accounts: Account[] }>("get_accounts");
  expect(accounts[0].balance_cents).toBe(600000);
  expect(accounts[0].available_cents).toBe(590000);
  expect(accounts[0].pending_debit_cents).toBe(10000);
  expect(
    (await read<{ holdings: { product_id: string }[] }>("get_holdings")).holdings.some(h=>h.product_id==="prd_bond"),
  ).toBe(false);
});
it("lost card replacement preserves limits and resets channel defaults", async () => {
  await action("CARD_LOST", { card_id: "crd_zs_1", replace: true });
  const { items } = await read<{ items: BankCard[] }>("get_cards");
  expect(items.find((c) => c.id === "crd_zs_1")?.status).toBe("LOST");
  const current = items.find((c) => c.current && c.account_id === "acc_zs_current")!;
  expect(current.id).not.toBe("crd_zs_1");
  expect(await request(`/cards/${current.id}/number`, { session_id: "ses_test", pin: "123456" })).toEqual({
    card_id: current.id, card_number: "6222000000000000928", synthetic: true,
  });
  expect(current.single_limit_cents).toBe(500000);
  expect(current.controls).toEqual({
    online: true,
    overseas: false,
    atm: false,
    quickpass: false,
  });
});

it("full synthetic numbers are explicit and separate from the assistant card read", async () => {
  const cards = await read<{ items: BankCard[] }>("get_cards");
  expect(JSON.stringify(cards)).not.toContain("6222000000000000818");
  expect(await request("/cards/crd_zs_1/number", { session_id: "ses_test", pin: "123456" })).toEqual({
    card_id: "crd_zs_1", card_number: "6222000000000000818", synthetic: true,
  });
  await expect(request("/cards/crd_missing/number", {})).rejects.toThrow();
});

it("card number PIN rejects missing/wrong PIN and locks even correct PIN until cooldown", async () => {
  const path = "/cards/crd_zs_1/number";
  await expect(request(path, { session_id: "ses_pin" })).rejects.toThrow("PIN 不正确");
  for (let i = 0; i < 3; i++) {
    await expect(request(path, { session_id: "ses_pin", pin: "000000" })).rejects.toThrow("PIN 不正确");
  }
  await expect(request(path, { session_id: "ses_pin", pin: "000000" })).rejects.toThrow("1 分钟");
  await expect(request(path, { session_id: "ses_pin", pin: "123456" })).rejects.toThrow("1 分钟");
  const clock = vi.spyOn(Date, "now").mockReturnValue(Date.now() + 61000);
  try {
    expect(await request(path, { session_id: "ses_pin", pin: "123456" })).toMatchObject({card_number:"6222000000000000818"});
  } finally { clock.mockRestore(); }
});

it("multi-card preview has separate accounts, full-number fixtures and controls", async () => {
  const { items } = await read<{ items: BankCard[] }>("get_cards");
  const { accounts } = await read<{ accounts: Account[] }>("get_accounts");
  expect(items).toHaveLength(3);
  expect(new Set(items.map(c => c.account_id)).size).toBe(3);
  for (const card of items) {
    expect(accounts.find(a => a.id === card.account_id)?.current_card_id).toBe(card.id);
    const result = await request(`/cards/${card.id}/number`, { session_id: "ses_test", pin: "123456" }) as { card_number: string };
    expect(result.card_number.endsWith(card.last4)).toBe(true);
  }
  await action("CARD_FREEZE", {card_id:"crd_zs_2"});
  const updated = await read<{ items: BankCard[] }>("get_cards");
  expect(updated.items.find(c => c.id === "crd_zs_2")?.status).toBe("FROZEN");
  expect(updated.items.find(c => c.id === "crd_zs_1")?.status).toBe("ACTIVE");
});

it("expanded synthetic catalogue covers R1 to R5 and seeded redemption reserves receivables", async () => {
  const {items} = await read<{items:{id:string;risk_level:number}[]}>("list_products");
  expect(items).toHaveLength(8);
  expect([...new Set(items.map(p=>p.risk_level))].sort()).toEqual([1,2,3,4,5]);
  const before=await read<{accounts:Account[]}>("get_accounts");
  await action("FUND_REDEEM",{account_id:"acc_zs_current",product_id:"prd_flex",units:"1000.0000"});
  const after=await read<{accounts:Account[]}>("get_accounts");
  expect(after.accounts[0].available_cents).toBe(before.accounts[0].available_cents);
  const result=await read<{holdings:{product_id:string;available_units:string;reserved_receivable_cents:number}[]}>("get_holdings");
  expect(result.holdings.find(h=>h.product_id==="prd_flex")).toMatchObject({available_units:"0.0000",reserved_receivable_cents:100000});
});
it("a confirmed synthetic card application creates an independent zero-balance account and a new card", async () => {
  const before=await read<{items:BankCard[]}>("get_cards");
  await action("CARD_APPLY",{purpose:"工资收款"});
  const after=await read<{items:BankCard[]}>("get_cards");
  expect(after.items).toHaveLength(before.items.length+1);
  const added=after.items.find(card=>!before.items.some(old=>old.id===card.id))!;
  const {accounts}=await read<{accounts:Account[]}>("get_accounts");
  expect(accounts.find(account=>account.id===added.account_id)).toMatchObject({balance_cents:0,available_cents:0,current_card_id:added.id});
  expect(added.controls).toEqual({online:false,overseas:false,atm:true,quickpass:false});
  expect(await request(`/cards/${added.id}/number`,{session_id:"ses_test",pin:"123456"})).toMatchObject({card_id:added.id,synthetic:true});
});
