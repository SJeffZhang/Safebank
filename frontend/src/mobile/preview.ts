/** Isolated synthetic preview adapter. Never calls banking services or claims real settlement. */
import type {
  Account,
  BankCard,
  Payee,
  Product,
  Subscription,
  Transaction,
  Decision,
  PlanView,
  PlanStatus,
  Fields,
  StreamEvent,
  Scheduled,
  Split,
} from "./types";
const stamp = "2026-10-28T10:00:00+08:00";
const accounts: Account[] = [
  {
    id: "acc_zs_current",
    type: "CURRENT",
    display_name: "日常活期",
    account_no_masked: "6222 **** 0818",
    balance_cents: 600000,
    available_cents: 600000,
    pending_debit_cents: 0,
    pending_credit_cents: 0,
    current_card_id: "crd_zs_1",
  },
  {
    id: "acc_zs_savings",
    type: "SAVINGS",
    display_name: "安心储蓄",
    account_no_masked: "6222 **** 0926",
    balance_cents: 2000000,
    available_cents: 2000000,
    pending_debit_cents: 0,
    pending_credit_cents: 0,
    current_card_id: null,
  },
  {
    id: "acc_zs_travel", type: "CURRENT", display_name: "出行备用",
    account_no_masked: "6222 **** 6612", balance_cents: 350000,
    available_cents: 350000, pending_debit_cents: 0, pending_credit_cents: 0,
    current_card_id: "crd_zs_2",
  },
  {
    id: "acc_zs_life", type: "CURRENT", display_name: "生活消费",
    account_no_masked: "6222 **** 3906", balance_cents: 128000,
    available_cents: 128000, pending_debit_cents: 0, pending_credit_cents: 0,
    current_card_id: "crd_zs_3",
  },
];
const payees: Payee[] = [
  {
    id: "pye_ww",
    name: "王五",
    bank_name: "SafeBank",
    account_no_masked: "**** 8891",
    has_transfer_history: true,
    alias: "房租",
  },
  {
    id: "pye_zl",
    name: "赵六",
    bank_name: "招商银行",
    account_no_masked: "**** 6608",
    has_transfer_history: true,
    alias: null,
  },
  {
    id: "pye_ls",
    name: "李四",
    bank_name: "SafeBank",
    account_no_masked: "**** 1026",
    has_transfer_history: false,
    alias: null,
  },
];
const cards: BankCard[] = [
  {
    id: "crd_zs_1",
    account_id: accounts[0].id,
    last4: "0818",
    status: "ACTIVE",
    single_limit_cents: 500000,
    daily_limit_cents: 2000000,
    daily_used_cents: 128000,
    remaining_daily_cents: 1872000,
    controls: { online: true, overseas: false, atm: true, quickpass: false },
    current: true,
  },
  {
    id: "crd_zs_2", account_id: "acc_zs_travel", last4: "6612", status: "ACTIVE",
    single_limit_cents: 200000, daily_limit_cents: 800000,
    daily_used_cents: 24000, remaining_daily_cents: 776000,
    controls: { online: false, overseas: true, atm: true, quickpass: false }, current: true,
  },
  {
    id: "crd_zs_3", account_id: "acc_zs_life", last4: "3906", status: "FROZEN",
    single_limit_cents: 100000, daily_limit_cents: 300000,
    daily_used_cents: 8600, remaining_daily_cents: 291400,
    controls: { online: true, overseas: false, atm: false, quickpass: true }, current: true,
  },
];
// Explicit fictional PAN fixtures; not reconstructed from account or card suffixes.
const cardPinAttempts = new Map<string, { failures: number; lockedUntil: number }>();
const cardNumbers=new Map<string,string>([['crd_zs_1','6222000000000000818'], ['crd_zs_2','6222000000000006612'], ['crd_zs_3','6222000000000003906']]);
const subscriptions: Subscription[] = [
  {
    instruction_id: "si_music",
    account_id: accounts[0].id,
    merchant_name: "云音乐",
    period: "MONTHLY",
    amount_cents: 1800,
    annual_cost_cents: 21600,
    next_due_at: "2026-10-30T06:00:00+08:00",
    status: "ACTIVE",
  },
  {
    instruction_id: "si_video",
    account_id: accounts[0].id,
    merchant_name: "悦享视频",
    period: "MONTHLY",
    amount_cents: 2700,
    annual_cost_cents: 32400,
    next_due_at: "2026-11-02T06:00:00+08:00",
    status: "ACTIVE",
  },
  {
    instruction_id: "si_gym",
    account_id: accounts[0].id,
    merchant_name: "力量健身",
    period: "MONTHLY",
    amount_cents: 19900,
    annual_cost_cents: 238800,
    next_due_at: "2026-11-10T06:00:00+08:00",
    status: "ACTIVE",
  },
];
const products: Product[] = [
  {
    id: "prd_cash",
    name: "日日盈货币",
    product_type: "MONEY_MARKET",
    risk_level: 1,
    risk_label: "保守",
    nav: "1.0000",
    nav_date: "2026-10-28",
    recent_return_bp: 16,
    confirm_days: 0,
    settle_days: 1,
    min_amount_cents: 100,
    risk_disclosure:
      "本产品不保证本金及收益，历史表现不代表未来收益。请结合您的流动性需求与风险承受能力作出决定。",
    disclosure_version: "v1",
    nav_history: [],
  },
  {
    id: "prd_bond",
    name: "稳盈短债",
    product_type: "BOND",
    risk_level: 2,
    risk_label: "稳健",
    nav: "1.0528",
    nav_date: "2026-10-28",
    recent_return_bp: 32,
    confirm_days: 1,
    settle_days: 2,
    min_amount_cents: 10000,
    risk_disclosure:
      "净值可能波动，存在本金损失风险。申购金额将先冻结，确认后形成份额；赎回资金需等待结算。",
    disclosure_version: "v1",
    nav_history: [],
  },
  {
    id: "prd_mix",
    name: "均衡混合",
    product_type: "MIXED",
    risk_level: 3,
    risk_label: "平衡",
    nav: "1.1872",
    nav_date: "2026-10-28",
    recent_return_bp: -24,
    confirm_days: 1,
    settle_days: 3,
    min_amount_cents: 100000,
    risk_disclosure:
      "本产品具有市场风险与净值波动风险，可能产生本金损失，不承诺收益。",
    disclosure_version: "v1",
    nav_history: [],
  },
  {
    id:"prd_flex",name:"安心活钱",product_type:"MONEY_MARKET",risk_level:1,risk_label:"保守",
    nav:"1.0000",nav_date:"2026-10-28",recent_return_bp:12,confirm_days:0,settle_days:1,min_amount_cents:100,
    risk_disclosure:"合成货币基金示例，仍存在信用及流动性风险，不保证本金或收益。赎回需等待结算。",disclosure_version:"v1",nav_history:[],
  },
  {
    id:"prd_bond_plus",name:"稳健增利债券",product_type:"BOND",risk_level:2,risk_label:"稳健",
    nav:"1.0750",nav_date:"2026-10-28",recent_return_bp:28,confirm_days:1,settle_days:2,min_amount_cents:10000,
    risk_disclosure:"合成债券基金示例，净值受利率与信用变化影响，可能发生本金损失，赎回T+2到账。",disclosure_version:"v1",nav_history:[],
  },
  {
    id:"prd_stable",name:"稳享中短债",product_type:"BOND",risk_level:2,risk_label:"稳健",
    nav:"1.0320",nav_date:"2026-10-28",recent_return_bp:-8,confirm_days:1,settle_days:3,min_amount_cents:100000,
    risk_disclosure:"合成中短债示例，存在利率、信用及净值波动风险，短期可能亏损，赎回T+3到账。",disclosure_version:"v1",nav_history:[],
  },
  {
    id:"prd_growth",name:"成长优选混合",product_type:"MIXED",risk_level:4,risk_label:"进取",
    nav:"1.2810",nav_date:"2026-10-28",recent_return_bp:156,confirm_days:2,settle_days:4,min_amount_cents:100000,
    risk_disclosure:"合成权益混合产品，市场波动较大，可能发生较大本金损失，仅适用于风险承受能力匹配的客户。",disclosure_version:"v1",nav_history:[],
  },
  {
    id:"prd_opportunity",name:"创新机遇混合",product_type:"MIXED",risk_level:5,risk_label:"激进",
    nav:"0.9680",nav_date:"2026-10-28",recent_return_bp:-213,confirm_days:2,settle_days:5,min_amount_cents:100000,
    risk_disclosure:"合成高风险混合产品，集中度及波动风险较高，可能发生重大本金损失，赎回资金不即时可用。",disclosure_version:"v1",nav_history:[],
  },
];
const tx: Transaction[] = [
  ["tx_1", "城市便利店", 3680, "购物", "2026-10-28T08:34:00+08:00"],
  ["tx_2", "青禾餐厅", 8600, "餐饮", "2026-10-27T12:10:00+08:00"],
  ["tx_3", "地铁出行", 600, "交通", "2026-10-27T08:20:00+08:00"],
  ["tx_4", "云音乐", 1800, "娱乐", "2026-10-26T06:00:00+08:00"],
  ["tx_5", "星光数码", 328000, "数码", "2026-10-25T02:13:00+08:00"],
  ["tx_6", "青禾餐厅", 12800, "餐饮", "2026-10-24T19:30:00+08:00"],
  ["tx_7", "工资入账", 1200000, "收入", "2026-10-23T09:00:00+08:00"],
  ["tx_history_1", "工资入账", 1200000, "收入", "2026-09-23T09:00:00+08:00"],
  ["tx_history_2", "日常采购", 156000, "购物", "2026-09-18T18:30:00+08:00"],
  ["tx_history_3", "便民服务", 25000, "其他", "2026-09-12T12:00:00+08:00"],
  ["tx_history_4", "工资入账", 1200000, "收入", "2026-08-23T09:00:00+08:00"],
  ["tx_history_5", "城市交通", 32000, "交通", "2026-08-15T12:00:00+08:00"],
  ["tx_history_6", "家庭聚餐", 88000, "餐饮", "2026-08-10T19:00:00+08:00"],
  ["tx_history_7", "工资入账", 1200000, "收入", "2026-07-23T09:00:00+08:00"],
  ["tx_history_8", "生活采购", 205000, "购物", "2026-07-16T14:00:00+08:00"],
].map(([id, name, amount, category, date]) => ({
  id: String(id),
  account_id: accounts[0].id,
  direction: category === "收入" ? "IN" : "OUT",
  kind: category === "收入" ? "SALARY" : "CARD_SPEND",
  status: "POSTED",
  amount_cents: Number(amount),
  counterparty: String(name),
  memo: null,
  category: String(category),
  occurred_at: String(date),
  card_id: "crd_zs_1",
}));
let riskLevel = 2;
const schedules: Scheduled[] = [];
const splits: Split[] = [];
const holdings: {
  account_id: string;
  product_id: string;
  units: string;
  available_units: string;
  market_value_cents: number;
  unrealized_change_cents: number;
  reserved_receivable_cents: number;
}[] = [
  {account_id:"acc_zs_current",product_id:"prd_flex",units:"1000.0000",available_units:"1000.0000",market_value_cents:100000,unrealized_change_cents:0,reserved_receivable_cents:0},
  {account_id:"acc_zs_savings",product_id:"prd_bond_plus",units:"800.0000",available_units:"800.0000",market_value_cents:86000,unrealized_change_cents:6000,reserved_receivable_cents:0},
];
const orders: {
  id: string;
  product_id: string;
  type: string;
  status: string;
  amount_cents: number;
  units: string;
  confirm_at: string | null;
  settle_at: string | null;
}[] = [];
const activeUpcoming = (days = 7) =>
  subscriptions.filter(
    (s) =>
      s.status === "ACTIVE" &&
      Date.parse(s.next_due_at) > Date.parse(stamp) &&
      Date.parse(s.next_due_at) <= Date.parse(stamp) + days * 86400000,
  );
