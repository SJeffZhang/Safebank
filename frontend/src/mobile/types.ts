export interface Account {
  id: string;
  type: string;
  display_name: string;
  account_no_masked: string;
  balance_cents: number;
  available_cents: number;
  pending_debit_cents: number;
  pending_credit_cents: number;
  current_card_id: string | null;
}
export interface Payee {
  id: string;
  name: string;
  bank_name: string;
  account_no_masked: string;
  has_transfer_history: boolean;
  alias: string | null;
}
export interface Transaction {
  id: string;
  account_id: string;
  direction: "IN" | "OUT";
  kind: string;
  status: string;
  amount_cents: number;
  counterparty: string;
  memo: string | null;
  category: string;
  occurred_at: string;
  card_id: string | null;
}
export interface BankCard {
  id: string;
  account_id: string;
  last4: string;
  status: string;
  single_limit_cents: number;
  daily_limit_cents: number;
  daily_used_cents: number;
  remaining_daily_cents: number;
  controls: {
    online: boolean;
    overseas: boolean;
    atm: boolean;
    quickpass: boolean;
  };
  current: boolean;
}
export interface Subscription {
  instruction_id: string;
  account_id: string;
  merchant_name: string;
  period: string;
  amount_cents: number;
  annual_cost_cents: number;
  next_due_at: string;
  status: string;
}
export type ProductType = "MMF" | "BOND" | "MIXED" | "EQUITY";
export interface Product {
  id: string;
  name: string;
  product_type: ProductType;
  risk_level: number;
  risk_label: string;
  nav: string;
  nav_date: string;
  recent_return_bp: number;
  confirm_days: number;
  settle_days: number;
  min_amount_cents: number;
  risk_disclosure: string;
  disclosure_version: string;
  nav_history: { date: string; nav: string }[];
}
export interface Finding {
  code: string;
  message?: string;
  severity?: string;
}
export interface Preflight {
  executable: boolean;
  balances: {
    account_id: string;
    balance_before_cents: number;
    balance_after_cents: number;
    available_before_cents: number;
    available_after_cents: number;
    daily_used_before_cents: number;
    daily_used_after_cents: number;
  }[];
  scheduled_reserve_cents: number;
  steps: unknown[];
  holdings: unknown[];
}
export interface Decision {
  plan_id: string;
  evaluation_id: string;
  decision: "ALLOW" | "REQUIRE_APPROVAL" | "REPLAN" | "DENY";
  risk: "GREEN" | "YELLOW" | "RED";
  approval_id: string | null;
  findings: Finding[];
  preflight: Preflight | null;
  replan_hints: { id: string; label: string }[];
}
export interface PlanView {
  approval_id: string;
  plan_id: string;
  evaluation_id: string;
  session_id: string;
  status: string;
  origin: "app_form" | "agent";
  risk: "GREEN" | "YELLOW" | "RED";
  steps: {
    step_id: string;
    action_kind: string;
    title: string;
    fields: {
      key: string;
      label: string;
      value: unknown;
      unit: string | null;
    }[];
    irreversible: boolean;
  }[];
  preflight: Preflight | null;
  findings: Finding[];
  disclosures: { product_id: string; version: string; text: string }[];
  risk_ack_required: boolean;
  otp_required: boolean;
  expires_at_wall: string;
  view_hash: string;
}
export interface PlanStatus {
  plan_id: string;
  status: string;
  decision: Decision;
  execution?: {
    status: string;
    committed_at?: string;
    error_code?: string;
  } | null;
}
export interface Envelope<T> {
  data: T;
  source_ref: string;
  untrusted_paths: string[];
  versions: Record<string, number>;
  observed_at: string;
}
export interface Profile {
  customer_id: string;
  display_name: string;
  phone_masked: string;
  sim_now: string;
  risk_profile: {
    level: number | null;
    is_valid: boolean;
    valid_until: string | null;
  };
}
export interface Summary {
  month: string;
  total_cents: number;
  previous_total_cents: number;
  mom_change_cents: number;
  mom_change_bp: number | null;
  categories: {
    category: string;
    amount_cents: number;
    share_bp: number;
    previous_amount_cents: number;
  }[];
  daily: { date: string; amount_cents: number }[];
  transfer_out_cents: number;
}
export interface Upcoming {
  items: {
    billing_id: string;
    instruction_id: string;
    account_id: string;
    merchant_name: string;
    due_at: string;
    amount_cents: number;
  }[];
  reserve_total_cents: number;
  charges_total_cents: number;
  scheduled_transfers_total_cents: number;
  by_account: {
    account_id: string;
    reserve_cents: number;
    max_outflow_cents: number;
  }[];
}
export interface Scheduled {
  id: string;
  target_display: string;
  amount_cents: number;
  period: string;
  next_run_at: string;
  status: string;
}
export interface Split {
  id: string;
  total_cents: number;
  self_share_cents: number;
  received_cents: number;
  outstanding_cents: number;
  items: {
    id: string;
    payee_name: string;
    amount_cents: number;
    status: string;
  }[];
}
export interface Question {
  id: string;
  prompt: string;
  options: { id: string; label: string }[];
}
export interface UICard {
  id: string;
  type: string;
  source_ref?: string;
  data: unknown;
  actions: {
    kind: "HINT" | "CLARIFY" | "NAVIGATE" | "OPEN_APPROVAL";
    ref: string;
  }[];
}
export interface Clarification {
  id: string;
  kind: string;
  required_fields: string[];
  prompt_template_id: string;
  candidates: {
    field: string;
    entity_id: string;
    label: string;
    source_ref: string;
  }[];
}
export interface StreamEvent {
  event: string;
  data: Record<string, unknown>;
}
export interface Notice {
  id: string;
  type: string;
  title: string;
  body: string;
  created_at: string;
  read_at_wall: string | null;
}
export interface Audit {
  id: string;
  event_type: string;
  code: string | null;
  message: string;
  created_at_wall: string;
  plan_id: string | null;
  hash: string;
}
export interface ApprovalItem {
  approval_id: string;
  plan_id: string;
  session_id: string;
  status: string;
  risk: string;
  expires_at_wall: string;
}
export type Fields = Record<string, unknown>;
export const uid = () => `req_${crypto.randomUUID()}`;

export interface CardNumberDetail {card_id:string;card_number:string;synthetic:boolean}