const reserve = () => activeUpcoming().reduce((n, s) => n + s.amount_cents, 0);
const decimal4 = (raw: bigint) =>
  `${raw / 10000n}.${String(raw % 10000n).padStart(4, "0")}`;
const raw4 = (value: string) => {
  const [a, b = ""] = value.split(".");
  return BigInt(a) * 10000n + BigInt(b.padEnd(4, "0"));
};
const actions = new Map<
  string,
  { kind: string; fields: Fields; sid: string }
>();
const plans = new Map<
  string,
  {
    decision: Decision;
    view: PlanView | null;
    action: { kind: string; fields: Fields; sid: string };
    status: string;
  }
>();
let sequence = 0;
const id = (p: string) => `${p}_preview_${++sequence}`;
const error = (message: string) => {
  throw new Error(message);
};
const copy = <T>(v: T): T => structuredClone(v);
const fieldLabels: Record<string, string> = {
  purpose: "卡片用途",
  from_account_id: "付款账户",
  account_id: "账户",
  payee_id: "收款人",
  to_account_id: "本人收款账户",
  amount_cents: "金额",
  memo: "备注",
  single_limit_cents: "单笔限额",
  daily_limit_cents: "日累计限额",
  card_id: "银行卡",
  instruction_id: "订阅 / 定时任务",
  product_id: "理财产品",
  units: "赎回份额",
  period: "周期",
  first_run_at: "首次执行",
  end_at: "结束时间",
  replace: "是否补卡",
  online: "线上支付",
  overseas: "境外支付",
  atm: "ATM",
  quickpass: "小额免密",
  total_cents: "AA 总额",
  participant_payee_ids: "参与人",
  include_self: "包含本人",
  questionnaire_id: "问卷版本",
  answers: "作答",
};
const titles: Record<string, string> = {
  TRANSFER_EXTERNAL: "向他人转账",
  TRANSFER_SELF: "本人账户互转",
  PAYEE_ADD: "新增收款人",
  SCHEDULE_CREATE: "创建定时转账",
  SCHEDULE_CANCEL: "取消定时转账",
  SPLIT_CREATE: "发起 AA 收款",
  CARD_LIMIT_TIGHTEN: "调低银行卡限额",
  CARD_LIMIT_LOOSEN: "调高银行卡限额",
  CARD_CONTROL_TIGHTEN: "关闭支付渠道",
  CARD_CONTROL_LOOSEN: "开启支付渠道",
  CARD_FREEZE: "临时冻结银行卡",
  CARD_UNFREEZE: "解冻银行卡",
  CARD_APPLY: "申请合成演示借记卡",
  CARD_LOST: "挂失银行卡",
  MANDATE_CANCEL: "取消订阅授权",
  ASSESSMENT_SUBMIT: "提交风险测评",
  FUND_SUBSCRIBE: "申购理财产品",
  FUND_REDEEM: "赎回理财份额",
};
function display(key: string, value: unknown): unknown {
  if (key === "card_id")
    return `SafeBank 借记卡 · ${cards.find((c) => c.id === value)?.last4 ?? value}`;
  if (["account_id", "from_account_id", "to_account_id"].includes(key)) {
    const a = accounts.find((a) => a.id === value);
    return a ? `${a.display_name} · ${a.account_no_masked}` : value;
  }
  if (key === "payee_id") {
    const p = payees.find((p) => p.id === value);
    return p ? `${p.name} · ${p.bank_name} · ${p.account_no_masked}` : value;
  }
  if (key === "product_id")
    return products.find((p) => p.id === value)?.name ?? value;
  if (key === "participant_payee_ids" && Array.isArray(value))
    return value
      .map((id) => payees.find((p) => p.id === id)?.name ?? id)
      .join("、");
  if (key === "instruction_id")
    return (
      subscriptions.find((s) => s.instruction_id === value)?.merchant_name ??
      schedules.find((s) => s.id === value)?.target_display ??
      value
    );
  if (key === "period")
    return (
      (
        { ONCE: "仅一次", MONTHLY: "每月", WEEKLY: "每周" } as Record<
          string,
          string
        >
      )[String(value)] ?? value
    );
  if (key === "answers") return "6 题已完成（等级由服务端计算）";
  return value;
}
function evaluate(action: {
  kind: string;
  fields: Fields;
  sid: string;
}): Decision {
  const { kind, fields: f, sid } = action;
  const plan_id = id("pln");
  const source = accounts.find(
    (a) => a.id === (f.from_account_id ?? f.account_id),
  );
  const amount = /TRANSFER_EXTERNAL|TRANSFER_SELF|FUND_SUBSCRIBE/.test(kind)
    ? Number(f.amount_cents ?? 0)
    : 0;
  const denied = !!source && amount > source.available_cents;
  const unsuitable =
    kind === "FUND_SUBSCRIBE" &&
    (products.find((p) => p.id === f.product_id)?.risk_level ?? 0) > riskLevel;
  const risk: Decision["risk"] =
    /LOOSEN|UNFREEZE|LOST|CARD_APPLY|FUND_SUBSCRIBE/.test(kind) ||
    (kind === "TRANSFER_EXTERNAL" &&
      (amount > 500000 ||
        !payees.find((p) => p.id === f.payee_id)?.has_transfer_history))
      ? "RED"
      : /FREEZE|SCHEDULE_CANCEL|SPLIT_CREATE/.test(kind)
        ? "GREEN"
        : "YELLOW";
  const approval_id =
    denied || unsuitable || risk === "GREEN" ? null : id("apv");
  const preflight = {
    executable: !denied && !unsuitable,
    balances: source
      ? [
          {
            account_id: source.id,
            balance_before_cents: source.balance_cents,
            balance_after_cents: source.balance_cents - amount,
            available_before_cents: source.available_cents,
            available_after_cents: source.available_cents - amount,
            daily_used_before_cents: 128000,
            daily_used_after_cents: 128000 + amount,
          },
        ]
      : [],
    scheduled_reserve_cents: reserve(),
    steps: [],
    holdings: [],
  };
  const decision: Decision = {
    plan_id,
    evaluation_id: id("evl"),
    decision:
      denied || unsuitable
        ? "DENY"
        : risk === "GREEN"
          ? "ALLOW"
          : "REQUIRE_APPROVAL",
    risk,
    approval_id,
    findings: denied
      ? [{ code: "INSUFFICIENT_FUNDS", message: "可用余额不足，请调整金额。" }]
      : unsuitable
        ? [
            {
              code: "SUITABILITY_MISMATCH",
              message: "产品风险高于当前测评等级。",
            },
          ]
        : risk === "RED"
          ? [
              {
                code: "HIGH_RISK_OPERATION",
                message: "本次操作需要短信验证码。",
              },
            ]
          : [],
    preflight,
    replan_hints: [],
  };
  const view: PlanView | null = approval_id
    ? {
        approval_id,
        plan_id,
        evaluation_id: decision.evaluation_id,
        session_id: sid,
        status: "PENDING_APPROVAL",
        origin: "app_form",
        risk,
        steps: [
          {
            step_id: "step_1",
            action_kind: kind,
            title: titles[kind] ?? kind,
            fields: Object.entries(f)
              .filter(([, v]) => v !== null)
              .map(([key, v]) => ({
                key,
                label: fieldLabels[key] ?? key,
                value: display(key, v),
                unit: key.endsWith("_cents") ? "cents" : null,
              })),
            irreversible: kind === "CARD_LOST",
          },
        ],
        preflight,
        findings: decision.findings,
        disclosures:
          kind === "FUND_SUBSCRIBE"
            ? [
                {
                  product_id: String(f.product_id),
                  version: "v1",
                  text: products.find((p) => p.id === f.product_id)!
                    .risk_disclosure,
                },
              ]
            : [],
        risk_ack_required: kind === "FUND_SUBSCRIBE",
        otp_required: risk === "RED",
        expires_at_wall: new Date(Date.now() + 300000).toISOString(),
        view_hash: id("hash"),
      }
    : null;
  plans.set(plan_id, {
    decision,
    view,
    action,
    status: approval_id
      ? "PENDING_APPROVAL"
      : denied || unsuitable
        ? "DENIED"
        : "EVALUATED",
  });
  return decision;
}
function execute(planId: string) {
  const p = plans.get(planId) ?? error("计划不存在");
  if (p.status === "EXECUTED") return;
  if (p.view?.status !== "CONFIRMED" && p.decision.decision !== "ALLOW")
    error("请先确认操作");
  const f = p.action.fields;
  const kind = p.action.kind;
  if (kind.startsWith("TRANSFER")) {
    const a = accounts.find((a) => a.id === f.from_account_id)!;
    const amount = Number(f.amount_cents);
    if (a.available_cents < amount) error("余额发生变化，请重新发起");
    a.available_cents -= amount;
    a.balance_cents -= amount;
    const target = accounts.find((a) => a.id === f.to_account_id);
    if (target) {
      target.available_cents += amount;
      target.balance_cents += amount;
    }
    tx.unshift({
      id: id("tx"),
      account_id: a.id,
      direction: "OUT",
      kind: target ? "SELF_TRANSFER" : "P2P_EXTERNAL",
      status: "POSTED",
      amount_cents: amount,
      counterparty:
        target?.display_name ??
        payees.find((p) => p.id === f.payee_id)?.name ??
        "",
      memo: String(f.memo ?? ""),
      category: "转账",
      occurred_at: stamp,
      card_id: a.current_card_id,
    });
  }
  const card = cards.find((c) => c.id === f.card_id);
  if (card) {
    if (kind === "CARD_FREEZE") card.status = "FROZEN";
    if (kind === "CARD_UNFREEZE") card.status = "ACTIVE";
    if (kind === "CARD_LOST") card.status = "LOST";
    if (kind.includes("LIMIT")) {
      if (f.single_limit_cents !== null)
        card.single_limit_cents = Number(f.single_limit_cents);
      if (f.daily_limit_cents !== null)
        card.daily_limit_cents = Number(f.daily_limit_cents);
    }
    if (kind.includes("CONTROL"))
      for (const key of ["online", "overseas", "atm", "quickpass"] as const)
        if (typeof f[key] === "boolean") card.controls[key] = f[key] as boolean;
  }
  if (kind === "PAYEE_ADD") {
    payees.push({
      id: id("pye"),
      name: String(f.name),
      bank_name: String(f.bank_name),
      account_no_masked: `**** ${String(f.account_no).slice(-4)}`,
      alias: f.alias ? String(f.alias) : null,
      has_transfer_history: false,
    });
  }
  if (kind === "SCHEDULE_CREATE") {
    schedules.push({
      id: id("si"),
      target_display:
        accounts.find((a) => a.id === f.to_account_id)?.display_name ??
        payees.find((p) => p.id === f.payee_id)?.name ??
        "",
      amount_cents: Number(f.amount_cents),
      period: String(f.period),
      next_run_at: String(f.first_run_at),
      status: "ACTIVE",
    });
  }
  if (kind === "SCHEDULE_CANCEL") {
    const item = schedules.find((s) => s.id === f.instruction_id);
    if (item) item.status = "DELETED";
  }
  if (kind === "SPLIT_CREATE") {
    const participants = f.participant_payee_ids as string[];
    const count = participants.length + (f.include_self ? 1 : 0);
    const total = Number(f.total_cents);
    if (!count || total < count) error("金额不足以分摊");
    const share = Math.floor(total / count);
    const remainder = total % count;
    const self = f.include_self ? share + remainder : 0;
    splits.push({
      id: id("spl"),
      total_cents: total,
      self_share_cents: self,
      received_cents: 0,
      outstanding_cents: total - self,
      items: participants.map((pid, i) => ({
        id: id("spi"),
        payee_name: payees.find((p) => p.id === pid)?.name ?? pid,
        amount_cents: share + (!f.include_self && i === 0 ? remainder : 0),
        status: "UNPAID",
      })),
    });
  }
  if (kind === "ASSESSMENT_SUBMIT") {
    const answers = f.answers as { question_id: string; option_id: string }[];
    if (answers.length !== 6) error("请完成六题");
    const score = answers.reduce((n, a) => n + Number(a.option_id.slice(1)), 0);
    riskLevel =
      score <= 9 ? 1 : score <= 13 ? 2 : score <= 17 ? 3 : score <= 21 ? 4 : 5;
  }
  if (kind === "FUND_SUBSCRIBE") {
    const a = accounts.find((a) => a.id === f.account_id)!;
    const product = products.find((p) => p.id === f.product_id)!;
    const amount = Number(f.amount_cents);
    if (amount > a.available_cents || amount < product.min_amount_cents)
      error("金额不符合当前余额或起购要求");
    a.available_cents -= amount;
    const units = decimal4((BigInt(amount) * 1000000n) / raw4(product.nav));
    if (product.confirm_days === 0) {
      a.balance_cents -= amount;
      const h = holdings.find(
        (h) => h.account_id === a.id && h.product_id === product.id,
      );
      if (h) {
        h.units = decimal4(raw4(h.units) + raw4(units));
        h.available_units = h.units;
        h.market_value_cents += amount;
      } else
        holdings.push({
          account_id: a.id,
          product_id: product.id,
          units,
          available_units: units,
          market_value_cents: amount,
          unrealized_change_cents: 0,
          reserved_receivable_cents: 0,
        });
    } else a.pending_debit_cents += amount;
    orders.unshift({
      id: id("ord"),
      product_id: product.id,
      type: "SUBSCRIBE",
      status: product.confirm_days === 0 ? "CONFIRMED" : "PENDING",
      amount_cents: amount,
      units,
      confirm_at:
        product.confirm_days === 0 ? stamp : "2026-10-29T15:00:00+08:00",
      settle_at: null,
    });
  }
  if (kind === "FUND_REDEEM") {
    const h =
      holdings.find(
        (h) => h.account_id === f.account_id && h.product_id === f.product_id,
      ) ?? error("无可赎回持仓");
    const units = raw4(String(f.units));
    if (units <= 0n || units > raw4(h.available_units))
      error("份额超过可用持仓");
    const product = products.find((p) => p.id === f.product_id)!;
    const amount = Number((units * raw4(product.nav)) / 1000000n);
    h.available_units = decimal4(raw4(h.available_units) - units);
    h.reserved_receivable_cents += amount;
    h.market_value_cents -= amount;
    orders.unshift({
      id: id("ord"),
      product_id: product.id,
      type: "REDEEM",
      status: "PENDING",
      amount_cents: amount,
      units: String(f.units),
      confirm_at: null,
      settle_at: "2026-10-29T15:00:00+08:00",
    });
  }
  if (kind === "CARD_APPLY") {
    const purpose = String(f.purpose ?? "").trim();
    if (!purpose || purpose.length > 12) error("请设置 1–12 个字的卡片用途");
    const number = `622200000000${7000001 + cards.length}`;
    const accountId = id("acc");
    const cardId = id("crd");
    accounts.push({ id: accountId, type: "CURRENT", display_name: "新开活期",
      account_no_masked: `6222 **** ${number.slice(-4)}`, balance_cents: 0, available_cents: 0,
      pending_debit_cents: 0, pending_credit_cents: 0, current_card_id: cardId });
    cards.push({ id: cardId, account_id: accountId, last4: number.slice(-4), status: "ACTIVE",
      single_limit_cents: 100000, daily_limit_cents: 500000, daily_used_cents: 0,
      remaining_daily_cents: 500000, controls: { online: false, overseas: false, atm: true, quickpass: false }, current: true });
    cardNumbers.set(cardId, number);
  }
  if (kind === "CARD_LOST" && f.replace && card) {
    card.current = false;
    const newCard = {
      ...copy(card),
      id: id("crd"),
      status: "ACTIVE",
      last4: "0928",
      current: true,
      controls: { online: true, overseas: false, atm: false, quickpass: false },
    };
    cards.push(newCard);
    cardNumbers.set(newCard.id,'6222000000000000928');
    const account = accounts.find((a) => a.id === card.account_id);
    if (account) account.current_card_id = newCard.id;
  }
  if (kind === "MANDATE_CANCEL") {
    const s = subscriptions.find((s) => s.instruction_id === f.instruction_id);
    if (s) s.status = "DELETED";
  }
  p.status = "EXECUTED";
  if (p.view) p.view.status = "USED";
}
export async function previewRequest(
  path: string,
  body?: unknown,
): Promise<unknown> {
  await new Promise((r) => setTimeout(r, 120));
  const b = (body ?? {}) as Fields;
  const args = (b.args ?? {}) as Fields;
  const cardNumberPath = path.match(/^\/cards\/([a-zA-Z0-9_-]+)\/number$/);
  if (cardNumberPath) {
    const card = cards.find((c) => c.id === cardNumberPath[1]);
    const number = cardNumbers.get(cardNumberPath[1]);
    if (!card || !number) return error("此卡的完整卡号暂不可用");
    // Preview-only authentication simulation; real PIN checks belong in the backend.
    if (typeof b.session_id !== "string" || !b.session_id) return error("会话已失效，请重新登录");
    const key = b.session_id;
    const now = Date.now();
    const prior = cardPinAttempts.get(key);
    if (prior && prior.lockedUntil > now) return error("PIN 连续输错 5 次，请 1 分钟后重试");
    const failures = prior?.lockedUntil && prior.lockedUntil <= now ? 0 : prior?.failures ?? 0;
    if (b.pin !== "123456") {
      const next = failures + 1;
      cardPinAttempts.set(key, { failures: next, lockedUntil: next >= 5 ? now + 60000 : 0 });
      return error(next >= 5 ? "PIN 连续输错 5 次，请 1 分钟后重试" : `PIN 不正确，还可尝试 ${5 - next} 次`);
    }
    cardPinAttempts.delete(key);
    return { card_id: card.id, card_number: number, synthetic: true };
  }
  if (path === "/demo/customers")
    return { items: [{ customer_id: "cus_zs", display_name: "张三" }] };
  if (path === "/auth/login")
    return { customer: { customer_id: "cus_zs", display_name: "张三" } };
  if (path === "/auth/logout") return { revoked: true };
  if (path === "/sessions")
    return { session_id: id("ses"), created_at_wall: stamp };
  if (path.includes("/state")) return { values: { messages: [] }, tasks: [] };
  if (path.includes("/clarifications/current")) return { clarification: null };
  if (path.endsWith("/actions")) {
    const action_id = id("act");
    actions.set(action_id, {
      kind: String(b.action_kind),
      fields: b.fields as Fields,
      sid: path.split("/")[2],
    });
    return { action_id, normalized_fields: b.fields };
  }
  if (path === "/plans/evaluate") {
    const action =
      actions.get((b.form_action_ids as string[])[0]) ?? error("操作不存在");
    return copy(evaluate(action));
  }
  if (path.startsWith("/tools/read/")) {
    let data: unknown;
    const tool = path.split("/").pop();
    switch (tool) {
      case "get_profile":
        data = {
          customer_id: "cus_zs",
          display_name: "张三",
          phone_masked: "138 **** 0826",
          sim_now: stamp,
          risk_profile: {
            level: riskLevel,
            is_valid: true,
            valid_until: "2027-09-01T00:00:00+08:00",
          },
        };
        break;
      case "get_accounts":
        data = {
          accounts,
          daily_outflow_cents: 128000,
          daily_outflow_limit_cents: 5000000,
          remaining_daily_outflow_cents: 4872000,
          balance_history: Array.from({ length: 28 }, (_, i) =>
            accounts.map((a) => ({
              account_id: a.id,
              date: `2026-10-${String(i + 1).padStart(2, "0")}`,
              balance_cents:
                a.balance_cents +
                (i < 22 ? -100000 : 0) +
                (i === 27 ? 0 : (i % 5) * 12000),
              available_cents: a.available_cents,
            })),
          ).flat(),
        };
        break;
      case "list_payees":
        data = { items: payees, next_cursor: null };
        break;
      case "get_cards":
        data = { items: cards };
        break;
      case "get_transactions": {
        const items = tx.filter(
          (t) =>
            (!args.category || t.category === args.category) &&
            (!args.account_id || t.account_id === args.account_id) &&
            (!args.from_at || t.occurred_at >= String(args.from_at)) &&
            (!args.to_at || t.occurred_at < String(args.to_at)),
        );
        data = {
          items,
          next_cursor: null,
          income_cents: items
            .filter((t) => t.direction === "IN")
            .reduce((n, t) => n + t.amount_cents, 0),
          expense_cents: items
            .filter((t) => t.direction === "OUT")
            .reduce((n, t) => n + t.amount_cents, 0),
          totals_scope: "filtered_all",
        };
        break;
      }
      case "get_spending_summary": {
        const month = String(args.month);
        const dataTx = tx.filter((t) => t.occurred_at.startsWith(month));
        const spending = dataTx.filter(
          (t) => t.kind === "CARD_SPEND" || t.kind === "MANDATE",
        );
        const total = spending.reduce((n, t) => n + t.amount_cents, 0);
        const names = [...new Set(spending.map((t) => t.category))];
        data = {
          month,
          total_cents: total,
          previous_total_cents: 0,
          mom_change_cents: total,
          mom_change_bp: null,
          categories: names.map((category) => {
            const amount = spending
              .filter((t) => t.category === category)
              .reduce((n, t) => n + t.amount_cents, 0);
            return {
              category,
              amount_cents: amount,
              share_bp: Math.floor((amount * 10000) / Math.max(total, 1)),
              previous_amount_cents: 0,
            };
          }),
          daily: Array.from(
            {
              length: new Date(
                Number(month.slice(0, 4)),
                Number(month.slice(5)),
                0,
              ).getDate(),
            },
            (_, i) => {
              const date = `${month}-${String(i + 1).padStart(2, "0")}`;
              return {
                date,
                amount_cents: spending
                  .filter((t) => t.occurred_at.startsWith(date))
                  .reduce((n, t) => n + t.amount_cents, 0),
              };
            },
          ),
          transfer_out_cents: dataTx
            .filter((t) => t.kind === "P2P_EXTERNAL")
            .reduce((n, t) => n + t.amount_cents, 0),
        };
        break;
      }
      case "detect_anomalies":
        data = {
          items: tx
            .filter(
              (t) =>
                t.id === "tx_5" &&
                t.occurred_at >= String(args.from_at) &&
                t.occurred_at < String(args.to_at),
            )
            .map((transaction) => ({
              transaction,
              codes: ["ANOMALY_NIGHT_LARGE"],
              evidence: {
                baseline_count: 18,
                mean_cents: 24800,
                threshold_cents: 100000,
                window_minutes: null,
              },
            })),
        };
        break;
      case "list_subscriptions": {
        const active = subscriptions.filter((s) => s.status === "ACTIVE");
        data = {
          items: subscriptions.filter(
            (s) =>
              args.status === "ALL" || s.status === (args.status ?? "ACTIVE"),
          ),
          next_cursor: null,
          total_monthly_cents: active.reduce((n, s) => n + s.amount_cents, 0),
          total_annual_cents: active.reduce(
            (n, s) => n + s.annual_cost_cents,
            0,
          ),
        };
        break;
      }
      case "detect_subscriptions":
        data = {
          items: subscriptions.map((s) => ({
            ...s,
            is_idle: s.instruction_id === "si_gym",
            typical_amount_cents: s.amount_cents,
            occurrences: 6,
            usage_evidence: {
              recent_usage_count: s.instruction_id === "si_gym" ? 0 : 4,
              previous_usage_count: 8,
            },
          })),
        };
        break;
      case "upcoming_charges": {
        const active = activeUpcoming(Number(args.days ?? 7));
        const total = active.reduce((n, s) => n + s.amount_cents, 0);
        data = {
          items: active.map((s) => ({
            ...s,
            due_at: s.next_due_at,
            billing_id: s.instruction_id,
          })),
          scheduled_items: [],
          reserve_total_cents: total,
          charges_total_cents: total,
          scheduled_transfers_total_cents: 0,
          by_account: [
            {
              account_id: accounts[0].id,
              reserve_cents: total,
              max_outflow_cents: accounts[0].available_cents - total,
            },
          ],
        };
        break;
      }
      case "list_products":
      case "compare_products":
        data = {
          items:
            tool === "compare_products"
              ? products.filter((p) =>
                  (args.product_ids as string[]).includes(p.id),
                )
              : products,
          next_cursor: null,
        };
        break;
      case "get_product":
        data = products.find((p) => p.id === args.product_id);
        break;
      case "get_holdings":
        data = { holdings, orders };
        break;
      case "get_risk_profile":
        data = {
          level: riskLevel,
          is_valid: true,
          valid_until: "2027-09-01",
          assessment_id: "asm_preview",
        };
        break;
      case "get_risk_questionnaire":
        data = {
          questionnaire_id: "rq_v1",
          validity_months: 12,
          questions: [
            "您的投资经验",
            "您可接受的本金波动",
            "您的投资期限",
            "您的收入稳定性",
            "您的流动性需求",
            "您的投资目标",
          ].map((prompt, i) => ({
            id: `q${i + 1}`,
            prompt,
            options: [
              "谨慎保守",
              "稳健为主",
              "适度承担风险",
              "能够承担较大风险",
            ].map((label, j) => ({ id: `o${j + 1}`, label })),
          })),
        };
        break;
      case "list_scheduled":
        data = {
          items: schedules.filter(
            (s) => s.status === args.status || args.status === "ALL",
          ),
          next_cursor: null,
        };
        break;
      case "get_split_status":
        data = { items: splits, next_cursor: null };
        break;
      default:
        error("预览未实现此读取工具");
    }
    return copy({
      data,
      source_ref: id("src"),
      untrusted_paths: [],
      versions: { cus_zs: 1 },
      observed_at: stamp,
    });
  }
  if (path.startsWith("/approvals/")) {
    const approvalId = path.split("/")[2];
    const p =
      [...plans.values()].find((p) => p.view?.approval_id === approvalId) ??
      error("审批不存在");
    const view = p.view!;
    if (path.endsWith("/confirm")) {
      if (b.view_hash !== view.view_hash) error("面板已变化");
      if (Date.parse(view.expires_at_wall) <= Date.now()) error("授权已过期");
      if (view.otp_required && b.otp !== "123456") error("预览验证码为 123456");
      if (view.risk_ack_required && !b.risk_ack) error("请阅读风险揭示");
      view.status = "CONFIRMED";
      return {
        approval_id: approvalId,
        plan_id: view.plan_id,
        status: "CONFIRMED",
      };
    }
    if (path.endsWith("/reject")) {
      view.status = "REJECTED";
      p.status = "REJECTED";
      return { status: "REJECTED" };
    }
    if (path.endsWith("/otp"))
      return {
        sent: true,
        retry_after_seconds: 60,
        expires_at_wall: view.expires_at_wall,
      };
    return copy(view);
  }
  if (path.startsWith("/plans/")) {
    const planId = path.split("/")[2];
    const p = plans.get(planId) ?? error("计划不存在");
    if (path.endsWith("/execute")) execute(planId);
    return copy({
      plan_id: planId,
      status: p.status,
      decision: p.decision,
      execution:
        p.status === "EXECUTED"
          ? { status: "EXECUTED", committed_at: stamp }
          : null,
    } satisfies PlanStatus);
  }
  if (path.startsWith("/approvals"))
    return {
      items: [...plans.values()]
        .filter((p) => p.view?.status === "PENDING_APPROVAL")
        .map((p) => p.view),
      next_cursor: null,
    };
  if (path.startsWith("/notifications"))
    return { items: [], next_cursor: null, unread_count: 0 };
  if (path.startsWith("/audit"))
    return {
      items: [...plans.values()].map((p) => ({
        id: p.decision.evaluation_id,
        event_type:
          p.status === "EXECUTED" ? "EXECUTION_COMMITTED" : "PLAN_EVALUATED",
        code: null,
        message:
          p.status === "EXECUTED" ? "合成数据操作已完成" : "计划已完成演示预演",
        created_at_wall: stamp,
        plan_id: p.decision.plan_id,
        hash: "preview-only",
      })),
      next_cursor: null,
    };
  return error("此预览能力尚不可用");
}
export async function* previewStream(
  _sid: string,
  _body: Fields,
  resume: boolean,
): AsyncGenerator<StreamEvent> {
  yield {
    event: "updates",
    data: { progress: { step: "understand", label: "理解需求" } },
  };
  await new Promise((r) => setTimeout(r, 450));
  yield {
    event: "updates",
    data: { progress: { step: "gather", label: "读取账户信息" } },
  };
  await new Promise((r) => setTimeout(r, 450));
  yield {
    event: "updates",
    data: {
      reply: {
        id: id("msg"),
        type: "ai",
        content: resume
          ? "审批状态已更新。此处为交互预览，请在安全中心核对计划结果。"
          : "我可以帮助您分析账单、查看账户，以及说明转账、卡片、订阅和理财操作。当前为合成数据预览，您可以通过 App 的业务页面体验预演与确认流程。真实模式接入后，您的原话将先登记到安全网关，再由小安规划执行。",
      },
    },
  };
}
